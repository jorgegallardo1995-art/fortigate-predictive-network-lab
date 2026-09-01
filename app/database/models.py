"""SQLAlchemy ORM models - the experimental dataset schema.

Design rules baked into this schema:

* ``telemetry_raw`` holds one **immutable** row per acquisition cycle. Derived
  values live in ``telemetry_features`` and are always recomputable from RAW.
* ``data_source`` (``REAL`` / ``MOCK``) is mandatory on every observation table so
  synthetic and real data can never be mixed in a scientific export.
* ``label`` is always ``NULL`` on write. Labels are assigned manually later; the
  dashboard thresholds must never populate them.
* Types are portable (no SQLite-only constructs) so the same models run on
  PostgreSQL after a ``DATABASE_URL`` change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy import DateTime as _SADateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON as _JSON

# Store Python ``None`` as SQL NULL (not the JSON string 'null'), so
# ``col.isnot(None)`` and CSV exports behave as expected.
JSON = _JSON(none_as_null=True)


class DateTime(TypeDecorator):
    """Timezone-aware UTC datetime, portable across SQLite and PostgreSQL.

    SQLite does not persist tzinfo, so values read back would be naive. This
    decorator normalises: aware -> UTC on write, naive -> assumed-UTC-aware on
    read. Application code can therefore always assume aware UTC datetimes.
    """

    impl = _SADateTime
    cache_ok = True

    def __init__(self, timezone: bool = True) -> None:
        super().__init__()
        self.impl = _SADateTime(timezone=True)

    def process_bind_param(self, value: datetime | None, _dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, _dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

# --- controlled vocabularies (kept as plain strings for portability) ----------

DATA_SOURCE_REAL = "REAL"
DATA_SOURCE_MOCK = "MOCK"

GAP_REASONS = (
    "SYSTEM_REBOOT",
    "COLLECTOR_FAILURE",
    "NETWORK_FAILURE",
    "FORTIGATE_UNREACHABLE",
    "API_FAILURE",
    "POWER_FAILURE",
    "UNKNOWN",
)

EVENT_TYPES = (
    "WAN_DOWN",
    "WAN_UP",
    "SLA_FAILED",
    "SLA_RECOVERED",
    "INTERFACE_DOWN",
    "INTERFACE_UP",
    "FORTIGATE_RESTART",
    "COLLECTOR_STARTED",
    "COLLECTOR_STOPPED",
    "COLLECTOR_RESTARTED",
    "API_ERROR",
    "DATABASE_ERROR",
    "MISSING_INTERVAL",
    "EXPERIMENT_START",
    "EXPERIMENT_END",
)

SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

EXPERIMENT_SCENARIOS = (
    "NORMAL",
    "LATENCY_DEGRADATION",
    "PACKET_LOSS",
    "CONGESTION",
    "WAN_FAILURE",
    "RECOVERY",
)


class Base(DeclarativeBase):
    """Declarative base for all models."""

    type_annotation_map: ClassVar[dict] = {dict: JSON, list: JSON}


# =============================================================================
# RAW TELEMETRY
# =============================================================================
class TelemetryRaw(Base):
    """One immutable observation per acquisition cycle."""

    __tablename__ = "telemetry_raw"
    __table_args__ = (
        Index("ix_raw_ts_source", "timestamp_utc", "data_source"),
        Index("ix_raw_experiment", "experiment_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- cycle bookkeeping ---
    scheduled_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collection_start_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collection_end_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collection_duration_ms: Mapped[int] = mapped_column(Integer)
    collection_success: Mapped[bool] = mapped_column(Boolean, default=False)
    api_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_source: Mapped[str] = mapped_column(String(8), index=True)
    missing_data: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- timestamps ---
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timestamp_local: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64))

    # --- A. identification ---
    device_hostname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fortios_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vdom: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wan_interface: Mapped[str | None] = mapped_column(String(32), nullable=True)
    collector_version: Mapped[str] = mapped_column(String(16))
    schema_version: Mapped[str] = mapped_column(String(8))

    # --- B. network quality (aggregate / primary target) ---
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    availability: Mapped[float | None] = mapped_column(Float, nullable=True)
    link_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sla_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- D. WAN traffic (raw cumulative counters) ---
    rx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rx_packets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tx_packets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- D. WAN traffic (derived by delta between polls) ---
    rx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- E. utilisation ---
    interface_speed_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- F. errors (raw counters) ---
    rx_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tx_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rx_drops: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tx_drops: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- F. errors (derived rates) ---
    rx_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_drop_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_drop_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- G. FortiGate resources ---
    cpu_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- H. sessions ---
    active_sessions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_sessions_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    tcp_sessions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    udp_sessions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- I. WAN interface ---
    interface_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    interface_speed: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interface_duplex: Mapped[str | None] = mapped_column(String(16), nullable=True)
    interface_mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_route_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- J. VPN (NULL on this device - no VPN configured) ---
    vpn_tunnels_up: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vpn_tunnels_down: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vpn_rx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vpn_tx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- full API snapshot for traceability ---
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- ML labelling (always NULL on write) ---
    label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    minutes_before_failure: Mapped[float | None] = mapped_column(Float, nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    sla_results: Mapped[list[TelemetrySla]] = relationship(
        back_populates="raw", cascade="all, delete-orphan"
    )
    probe_results: Mapped[list[ProbeResult]] = relationship(
        back_populates="raw", cascade="all, delete-orphan"
    )


# =============================================================================
# PERFORMANCE SLA (one row per health-check target per cycle)
# =============================================================================
class TelemetrySla(Base):
    """Per-target Performance SLA measurement from the FortiGate health-check."""

    __tablename__ = "telemetry_sla"
    __table_args__ = (Index("ix_sla_ts_label", "timestamp_utc", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_id: Mapped[int] = mapped_column(ForeignKey("telemetry_raw.id"), index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_source: Mapped[str] = mapped_column(String(8), index=True)

    health_check_name: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)

    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sla_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    probe_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    raw: Mapped[TelemetryRaw] = relationship(back_populates="sla_results")


# =============================================================================
# ICMP PROBE (run on the Raspberry Pi - independent cross-check)
# =============================================================================
class ProbeResult(Base):
    """ICMP echo statistics measured from the collector host (Pi -> target)."""

    __tablename__ = "probe_results"
    __table_args__ = (Index("ix_probe_ts_label", "timestamp_utc", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_id: Mapped[int] = mapped_column(ForeignKey("telemetry_raw.id"), index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_source: Mapped[str] = mapped_column(String(8), index=True)

    label: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(String(128))

    rtt_min_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_avg_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_max_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_mdev_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    packets_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packets_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)

    raw: Mapped[TelemetryRaw] = relationship(back_populates="probe_results")


# =============================================================================
# DERIVED FEATURES
# =============================================================================
class TelemetryFeature(Base):
    """Windowed derived features. Recomputable from ``telemetry_raw`` at any time."""

    __tablename__ = "telemetry_features"
    __table_args__ = (
        UniqueConstraint(
            "timestamp_utc", "window_minutes", "data_source", name="uq_feature_ts_window"
        ),
        Index("ix_feature_ts", "timestamp_utc"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_minutes: Mapped[int] = mapped_column(Integer)
    data_source: Mapped[str] = mapped_column(String(8), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # latency
    latency_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    # jitter
    jitter_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    # packet loss
    packet_loss_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    # rx
    rx_mbps_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    # tx
    tx_mbps_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    # sessions
    sessions_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    sessions_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    sessions_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    sessions_pct_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    # cpu
    cpu_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    # memory
    memory_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    # instantaneous ratios (evaluated at window end)
    rx_drop_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_drop_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    traffic_growth_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_tx_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)


# =============================================================================
# EVENTS / GAPS / EXPERIMENTS / STATUS / METADATA
# =============================================================================
class NetworkEvent(Base):
    """Operational event timeline (WAN up/down, SLA failures, restarts, errors...)."""

    __tablename__ = "network_events"
    __table_args__ = (Index("ix_event_ts_type", "timestamp_utc", "event_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), default=SEVERITY_INFO)
    description: Mapped[str] = mapped_column(Text)
    related_interface: Mapped[str | None] = mapped_column(String(32), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_source: Mapped[str] = mapped_column(String(8), default=DATA_SOURCE_REAL)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CollectionGap(Base):
    """A detected interruption in acquisition. Data is never back-filled."""

    __tablename__ = "collection_gaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    gap_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    gap_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float] = mapped_column(Float)
    expected_samples: Mapped[int] = mapped_column(Integer)
    lost_samples: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Experiment(Base):
    """A controlled experiment window. Scenarios are registered, never auto-generated."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario: Mapped[str] = mapped_column(String(32))
    start_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemStatus(Base):
    """Periodic snapshot of platform health (for the /system dashboard)."""

    __tablename__ = "system_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    fortigate_api_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    collector_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    scheduler_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    database_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    last_successful_collection: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_scheduled_collection: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    average_api_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    failed_requests_24h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collector_uptime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    host_uptime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    database_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    disk_available_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchMetadata(Base):
    """Reproducibility snapshot - mirrors ``research_metadata.json``. No secrets."""

    __tablename__ = "research_metadata"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collector_version: Mapped[str] = mapped_column(String(16))
    schema_version: Mapped[str] = mapped_column(String(8))
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fortios_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sampling_interval_seconds: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64))
    python_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    os_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hardware_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
