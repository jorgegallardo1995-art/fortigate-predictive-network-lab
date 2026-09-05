"""Availability timeline: continuous grid, honest NULLs during outages."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import to_local_naive, utc_now
from app.database.models import DATA_SOURCE_REAL, AvailabilityMinute, CollectionGap, TelemetryRaw
from app.services import availability_service


def _raw(session, ts, **kw) -> TelemetryRaw:
    r = TelemetryRaw(
        scheduled_timestamp=ts, collection_start_timestamp=ts, collection_end_timestamp=ts,
        collection_duration_ms=100, collection_success=True, data_source=DATA_SOURCE_REAL,
        timestamp_utc=ts, timestamp_local=to_local_naive(ts), timezone="UTC",
        collector_version="0.1.0", schema_version="1", created_at=ts,
        link_status="up", sla_status="up", packet_loss_pct=0.0, latency_ms=8.0, **kw,
    )
    session.add(r)
    return r


def test_timeline_is_continuous_with_reason_during_gap(session) -> None:
    now = utc_now().replace(second=0, microsecond=0)
    # samples at t-10, t-9, t-8 min, then a gap, then t-2, t-1, t0
    for m in (10, 9, 8):
        _raw(session, now - timedelta(minutes=m))
    for m in (2, 1, 0):
        _raw(session, now - timedelta(minutes=m))
    session.add(
        CollectionGap(
            gap_start=now - timedelta(minutes=8), gap_end=now - timedelta(minutes=2),
            duration_seconds=360, expected_samples=5, lost_samples=5,
            reason="POWER_FAILURE", host_rebooted=True, detected_at=now,
        )
    )
    session.commit()

    availability_service.rebuild(session, DATA_SOURCE_REAL, full=True)
    session.commit()

    rows = session.query(AvailabilityMinute).order_by(AvailabilityMinute.slot_utc).all()
    # continuous: no time gap between consecutive slots
    deltas = {
        (rows[i + 1].slot_utc - rows[i].slot_utc).total_seconds() for i in range(len(rows) - 1)
    }
    assert deltas == {60.0}

    down = [r for r in rows if not r.collector_up]
    assert down, "expected some down slots"
    assert all(r.reason == "POWER_FAILURE" for r in down)
    assert all(r.network_up is None for r in down)  # never assumed during an outage

    up = [r for r in rows if r.collector_up]
    assert all(r.network_up is True for r in up)


def test_summary_kpis(session) -> None:
    now = utc_now().replace(second=0, microsecond=0)
    for m in range(10):
        _raw(session, now - timedelta(minutes=m))
    session.commit()
    availability_service.rebuild(session, DATA_SOURCE_REAL, full=True)
    session.commit()

    s = availability_service.summary(session, DATA_SOURCE_REAL)
    assert s["collector_availability_pct"] == 100.0
    assert s["network_availability_pct"] == 100.0
    assert s["outages"] == []
