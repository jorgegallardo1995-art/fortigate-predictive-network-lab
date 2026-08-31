"""Schema / persistence smoke tests."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import DATA_SOURCE_REAL, TelemetryRaw, TelemetrySla


def _raw(ts, success=True, **kw) -> TelemetryRaw:
    return TelemetryRaw(
        scheduled_timestamp=ts,
        collection_start_timestamp=ts,
        collection_end_timestamp=ts,
        collection_duration_ms=120,
        collection_success=success,
        data_source=DATA_SOURCE_REAL,
        timestamp_utc=ts,
        timestamp_local=ts,
        timezone="UTC",
        collector_version="0.1.0",
        schema_version="1",
        created_at=ts,
        **kw,
    )


def test_wal_enabled(session) -> None:
    mode = session.connection().exec_driver_sql("PRAGMA journal_mode").scalar()
    assert str(mode).lower() == "wal"


def test_insert_and_query_latest(session) -> None:
    now = utc_now()
    session.add(_raw(now - timedelta(minutes=1), latency_ms=10))
    session.add(_raw(now, latency_ms=20))
    session.commit()

    latest = repo.latest_raw(session, DATA_SOURCE_REAL)
    assert latest.latency_ms == 20
    assert repo.count_raw(session, DATA_SOURCE_REAL) == 2


def test_sla_cascade(session) -> None:
    now = utc_now()
    raw = _raw(now)
    raw.sla_results.append(
        TelemetrySla(
            timestamp_utc=now, data_source=DATA_SOURCE_REAL,
            health_check_name="HC_Google", label="google", target="8.8.8.8",
            latency_ms=12.3, jitter_ms=1.1, packet_loss_pct=0.0, sla_state="up",
        )
    )
    session.add(raw)
    session.commit()
    assert len(repo.latest_sla(session, DATA_SOURCE_REAL)) == 1


def test_label_defaults_to_null(session) -> None:
    now = utc_now()
    session.add(_raw(now, latency_ms=999))  # way over any threshold
    session.commit()
    assert repo.latest_raw(session, DATA_SOURCE_REAL).label is None


def test_time_bounds(session) -> None:
    now = utc_now()
    for i in range(5):
        session.add(_raw(now - timedelta(minutes=i)))
    session.commit()
    first, last = repo.raw_time_bounds(session, DATA_SOURCE_REAL)
    assert (last - first) == timedelta(minutes=4)
