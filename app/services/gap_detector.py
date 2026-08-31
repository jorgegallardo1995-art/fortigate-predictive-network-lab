"""Collection-gap detection.

Compares each new observation with the previous one. If the spacing exceeds the
sampling interval plus a tolerance, a :class:`CollectionGap` row is written.

The dataset is **never** back-filled: the gap is recorded as metadata and the
missing samples simply do not exist.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import CollectionGap, TelemetryRaw

logger = get_logger("gap_detector")


class GapDetector:
    """Stateless detector; all state lives in the DB."""

    def __init__(self) -> None:
        cfg = get_config()
        self.interval = cfg.sampling_interval
        self.tolerance = int(cfg.get("gaps.tolerance_seconds", 30))

    def check(
        self,
        session: Session,
        previous: TelemetryRaw | None,
        current: TelemetryRaw,
    ) -> CollectionGap | None:
        if previous is None or previous.timestamp_utc is None:
            return None

        elapsed = (current.timestamp_utc - previous.timestamp_utc).total_seconds()
        threshold = self.interval + self.tolerance
        if elapsed <= threshold:
            return None

        expected = max(1, round(elapsed / self.interval) - 1)
        gap = CollectionGap(
            gap_start=previous.timestamp_utc,
            gap_end=current.timestamp_utc,
            duration_seconds=round(elapsed, 1),
            expected_samples=expected,
            lost_samples=expected,
            reason=self._infer_reason(previous, current, elapsed),
            detected_at=utc_now(),
            notes=f"gap of {elapsed:.0f}s between consecutive samples "
            f"(interval={self.interval}s, tolerance={self.tolerance}s)",
        )
        repo.add_gap(session, gap)
        logger.warning(
            "GAP detected: %.0fs, ~%d samples lost, reason=%s", elapsed, expected, gap.reason
        )
        return gap

    def _infer_reason(
        self, previous: TelemetryRaw, current: TelemetryRaw, elapsed: float
    ) -> str:
        # FortiGate uptime went backwards -> the device rebooted.
        if (
            previous.uptime_seconds is not None
            and current.uptime_seconds is not None
            and current.uptime_seconds < previous.uptime_seconds
        ):
            return "SYSTEM_REBOOT"
        if not current.collection_success:
            err = (current.error_message or "").lower()
            if "unreach" in err or "connect" in err or "timeout" in err:
                return "FORTIGATE_UNREACHABLE"
            if "auth" in err:
                return "API_FAILURE"
            return "COLLECTOR_FAILURE"
        # Long gap but the next sample succeeded and the box did not reboot:
        # most likely the collector process (or the Pi) was down.
        if elapsed > 5 * self.interval:
            return "COLLECTOR_FAILURE"
        return "UNKNOWN"


def summarise_gaps(session: Session) -> dict[str, object]:
    """Aggregate gap stats for the dataset dashboard."""
    gaps = repo.all_gaps(session)
    by_reason: dict[str, int] = {}
    for g in gaps:
        by_reason[g.reason] = by_reason.get(g.reason, 0) + 1
    return {
        "count": len(gaps),
        "total_lost_samples": sum(g.lost_samples for g in gaps),
        "total_gap_seconds": round(sum(g.duration_seconds for g in gaps), 1),
        "by_reason": by_reason,
        "last_gap": gaps[0].gap_end.isoformat() if gaps else None,
    }


def expected_samples_between(start: datetime, end: datetime, interval_seconds: int) -> int:
    return max(0, int((end - start).total_seconds() // interval_seconds) + 1)
