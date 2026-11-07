"""Handle self-inflicted (induced) load so it does not corrupt the labels.

The collector now generates traffic on purpose (speed tests, congestion ramps)
and even cuts the WAN on a schedule. Feeding all of that to the LSTM as if it
were organic network behaviour would teach the wrong thing, so this module
turns the induced experiments + speed-test windows into two products:

* **exclusion windows** - time ranges whose telemetry must be *dropped* before
  training, because it is not organic and carries no learnable precursor:
    - every speed-test burst (a ~18 s saturation once an hour),
    - every scheduled abrupt outage (``EXP-WANDOWN-*``): the down state itself,
    - the *saturated tail* of a congestion ramp, i.e. from its onset T_f to its
      end (the pre-T_f ramp-up is kept - that is the precursor to learn).

* **predictive failures** - the failure instants to label as positives:
  congestion ramps (gradual degradation, a real precursor) plus any organic
  failure event/experiment, but **never** an induced abrupt outage, and never a
  failure instant that falls inside an exclusion window (e.g. an SLA_FAILED
  event emitted *because* we cut the link on purpose).

Induced outages stay in the database as confirmed-failure records for other
analysis; they are only kept out of the *predictive* training set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ml.config import (
    CONGESTION_EXPERIMENT_PREFIX,
    FAILURE_EVENT_TYPES,
    INDUCED_OUTAGE_EXPERIMENT_PREFIX,
    INDUCED_OUTAGE_MAX_HOURS,
    INDUCED_OUTAGE_NOTE,
    RECOVERY_EXCLUSION_MINUTES,
    SPEEDTEST_PAD_SECONDS,
)
from ml.labeling import (
    FailureEvent,
    dedupe_failures,
    failures_from_events,
    failures_from_experiments,
)

Window = tuple[pd.Timestamp, pd.Timestamp]


@dataclass
class InducedPlan:
    # All windows whose rows are dropped from training.
    exclude_windows: list[Window] = field(default_factory=list)
    # Subset used to filter the failure list (outages + speed tests). Congestion
    # tails are NOT here: a ramp's own T_f sits on its tail edge and must survive.
    failure_filter_windows: list[Window] = field(default_factory=list)
    induced_outage_ids: set[str] = field(default_factory=set)
    n_speedtests: int = 0
    n_outages: int = 0
    n_congestion_tails: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "exclusion_windows": len(self.exclude_windows),
            "speedtests": self.n_speedtests,
            "induced_outages": self.n_outages,
            "congestion_tails": self.n_congestion_tails,
        }


def _is_induced_outage(experiment_id: object, notes: object) -> bool:
    return str(experiment_id).startswith(INDUCED_OUTAGE_EXPERIMENT_PREFIX) or (
        INDUCED_OUTAGE_NOTE in str(notes or "")
    )


def _is_congestion(experiment_id: object, scenario: object) -> bool:
    return str(experiment_id).startswith(CONGESTION_EXPERIMENT_PREFIX) or scenario == "CONGESTION"


def build_induced_plan(
    experiments_df: pd.DataFrame,
    speed_tests_df: pd.DataFrame | None,
    pad_seconds: int = SPEEDTEST_PAD_SECONDS,
    recovery_minutes: int = RECOVERY_EXCLUSION_MINUTES,
) -> InducedPlan:
    plan = InducedPlan()
    recovery = pd.Timedelta(minutes=recovery_minutes)

    for r in experiments_df.itertuples():
        start = getattr(r, "start_timestamp", pd.NaT)
        end = getattr(r, "end_timestamp", pd.NaT)
        tf = getattr(r, "failure_timestamp", pd.NaT)
        if _is_induced_outage(r.experiment_id, getattr(r, "notes", None)):
            plan.induced_outage_ids.add(r.experiment_id)
            if pd.notna(start):
                stop = end if pd.notna(end) else start + pd.Timedelta(hours=INDUCED_OUTAGE_MAX_HOURS)
                # Exclude the down period AND the recovery transient after it.
                plan.exclude_windows.append((start, stop + recovery))
                plan.failure_filter_windows.append((start, stop + recovery))
                plan.n_outages += 1
        elif _is_congestion(r.experiment_id, getattr(r, "scenario", None)):
            if pd.notna(tf) and pd.notna(end):
                # saturated tail + recovery; keep the pre-T_f ramp-up (the precursor)
                plan.exclude_windows.append((tf, end + recovery))
                plan.n_congestion_tails += 1
            elif pd.notna(start) and pd.notna(end):
                plan.exclude_windows.append((start, end + recovery))  # no onset: drop whole ramp
                plan.n_congestion_tails += 1

    if speed_tests_df is not None and not speed_tests_df.empty:
        pad = pd.Timedelta(seconds=pad_seconds)
        for r in speed_tests_df.itertuples():
            # A ramp's per-step speed test is the traffic that makes the shrunk
            # pipe visible - it is the precursor SIGNAL, handled by the congestion
            # experiment, not standalone noise. Only the hourly standalone tests
            # are excluded from the organic baseline (and never filter a failure,
            # or they would discard the ramp's own T_f that sits beside one).
            if getattr(r, "trigger", None) == "ramp":
                continue
            s = getattr(r, "started_at", pd.NaT)
            if pd.isna(s):
                continue
            e = getattr(r, "finished_at", pd.NaT)
            e = e if pd.notna(e) else s
            plan.exclude_windows.append((s - pad, e + pad))
            plan.failure_filter_windows.append((s - pad, e + pad))
            plan.n_speedtests += 1

    return plan


def drop_excluded_rows(df: pd.DataFrame, windows: list[Window]) -> pd.DataFrame:
    """Return ``df`` without rows whose timestamp falls inside any window."""
    if df.empty or not windows:
        return df
    ts = df["timestamp_utc"]
    mask = pd.Series(False, index=df.index)
    for start, end in windows:
        mask |= (ts >= start) & (ts <= end)
    return df.loc[~mask].reset_index(drop=True)


def filter_failures(failures: list[FailureEvent], windows: list[Window]) -> list[FailureEvent]:
    """Drop failure instants that fall inside an exclusion window."""
    if not windows:
        return failures
    return [
        f for f in failures
        if not any(start <= f.failure_timestamp <= end for start, end in windows)
    ]


def predictive_failures(
    experiments_df: pd.DataFrame,
    events_df: pd.DataFrame,
    manual: list[FailureEvent],
    plan: InducedPlan,
) -> list[FailureEvent]:
    """Failure instants to label as positives: congestion ramps + organic
    events/experiments + manual, minus induced outages and anything inside an
    exclusion window."""
    keep = experiments_df[~experiments_df["experiment_id"].isin(plan.induced_outage_ids)]
    failures = (
        failures_from_experiments(keep)
        + failures_from_events(events_df, FAILURE_EVENT_TYPES)
        + list(manual)
    )
    return dedupe_failures(filter_failures(failures, plan.failure_filter_windows))
