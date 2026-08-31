"""Dataset statistics and chart series for the dashboards and the JSON API."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import DATA_SOURCE_REAL, TelemetryRaw
from app.services.gap_detector import expected_samples_between, summarise_gaps

_RANGE_TO_HOURS = {"1h": 1, "6h": 6, "12h": 12, "24h": 24, "7d": 168, "30d": 720}

_NUMERIC_COLUMNS = (
    "latency_ms", "jitter_ms", "packet_loss_pct", "availability",
    "rx_mbps", "tx_mbps", "rx_utilization_pct", "tx_utilization_pct",
    "rx_errors", "tx_errors", "rx_drops", "tx_drops",
    "cpu_pct", "memory_pct", "disk_pct", "temperature_c", "uptime_seconds",
    "active_sessions", "new_sessions_per_second", "tcp_sessions", "udp_sessions",
    "api_latency_ms", "collection_duration_ms",
)


def dataset_overview(session: Session, data_source: str = DATA_SOURCE_REAL) -> dict[str, Any]:
    cfg = get_config()
    interval = cfg.sampling_interval
    total = repo.count_raw(session, data_source)
    first, last = repo.raw_time_bounds(session, data_source)

    days = round((last - first).total_seconds() / 86400, 2) if first and last else 0
    expected = expected_samples_between(first, last, interval) if first and last else 0
    lost = repo.total_lost_samples(session)
    completeness = round(total / expected * 100, 2) if expected else 0.0

    incomplete = session.execute(
        select(TelemetryRaw)
        .where(TelemetryRaw.data_source == data_source)
        .where((TelemetryRaw.collection_success.is_(False)) | (TelemetryRaw.missing_data.isnot(None)))
    ).scalars().all()

    return {
        "data_source": data_source,
        "total_observations": total,
        "first_observation": first.isoformat() if first else None,
        "last_observation": last.isoformat() if last else None,
        "capture_days": days,
        "sampling_interval_seconds": interval,
        "expected_samples": expected,
        "real_samples": total,
        "lost_samples": lost,
        "completeness_pct": completeness,
        "valid_records": total - len(incomplete),
        "incomplete_records": len(incomplete),
        "gaps": summarise_gaps(session),
        "events_total": repo.count_events_since(session, first) if first else 0,
        "experiments_total": len(repo.list_experiments(session)),
    }


def _raw_dataframe(session: Session, since, until, data_source: str) -> pd.DataFrame:
    rows = repo.raw_history(session, since, until, data_source)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        [{"timestamp_utc": r.timestamp_utc, **{c: getattr(r, c) for c in _NUMERIC_COLUMNS}} for r in rows]
    )


def variable_statistics(
    session: Session, data_source: str = DATA_SOURCE_REAL
) -> list[dict[str, Any]]:
    first, last = repo.raw_time_bounds(session, data_source)
    if not first:
        return []
    df = _raw_dataframe(session, first, last, data_source)
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    n = len(df)
    for col in _NUMERIC_COLUMNS:
        s = pd.to_numeric(df[col], errors="coerce")
        non_null = int(s.notna().sum())
        out.append(
            {
                "variable": col,
                "count": non_null,
                "null_count": n - non_null,
                "null_pct": round((n - non_null) / n * 100, 2) if n else 100.0,
                "min": _r(s.min()),
                "max": _r(s.max()),
                "mean": _r(s.mean()),
                "std": _r(s.std()),
                "p50": _r(s.quantile(0.5)) if non_null else None,
                "p95": _r(s.quantile(0.95)) if non_null else None,
            }
        )
    return out


def latest_values(session: Session, data_source: str = DATA_SOURCE_REAL) -> dict[str, Any]:
    last = repo.latest_raw(session, data_source)
    if last is None:
        return {}
    cfg = get_config()
    thresholds = cfg.get("thresholds", {})
    metrics = {
        "latency_ms": last.latency_ms,
        "jitter_ms": last.jitter_ms,
        "packet_loss_pct": last.packet_loss_pct,
        "rx_mbps": last.rx_mbps,
        "tx_mbps": last.tx_mbps,
        "cpu_pct": last.cpu_pct,
        "memory_pct": last.memory_pct,
        "active_sessions": last.active_sessions,
        "rx_utilization_pct": last.rx_utilization_pct,
        "tx_utilization_pct": last.tx_utilization_pct,
    }
    threshold_key = {
        "latency_ms": "latency", "jitter_ms": "jitter", "packet_loss_pct": "packet_loss",
        "cpu_pct": "cpu", "memory_pct": "memory",
        "rx_utilization_pct": "rx_utilization", "tx_utilization_pct": "tx_utilization",
    }
    states = {
        name: _threshold_state(value, thresholds.get(threshold_key.get(name, ""), {}))
        for name, value in metrics.items()
    }
    return {
        "timestamp_utc": last.timestamp_utc.isoformat(),
        "data_source": last.data_source,
        "collection_success": last.collection_success,
        "metrics": metrics,
        "states": states,
    }


def _threshold_state(value: Any, spec: dict[str, Any]) -> str:
    if value is None or not spec:
        return "unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if "critical" in spec and v >= spec["critical"]:
        return "critical"
    if "warning" in spec and v >= spec["warning"]:
        return "warning"
    return "normal"


def history_series(
    session: Session,
    metrics: list[str],
    range_key: str = "24h",
    data_source: str = DATA_SOURCE_REAL,
) -> dict[str, Any]:
    hours = _RANGE_TO_HOURS.get(range_key, 24)
    since = utc_now() - timedelta(hours=hours)
    rows = repo.raw_history(session, since, None, data_source)
    timestamps = [r.timestamp_utc.isoformat() for r in rows]
    series = {
        m: [getattr(r, m, None) for r in rows]
        for m in metrics
        if m in TelemetryRaw.__table__.columns
    }
    return {"range": range_key, "data_source": data_source, "timestamps": timestamps, "series": series}


def _r(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 4)
