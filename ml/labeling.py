"""Ground-truth labeling (§3.16.6 / §3.16.7).

y_t = 1  if a failure occurs in (t, t + horizon_minutes]
y_t = 0  otherwise

Labels are NEVER derived from dashboard thresholds or inferred automatically
from telemetry values - only from an explicit, operator-confirmed failure
instant T_f, coming from either:

  * ``experiments.failure_timestamp`` (a controlled experiment, §3.16.8), or
  * a curated ``network_events`` row (WAN_DOWN / SLA_FAILED / INTERFACE_DOWN)
    that an operator has reviewed and confirmed is a real failure, or
  * a manually supplied timestamp (e.g. from operator memory of an incident
    that predates this tooling).

This module only ever *reads* failure instants that already exist; it does not
decide what counts as a failure.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.config import FAILURE_EVENT_TYPES


@dataclass(frozen=True)
class FailureEvent:
    failure_timestamp: pd.Timestamp
    experiment_id: str | None = None
    scenario_id: str | None = None
    source: str = "manual"  # "experiment" | "event" | "manual"


def failures_from_experiments(experiments_df: pd.DataFrame) -> list[FailureEvent]:
    if experiments_df.empty:
        return []
    rows = experiments_df.dropna(subset=["failure_timestamp"])
    return [
        FailureEvent(r.failure_timestamp, r.experiment_id, r.scenario, "experiment")
        for r in rows.itertuples()
    ]


def failures_from_events(
    events_df: pd.DataFrame, event_types: tuple[str, ...] = FAILURE_EVENT_TYPES
) -> list[FailureEvent]:
    if events_df.empty:
        return []
    rows = events_df[events_df["event_type"].isin(event_types)]
    return [
        FailureEvent(r.timestamp_utc, r.experiment_id, None, "event")
        for r in rows.itertuples()
    ]


def _as_utc_timestamp(ts: str | pd.Timestamp) -> pd.Timestamp:
    parsed = pd.Timestamp(ts)
    return parsed.tz_localize("UTC") if parsed.tz is None else parsed.tz_convert("UTC")


def failures_from_manual(timestamps: list[str | pd.Timestamp]) -> list[FailureEvent]:
    return [FailureEvent(_as_utc_timestamp(ts), source="manual") for ts in timestamps]


def dedupe_failures(failures: list[FailureEvent], min_gap_minutes: int = 5) -> list[FailureEvent]:
    """Collapse failure instants that fall within min_gap_minutes of each other
    (e.g. an experiment's failure_timestamp duplicating a network_event)."""
    if not failures:
        return []
    ordered = sorted(failures, key=lambda f: f.failure_timestamp)
    kept = [ordered[0]]
    for f in ordered[1:]:
        if (f.failure_timestamp - kept[-1].failure_timestamp) > pd.Timedelta(minutes=min_gap_minutes):
            kept.append(f)
    return kept


def apply_labels(
    df: pd.DataFrame,
    failures: list[FailureEvent],
    horizon_minutes: int,
    lookback_minutes: int | None = None,
) -> pd.DataFrame:
    """Return a copy of ``df`` with label / failure_timestamp / minutes_before_failure
    / experiment_id / scenario_id populated. All rows default to label=0."""
    out = df.copy()
    out["label"] = 0
    out["minutes_before_failure"] = np.nan
    out["failure_timestamp"] = pd.Series(
        pd.NaT, index=out.index, dtype=out["timestamp_utc"].dtype
    )
    lookback = pd.Timedelta(minutes=int(lookback_minutes or horizon_minutes * 4))

    for f in dedupe_failures(failures):
        window_start = f.failure_timestamp - lookback
        scope = (out["timestamp_utc"] >= window_start) & (out["timestamp_utc"] < f.failure_timestamp)
        if not scope.any():
            continue
        minutes_before = (f.failure_timestamp - out.loc[scope, "timestamp_utc"]).dt.total_seconds() / 60.0
        current = out.loc[scope, "minutes_before_failure"].fillna(np.inf)
        closer = minutes_before.to_numpy() < current.to_numpy()
        idx = out.loc[scope].index[closer]
        out.loc[idx, "minutes_before_failure"] = minutes_before.to_numpy()[closer]
        out.loc[idx, "failure_timestamp"] = f.failure_timestamp
        if f.experiment_id:
            out.loc[idx, "experiment_id"] = f.experiment_id
        if f.scenario_id:
            out.loc[idx, "scenario_id"] = f.scenario_id

    out["label"] = (
        out["minutes_before_failure"].notna() & (out["minutes_before_failure"] <= horizon_minutes)
    ).astype(int)
    return out


def labeling_summary(df: pd.DataFrame) -> dict[str, object]:
    return {
        "total_rows": len(df),
        "positive_rows": int(df["label"].sum()),
        "negative_rows": int((df["label"] == 0).sum()),
        "positive_pct": round(df["label"].mean() * 100, 3) if len(df) else 0.0,
        "distinct_failures": int(df["failure_timestamp"].nunique()),
    }
