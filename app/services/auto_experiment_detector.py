"""Automatic detection and registration of real WAN outages as controlled
experiments (scenario=WAN_FAILURE), from telemetry_sla state transitions.

Runs periodically from the collector scheduler so a recurring, unattended
outage protocol (e.g. a daily internet cut at a fixed time window) doesn't
need a human to register each one by hand. This only detects and records
real, already-observed sla_state transitions in telemetry_sla - it never
predicts or infers a failure that telemetry didn't actually show, matching
the same ground-truth discipline as the manual POST /api/experiments flow.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database.database import session_scope
from app.database.models import DATA_SOURCE_REAL, Experiment, TelemetrySla

logger = get_logger("auto_experiment_detector")

LOOKBACK = timedelta(hours=8)
RECOVERY_CONFIRM_ROWS = 2  # consecutive "up" rows required before a down-run counts as finished


def detect_and_register(data_source: str = DATA_SOURCE_REAL) -> list[str]:
    """Scan recent telemetry_sla for down->up transitions and auto-register
    any that aren't already covered by an existing experiment (manual or
    auto). Returns the list of newly created experiment_ids."""
    created: list[str] = []
    with session_scope() as session:
        since = utc_now() - LOOKBACK
        rows = session.execute(
            select(TelemetrySla.timestamp_utc, TelemetrySla.sla_state)
            .where(TelemetrySla.data_source == data_source, TelemetrySla.timestamp_utc >= since)
            .order_by(TelemetrySla.timestamp_utc)
        ).all()
        if not rows:
            return created

        runs: list[tuple[Any, Any]] = []
        down_start = None
        down_end = None
        up_streak = 0
        for ts, state in rows:
            if state == "down":
                if down_start is None:
                    down_start = ts
                down_end = ts
                up_streak = 0
            elif state == "up":
                up_streak += 1
                if down_start is not None and up_streak >= RECOVERY_CONFIRM_ROWS:
                    runs.append((down_start, down_end))
                    down_start = None
                    down_end = None
            # any other/NULL state: ignore, keep the current run (if any) open

        buffer = timedelta(minutes=2)  # touching/adjacent counts as the same outage
        for start, end in runs:
            existing = session.execute(
                select(Experiment).where(
                    and_(
                        Experiment.start_timestamp <= end + buffer,
                        or_(
                            Experiment.end_timestamp.is_(None),
                            Experiment.end_timestamp >= start - buffer,
                        ),
                    )
                )
            ).scalars().first()
            if existing:
                continue

            exp_id = f"EXP-AUTO-{start.strftime('%Y%m%dT%H%M%SZ')}"
            exp = Experiment(
                experiment_id=exp_id,
                name="Corte de ISP (auto-detectado)",
                scenario="WAN_FAILURE",
                description="Detectado automaticamente desde transiciones sla_state en "
                             "telemetry_sla (protocolo de corte recurrente). Ver ml/README.md.",
                start_timestamp=start,
                end_timestamp=end,
                failure_timestamp=start,
                operator="auto-detector",
                created_at=utc_now(),
            )
            session.add(exp)
            from app.services.event_service import record_event

            record_event(session, "EXPERIMENT_START",
                         f"Auto-detected WAN_FAILURE experiment {exp_id}", experiment_id=exp_id)
            record_event(session, "EXPERIMENT_END", f"Experiment {exp_id} auto-closed (recovered)",
                         experiment_id=exp_id)
            created.append(exp_id)
            logger.info("auto-registered experiment %s (down %s -> %s)", exp_id, start, end)

    return created
