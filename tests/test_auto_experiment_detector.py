"""Auto-registration of real WAN outages from telemetry_sla transitions -
never invents a failure, only records ones telemetry already shows."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utc_now
from app.database.models import DATA_SOURCE_REAL, Experiment, TelemetryRaw, TelemetrySla
from app.services.auto_experiment_detector import detect_and_register


def _sla_row(session, ts, state: str) -> None:
    raw = TelemetryRaw(
        scheduled_timestamp=ts, collection_start_timestamp=ts, collection_end_timestamp=ts,
        collection_duration_ms=100, collection_success=True,
        data_source=DATA_SOURCE_REAL, timestamp_utc=ts, timestamp_local=ts, timezone="UTC",
        collector_version="0.1.0", schema_version="1", created_at=ts,
    )
    session.add(raw)
    session.flush()
    session.add(TelemetrySla(
        raw_id=raw.id, timestamp_utc=ts, data_source=DATA_SOURCE_REAL,
        health_check_name="Internet", label="fortigate_wan1", sla_state=state,
    ))


def test_detects_and_registers_a_recovered_outage(session) -> None:
    now = utc_now()
    start = now - timedelta(minutes=30)
    for i in range(10):
        _sla_row(session, start + timedelta(minutes=i), "up")
    down_start = start + timedelta(minutes=10)
    for i in range(8):
        _sla_row(session, down_start + timedelta(minutes=i), "down")
    recovered_at = down_start + timedelta(minutes=8)
    for i in range(5):
        _sla_row(session, recovered_at + timedelta(minutes=i), "up")
    session.commit()

    created = detect_and_register()

    assert len(created) == 1
    exp = session.query(Experiment).filter_by(experiment_id=created[0]).first()
    assert exp is not None
    assert exp.scenario == "WAN_FAILURE"
    assert exp.operator == "auto-detector"
    assert exp.failure_timestamp == down_start
    assert exp.end_timestamp == down_start + timedelta(minutes=7)


def test_does_not_register_an_outage_still_in_progress(session) -> None:
    now = utc_now()
    start = now - timedelta(minutes=20)
    for i in range(10):
        _sla_row(session, start + timedelta(minutes=i), "up")
    down_start = start + timedelta(minutes=10)
    for i in range(9):
        _sla_row(session, down_start + timedelta(minutes=i), "down")
    session.commit()

    created = detect_and_register()

    assert created == []
    assert session.query(Experiment).count() == 0


def test_does_not_duplicate_an_existing_experiment(session) -> None:
    now = utc_now()
    start = now - timedelta(minutes=30)
    for i in range(5):
        _sla_row(session, start + timedelta(minutes=i), "up")
    down_start = start + timedelta(minutes=5)
    for i in range(6):
        _sla_row(session, down_start + timedelta(minutes=i), "down")
    for i in range(5):
        _sla_row(session, down_start + timedelta(minutes=6 + i), "up")
    session.add(Experiment(
        experiment_id="EXP-MANUAL-1", name="manual", scenario="WAN_FAILURE",
        start_timestamp=down_start, failure_timestamp=down_start,
        operator="jorge", created_at=now,
    ))
    session.commit()

    created = detect_and_register()

    assert created == []
    assert session.query(Experiment).count() == 1


def test_lookback_sliding_past_an_old_outage_does_not_fragment_it(session, monkeypatch) -> None:
    """Regression test: a real incident. As LOOKBACK slides forward past an
    already-recorded, already-recovered outage, the earliest still-visible
    "down" row must NOT be mistaken for a new failure - it's the tail of one
    already covered by an existing experiment. This is exactly what happened
    live: a single 78-minute outage was rediscovered 11 extra times, each
    "start" a few minutes later than the last, as the 8h lookback swept
    through it - because the old proximity-only dedupe only looked within
    +-5 min of failure_timestamp, not whether the two windows overlapped."""
    import app.services.auto_experiment_detector as det

    now = utc_now()
    down_start = now - timedelta(hours=10)
    for i in range(20):
        _sla_row(session, down_start + timedelta(minutes=i), "down")
    recovered_at = down_start + timedelta(minutes=20)
    for i in range(3):
        _sla_row(session, recovered_at + timedelta(minutes=i), "up")
    session.commit()

    # First pass: LOOKBACK covers the whole thing -> registers exactly once.
    monkeypatch.setattr(det, "LOOKBACK", timedelta(hours=11))
    first = det.detect_and_register()
    assert len(first) == 1

    # Second pass: LOOKBACK has since shrunk (simulating real time passing)
    # so "since" now falls in the MIDDLE of the already-recorded down run -
    # the algorithm sees a "new" down_start partway through it.
    monkeypatch.setattr(det, "LOOKBACK", timedelta(hours=9, minutes=45))
    second = det.detect_and_register()

    assert second == [], f"must not re-fragment an already-recorded outage, got {second}"
    assert session.query(Experiment).count() == 1
