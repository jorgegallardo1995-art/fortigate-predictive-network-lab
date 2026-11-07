"""raw_json retention: clears the blob on old rows, never touches the
structured columns the dashboard and ml/ actually read."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.core.timeutils import utc_now
from app.database.models import DATA_SOURCE_REAL, TelemetryRaw
from app.services.retention_service import prune_raw_json


@pytest.fixture(autouse=True)
def _fresh_backup(monkeypatch):
    """Pretend a valid backup was just taken, so only the age cutoff applies."""
    from app.services import backup_service

    monkeypatch.setattr(backup_service, "latest_backup_time", utc_now)


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


def test_never_prunes_rows_newer_than_the_latest_backup(session, monkeypatch) -> None:
    from app.services import backup_service

    now = utc_now()
    backed_up = _raw(now - timedelta(days=10))
    not_backed_up = _raw(now - timedelta(days=6))  # old enough, but no backup holds it
    session.add_all([backed_up, not_backed_up])
    session.commit()
    monkeypatch.setattr(backup_service, "latest_backup_time", lambda: now - timedelta(days=8))

    result = prune_raw_json(keep_days=3)

    session.refresh(backed_up)
    session.refresh(not_backed_up)
    assert result["pruned_rows"] == 1
    assert backed_up.raw_json is None
    assert not_backed_up.raw_json == {"some": "payload"}


def test_prunes_nothing_without_any_backup(session, monkeypatch) -> None:
    from app.services import backup_service

    row = _raw(utc_now() - timedelta(days=10))
    session.add(row)
    session.commit()
    monkeypatch.setattr(backup_service, "latest_backup_time", lambda: None)

    result = prune_raw_json(keep_days=3)

    session.refresh(row)
    assert result["pruned_rows"] == 0
    assert row.raw_json == {"some": "payload"}
