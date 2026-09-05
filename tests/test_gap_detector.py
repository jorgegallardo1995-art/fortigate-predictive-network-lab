"""Gap detection: gaps are recorded, never back-filled."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import DATA_SOURCE_REAL, TelemetryRaw
from app.services.gap_detector import GapDetector


def _raw(ts, **kw) -> TelemetryRaw:
    return TelemetryRaw(
        scheduled_timestamp=ts, collection_start_timestamp=ts, collection_end_timestamp=ts,
        collection_duration_ms=100, collection_success=kw.pop("success", True),
        data_source=DATA_SOURCE_REAL, timestamp_utc=ts, timestamp_local=ts, timezone="UTC",
        collector_version="0.1.0", schema_version="1", created_at=ts, **kw,
    )


def test_no_gap_within_tolerance(session) -> None:
    d = GapDetector()
    now = utc_now()
    prev = _raw(now - timedelta(seconds=65))
    cur = _raw(now)
    assert d.check(session, prev, cur) is None
    assert repo.count_gaps(session) == 0


def test_gap_recorded_and_sized(session) -> None:
    d = GapDetector()
    now = utc_now()
    prev = _raw(now - timedelta(minutes=10))
    cur = _raw(now)
    gap = d.check(session, prev, cur)
    session.commit()
    assert gap is not None
    assert gap.lost_samples >= 8
    assert repo.count_gaps(session) == 1


def test_fortigate_reboot_reason(session) -> None:
    d = GapDetector()
    now = utc_now()
    prev = _raw(now - timedelta(minutes=6), uptime_seconds=100_000)
    cur = _raw(now, uptime_seconds=42)  # FortiGate uptime went backwards
    gap = d.check(session, prev, cur)
    assert gap.reason == "FORTIGATE_UNREACHABLE"
    assert gap.host_rebooted is None  # no /proc on the test host


def test_power_failure_when_host_rebooted(session, monkeypatch) -> None:
    now = utc_now()
    # host booted in the middle of the gap, and no COLLECTOR_STOPPED before it
    monkeypatch.setattr(
        "app.services.gap_detector.host_boot_time", lambda: now - timedelta(minutes=3)
    )
    d = GapDetector()
    prev = _raw(now - timedelta(minutes=8))
    cur = _raw(now)
    gap = d.check(session, prev, cur)
    assert gap.reason == "POWER_FAILURE"
    assert gap.host_rebooted is True
    assert gap.host_boot_time is not None


def test_system_reboot_when_stopped_event_precedes(session, monkeypatch) -> None:
    from app.database.models import NetworkEvent

    now = utc_now()
    session.add(
        NetworkEvent(
            timestamp_utc=now - timedelta(minutes=8, seconds=10),
            event_type="COLLECTOR_STOPPED", severity="INFO", description="stop",
            data_source="REAL", created_at=now,
        )
    )
    session.flush()
    monkeypatch.setattr(
        "app.services.gap_detector.host_boot_time", lambda: now - timedelta(minutes=3)
    )
    d = GapDetector()
    prev = _raw(now - timedelta(minutes=8))
    cur = _raw(now)
    gap = d.check(session, prev, cur)
    assert gap.reason == "SYSTEM_REBOOT"


def test_unreachable_reason(session) -> None:
    d = GapDetector()
    now = utc_now()
    prev = _raw(now - timedelta(minutes=6))
    cur = _raw(now, success=False, error_message="unreachable: ConnectTimeout")
    gap = d.check(session, prev, cur)
    assert gap.reason == "FORTIGATE_UNREACHABLE"
