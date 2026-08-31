"""Data-access helpers.

Thin functions over the ORM so services and web routes never build queries by
hand. All read helpers accept an explicit :class:`Session`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.timeutils import utc_now
from app.database.models import (
    CollectionGap,
    Experiment,
    NetworkEvent,
    ProbeResult,
    ResearchMetadata,
    SystemStatus,
    TelemetryFeature,
    TelemetryRaw,
    TelemetrySla,
)


# --- telemetry_raw ---------------------------------------------------------
def add_raw(session: Session, raw: TelemetryRaw) -> TelemetryRaw:
    session.add(raw)
    session.flush()
    return raw


def latest_raw(session: Session, data_source: str | None = None) -> TelemetryRaw | None:
    stmt: Select = select(TelemetryRaw).order_by(TelemetryRaw.timestamp_utc.desc()).limit(1)
    if data_source:
        stmt = stmt.where(TelemetryRaw.data_source == data_source)
    return session.execute(stmt).scalar_one_or_none()


def previous_successful_raw(
    session: Session, before: datetime, data_source: str
) -> TelemetryRaw | None:
    stmt = (
        select(TelemetryRaw)
        .where(
            TelemetryRaw.timestamp_utc < before,
            TelemetryRaw.data_source == data_source,
            TelemetryRaw.collection_success.is_(True),
        )
        .order_by(TelemetryRaw.timestamp_utc.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def raw_history(
    session: Session,
    since: datetime,
    until: datetime | None = None,
    data_source: str | None = None,
) -> list[TelemetryRaw]:
    stmt = select(TelemetryRaw).where(TelemetryRaw.timestamp_utc >= since)
    if until:
        stmt = stmt.where(TelemetryRaw.timestamp_utc <= until)
    if data_source:
        stmt = stmt.where(TelemetryRaw.data_source == data_source)
    stmt = stmt.order_by(TelemetryRaw.timestamp_utc.asc())
    return list(session.execute(stmt).scalars().all())


def count_raw(session: Session, data_source: str | None = None) -> int:
    stmt = select(func.count(TelemetryRaw.id))
    if data_source:
        stmt = stmt.where(TelemetryRaw.data_source == data_source)
    return int(session.execute(stmt).scalar_one())


def count_raw_since(session: Session, since: datetime, data_source: str | None = None) -> int:
    stmt = select(func.count(TelemetryRaw.id)).where(TelemetryRaw.timestamp_utc >= since)
    if data_source:
        stmt = stmt.where(TelemetryRaw.data_source == data_source)
    return int(session.execute(stmt).scalar_one())


def raw_time_bounds(
    session: Session, data_source: str | None = None
) -> tuple[datetime | None, datetime | None]:
    stmt = select(func.min(TelemetryRaw.timestamp_utc), func.max(TelemetryRaw.timestamp_utc))
    if data_source:
        stmt = stmt.where(TelemetryRaw.data_source == data_source)
    row = session.execute(stmt).one()
    return row[0], row[1]


# --- SLA / probe ----------------------------------------------------------
def latest_sla(session: Session, data_source: str | None = None) -> list[TelemetrySla]:
    last = latest_raw(session, data_source)
    if last is None:
        return []
    stmt = select(TelemetrySla).where(TelemetrySla.raw_id == last.id)
    return list(session.execute(stmt).scalars().all())


def latest_probes(session: Session, data_source: str | None = None) -> list[ProbeResult]:
    last = latest_raw(session, data_source)
    if last is None:
        return []
    stmt = select(ProbeResult).where(ProbeResult.raw_id == last.id)
    return list(session.execute(stmt).scalars().all())


# --- features ------------------------------------------------------------
def upsert_feature(session: Session, feature: TelemetryFeature) -> None:
    existing = session.execute(
        select(TelemetryFeature).where(
            TelemetryFeature.timestamp_utc == feature.timestamp_utc,
            TelemetryFeature.window_minutes == feature.window_minutes,
            TelemetryFeature.data_source == feature.data_source,
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(feature)
        return
    skip = {"id", "timestamp_utc", "window_minutes", "data_source"}
    for col in TelemetryFeature.__table__.columns:
        if col.name not in skip:
            setattr(existing, col.name, getattr(feature, col.name))


def last_feature_timestamp(session: Session, data_source: str) -> datetime | None:
    stmt = (
        select(func.max(TelemetryFeature.timestamp_utc))
        .where(TelemetryFeature.data_source == data_source)
    )
    return session.execute(stmt).scalar_one_or_none()


# --- events / gaps ------------------------------------------------------
def add_event(session: Session, event: NetworkEvent) -> None:
    session.add(event)


def recent_events(
    session: Session,
    since: datetime,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 500,
) -> list[NetworkEvent]:
    stmt = select(NetworkEvent).where(NetworkEvent.timestamp_utc >= since)
    if event_type:
        stmt = stmt.where(NetworkEvent.event_type == event_type)
    if severity:
        stmt = stmt.where(NetworkEvent.severity == severity)
    stmt = stmt.order_by(NetworkEvent.timestamp_utc.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def count_events_since(session: Session, since: datetime, event_type: str | None = None) -> int:
    stmt = select(func.count(NetworkEvent.id)).where(NetworkEvent.timestamp_utc >= since)
    if event_type:
        stmt = stmt.where(NetworkEvent.event_type == event_type)
    return int(session.execute(stmt).scalar_one())


def last_event_of_type(session: Session, event_type: str) -> NetworkEvent | None:
    stmt = (
        select(NetworkEvent)
        .where(NetworkEvent.event_type == event_type)
        .order_by(NetworkEvent.timestamp_utc.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def add_gap(session: Session, gap: CollectionGap) -> None:
    session.add(gap)


def all_gaps(session: Session) -> list[CollectionGap]:
    return list(
        session.execute(select(CollectionGap).order_by(CollectionGap.gap_start.desc())).scalars()
    )


def count_gaps(session: Session) -> int:
    return int(session.execute(select(func.count(CollectionGap.id))).scalar_one())


def total_lost_samples(session: Session) -> int:
    return int(session.execute(select(func.coalesce(func.sum(CollectionGap.lost_samples), 0))).scalar_one())


# --- experiments -------------------------------------------------------
def list_experiments(session: Session) -> list[Experiment]:
    return list(
        session.execute(select(Experiment).order_by(Experiment.start_timestamp.desc())).scalars()
    )


def active_experiment(session: Session, at: datetime | None = None) -> Experiment | None:
    at = at or utc_now()
    stmt = (
        select(Experiment)
        .where(
            Experiment.start_timestamp <= at,
            (Experiment.end_timestamp.is_(None)) | (Experiment.end_timestamp >= at),
        )
        .order_by(Experiment.start_timestamp.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


# --- system status / metadata ----------------------------------------
def add_system_status(session: Session, status: SystemStatus) -> None:
    session.add(status)


def latest_system_status(session: Session) -> SystemStatus | None:
    stmt = select(SystemStatus).order_by(SystemStatus.timestamp_utc.desc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


def latest_research_metadata(session: Session) -> ResearchMetadata | None:
    stmt = select(ResearchMetadata).order_by(ResearchMetadata.created_at.desc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


def add_research_metadata(session: Session, meta: ResearchMetadata) -> None:
    session.add(meta)


def failed_requests_since(session: Session, since: datetime) -> int:
    """Count of failed acquisition cycles (proxy for failed API requests)."""
    stmt = select(func.count(TelemetryRaw.id)).where(
        TelemetryRaw.timestamp_utc >= since,
        TelemetryRaw.collection_success.is_(False),
    )
    return int(session.execute(stmt).scalar_one())


def default_window_start(hours: int = 24) -> datetime:
    return utc_now() - timedelta(hours=hours)
