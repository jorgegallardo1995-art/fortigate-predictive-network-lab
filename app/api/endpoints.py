"""FortiOS REST API endpoint catalogue.

Every path here is **configurable** from ``config/config.yaml`` (section
``fortios_endpoints``). The values below are only the fallback defaults and are
CANDIDATE paths for FortiOS 7.x - they must be validated against the real device
with ``scripts/probe_fortios_endpoints.py`` before trusting them (ETAPA 6).

Only GET is ever issued against these. See :mod:`app.api.fortigate_client`.
"""

from __future__ import annotations

from app.core.config import get_config

# Documented default paths (relative to /api/v2/). Keep this dict and config.yaml
# in sync; config.yaml wins at runtime.
DEFAULT_ENDPOINTS: dict[str, str] = {
    # identification / device
    "system_status": "monitor/system/status",
    "firmware": "monitor/system/firmware",
    "time": "monitor/system/time",
    # resources
    "system_resource": "monitor/system/resource/usage",
    "sensor_info": "monitor/system/sensor-info",
    # interfaces
    "system_interface": "monitor/system/interface",
    "interface_select": "monitor/system/interface/select",
    "available_interfaces": "monitor/system/available-interfaces",
    "cmdb_interface": "cmdb/system/interface",
    "cmdb_global": "cmdb/system/global",
    # routing
    "router_ipv4": "monitor/router/ipv4",
    # SD-WAN / performance SLA
    "sdwan_health_check": "monitor/virtual-wan/health-check",
    "sdwan_members": "monitor/virtual-wan/members",
}

# Human-readable notes used by the probe script and the /research page.
ENDPOINT_NOTES: dict[str, str] = {
    "system_status": "hostname, model, serial, FortiOS version, uptime",
    "firmware": "installed firmware branch/build",
    "time": "device clock / timezone",
    "system_resource": "CPU %, memory %, sessions, session setup rate",
    "sensor_info": "temperature/fan sensors (may be absent on a 50F)",
    "system_interface": "per-interface counters: rx/tx bytes, packets, errors, drops, link",
    "interface_select": "same as system_interface filtered by ?interface_name=",
    "available_interfaces": "interface inventory + speed/state",
    "cmdb_interface": "configured speed, duplex, mtu, alias, mode",
    "cmdb_global": "hostname, timezone, admin timeout",
    "router_ipv4": "active routing table (default-route presence => WAN reachable)",
    "sdwan_health_check": "Performance SLA results: latency, jitter, packet loss, state",
    "sdwan_members": "SD-WAN member interface status",
}


def resolve_endpoints() -> dict[str, str]:
    """Merge config.yaml overrides on top of :data:`DEFAULT_ENDPOINTS`."""
    merged = dict(DEFAULT_ENDPOINTS)
    merged.update({k: v for k, v in get_config().endpoints.items() if v})
    return merged
