"""CSV export of the experimental dataset.

Design constraints:

* Default ``data_source`` is ``REAL``. MOCK rows are only exported when explicitly
  requested and are never mixed into the same file as REAL rows.
* RAW export is the wide, one-row-per-cycle table with the per-target SLA metrics
  pivoted into ``latency_<label>_ms`` / ``jitter_<label>_ms`` / ``loss_<label>_pct``
  columns (names come from ``config.yaml`` -> ``sla_targets``).
* Timestamps are ISO-8601 UTC. Files are UTF-8, ready for pandas / NumPy /
  scikit-learn / TensorFlow-Keras.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database.database import engine
from app.database.models import (
    DATA_SOURCE_REAL,
    AvailabilityMinute,
    CollectionGap,
    Experiment,
    NetworkEvent,
    ProbeResult,
    TelemetryFeature,
    TelemetryRaw,
    TelemetrySla,
)
from app.services.feature_engineering import FEATURE_DEFINITIONS

logger = get_logger("export")


@dataclass(slots=True)
class ExportFilter:
    start: datetime | None = None
    end: datetime | None = None
    experiment_id: str | None = None
    data_source: str = DATA_SOURCE_REAL


def _stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def _apply(stmt, model, f: ExportFilter, ts_col: str = "timestamp_utc"):
    col = getattr(model, ts_col)
    if f.start:
        stmt = stmt.where(col >= f.start)
    if f.end:
        stmt = stmt.where(col <= f.end)
    if hasattr(model, "data_source") and f.data_source:
        stmt = stmt.where(model.data_source == f.data_source)
    if f.experiment_id and hasattr(model, "experiment_id"):
        stmt = stmt.where(model.experiment_id == f.experiment_id)
    return stmt


def _write(df: pd.DataFrame, name: str) -> Path:
    settings = get_settings()
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.exports_dir / f"{name}_{_stamp()}.csv"
    df.to_csv(path, index=False)
    logger.info("exported %s rows -> %s", len(df), path.name)
    return path


def export_raw(f: ExportFilter) -> Path:
    stmt = _apply(select(TelemetryRaw), TelemetryRaw, f).order_by(TelemetryRaw.timestamp_utc)
    with engine.connect() as conn:
        raw = pd.read_sql(stmt, conn)
        sla = pd.read_sql(_apply(select(TelemetrySla), TelemetrySla, f), conn)

    if "raw_json" in raw.columns:
        raw = raw.drop(columns=["raw_json"])

    if not sla.empty:
        piv = sla.pivot_table(
            index="raw_id",
            columns="label",
            values=["latency_ms", "jitter_ms", "packet_loss_pct"],
            aggfunc="mean",
        )
        piv.columns = [
            f"{'loss' if metric == 'packet_loss_pct' else metric.replace('_ms', '')}_{label}"
            + ("_pct" if metric == "packet_loss_pct" else "_ms")
            for metric, label in piv.columns
        ]
        raw = raw.merge(piv.reset_index(), left_on="id", right_on="raw_id", how="left")
        raw = raw.drop(columns=[c for c in ("raw_id",) if c in raw.columns])

    return _write(raw, f"raw_dataset_{f.data_source.lower()}")


def export_features(f: ExportFilter) -> Path:
    stmt = _apply(select(TelemetryFeature), TelemetryFeature, f).order_by(
        TelemetryFeature.timestamp_utc, TelemetryFeature.window_minutes
    )
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    return _write(df, f"features_dataset_{f.data_source.lower()}")


def export_events(f: ExportFilter) -> Path:
    stmt = _apply(select(NetworkEvent), NetworkEvent, f).order_by(NetworkEvent.timestamp_utc)
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    if "details" in df.columns:
        df["details"] = df["details"].astype(str)
    return _write(df, "events_dataset")


def export_gaps(f: ExportFilter) -> Path:
    stmt = select(CollectionGap).order_by(CollectionGap.gap_start)
    if f.start:
        stmt = stmt.where(CollectionGap.gap_start >= f.start)
    if f.end:
        stmt = stmt.where(CollectionGap.gap_end <= f.end)
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    return _write(df, "gaps_dataset")


def export_experiments(_f: ExportFilter) -> Path:
    with engine.connect() as conn:
        df = pd.read_sql(select(Experiment).order_by(Experiment.start_timestamp), conn)
    return _write(df, "experiments_dataset")


def export_availability(f: ExportFilter) -> Path:
    """Continuous, gap-free timeline: one row per sampling slot with
    collector_up / network_up / reason. The 'continuidad operativa' dataset."""
    stmt = select(AvailabilityMinute).where(AvailabilityMinute.data_source == f.data_source)
    if f.start:
        stmt = stmt.where(AvailabilityMinute.slot_utc >= f.start)
    if f.end:
        stmt = stmt.where(AvailabilityMinute.slot_utc <= f.end)
    with engine.connect() as conn:
        df = pd.read_sql(stmt.order_by(AvailabilityMinute.slot_utc), conn)
    return _write(df, f"availability_dataset_{f.data_source.lower()}")


def export_probes(f: ExportFilter) -> Path:
    stmt = _apply(select(ProbeResult), ProbeResult, f).order_by(ProbeResult.timestamp_utc)
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    return _write(df, f"icmp_dataset_{f.data_source.lower()}")


def export_data_dictionary(_f: ExportFilter) -> Path:
    rows: list[dict[str, Any]] = []
    for col in TelemetryRaw.__table__.columns:
        rows.append(
            {
                "table": "telemetry_raw",
                "name": col.name,
                "type": str(col.type),
                "nullable": col.nullable,
                "level": "RAW",
                "formula": "",
            }
        )
    for d in FEATURE_DEFINITIONS:
        rows.append(
            {
                "table": "telemetry_features",
                "name": d["name"],
                "type": "FLOAT",
                "nullable": True,
                "level": "DERIVED",
                "window": d["window"],
                "source": d["source"],
                "formula": d["formula"],
            }
        )
    return _write(pd.DataFrame(rows), "data_dictionary")


EXPORTERS = {
    "raw": export_raw,
    "features": export_features,
    "availability": export_availability,
    "events": export_events,
    "gaps": export_gaps,
    "experiments": export_experiments,
    "icmp": export_probes,
    "dictionary": export_data_dictionary,
}


def export_bundle(f: ExportFilter) -> list[Path]:
    """Export every artefact for the given filter (used by the /export button)."""
    return [fn(f) for fn in EXPORTERS.values()]
