"""Platform health for the /system dashboard and GET /api/status."""

from __future__ import annotations

import platform
import shutil
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.core.version import COLLECTOR_VERSION, SCHEMA_VERSION
from app.database import repositories as repo
from app.database.database import engine, session_scope
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, SystemStatus

logger = get_logger("health")


def _database_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("database health check failed")
        return False


def _db_size_bytes() -> int | None:
    settings = get_settings()
    if settings.effective_database_url.startswith("sqlite") and settings.database_path.exists():
        return settings.database_path.stat().st_size
    return None


def _host_uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime") as fh:  # noqa: PTH123 - Linux only, guarded
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _backup_ok(last_backup: Any) -> bool | None:
    """A backup is expected once a day; older than 36h means the job is failing."""
    if not get_config().get("backup.enabled", True):
        return None
    return last_backup is not None and utc_now() - last_backup < timedelta(hours=36)


def platform_health() -> dict[str, Any]:
    """Full health snapshot. Does not hit the FortiGate directly (uses last sample)."""
    from app.collector.scheduler import scheduler

    settings = get_settings()
    data_source = DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL
    sched = scheduler.status()

    with session_scope() as session:
        last = repo.latest_raw(session, data_source)
        last_success = None
        if last and last.collection_success:
            last_success = last.timestamp_utc
        elif last:
            prev = repo.previous_successful_raw(session, last.timestamp_utc, data_source)
            last_success = prev.timestamp_utc if prev else None
        failed_24h = repo.failed_requests_since(session, utc_now() - timedelta(hours=24))
        recent = repo.raw_history(session, utc_now() - timedelta(hours=1), None, data_source)
        api_latencies = [r.api_latency_ms for r in recent if r.api_latency_ms is not None]

    fortigate_ok = bool(last and last.collection_success) if not settings.mock_mode else None
    collector_ok = sched["running"] and (
        last is None or (utc_now() - last.timestamp_utc).total_seconds() < 5 * sched["interval_seconds"]
    )

    from app.services.backup_service import latest_backup_time

    last_backup = latest_backup_time()
    disk = shutil.disk_usage(str(settings.data_dir if settings.data_dir.exists() else "."))

    return {
        "mode": "MOCK" if settings.mock_mode else "REAL",
        "collector_version": COLLECTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "checks": {
            "fortigate_api": _tri(fortigate_ok),
            "collector": _tri(collector_ok),
            "scheduler": _tri(sched["running"]),
            "database": _tri(_database_ok()),
            "backup": _tri(_backup_ok(last_backup)),
        },
        "last_backup": last_backup.isoformat() if last_backup else None,
        "last_successful_collection": last_success.isoformat() if last_success else None,
        "next_scheduled_collection": sched["next_run"],
        "average_api_latency_ms": (
            round(sum(api_latencies) / len(api_latencies), 1) if api_latencies else None
        ),
        "failed_requests_24h": failed_24h,
        "collector_uptime_seconds": sched["uptime_seconds"],
        "host_uptime_seconds": _host_uptime_seconds(),
        "database_size_bytes": _db_size_bytes(),
        "disk_total_bytes": disk.total,
        "disk_available_bytes": disk.free,
        "platform": f"{platform.system()} {platform.machine()} / Python {platform.python_version()}",
    }


def _tri(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "ok" if value else "error"


def snapshot_system_status() -> None:
    """Persist a :class:`SystemStatus` row (called periodically by the scheduler)."""
    h = platform_health()
    with session_scope() as session:
        session.add(
            SystemStatus(
                timestamp_utc=utc_now(),
                fortigate_api_ok=_bool(h["checks"]["fortigate_api"]),
                collector_ok=_bool(h["checks"]["collector"]),
                scheduler_ok=_bool(h["checks"]["scheduler"]),
                database_ok=_bool(h["checks"]["database"]),
                last_successful_collection=_dt(h["last_successful_collection"]),
                next_scheduled_collection=_dt(h["next_scheduled_collection"]),
                average_api_latency_ms=h["average_api_latency_ms"],
                failed_requests_24h=h["failed_requests_24h"],
                collector_uptime_seconds=h["collector_uptime_seconds"],
                host_uptime_seconds=h["host_uptime_seconds"],
                database_size_bytes=h["database_size_bytes"],
                disk_available_bytes=h["disk_available_bytes"],
            )
        )


def _bool(tri: str) -> bool | None:
    return {"ok": True, "error": False}.get(tri)


def _dt(value: str | None):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None
