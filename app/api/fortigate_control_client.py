"""Write-capable FortiGate client used ONLY to inject controlled faults.

This is deliberately separate from :class:`app.api.fortigate_client.FortiGateClient`,
which is read-only and does the scientific measurement. This client:

* uses its OWN token (``FORTIGATE_CONTROL_TOKEN``), issued to a REST API admin
  whose accprofile has write access to ``netgrp`` (system.interface) only -
  never the read token used by the collector;
* only ever touches ``system/interface`` (status + shaping bandwidth);
* reads the current value before changing it, so the fault injector can always
  restore the exact previous state.

If no control token is configured the client is inert (``configured`` is False)
and every method raises, so the measurement path can never be affected by a
missing credential.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.api.fortigate_client import (
    FortiGateAuthError,
    FortiGateError,
    FortiGateUnavailableError,
)
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("fortigate-control")


class FortiGateControlClient:
    """Minimal PUT/GET client for controlled fault injection on one interface."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        vdom: str | None = None,
        verify_ssl: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.fortigate_base_url).rstrip("/")
        self._token = token if token is not None else s.fortigate_control_token
        self.vdom = vdom or s.fortigate_vdom
        self.verify_ssl = s.verify_ssl if verify_ssl is None else verify_ssl
        self.timeout = timeout or s.fortigate_timeout
        self._client: httpx.Client | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token)

    # --- lifecycle -----------------------------------------------------
    def _ensure(self) -> httpx.Client:
        if not self.configured:
            raise FortiGateAuthError("no FORTIGATE_CONTROL_TOKEN configured")
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.base_url,
                verify=self.verify_ssl,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> FortiGateControlClient:
        self._ensure()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- requests ------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _handle(self, resp: httpx.Response, what: str) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise FortiGateAuthError(f"{what}: {resp.status_code} (token/trusted-host/permissions)")
        if resp.status_code >= 500:
            raise FortiGateUnavailableError(f"{what}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FortiGateError(f"{what}: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return {}

    def get_interface(self, name: str) -> dict[str, Any]:
        """Current cmdb config for ``name`` (status, inbandwidth, outbandwidth)."""
        client = self._ensure()
        try:
            resp = client.get(self._url(f"cmdb/system/interface/{name}"), params={"vdom": self.vdom})
        except httpx.HTTPError as exc:
            raise FortiGateUnavailableError(f"get_interface: {exc}") from exc
        payload = self._handle(resp, "get_interface")
        results = payload.get("results") or []
        if not results:
            raise FortiGateError(f"interface {name!r} not found")
        return results[0]

    def _put_interface(self, name: str, body: dict[str, Any], what: str) -> dict[str, Any]:
        client = self._ensure()
        try:
            resp = client.put(
                self._url(f"cmdb/system/interface/{name}"), params={"vdom": self.vdom}, json=body
            )
        except httpx.HTTPError as exc:
            raise FortiGateUnavailableError(f"{what}: {exc}") from exc
        return self._handle(resp, what)

    def set_status(self, name: str, up: bool) -> dict[str, Any]:
        """Administratively bring the interface up or down."""
        status = "up" if up else "down"
        logger.warning("fault-injection: setting %s status=%s", name, status)
        return self._put_interface(name, {"status": status}, f"set_status({status})")

    def set_bandwidth(self, name: str, in_kbps: int, out_kbps: int) -> dict[str, Any]:
        """Set the shaping ceiling (kbps). 0 means unlimited on FortiOS."""
        logger.warning("fault-injection: setting %s bandwidth in=%d out=%d kbps",
                       name, in_kbps, out_kbps)
        return self._put_interface(
            name, {"inbandwidth": int(in_kbps), "outbandwidth": int(out_kbps)},
            "set_bandwidth",
        )
