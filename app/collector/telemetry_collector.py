"""The acquisition orchestrator.

``TelemetryCollector.run_cycle()`` performs one full cycle:

1. Ask the active source (real FortiGate or mock) for a :class:`SourceSnapshot`.
2. Run the ICMP prober (independent cross-check).
3. Derive rate metrics from the previous successful sample (bps, error rates...).
4. Persist an **immutable** ``telemetry_raw`` row + per-target SLA + probe rows.
5. Detect collection gaps and operational events.

Every metric is best-effort: a missing endpoint is recorded in ``missing_data``
and stored as NULL; it never aborts the cycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.api.fortigate_client import (
    FortiGateAuthError,
    FortiGateClient,
    FortiGateEndpointError,
    FortiGateError,
    FortiGateUnavailableError,
)
from app.collector.icmp_prober import IcmpProber, ProbeReading
from app.collector.mock_provider import MockSource
from app.collector.sla_collector import aggregate, parse_health_check, parse_link_monitor
from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import duration_ms, to_local_naive, utc_now
from app.core.version import COLLECTOR_VERSION, SCHEMA_VERSION
from app.database import repositories as repo
from app.database.database import session_scope
from app.database.models import (
    DATA_SOURCE_MOCK,
    DATA_SOURCE_REAL,
    ProbeResult,
    TelemetryRaw,
    TelemetrySla,
)
from app.schemas.telemetry import CycleContext, SlaReading, SourceSnapshot
from app.services import event_service
from app.services.gap_detector import GapDetector

logger = get_logger("collector")


class FortiGateSource:
    """Maps FortiOS REST responses to a :class:`SourceSnapshot`. GET only."""

    def __init__(self, client: FortiGateClient) -> None:
        self.client = client

    def fetch(self, ctx: CycleContext) -> SourceSnapshot:
        snap = SourceSnapshot(data_source=DATA_SOURCE_REAL)
        raw: dict[str, Any] = {}

        def _try(key: str, fn: Any) -> Any:
            try:
                value = fn()
                raw[key] = value
                return value
            except FortiGateEndpointError:
                snap.missing.append(key)
                logger.info("endpoint '%s' not available on this FortiOS - storing NULL", key)
            except FortiGateAuthError:
                raise
            except (FortiGateUnavailableError, FortiGateError) as exc:
                snap.missing.append(key)
                logger.warning("endpoint '%s' failed: %s", key, exc)
            return None

        try:
            status = _try("system_status", self.client.get_system_status)
            resource = _try("system_resource", self.client.get_system_resource)
            iface = _try("interface", lambda: self.client.get_interface(ctx.wan_interface))
            iface_cfg = _try("cmdb_interface", lambda: self.client.get_cmdb_interface(ctx.wan_interface))
            routes = _try("router_ipv4", self.client.get_router_ipv4)
            health = _try("sdwan_health_check", self.client.get_sdwan_health_check)
            link_mon = _try("link_monitor", self.client.get_link_monitor)
            sensors = _try("sensor_info", self.client.get_sensor_info)
            firmware = _try("firmware", self.client.get_firmware)
            cfg_global = _try("cmdb_global", self.client.get_cmdb_global)
        except FortiGateAuthError as exc:
            snap.error = f"auth: {exc}"
            return snap

        if status is None and resource is None and iface is None:
            snap.error = "no core endpoint responded"
            return snap

        status = status or {}
        resource = resource or {}
        iface = iface or {}
        iface_cfg = iface_cfg or {}
        firmware = firmware or {}
        cfg_global = cfg_global or {}

        model = status.get("model") or status.get("model_name")
        if not model:
            model = (firmware.get("current") or {}).get("platform-id")
        snap.identification = {
            "device_hostname": status.get("hostname") or cfg_global.get("hostname"),
            "device_model": model,
            "serial_number": status.get("serial"),
            "fortios_version": self.client.detected_fortios_version or status.get("version"),
            "wan_interface": ctx.wan_interface,
        }

        m: dict[str, Any] = {}
        _map_resource(resource, m)
        _map_interface(iface, iface_cfg, m, ctx)
        m["default_route_present"] = _has_default_route(routes)
        m["temperature_c"] = _max_temperature(sensors)

        sla_readings: list[SlaReading] = []
        if isinstance(health, dict) and health:
            sla_readings = parse_health_check(health, ctx.sla_targets, ctx.wan_interface)
        elif isinstance(link_mon, dict) and link_mon:
            sla_readings = parse_link_monitor(link_mon, ctx.sla_targets, ctx.wan_interface)
        if sla_readings:
            m.update(aggregate(sla_readings))
        else:
            snap.missing.append("sla_metrics")

        snap.metrics = m
        snap.sla = sla_readings
        snap.raw_json = raw
        snap.api_latency_ms = self.client.last_api_latency_ms
        snap.success = True
        return snap


def _map_resource(resource: dict[str, Any], m: dict[str, Any]) -> None:
    def _pct(name: str) -> float | None:
        node = resource.get(name)
        if isinstance(node, list) and node:
            val = node[0].get("current") if isinstance(node[0], dict) else node[0]
            return _f(val)
        return _f(node)

    m["cpu_pct"] = _pct("cpu")
    m["memory_pct"] = _pct("mem") if "mem" in resource else _pct("memory")
    m["disk_pct"] = _pct("disk")
    m["active_sessions"] = _i(_pct("session"))
    # 7.x: session_setup_rate ; 6.0.x: setuprate ; some builds: setup_rate
    for _key in ("session_setup_rate", "setuprate"):
        if _key in resource:
            m["new_sessions_per_second"] = _pct(_key)
            break
    else:
        m["new_sessions_per_second"] = _f(resource.get("setup_rate"))
    # 6.0.x does not expose uptime via REST; leave NULL (restart detection falls
    # back to collection gaps).
    m["uptime_seconds"] = _i(resource.get("uptime"))


_DUPLEX_MAP = {1: "full", 2: "half", "1": "full", "2": "half"}


def _map_interface(
    iface: dict[str, Any], iface_cfg: dict[str, Any], m: dict[str, Any], ctx: CycleContext
) -> None:
    m["rx_bytes"] = _i(iface.get("rx_bytes"))
    m["tx_bytes"] = _i(iface.get("tx_bytes"))
    m["rx_packets"] = _i(iface.get("rx_packets"))
    m["tx_packets"] = _i(iface.get("tx_packets"))
    m["rx_errors"] = _i(iface.get("rx_errors"))
    m["tx_errors"] = _i(iface.get("tx_errors"))
    # 6.0.x interface/select has no drop counters -> NULL.
    m["rx_drops"] = _i(iface.get("rx_dropped") or iface.get("rx_drops"))
    m["tx_drops"] = _i(iface.get("tx_dropped") or iface.get("tx_drops"))
    link = iface.get("link") if "link" in iface else iface.get("status")
    m["interface_status"] = _link_str(link)
    m["link_status"] = _link_str(link)
    m["interface_speed"] = str(iface.get("speed")) if iface.get("speed") is not None else None
    duplex = iface.get("duplex")
    m["interface_duplex"] = _DUPLEX_MAP.get(duplex, duplex if isinstance(duplex, str) else None)
    m["interface_mtu"] = _i(iface.get("mtu")) or _i(iface_cfg.get("mtu"))
    negotiated = _f(iface.get("speed"))
    configured = ctx.wan_speed_down_mbps
    m["interface_speed_mbps"] = configured if configured else negotiated


def _link_str(link: Any) -> str | None:
    if link is None:
        return None
    if isinstance(link, bool):
        return "up" if link else "down"
    return str(link).lower()


def _has_default_route(routes: Any) -> bool | None:
    if not isinstance(routes, list):
        return None
    return any(str(r.get("ip_mask", r.get("network", ""))).startswith("0.0.0.0") for r in routes)


def _fill_quality_from_icmp(snap: SourceSnapshot, probes: list[ProbeReading]) -> None:
    """Populate the aggregate quality metrics from ICMP when no FortiGate SLA exists."""
    agg = [p for p in probes if p.aggregate]
    ok = [p for p in agg if p.success and p.rtt_avg_ms is not None]
    lat = [p.rtt_avg_ms for p in ok]
    jit = [p.rtt_mdev_ms for p in ok if p.rtt_mdev_ms is not None]
    loss = [p.packet_loss_pct for p in agg if p.packet_loss_pct is not None]
    if lat:
        snap.metrics["latency_ms"] = round(sum(lat) / len(lat), 3)
    if jit:
        snap.metrics["jitter_ms"] = round(sum(jit) / len(jit), 3)
    if loss:
        snap.metrics["packet_loss_pct"] = round(sum(loss) / len(loss), 3)
    snap.metrics["sla_status"] = "up" if ok else "down"
    snap.metrics.setdefault("availability", 100.0 if ok else 0.0)
    snap.raw_json["quality_source"] = "icmp_from_collector"


def _max_temperature(sensors: Any) -> float | None:
    if not isinstance(sensors, list):
        return None
    temps = [
        _f(s.get("value"))
        for s in sensors
        if isinstance(s, dict) and str(s.get("type", "")).lower() in ("temperature", "temp")
    ]
    temps = [t for t in temps if t is not None]
    return max(temps) if temps else None


def _f(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        return None if v is None else int(float(v))
    except (TypeError, ValueError):
        return None


class TelemetryCollector:
    """Owns the source and drives one cycle end to end."""

    def __init__(self, source: Any | None = None) -> None:
        self.settings = get_settings()
        self.cfg = get_config()
        self.mock_mode = self.settings.mock_mode
        self.data_source = DATA_SOURCE_MOCK if self.mock_mode else DATA_SOURCE_REAL
        self._source = source or (MockSource() if self.mock_mode else None)
        self._client: FortiGateClient | None = None
        self._icmp = IcmpProber()
        self._gap_detector = GapDetector()

    # --- source management ---
    def _get_source(self) -> Any:
        if self._source is not None:
            return self._source
        self._client = FortiGateClient()
        return FortiGateSource(self._client)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _context(self, scheduled: datetime) -> CycleContext:
        return CycleContext(
            scheduled_timestamp=scheduled,
            wan_interface=self.cfg.get("device.wan_interface", "wan1"),
            wan_speed_down_mbps=self.cfg.get("device.wan_speed_down_mbps"),
            wan_speed_up_mbps=self.cfg.get("device.wan_speed_up_mbps"),
            sla_targets=self.cfg.sla_targets,
        )

    # --- the cycle ---
    def run_cycle(self, scheduled: datetime | None = None) -> dict[str, Any]:
        scheduled = scheduled or utc_now()
        start = utc_now()
        ctx = self._context(scheduled)
        source = self._get_source()

        try:
            snap = source.fetch(ctx)
        except Exception as exc:
            logger.exception("source.fetch crashed")
            snap = SourceSnapshot(data_source=self.data_source, success=False, error=str(exc))

        probes = self._collect_icmp(source, ctx)
        # When the FortiGate has no Performance SLA configured (e.g. no SD-WAN),
        # fall back to the Pi's ICMP measurements for the aggregate quality metrics
        # so the network-quality family is not entirely NULL. This is measured at a
        # different point (Pi -> target) and is recorded as such via probe_results.
        if snap.success and not snap.sla and probes:
            _fill_quality_from_icmp(snap, probes)
        end = utc_now()

        with session_scope() as session:
            previous = repo.previous_successful_raw(session, scheduled, self.data_source)
            raw = self._build_raw(snap, ctx, scheduled, start, end, previous)
            repo.add_raw(session, raw)

            for r in snap.sla:
                session.add(_sla_row(r, raw))
            for p in probes:
                session.add(_probe_row(p, raw))

            gap = self._gap_detector.check(session, previous, raw)
            event_service.detect_cycle_events(session, previous, raw, snap, gap)

        if snap.success:
            logger.info(
                "cycle ok  ts=%s  latency=%s ms  rx=%s Mbps  cpu=%s%%  (%d ms)",
                scheduled.strftime("%H:%M:%S"),
                snap.metrics.get("latency_ms"),
                raw.rx_mbps,
                snap.metrics.get("cpu_pct"),
                raw.collection_duration_ms,
            )
        else:
            logger.warning("cycle FAILED ts=%s: %s", scheduled.strftime("%H:%M:%S"), snap.error)

        return {
            "scheduled": scheduled,
            "success": snap.success,
            "duration_ms": raw.collection_duration_ms,
            "missing": snap.missing,
            "error": snap.error,
        }

    def _collect_icmp(self, source: Any, ctx: CycleContext) -> list[ProbeReading]:
        try:
            if self.mock_mode and hasattr(source, "probe_icmp"):
                return source.probe_icmp(ctx)
            return self._icmp.probe_all()
        except Exception:
            logger.exception("ICMP probing failed")
            return []

    def _build_raw(
        self,
        snap: SourceSnapshot,
        ctx: CycleContext,
        scheduled: datetime,
        start: datetime,
        end: datetime,
        previous: TelemetryRaw | None,
    ) -> TelemetryRaw:
        m = dict(snap.metrics)
        ident = snap.identification
        derived = _derive_rates(m, previous, scheduled)
        m.update(derived)
        _derive_utilisation(m)

        now = utc_now()
        return TelemetryRaw(
            scheduled_timestamp=scheduled,
            collection_start_timestamp=start,
            collection_end_timestamp=end,
            collection_duration_ms=duration_ms(start, end),
            collection_success=snap.success,
            api_latency_ms=snap.api_latency_ms,
            data_source=snap.data_source,
            missing_data=snap.missing or None,
            error_message=snap.error,
            timestamp_utc=scheduled,
            timestamp_local=to_local_naive(scheduled),
            timezone=self.settings.timezone,
            device_hostname=ident.get("device_hostname"),
            device_model=ident.get("device_model"),
            serial_number=ident.get("serial_number"),
            fortios_version=ident.get("fortios_version"),
            vdom=self.settings.fortigate_vdom,
            wan_interface=ident.get("wan_interface", ctx.wan_interface),
            collector_version=COLLECTOR_VERSION,
            schema_version=SCHEMA_VERSION,
            raw_json=snap.raw_json or None,
            created_at=now,
            **{k: m.get(k) for k in _RAW_METRIC_COLUMNS if k in m},
        )


# Columns of TelemetryRaw that come straight from the metrics dict.
_RAW_METRIC_COLUMNS = (
    "latency_ms", "jitter_ms", "packet_loss_pct", "availability", "link_status", "sla_status",
    "rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
    "rx_bps", "tx_bps", "rx_mbps", "tx_mbps",
    "interface_speed_mbps", "rx_utilization_pct", "tx_utilization_pct",
    "rx_errors", "tx_errors", "rx_drops", "tx_drops",
    "rx_error_rate", "tx_error_rate", "rx_drop_rate", "tx_drop_rate",
    "cpu_pct", "memory_pct", "disk_pct", "uptime_seconds", "temperature_c",
    "active_sessions", "new_sessions_per_second", "tcp_sessions", "udp_sessions",
    "interface_status", "interface_speed", "interface_duplex", "interface_mtu",
    "default_route_present",
    "vpn_tunnels_up", "vpn_tunnels_down", "vpn_rx_bytes", "vpn_tx_bytes",
)


def _derive_rates(
    m: dict[str, Any], previous: TelemetryRaw | None, scheduled: datetime
) -> dict[str, Any]:
    """Compute bps / rates from counter deltas vs the previous successful sample."""
    out: dict[str, Any] = {
        "rx_bps": None, "tx_bps": None, "rx_mbps": None, "tx_mbps": None,
        "rx_error_rate": None, "tx_error_rate": None,
        "rx_drop_rate": None, "tx_drop_rate": None,
    }
    if previous is None or previous.timestamp_utc is None:
        return out
    dt = (scheduled - previous.timestamp_utc).total_seconds()
    if dt <= 0:
        return out

    def delta(field: str) -> float | None:
        cur, prev = m.get(field), getattr(previous, field, None)
        if cur is None or prev is None:
            return None
        d = cur - prev
        return d if d >= 0 else None  # negative => counter reset / reboot: skip this sample

    d_rx = delta("rx_bytes")
    d_tx = delta("tx_bytes")
    if d_rx is not None:
        out["rx_bps"] = round(d_rx * 8 / dt, 2)
        out["rx_mbps"] = round(out["rx_bps"] / 1e6, 4)
    if d_tx is not None:
        out["tx_bps"] = round(d_tx * 8 / dt, 2)
        out["tx_mbps"] = round(out["tx_bps"] / 1e6, 4)

    d_rxp, d_txp = delta("rx_packets"), delta("tx_packets")
    for pfx, dp in (("rx", d_rxp), ("tx", d_txp)):
        if dp and dp > 0:
            de, dd = delta(f"{pfx}_errors"), delta(f"{pfx}_drops")
            if de is not None:
                out[f"{pfx}_error_rate"] = round(de / dp, 6)
            if dd is not None:
                out[f"{pfx}_drop_rate"] = round(dd / dp, 6)
    return out


def _derive_utilisation(m: dict[str, Any]) -> None:
    speed = m.get("interface_speed_mbps")
    if not speed:
        m.setdefault("rx_utilization_pct", None)
        m.setdefault("tx_utilization_pct", None)
        return
    cap_bps = float(speed) * 1e6
    m["rx_utilization_pct"] = (
        round(m["rx_bps"] / cap_bps * 100, 3) if m.get("rx_bps") is not None else None
    )
    m["tx_utilization_pct"] = (
        round(m["tx_bps"] / cap_bps * 100, 3) if m.get("tx_bps") is not None else None
    )
    # availability proxy for this single sample
    link_up = str(m.get("link_status")).lower() in ("up", "true", "1")
    m["availability"] = 100.0 if link_up else 0.0


def _sla_row(r: SlaReading, raw: TelemetryRaw) -> TelemetrySla:
    return TelemetrySla(
        raw=raw,
        timestamp_utc=raw.timestamp_utc,
        data_source=raw.data_source,
        health_check_name=r.health_check_name,
        label=r.label,
        target=r.target,
        latency_ms=r.latency_ms,
        jitter_ms=r.jitter_ms,
        packet_loss_pct=r.packet_loss_pct,
        sla_state=str(r.sla_state).lower() if r.sla_state is not None else None,
        probe_sessions=r.probe_sessions,
    )


def _probe_row(p: ProbeReading, raw: TelemetryRaw) -> ProbeResult:
    return ProbeResult(
        raw=raw,
        timestamp_utc=raw.timestamp_utc,
        data_source=raw.data_source,
        label=p.label,
        address=p.address,
        rtt_min_ms=p.rtt_min_ms,
        rtt_avg_ms=p.rtt_avg_ms,
        rtt_max_ms=p.rtt_max_ms,
        rtt_mdev_ms=p.rtt_mdev_ms,
        packet_loss_pct=p.packet_loss_pct,
        packets_sent=p.packets_sent,
        packets_received=p.packets_received,
        success=p.success,
    )
