"""FortiGateClient tests - all responses are mocked with respx, no real device."""

from __future__ import annotations

import httpx
import pytest
import respx
from app.api.fortigate_client import (
    FortiGateAuthError,
    FortiGateClient,
    FortiGateEndpointError,
    FortiGateUnavailableError,
)

# httpx drops the default :443, so respx routes must be registered without it.
BASE = "https://fortigate.test/api/v2"


def _client() -> FortiGateClient:
    return FortiGateClient(max_retries=2, timeout=2.0)


@respx.mock
def test_system_status_parsed() -> None:
    respx.get(f"{BASE}/monitor/system/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "hostname": "FGT50F", "model": "FGT50F",
                    "serial": "FGT50Fx", "version": "v7.4.4",
                },
                "version": "v7.4.4",
                "status": "success",
            },
        )
    )
    with _client() as c:
        status = c.get_system_status()
    assert status["hostname"] == "FGT50F"
    assert c.detected_fortios_version == "v7.4.4"
    assert c.last_api_latency_ms is not None


@respx.mock
def test_fortios_6_0_empty_results_uses_top_level_fields() -> None:
    """FortiOS 6.0.x returns results={} with serial/version/build at the top level."""
    respx.get(f"{BASE}/monitor/system/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {},
                "serial": "FGT50E5619016282",
                "version": "v6.0.4",
                "build": 231,
                "status": "success",
            },
        )
    )
    with _client() as c:
        status = c.get_system_status()
    assert status["serial"] == "FGT50E5619016282"
    assert status["version"] == "v6.0.4"
    assert c.detected_fortios_version == "v6.0.4"


@respx.mock
def test_404_endpoint_raises_endpoint_error() -> None:
    respx.get(f"{BASE}/monitor/virtual-wan/health-check").mock(return_value=httpx.Response(404))
    with _client() as c, pytest.raises(FortiGateEndpointError):
        c.get_sdwan_health_check()


@respx.mock
def test_401_raises_auth_error() -> None:
    respx.get(f"{BASE}/monitor/system/status").mock(return_value=httpx.Response(401))
    with _client() as c, pytest.raises(FortiGateAuthError):
        c.get_system_status()


@respx.mock
def test_retries_then_unavailable() -> None:
    route = respx.get(f"{BASE}/monitor/system/resource/usage").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with _client() as c, pytest.raises(FortiGateUnavailableError):
        c.get_system_resource()
    assert route.call_count == 2  # max_retries


@respx.mock
def test_test_connection_reports_auth_failure() -> None:
    respx.get(f"{BASE}/monitor/system/status").mock(return_value=httpx.Response(403))
    with _client() as c:
        info = c.test_connection()
    assert info["reachable"] is True
    assert info["authenticated"] is False


def test_client_has_no_write_methods() -> None:
    """Guard: the client must never expose a way to modify the FortiGate."""
    for verb in ("post", "put", "delete", "patch"):
        assert not hasattr(FortiGateClient, verb)
