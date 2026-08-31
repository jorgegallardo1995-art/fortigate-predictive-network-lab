"""Parse the FortiOS ``virtual-wan/health-check`` monitor response.

The exact JSON shape of this endpoint varies between FortiOS builds, so the
parser is deliberately tolerant: it looks for the usual keys
(``latency``/``jitter``/``packet_loss``/``packetloss``/``state``) at either the
health-check level or a nested member level, and returns NULLs for anything it
cannot find rather than raising.

Run ``scripts/probe_fortios_endpoints.py`` to capture the real shape from your
device and tighten this if needed.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.schemas.telemetry import SlaReading

logger = get_logger("collector.sla")

_LATENCY_KEYS = ("latency", "latency_ms")
_JITTER_KEYS = ("jitter", "jitter_ms")
_LOSS_KEYS = ("packet_loss", "packetloss", "packet_loss_pct", "packetloss")
_STATE_KEYS = ("state", "status", "sla_state")
_SESSION_KEYS = ("session", "sessions", "tx_bandwidth")


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _extract_metrics(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "latency_ms": _to_float(_first(node, _LATENCY_KEYS)),
        "jitter_ms": _to_float(_first(node, _JITTER_KEYS)),
        "packet_loss_pct": _to_float(_first(node, _LOSS_KEYS)),
        "sla_state": (_first(node, _STATE_KEYS) or None),
        "probe_sessions": _first(node, _SESSION_KEYS),
    }


def parse_health_check(
    results: dict[str, Any], target_map: dict[str, dict[str, str]], wan_interface: str
) -> list[SlaReading]:
    """Turn a ``health-check`` results dict into a list of :class:`SlaReading`.

    ``target_map`` maps the health-check name -> ``{label, target}`` from config.
    Health-checks not present in the map are still captured, labelled by name.
    """
    readings: list[SlaReading] = []
    if not isinstance(results, dict):
        return readings

    for hc_name, hc_data in results.items():
        if not isinstance(hc_data, dict):
            continue
        mapping = target_map.get(hc_name, {})
        label = mapping.get("label", hc_name.lower())
        target = mapping.get("target")

        node: dict[str, Any] = hc_data
        members = hc_data.get("members")
        if isinstance(members, dict):
            node = members.get(wan_interface) or next(iter(members.values()), hc_data)
        elif isinstance(members, list) and members:
            node = next(
                (m for m in members if str(m.get("interface")) == wan_interface), members[0]
            )

        metrics = _extract_metrics(node if isinstance(node, dict) else hc_data)
        sessions = metrics.pop("probe_sessions")
        readings.append(
            SlaReading(
                health_check_name=hc_name,
                label=label,
                target=target,
                probe_sessions=int(sessions) if isinstance(sessions, (int, float)) else None,
                **metrics,
            )
        )

    if not readings:
        logger.debug("health-check response contained no parseable entries")
    return readings


def aggregate(readings: list[SlaReading]) -> dict[str, Any]:
    """Collapse per-target SLA readings into the aggregate columns of ``telemetry_raw``."""
    lat = [r.latency_ms for r in readings if r.latency_ms is not None]
    jit = [r.jitter_ms for r in readings if r.jitter_ms is not None]
    loss = [r.packet_loss_pct for r in readings if r.packet_loss_pct is not None]
    states = [str(r.sla_state).lower() for r in readings if r.sla_state]
    any_up = any(s in ("up", "alive", "1", "true") for s in states)
    return {
        "latency_ms": round(sum(lat) / len(lat), 3) if lat else None,
        "jitter_ms": round(sum(jit) / len(jit), 3) if jit else None,
        "packet_loss_pct": round(sum(loss) / len(loss), 3) if loss else None,
        "sla_status": ("up" if any_up else "down") if states else None,
    }
