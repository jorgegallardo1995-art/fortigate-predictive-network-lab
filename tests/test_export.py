"""Export: REAL/MOCK never mixed, CSVs load back into pandas."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from app.core.timeutils import utc_now
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, TelemetryRaw
from app.services.export_service import ExportFilter, export_raw
from app.services.statistics_service import dataset_overview


def _raw(session, ts, source, **kw) -> None:
    session.add(
        TelemetryRaw(
            scheduled_timestamp=ts, collection_start_timestamp=ts, collection_end_timestamp=ts,
            collection_duration_ms=100, collection_success=True, data_source=source,
            timestamp_utc=ts, timestamp_local=ts, timezone="UTC",
            collector_version="0.1.0", schema_version="1", created_at=ts, **kw,
        )
    )


def test_raw_export_filters_by_source(session) -> None:
    now = utc_now()
    _raw(session, now, DATA_SOURCE_REAL, latency_ms=11)
    _raw(session, now - timedelta(minutes=1), DATA_SOURCE_MOCK, latency_ms=999)
    session.commit()

    path = export_raw(ExportFilter(data_source=DATA_SOURCE_REAL))
    df = pd.read_csv(path)
    assert (df["data_source"] == "REAL").all()
    assert 999 not in df["latency_ms"].values
    assert "raw_json" not in df.columns


def test_dataset_overview_counts(session) -> None:
    now = utc_now()
    for i in range(3):
        _raw(session, now - timedelta(minutes=i), DATA_SOURCE_REAL)
    session.commit()
    ov = dataset_overview(session, DATA_SOURCE_REAL)
    assert ov["total_observations"] == 3
    assert ov["data_source"] == "REAL"
