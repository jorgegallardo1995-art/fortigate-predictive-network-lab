"""Continuous availability timeline.

``telemetry_raw`` is sparse (it has real holes where nothing was captured). For
"continuidad operativa" analysis the thesis needs a **gap-free** sequence: one
row per expected sampling slot from the dataset start to now, each marked:

* ``collector_up`` - was there a real observation for this slot?
* ``network_up``   - the measured WAN/SLA state (NULL while ``collector_up`` is
  False: genuinely unknown, never assumed)
* ``reason``       - why the collector was down (from the covering gap)

This table is fully derived and can be rebuilt from scratch at any time. It never
adds invented telemetry - it only records presence/absence on a regular grid.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.database import session_scope
from app.database.models import (
    DATA_SOURCE_MOCK,
    DATA_SOURCE_REAL,
    AvailabilityMinute,
    CollectionGap,
    TelemetryRaw,
)

logger = get_logger("availability")


def _network_up(link_status: str | None, sla_status: str | None, loss: float | None) -> bool:
    if link_status is not None and str(link_status).lower() not in ("up", "true", "1", "alive"):
        return False
    if sla_status is not None and str(sla_status).lower() == "down":
        return False
    if loss is not None and loss >= 100.0:
        return False
    return True


def rebuild(session: Session, data_source: str, *, full: bool = False) -> int:
    """(Re)build the availability grid for ``data_source``. Returns rows written."""
    cfg = get_config()
    step = timedelta(seconds=cfg.sampling_interval)
    tol = timedelta(seconds=cfg.get("gaps.tolerance_seconds", 30) + cfg.sampling_interval / 2)

    first, last = repo.raw_time_bounds(session, data_source)
    if first is None:
        return 0
    end = utc_now()

    if full:
        session.execute(
            delete(AvailabilityMinute).where(AvailabilityMinute.data_source == data_source)
        )
        start = _floor(first, step)
    else:
        existing_max = session.execute(
            select(AvailabilityMinute.slot_utc)
            .where(AvailabilityMinute.data_source == data_source)
            .order_by(AvailabilityMinute.slot_utc.desc())
            .limit(1)
        ).scalar_one_or_none()
        # Rebuild the last two slots (they may have flipped) plus everything new.
        start = (existing_max - 2 * step) if existing_max else _floor(first, step)
        session.execute(
            delete(AvailabilityMinute).where(
                AvailabilityMinute.data_source == data_source,
                AvailabilityMinute.slot_utc >= start,
            )
        )

    # Load the observations and gaps once for the window.
    samples = session.execute(
        select(
            TelemetryRaw.id,
            TelemetryRaw.timestamp_utc,
            TelemetryRaw.link_status,
            TelemetryRaw.sla_status,
            TelemetryRaw.latency_ms,
            TelemetryRaw.packet_loss_pct,
        )
        .where(TelemetryRaw.data_source == data_source, TelemetryRaw.timestamp_utc >= start - step)
        .order_by(TelemetryRaw.timestamp_utc)
    ).all()
    gaps = session.execute(
        select(CollectionGap).where(CollectionGap.gap_end >= start - step).order_by(CollectionGap.gap_start)
    ).scalars().all()

    written = 0
    si = 0
    slot = start
    while slot <= end:
        # advance sample pointer to the first sample at/after slot-tol
        while si < len(samples) and samples[si][1] < slot - tol:
            si += 1
        match = None
        if si < len(samples) and abs((samples[si][1] - slot).total_seconds()) <= tol.total_seconds():
            match = samples[si]

        if match is not None:
            row = AvailabilityMinute(
                slot_utc=slot,
                data_source=data_source,
                collector_up=True,
                network_up=_network_up(match[2], match[3], match[5]),
                reason=None,
                sample_id=match[0],
                latency_ms=match[4],
                packet_loss_pct=match[5],
                rebuilt_at=utc_now(),
            )
        else:
            gap = next((g for g in gaps if g.gap_start <= slot <= g.gap_end), None)
            row = AvailabilityMinute(
                slot_utc=slot,
                data_source=data_source,
                collector_up=False,
                network_up=None,
                reason=(gap.reason if gap else "UNKNOWN"),
                gap_id=(gap.id if gap else None),
                rebuilt_at=utc_now(),
            )
        session.add(row)
        written += 1
        slot += step

    logger.info("availability timeline rebuilt (%s): %d slots from %s", data_source, written, start)
    return written


def rebuild_all(*, full: bool = False) -> int:
    total = 0
    with session_scope() as session:
        for ds in (DATA_SOURCE_REAL, DATA_SOURCE_MOCK):
            if repo.count_raw(session, ds):
                total += rebuild(session, ds, full=full)
    return total


def summary(session: Session, data_source: str, hours: int | None = None) -> dict[str, Any]:
    """Availability KPIs + the list of outages for the given window (or all-time)."""
    stmt = select(AvailabilityMinute).where(AvailabilityMinute.data_source == data_source)
    if hours:
        stmt = stmt.where(AvailabilityMinute.slot_utc >= utc_now() - timedelta(hours=hours))
    rows = session.execute(stmt.order_by(AvailabilityMinute.slot_utc)).scalars().all()
    if not rows:
        return {"data_source": data_source, "total_slots": 0}

    total = len(rows)
    collector_up = sum(1 for r in rows if r.collector_up)
    net_known = [r for r in rows if r.network_up is not None]
    net_up = sum(1 for r in net_known if r.network_up)
    step_min = get_config().sampling_interval / 60.0

    by_reason: dict[str, int] = {}
    for r in rows:
        if not r.collector_up:
            by_reason[r.reason or "UNKNOWN"] = by_reason.get(r.reason or "UNKNOWN", 0) + 1

    return {
        "data_source": data_source,
        "window_hours": hours,
        "first_slot": rows[0].slot_utc.isoformat(),
        "last_slot": rows[-1].slot_utc.isoformat(),
        "total_slots": total,
        "slot_minutes": step_min,
        "collector_up_slots": collector_up,
        "collector_down_slots": total - collector_up,
        "collector_availability_pct": round(collector_up / total * 100, 3),
        "collector_downtime_minutes": round((total - collector_up) * step_min, 1),
        "network_up_slots": net_up,
        "network_measured_slots": len(net_known),
        "network_availability_pct": round(net_up / len(net_known) * 100, 3) if net_known else None,
        "network_downtime_minutes": round((len(net_known) - net_up) * step_min, 1),
        "down_by_reason": by_reason,
        "outages": _outages(rows, step_min),
    }


def _outages(rows: list[AvailabilityMinute], step_min: float) -> list[dict[str, Any]]:
    """Collapse consecutive collector-down slots into discrete outage records."""
    out: list[dict[str, Any]] = []
    run: list[AvailabilityMinute] = []
    for r in rows:
        if not r.collector_up:
            run.append(r)
        elif run:
            out.append(_outage_record(run, step_min))
            run = []
    if run:
        out.append(_outage_record(run, step_min))
    return out


def _outage_record(run: list[AvailabilityMinute], step_min: float) -> dict[str, Any]:
    reasons = {r.reason for r in run if r.reason}
    return {
        "start": run[0].slot_utc.isoformat(),
        "end": run[-1].slot_utc.isoformat(),
        "slots": len(run),
        "minutes": round(len(run) * step_min, 1),
        "reason": sorted(reasons)[0] if len(reasons) == 1 else "/".join(sorted(reasons)) or "UNKNOWN",
    }


def timeline_segments(session: Session, data_source: str, hours: int) -> list[dict[str, Any]]:
    """Run-length-encoded timeline for a strip chart (never returns 43k points)."""
    rows = session.execute(
        select(AvailabilityMinute.slot_utc, AvailabilityMinute.collector_up,
               AvailabilityMinute.network_up, AvailabilityMinute.reason)
        .where(
            AvailabilityMinute.data_source == data_source,
            AvailabilityMinute.slot_utc >= utc_now() - timedelta(hours=hours),
        )
        .order_by(AvailabilityMinute.slot_utc)
    ).all()
    segs: list[dict[str, Any]] = []
    for slot, cu, nu, reason in rows:
        state = "up" if (cu and nu) else "degraded" if cu else "down"
        if segs and segs[-1]["state"] == state and segs[-1].get("reason") == (reason if not cu else None):
            segs[-1]["end"] = slot.isoformat()
        else:
            segs.append(
                {"start": slot.isoformat(), "end": slot.isoformat(), "state": state,
                 "reason": reason if not cu else None}
            )
    return segs


def _floor(dt: datetime, step: timedelta) -> datetime:
    epoch = dt.timestamp()
    return datetime.fromtimestamp(epoch - (epoch % step.total_seconds()), dt.tzinfo)
