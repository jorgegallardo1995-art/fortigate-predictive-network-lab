"""Internal data-transfer objects for one acquisition cycle.

These are plain dataclasses passed between the telemetry source (real or mock),
the collector orchestrator and the persistence layer. They are deliberately
separate from the SQLAlchemy models and from the API response schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Raw counter fields that the delta stage uses to derive rates (bps, error rate...).
COUNTER_FIELDS = (
    "rx_bytes",
    "tx_bytes",
    "rx_packets",
    "tx_packets",
    "rx_errors",
    "tx_errors",
    "rx_drops",
    "tx_drops",
)

# Numeric metric fields exposed on charts / statistics.
CHART_METRICS = (
    "latency_ms",
    "jitter_ms",
    "packet_loss_pct",
    "rx_mbps",
    "tx_mbps",
    "cpu_pct",
    "memory_pct",
    "active_sessions",
)


@dataclass(slots=True)
class SlaReading:
    """One Performance SLA health-check result for one target."""

    health_check_name: str
    label: str
    target: str | None = None
    latency_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None
    sla_state: str | None = None
    probe_sessions: int | None = None


@dataclass(slots=True)
class SourceSnapshot:
    """Everything one telemetry source returns for a single cycle."""

    data_source: str
    success: bool = False
    api_latency_ms: float | None = None
    error: str | None = None
    missing: list[str] = field(default_factory=list)

    identification: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    sla: list[SlaReading] = field(default_factory=list)
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CycleContext:
    """Inputs handed to a source for a cycle."""

    scheduled_timestamp: datetime
    wan_interface: str
    wan_speed_down_mbps: float | None
    wan_speed_up_mbps: float | None
    sla_targets: dict[str, dict[str, str]]
