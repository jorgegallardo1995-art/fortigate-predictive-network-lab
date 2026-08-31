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


def test_reboot_reason(session) -> None:
    d = GapDetector()
    now = utc_now()
    prev = _raw(now - timedelta(minutes=6), uptime_seconds=100_000)
    cur = _raw(now, uptime_seconds=42)
    gap = d.check(session, prev, cur)
    assert gap.reason == "SYSTEM_REBOOT"


def test_unreachable_reason(session) -> None:
    d = GapDetector()
    now = utc_now()
    prev = _raw(now - timedelta(minutes=6))
    cur = _raw(now, success=False, error_message="unreachable: ConnectTimeout")
    gap = d.check(session, prev, cur)
    assert gap.reason == "FORTIGATE_UNREACHABLE"
