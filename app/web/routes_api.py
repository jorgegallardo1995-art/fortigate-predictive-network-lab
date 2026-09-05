"""JSON API (documented at /docs).

All FortiGate-facing calls are GET (see :mod:`app.api.fortigate_client`). The
local POST endpoints here only ever write to the local research database
(experiment registration, metadata snapshot, export triggers).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_config, get_settings
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.database import get_session, session_scope
from app.database.models import (
    DATA_SOURCE_MOCK,
    DATA_SOURCE_REAL,
    EXPERIMENT_SCENARIOS,
    Experiment,
)
from app.services import health_service, research_service
from app.services.export_service import EXPORTERS, ExportFilter
from app.services.statistics_service import (
    dataset_overview,
    history_series,
    latest_values,
    variable_coverage,
    variable_statistics,
)

router = APIRouter(prefix="/api", tags=["api"])


def _default_source() -> str:
    return DATA_SOURCE_MOCK if get_settings().mock_mode else DATA_SOURCE_REAL


# --- status / health -------------------------------------------------------
@router.get("/status")
def get_status() -> dict[str, Any]:
    return health_service.platform_health()


@router.get("/health")
def get_health() -> dict[str, Any]:
    h = health_service.platform_health()
    return {"status": "ok" if h["checks"]["database"] == "ok" else "degraded", "checks": h["checks"]}


# --- telemetry ------------------------------------------------------------
@router.get("/telemetry/latest")
def telemetry_latest(
    data_source: str | None = None, session: Session = Depends(get_session)
) -> dict[str, Any]:
    ds = data_source or _default_source()
    values = latest_values(session, ds)
    if not values:
        raise HTTPException(404, "no telemetry yet")
    values["sla"] = [
        {
            "label": s.label, "target": s.target, "latency_ms": s.latency_ms,
            "jitter_ms": s.jitter_ms, "packet_loss_pct": s.packet_loss_pct, "state": s.sla_state,
        }
        for s in repo.latest_sla(session, ds)
    ]
    values["icmp"] = [
        {"label": p.label, "address": p.address, "rtt_avg_ms": p.rtt_avg_ms,
         "loss_pct": p.packet_loss_pct, "success": p.success}
        for p in repo.latest_probes(session, ds)
    ]
    return values


@router.get("/telemetry/history")
def telemetry_history(
    metrics: str = Query("latency_ms,rx_mbps,tx_mbps,cpu_pct"),
    range: str = Query("24h"),
    data_source: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ds = data_source or _default_source()
    return history_series(session, [m.strip() for m in metrics.split(",") if m.strip()], range, ds)


# --- dataset -----------------------------------------------------------
@router.get("/dataset/statistics")
def dataset_statistics(
    data_source: str | None = None, session: Session = Depends(get_session)
) -> dict[str, Any]:
    return dataset_overview(session, data_source or _default_source())


@router.get("/dataset/variables")
def dataset_variables(
    data_source: str | None = None, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    return variable_statistics(session, data_source or _default_source())


@router.get("/dataset/coverage")
def dataset_coverage(
    data_source: str | None = None, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Records collected per variable, grouped by family (feeds the /variables page)."""
    return variable_coverage(session, data_source or _default_source())


# --- availability (continuous timeline) ------------------------------------
_RANGE_HOURS = {"24h": 24, "7d": 168, "30d": 720, "all": None}


