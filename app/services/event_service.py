"""Operational event detection and recording.

Events are derived from state *transitions* between consecutive samples (WAN
up->down, SLA ok->failed, FortiGate reboot, ...) plus lifecycle events emitted
by the scheduler and other services.

None of this touches ``telemetry_raw.label`` - events are candidate ground truth
for later manual labelling, not automatic labels.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    CollectionGap,
    NetworkEvent,
    TelemetryRaw,
)
from app.schemas.telemetry import SourceSnapshot

logger = get_logger("events")

_GAP_REASON_EVENT = {
    "SYSTEM_REBOOT": "FORTIGATE_RESTART",
    "FORTIGATE_UNREACHABLE": "API_ERROR",
    "API_FAILURE": "API_ERROR",
    "COLLECTOR_FAILURE": "COLLECTOR_RESTARTED",
}


def record_event(
    session: Session,
    event_type: str,
    description: str,
    *,
    severity: str = SEVERITY_INFO,
    related_interface: str | None = None,
    experiment_id: str | None = None,
    data_source: str = "REAL",
    details: dict[str, Any] | None = None,
) -> NetworkEvent:
    """Persist one event. Used by the collector, scheduler and services."""
    event = NetworkEvent(
        timestamp_utc=utc_now(),
        event_type=event_type,
        severity=severity,
        description=description,
        related_interface=related_interface,
        experiment_id=experiment_id,
        data_source=data_source,
        details=details,
        created_at=utc_now(),
    )
    repo.add_event(session, event)
    logger.info("event %s [%s] %s", event_type, severity, description)
    return event


def emit(event_type: str, description: str, **kwargs: Any) -> None:
    """Convenience wrapper that opens its own session (lifecycle events)."""
    from app.database.database import session_scope

    with session_scope() as session:
        record_event(session, event_type, description, **kwargs)


def _link_up(value: str | None) -> bool | None:
    if value is None:
        return None
    return str(value).lower() in ("up", "true", "1", "alive")


def detect_cycle_events(
    session: Session,
    previous: TelemetryRaw | None,
    current: TelemetryRaw,
    snapshot: SourceSnapshot,
    gap: CollectionGap | None,
) -> None:
    """Compare previous vs current sample and record any transitions."""
    ds = current.data_source
    exp = repo.active_experiment(session, current.timestamp_utc)
    exp_id = exp.experiment_id if exp else None
    iface = current.wan_interface

    # --- gap -> corresponding event ---
    if gap is not None:
        ev_type = _GAP_REASON_EVENT.get(gap.reason, "MISSING_INTERVAL")
        record_event(
            session,
            ev_type,
            f"Collection gap of {gap.duration_seconds:.0f}s "
            f"(~{gap.lost_samples} samples), reason={gap.reason}",
            severity=SEVERITY_WARNING,
            related_interface=iface,
            experiment_id=exp_id,
            data_source=ds,
            details={"gap_start": gap.gap_start.isoformat(), "reason": gap.reason},
        )

    # --- API failure ---
    if not current.collection_success:
        recent_fail = repo.count_events_since(
            session, utc_now() - timedelta(minutes=10), "API_ERROR"
        )
        if recent_fail == 0:
            record_event(
                session,
                "API_ERROR",
                f"Acquisition failed: {snapshot.error or 'unknown error'}",
                severity=SEVERITY_CRITICAL,
                related_interface=iface,
                experiment_id=exp_id,
                data_source=ds,
            )
        return

    if previous is None:
        return

    # --- WAN / interface link transitions ---
    prev_up = _link_up(previous.link_status)
    cur_up = _link_up(current.link_status)
    if prev_up is not None and cur_up is not None and prev_up != cur_up:
        if cur_up:
            record_event(session, "WAN_UP", f"WAN link {iface} came up",
                         severity=SEVERITY_INFO, related_interface=iface,
                         experiment_id=exp_id, data_source=ds)
            record_event(session, "INTERFACE_UP", f"Interface {iface} up",
                         severity=SEVERITY_INFO, related_interface=iface,
                         experiment_id=exp_id, data_source=ds)
        else:
            record_event(session, "WAN_DOWN", f"WAN link {iface} went down",
                         severity=SEVERITY_CRITICAL, related_interface=iface,
                         experiment_id=exp_id, data_source=ds)
            record_event(session, "INTERFACE_DOWN", f"Interface {iface} down",
                         severity=SEVERITY_CRITICAL, related_interface=iface,
                         experiment_id=exp_id, data_source=ds)

    # --- SD-WAN SLA transitions (aggregate) ---
    prev_sla = str(previous.sla_status).lower() if previous.sla_status else None
    cur_sla = str(current.sla_status).lower() if current.sla_status else None
    if prev_sla and cur_sla and prev_sla != cur_sla:
        if cur_sla == "down":
            record_event(session, "SLA_FAILED", "Performance SLA aggregate state -> down",
                         severity=SEVERITY_CRITICAL, related_interface=iface,
                         experiment_id=exp_id, data_source=ds)
        elif cur_sla == "up":
            record_event(session, "SLA_RECOVERED", "Performance SLA aggregate state -> up",
                         severity=SEVERITY_INFO, related_interface=iface,
                         experiment_id=exp_id, data_source=ds)

    # --- FortiGate restart (uptime went backwards) ---
    if (
        previous.uptime_seconds is not None
        and current.uptime_seconds is not None
        and current.uptime_seconds < previous.uptime_seconds
        and gap is None
    ):
        record_event(
            session, "FORTIGATE_RESTART",
            f"FortiGate uptime dropped ({previous.uptime_seconds}s -> {current.uptime_seconds}s)",
            severity=SEVERITY_WARNING, related_interface=iface,
            experiment_id=exp_id, data_source=ds,
        )
