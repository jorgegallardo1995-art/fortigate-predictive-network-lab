"""``FortiGateClient`` - read-only FortiOS REST API client.

Hard guarantees:

* **GET only.** There is no ``post``/``put``/``delete`` method on this class.
  The collector cannot modify the FortiGate even by accident.
* The API token is sent in the ``Authorization: Bearer`` header, never in the URL,
  and is never logged (see :class:`app.core.logging.SecretFilter`).
* A failed or missing endpoint raises a typed exception that the collector
  catches per-metric; one bad endpoint never aborts a whole cycle.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.api.endpoints import resolve_endpoints
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("fortigate.client")


class FortiGateError(Exception):
    """Base class for FortiGate client errors."""


class FortiGateAuthError(FortiGateError):
    """401/403 - token missing, wrong, or trusted-host mismatch."""


class FortiGateEndpointError(FortiGateError):
    """404 / unsupported endpoint on this FortiOS version -> caller stores NULL."""


class FortiGateUnavailableError(FortiGateError):
    """Connection/timeout/5xx - the device is unreachable right now."""


class FortiGateResponse:
    """Parsed FortiOS response plus timing metadata."""

    __slots__ = ("elapsed_ms", "http_status", "raw", "results", "status", "version")

    def __init__(self, payload: dict[str, Any], elapsed_ms: float, http_status: int) -> None:
        self.raw: dict[str, Any] = payload
        self.results: Any = payload.get("results", payload)
        self.elapsed_ms = elapsed_ms
        self.http_status = http_status
        self.status: str | None = payload.get("status")
        self.version: str | None = payload.get("version")  # FortiOS version, when present


class FortiGateClient:
    """Synchronous, read-only client for the FortiOS REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        vdom: str | None = None,
        verify_ssl: bool | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.fortigate_base_url).rstrip("/")
        self._token = token if token is not None else s.fortigate_api_token
        self.vdom = vdom or s.fortigate_vdom
        self.verify_ssl = s.verify_ssl if verify_ssl is None else verify_ssl
        self.timeout = timeout or s.fortigate_timeout
        self.max_retries = max_retries if max_retries is not None else s.fortigate_max_retries
        self.endpoints = resolve_endpoints()

        self._client: httpx.Client | None = None
        self._last_api_latency_ms: float | None = None
        self._detected_version: str | None = None

        if not self.verify_ssl:
            # Self-signed cert on the FortiGate - silence the per-request warning once.
            import warnings

            warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    # --- lifecycle -------------------------------------------------------
    def _ensure_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.base_url,
                verify=self.verify_ssl,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> FortiGateClient:
        self._ensure_client()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- properties ----------------------------------------------------
    @property
    def last_api_latency_ms(self) -> float | None:
        """Response time (ms) of the most recent successful GET."""
        return self._last_api_latency_ms

    @property
    def detected_fortios_version(self) -> str | None:
        """FortiOS version string discovered via :meth:`get_system_status`."""
        return self._detected_version

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # --- core request -------------------------------------------------
    def _request(self, path: str, params: dict[str, Any] | None = None) -> FortiGateResponse:
        """Issue one GET with retry/backoff. Never issues any other verb."""
        client = self._ensure_client()
        query: dict[str, Any] = {"vdom": self.vdom}
        if params:
            query.update(params)
        # base_url already ends with /api/v2 ; endpoint paths are given relative to it.
        if path.startswith("http"):
            url = path
        elif path.startswith("/api/"):
            url = f"https://{self.base_url.split('://', 1)[1].split('/', 1)[0]}{path}"
        else:
            url = f"{self.base_url}/{path.lstrip('/')}"

        @retry(
            retry=retry_if_exception_type(FortiGateUnavailableError),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            reraise=True,
        )
        def _do() -> FortiGateResponse:
            start = time.perf_counter()
            try:
                resp = client.get(url, params=query)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                raise FortiGateUnavailableError(f"{type(exc).__name__} for {path}") from exc
            except httpx.HTTPError as exc:
                raise FortiGateUnavailableError(str(exc)) from exc
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            if resp.status_code in (401, 403):
                raise FortiGateAuthError(f"HTTP {resp.status_code} on {path} (check token / trusthost)")
            if resp.status_code == 404:
                raise FortiGateEndpointError(f"HTTP 404 on {path} (unsupported on this FortiOS?)")
            if resp.status_code >= 500:
                raise FortiGateUnavailableError(f"HTTP {resp.status_code} on {path}")
            if resp.status_code != 200:
                raise FortiGateError(f"HTTP {resp.status_code} on {path}")

            try:
                payload = resp.json()
            except ValueError as exc:
                raise FortiGateError(f"Non-JSON response on {path}") from exc

            self._last_api_latency_ms = elapsed_ms
            logger.debug("GET %s -> 200 in %.0f ms", path, elapsed_ms)
            return FortiGateResponse(payload, elapsed_ms, resp.status_code)

        try:
            return _do()
        except RetryError as exc:  # pragma: no cover - reraise=True makes this rare
            raise FortiGateUnavailableError(str(exc)) from exc

    def get(self, endpoint_key: str, params: dict[str, Any] | None = None) -> FortiGateResponse:
        """GET a catalogued endpoint by its key (see :mod:`app.api.endpoints`)."""
        if endpoint_key not in self.endpoints:
            raise FortiGateEndpointError(f"Unknown endpoint key '{endpoint_key}'")
        return self._request(self.endpoints[endpoint_key], params)

    def get_path(self, path: str, params: dict[str, Any] | None = None) -> FortiGateResponse:
        """GET an arbitrary API path (used by the probe script)."""
        return self._request(path, params)

    # --- convenience wrappers ---------------------------------------
    def test_connection(self) -> dict[str, Any]:
        """Lightweight reachability + auth check. Returns a small status dict."""
        info: dict[str, Any] = {"reachable": False, "authenticated": False}
        try:
            resp = self.get("system_status")
            info["reachable"] = True
            info["authenticated"] = True
            info["api_latency_ms"] = round(resp.elapsed_ms, 1)
            res = resp.results if isinstance(resp.results, dict) else {}
            info["hostname"] = res.get("hostname")
            info["model"] = res.get("model") or res.get("model_name")
            info["fortios_version"] = resp.version or res.get("version")
            self._detected_version = info["fortios_version"]
        except FortiGateAuthError as exc:
            info["reachable"] = True
            info["error"] = f"auth: {exc}"
        except FortiGateUnavailableError as exc:
            info["error"] = f"unreachable: {exc}"
        except FortiGateError as exc:
            info["error"] = str(exc)
        return info

    def get_system_status(self) -> dict[str, Any]:
        resp = self.get("system_status")
        data = dict(resp.results) if isinstance(resp.results, dict) else {}
        # FortiOS 6.0.x returns an empty "results"; serial/version/build/hostname/model
        # live at the top level of the payload.
        for k in ("serial", "version", "build", "hostname", "model", "model_name"):
            if isinstance(resp.raw, dict) and resp.raw.get(k) is not None:
                data.setdefault(k, resp.raw[k])
        self._detected_version = resp.version or data.get("version") or self._detected_version
        return data

    def get_system_resource(self) -> dict[str, Any]:
        resp = self.get("system_resource")
        return resp.results if isinstance(resp.results, dict) else {}

    def get_firmware(self) -> dict[str, Any]:
        """Firmware info - ``results.current.platform-id`` gives the hardware model."""
        resp = self.get("firmware")
        return resp.results if isinstance(resp.results, dict) else {}

    def get_cmdb_global(self) -> dict[str, Any]:
        """``cmdb/system/global`` - hostname, timezone and other device-wide config."""
        resp = self.get("cmdb_global")
        return resp.results if isinstance(resp.results, dict) else {}

    def get_cmdb_interface(self, name: str) -> dict[str, Any]:
        """Configured (not runtime) interface attributes: mtu, mode, speed, alias."""
        resp = self.get("cmdb_interface", params={"mkey": name})
        res = resp.results
        if isinstance(res, list):
            return next((i for i in res if i.get("name") == name), res[0] if res else {})
        return res if isinstance(res, dict) else {}

    def get_interface(self, name: str) -> dict[str, Any]:
        """Counters for a single interface via the ``interface_select`` endpoint."""
        resp = self.get("interface_select", params={"interface_name": name})
        res = resp.results
        if isinstance(res, dict):
            return res.get(name, res)
        return {}

    def get_router_ipv4(self) -> list[dict[str, Any]]:
        resp = self.get("router_ipv4")
        return resp.results if isinstance(resp.results, list) else []

    def get_sdwan_health_check(self) -> dict[str, Any]:
        """Performance SLA results keyed by health-check name."""
        resp = self.get("sdwan_health_check")
        return resp.results if isinstance(resp.results, dict) else {}

    def get_link_monitor(self) -> dict[str, Any]:
        """link-monitor probe results keyed by monitor name."""
        resp = self.get("link_monitor")
        return resp.results if isinstance(resp.results, dict) else {}

    def get_sensor_info(self) -> list[dict[str, Any]]:
        resp = self.get("sensor_info")
        return resp.results if isinstance(resp.results, list) else []
