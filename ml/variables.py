"""The 23 input variables selected in Cap. 3.16.4, and why the rest are excluded.

Deliberately excluded (§3.16.4 / §3.16.5):
  - Cumulative counters (rx_bytes, tx_bytes, rx_packets, tx_packets, ...): use the
    derived rates instead - a counter's absolute value depends on device uptime,
    not on network condition.
  - Identification/administrative columns (device_hostname, device_model,
    serial_number, fortios_version, vdom, wan_interface, collector_version,
    schema_version): constant within one deployment, zero predictive variance.
  - Explicit-state fields that would leak the label (link_status, sla_status,
    availability, default_route_present): these ALREADY say the link is down,
    turning "predict a future failure" into "detect the current one". They are
    kept out of X and reserved for building ground truth / y instead.
  - Columns permanently NULL on this device (rx_drops, tx_drops, uptime_seconds,
    temperature_c, tcp_sessions, udp_sessions, vpn_*).
"""

from __future__ import annotations

ICMP_DESTINATIONS: tuple[str, ...] = ("cloudflare", "google", "quad9", "isp_router")

# Aggregate RAW variables (telemetry_raw), 11 of the 23.
AGGREGATE_VARIABLES: tuple[str, ...] = (
    "latency_ms",
    "jitter_ms",
    "packet_loss_pct",
    "rx_mbps",
    "tx_mbps",
    "rx_utilization_pct",
    "tx_utilization_pct",
    "cpu_pct",
    "memory_pct",
    "active_sessions",
    "new_sessions_per_second",
)

# Per-destination ICMP variables (probe_results, pivoted), 4 destinations x 3 = 12.
_PER_DEST_METRICS: tuple[str, ...] = ("latency_ms", "jitter_ms", "loss_pct")


def per_destination_columns() -> list[str]:
    return [f"{dest}_{metric}" for dest in ICMP_DESTINATIONS for metric in _PER_DEST_METRICS]


def input_columns() -> list[str]:
    """The 23 feature-matrix column names, in a fixed, reproducible order."""
    cols = [*AGGREGATE_VARIABLES, *per_destination_columns()]
    assert len(cols) == 23, f"expected 23 input variables, got {len(cols)}"
    return cols


# Columns kept alongside X for labeling / traceability / leakage-safe ground truth,
# never fed to the model as input.
LEAKAGE_RESERVED_COLUMNS: tuple[str, ...] = (
    "link_status",
    "sla_status",
    "availability",
    "default_route_present",
)

BOOKKEEPING_COLUMNS: tuple[str, ...] = (
    "timestamp_utc",
    "data_source",
    "collector_up",
    "experiment_id",
    "label",
    "failure_timestamp",
    "minutes_before_failure",
    "scenario_id",
)
