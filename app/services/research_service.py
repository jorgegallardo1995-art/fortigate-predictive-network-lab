"""Scientific metadata and reproducibility artefacts.

Produces the ``/research`` dashboard context, the ``research_metadata.json`` file
(a snapshot of the experimental configuration, no secrets) and the matching
``research_metadata`` DB row.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.core.version import COLLECTOR_VERSION, PROJECT_ROOT, SCHEMA_VERSION, get_git_commit
from app.database import repositories as repo
from app.database.database import session_scope
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, ResearchMetadata
from app.services.feature_engineering import FEATURE_DEFINITIONS
from app.services.statistics_service import dataset_overview

logger = get_logger("research")


def _describe_host() -> str:
    """Best-effort description of the machine actually running the collector."""
    machine = platform.machine().lower()
    system = platform.system()
    if system == "Linux" and machine in ("aarch64", "arm64", "armv7l", "armv6l"):
        model_file = Path("/proc/device-tree/model")
        if model_file.exists():
            try:
                model = model_file.read_text(errors="ignore").strip("\x00 \n")
                if "raspberry pi" in model.lower():
                    bits = "ARM64" if machine in ("aarch64", "arm64") else "ARM32"
                    return f"{model} ({bits})"
            except OSError:
                pass
        return f"Linux {machine}"
    return f"{system} {machine} (development)"

# The variables the collector attempts to obtain, grouped as in the thesis plan.
REQUESTED_VARIABLES: dict[str, list[str]] = {
    "identification": [
        "timestamp_utc", "timestamp_local", "device_hostname", "device_model",
        "serial_number", "fortios_version", "vdom", "wan_interface", "collector_version",
    ],
    "network_quality": ["latency_ms", "jitter_ms", "packet_loss_pct", "availability",
                        "link_status", "sla_status"],
    "wan_traffic": ["rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
                    "rx_bps", "tx_bps", "rx_mbps", "tx_mbps"],
    "utilisation": ["interface_speed_mbps", "rx_utilization_pct", "tx_utilization_pct"],
    "errors": ["rx_errors", "tx_errors", "rx_drops", "tx_drops"],
    "resources": ["cpu_pct", "memory_pct", "disk_pct", "uptime_seconds", "temperature_c"],
    "sessions": ["active_sessions", "new_sessions_per_second", "tcp_sessions", "udp_sessions"],
    "interface": ["interface_status", "interface_speed", "interface_duplex", "interface_mtu"],
    "vpn": ["vpn_tunnels_up", "vpn_tunnels_down", "vpn_rx_bytes", "vpn_tx_bytes"],
}

# Known-unavailable on this deployment, with the documented reason.
KNOWN_UNAVAILABLE: dict[str, str] = {
    "vpn_tunnels_up": "no VPN configured on the FortiGate 50F",
    "vpn_tunnels_down": "no VPN configured on the FortiGate 50F",
    "vpn_rx_bytes": "no VPN configured on the FortiGate 50F",
    "vpn_tx_bytes": "no VPN configured on the FortiGate 50F",
}


def _availability_from_data(session: Session, data_source: str) -> tuple[list[str], list[str]]:
    """Split requested variables into available / unavailable using the latest sample."""
    last = repo.latest_raw(session, data_source)
    available: list[str] = []
    unavailable: list[str] = []
    flat = [v for group in REQUESTED_VARIABLES.values() for v in group]
    for var in flat:
        if var in ("timestamp_utc", "timestamp_local"):
            available.append(var)
            continue
        if var in KNOWN_UNAVAILABLE:
            unavailable.append(var)
            continue
        value = getattr(last, var, None) if last else None
        (available if value is not None else unavailable).append(var)
    return available, unavailable


def build_research_metadata(session: Session) -> dict[str, Any]:
    settings = get_settings()
    cfg = get_config()
    data_source = DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL

    overview = dataset_overview(session, data_source)
    last = repo.latest_raw(session, data_source)
    available, unavailable = _availability_from_data(session, data_source)

    return {
        "generated_at": utc_now().isoformat(),
        "project": {
            "technical_name": cfg.get("app.technical_name"),
            "display_name": cfg.get("app.display_name"),
        },
        "provenance": {
            "collector_version": COLLECTOR_VERSION,
            "database_schema_version": SCHEMA_VERSION,
            "git_commit": get_git_commit(short=False),
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "hardware": f"{platform.machine()} ({platform.processor() or 'n/a'})",
            "collector_architecture": _describe_host(),
        },
        "device": {
            "model": last.device_model if last else None,
            "serial": _serial(last.serial_number if last else None),
            "fortios_version": last.fortios_version if last else None,
            "wan_interface": cfg.get("device.wan_interface"),
        },
        "acquisition": {
            "data_source": data_source,
            "sampling_interval_seconds": cfg.sampling_interval,
            "timezone": settings.timezone,
            "feature_windows_minutes": cfg.feature_windows,
            "sla_targets": cfg.sla_targets,
            "icmp_targets": cfg.get("icmp_prober.targets", []),
        },
        "dataset": {
            "start": overview["first_observation"],
            "end": overview["last_observation"],
            "capture_days": overview["capture_days"],
            "total_observations": overview["total_observations"],
            "expected_samples": overview["expected_samples"],
            "lost_samples": overview["lost_samples"],
            "completeness_pct": overview["completeness_pct"],
            "gaps": overview["gaps"],
            "experiments_total": overview["experiments_total"],
        },
        "variables": {
            "requested": REQUESTED_VARIABLES,
            "available": sorted(available),
            "unavailable": sorted(unavailable),
            "unavailable_reasons": KNOWN_UNAVAILABLE,
            "derived_features": FEATURE_DEFINITIONS,
        },
        "notes": "Dashboard thresholds are visual only and are NOT used to label the dataset.",
    }


def _serial(serial: str | None) -> str | None:
    if not serial:
        return None
    if get_settings().anonymize_serial:
        return f"{serial[:4]}…{serial[-2:]}" if len(serial) > 6 else "REDACTED"
    return serial


def write_research_metadata_file() -> dict[str, Any]:
    """Write ``research_metadata.json`` at the project root and persist a DB row."""
    with session_scope() as session:
        meta = build_research_metadata(session)
        path = PROJECT_ROOT / "research_metadata.json"
        path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        session.add(
            ResearchMetadata(
                created_at=utc_now(),
                collector_version=COLLECTOR_VERSION,
                schema_version=SCHEMA_VERSION,
                git_commit=meta["provenance"]["git_commit"],
                fortios_version=meta["device"]["fortios_version"],
                sampling_interval_seconds=meta["acquisition"]["sampling_interval_seconds"],
                timezone=meta["acquisition"]["timezone"],
                python_version=meta["provenance"]["python_version"],
                os_info=meta["provenance"]["os"],
                hardware_info=meta["provenance"]["hardware"],
                dataset_start=_dt(meta["dataset"]["start"]),
                payload=meta,
            )
        )
    logger.info("research_metadata.json written")
    return meta


def _dt(value: str | None):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None


def research_dashboard_context() -> dict[str, Any]:
    with session_scope() as session:
        return build_research_metadata(session)
