"""Synthetic data for PIPELINE VALIDATION ONLY.

Generates a plausible-shaped dataset with a few injected "failures" so the
train/evaluate code path can be exercised end to end before any real ground
truth exists. Every artefact produced from this data is tagged
``synthetic: true`` and must never be cited as a thesis result - see
ml/train.py's --synthetic-demo flag and the loud warnings it prints.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.variables import ICMP_DESTINATIONS, input_columns


def make_synthetic_dataset(
    n_minutes: int = 20_000,
    n_failures: int = 6,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    timestamps = start + pd.to_timedelta(np.arange(n_minutes), unit="m")

    df = pd.DataFrame({"timestamp_utc": timestamps})
    df["data_source"] = "SYNTHETIC"
    df["experiment_id"] = None
    df["scenario_id"] = None

    base = {
        "latency_ms": 8.0, "jitter_ms": 1.0, "packet_loss_pct": 0.0,
        "rx_mbps": 20.0, "tx_mbps": 5.0, "rx_utilization_pct": 10.0, "tx_utilization_pct": 3.0,
        "cpu_pct": 15.0, "memory_pct": 30.0, "active_sessions": 100.0, "new_sessions_per_second": 3.0,
    }
    for col, mean in base.items():
        df[col] = mean + rng.normal(0, mean * 0.05 + 0.1, n_minutes)

    for dest in ICMP_DESTINATIONS:
        df[f"{dest}_latency_ms"] = df["latency_ms"] + rng.normal(0, 0.5, n_minutes)
        df[f"{dest}_jitter_ms"] = np.abs(df["jitter_ms"] + rng.normal(0, 0.2, n_minutes))
        df[f"{dest}_loss_pct"] = np.clip(df["packet_loss_pct"] + rng.normal(0, 0.1, n_minutes), 0, 100)

    df["link_status"] = "up"
    df["sla_status"] = "up"
    df["availability"] = 100.0
    df["default_route_present"] = True

    margin = max(50, min(2000, n_minutes // 5))
    failure_starts = rng.choice(
        np.arange(margin, n_minutes - margin), size=n_failures, replace=False
    )
    failure_timestamps = []
    for f_idx in sorted(failure_starts):
        ramp = 30  # minutes of degradation ramping up before the failure instant
        f_end = f_idx + ramp
        failure_timestamps.append(timestamps[f_end])
        ramp_factor = np.linspace(0, 1, ramp)
        df.loc[f_idx:f_end - 1, "latency_ms"] += ramp_factor * 180
        df.loc[f_idx:f_end - 1, "packet_loss_pct"] += ramp_factor * 40
        df.loc[f_idx:f_end - 1, "jitter_ms"] += ramp_factor * 25
        for dest in ICMP_DESTINATIONS:
            df.loc[f_idx:f_end - 1, f"{dest}_latency_ms"] += ramp_factor * 180
            df.loc[f_idx:f_end - 1, f"{dest}_loss_pct"] += ramp_factor * 40
        df.loc[f_end, "link_status"] = "down"
        df.loc[f_end, "sla_status"] = "down"
        df.loc[f_end, "availability"] = 0.0

    assert set(input_columns()).issubset(df.columns)
    df.attrs["synthetic_failure_timestamps"] = failure_timestamps
    return df