@router.get("/availability/summary")
def availability_summary(
    range: str = "all",
    data_source: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    from app.services import availability_service

    return availability_service.summary(
        session, data_source or _default_source(), _RANGE_HOURS.get(range, None)
    )


@router.get("/availability/timeline")
def availability_timeline(
    range: str = "7d",
    data_source: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    from app.services import availability_service

    hours = _RANGE_HOURS.get(range) or 720
    return {
        "range": range,
        "segments": availability_service.timeline_segments(
            session, data_source or _default_source(), hours
        ),
    }


@router.post("/availability/rebuild")
def availability_rebuild(full: bool = False) -> dict[str, Any]:
    from app.services import availability_service

    return {"slots_written": availability_service.rebuild_all(full=full)}


# --- events / gaps ----------------------------------------------------
@router.get("/events")
def get_events(
    hours: int = 168,
    event_type: str | None = None,
    severity: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    since = utc_now().replace(microsecond=0) - timedelta(hours=hours)
    events = repo.recent_events(session, since, event_type, severity)
    return [
        {
            "timestamp_utc": e.timestamp_utc.isoformat(),
            "event_type": e.event_type,
            "severity": e.severity,
            "description": e.description,
            "related_interface": e.related_interface,
            "experiment_id": e.experiment_id,
            "data_source": e.data_source,
        }
        for e in events
    ]


@router.get("/gaps")
def get_gaps(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [
        {
            "id": g.id,
            "gap_start": g.gap_start.isoformat(),
            "gap_end": g.gap_end.isoformat(),
            "duration_seconds": g.duration_seconds,
            "expected_samples": g.expected_samples,
            "lost_samples": g.lost_samples,
            "reason": g.reason,
            "host_rebooted": g.host_rebooted,
            "notes": g.notes,
        }
        for g in repo.all_gaps(session)
    ]


class GapReasonIn(BaseModel):
    reason: str
    notes: str | None = None


@router.patch("/gaps/{gap_id}")
def set_gap_reason(gap_id: int, payload: GapReasonIn) -> dict[str, Any]:
    """Manually correct a gap's cause (e.g. mark a known power outage the host
    could not self-detect). Rebuilds the availability timeline afterwards."""
    from app.database.models import GAP_REASONS, CollectionGap
    from app.services.availability_service import rebuild_all

    if payload.reason not in GAP_REASONS:
        raise HTTPException(422, f"reason must be one of {list(GAP_REASONS)}")
    with session_scope() as session:
        gap = session.get(CollectionGap, gap_id)
        if gap is None:
            raise HTTPException(404, "gap not found")
        gap.reason = payload.reason
        if payload.notes:
            gap.notes = f"{gap.notes or ''} | manual: {payload.notes}".strip(" |")
    rebuild_all(full=True)
    return {"id": gap_id, "reason": payload.reason}


# --- experiments -----------------------------------------------------
class ExperimentIn(BaseModel):
    experiment_id: str = Field(..., min_length=1, max_length=64)
    name: str
    scenario: str
    description: str | None = None
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None
    notes: str | None = None
    operator: str | None = None


@router.get("/experiments")
def get_experiments(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [
        {
            "experiment_id": e.experiment_id,
            "name": e.name,
            "scenario": e.scenario,
            "description": e.description,
            "start_timestamp": e.start_timestamp.isoformat(),
            "end_timestamp": e.end_timestamp.isoformat() if e.end_timestamp else None,
            "operator": e.operator,
            "notes": e.notes,
        }
        for e in repo.list_experiments(session)
    ]


@router.post("/experiments", status_code=201)
def create_experiment(payload: ExperimentIn) -> dict[str, Any]:
    if payload.scenario not in EXPERIMENT_SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {EXPERIMENT_SCENARIOS}")
    with session_scope() as session:
        if session.query(Experiment).filter_by(experiment_id=payload.experiment_id).first():
            raise HTTPException(409, "experiment_id already exists")
        exp = Experiment(
            experiment_id=payload.experiment_id,
            name=payload.name,
            scenario=payload.scenario,
            description=payload.description,
            start_timestamp=payload.start_timestamp or utc_now(),
            end_timestamp=payload.end_timestamp,
            notes=payload.notes,
            operator=payload.operator,
            created_at=utc_now(),
        )
        session.add(exp)
        from app.services.event_service import record_event

        record_event(session, "EXPERIMENT_START", f"Experiment {exp.experiment_id} ({exp.scenario})",
                     experiment_id=exp.experiment_id)
    return {"created": payload.experiment_id}


@router.post("/experiments/{experiment_id}/close")
def close_experiment(experiment_id: str) -> dict[str, Any]:
    with session_scope() as session:
        exp = session.query(Experiment).filter_by(experiment_id=experiment_id).first()
        if not exp:
            raise HTTPException(404, "not found")
        exp.end_timestamp = utc_now()
        from app.services.event_service import record_event

        record_event(session, "EXPERIMENT_END", f"Experiment {experiment_id} closed",
                     experiment_id=experiment_id)
    return {"closed": experiment_id}


# --- research / metadata --------------------------------------------
@router.get("/research/metadata")
def research_metadata() -> dict[str, Any]:
    return research_service.research_dashboard_context()


@router.post("/research/metadata/snapshot")
def research_metadata_snapshot() -> dict[str, Any]:
    meta = research_service.write_research_metadata_file()
    return {"written": "research_metadata.json", "dataset": meta["dataset"]}


# --- export ---------------------------------------------------------
@router.get("/export/{kind}")
def export(
    kind: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    data_source: str = DATA_SOURCE_REAL,
    download: bool = True,
) -> Any:
    if kind not in EXPORTERS:
        raise HTTPException(404, f"unknown export kind '{kind}'. options: {list(EXPORTERS)}")
    f = ExportFilter(start=start, end=end, experiment_id=experiment_id, data_source=data_source)
    path = EXPORTERS[kind](f)
    if download:
        return FileResponse(path, media_type="text/csv", filename=path.name)
    return {"file": str(path), "name": path.name}


@router.get("/config/summary")
def config_summary() -> dict[str, Any]:
    cfg = get_config()
    s = get_settings()
    return {
        "mode": "MOCK" if s.mock_mode else "REAL",
        "sampling_interval_seconds": cfg.sampling_interval,
        "feature_windows_minutes": cfg.feature_windows,
        "sla_targets": cfg.sla_targets,
        "thresholds": cfg.get("thresholds", {}),
        "timezone": s.timezone,
        "fortigate_host": s.fortigate_host,
        "verify_ssl": s.verify_ssl,
        "token_configured": bool(s.fortigate_api_token),
    }
