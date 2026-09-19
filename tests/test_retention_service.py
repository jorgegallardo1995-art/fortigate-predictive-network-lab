"""raw_json retention: clears the blob on old rows, never touches the
structured columns the dashboard and ml/ actually read."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utc_now
from app.database.models import DATA_SOURCE_REAL, TelemetryRaw
from app.services.retention_service import prune_raw_json


def _raw(ts, **kw) -> TelemetryRaw:
    return TelemetryRaw(
        scheduled_timestamp=ts, collection_start_timestamp=ts, collection_end_timestamp=ts,
        collection_duration_ms=100, collection_success=True,
        data_source=DATA_SOURCE_REAL, timestamp_utc=ts, timestamp_local=ts, timezone="UTC",
        collector_version="0.1.0", schema_version="1", created_at=ts,
        raw_json={"some": "payload"}, latency_ms=12.3, cpu_pct=5.0, **kw,
    )


def test_prunes_only_rows_older_than_cutoff(session) -> None:
    now = utc_now()
    old_row = _raw(now - timedelta(days=5))
    recent_row = _raw(now - timedelta(hours=1))
    session.add_all([old_row, recent_row])
    session.commit()

    result = prune_raw_json(keep_days=3)

    session.refresh(old_row)
    session.refresh(recent_row)
    assert result["pruned_rows"] == 1
    assert old_row.raw_json is None
    assert recent_row.raw_json == {"some": "payload"}


def test_never_touches_structured_columns(session) -> None:
    now = utc_now()
    old_row = _raw(now - timedelta(days=10))
    session.add(old_row)
    session.commit()

    prune_raw_json(keep_days=3)

    session.refresh(old_row)
    assert old_row.raw_json is None
    assert old_row.latency_ms == 12.3
    assert old_row.cpu_pct == 5.0


def test_idempotent_second_run_prunes_nothing(session) -> None:
    now = utc_now()
    session.add(_raw(now - timedelta(days=5)))
    session.commit()

    first = prune_raw_json(keep_days=3)
    second = prune_raw_json(keep_days=3)

    assert first["pruned_rows"] == 1
    assert second["pruned_rows"] == 0
