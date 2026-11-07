"""Induced-load handling: self-inflicted traffic and scheduled outages must not
corrupt the labels or the organic baseline."""

from __future__ import annotations

import pandas as pd

from ml.induced import (
    build_induced_plan,
    drop_excluded_rows,
    filter_failures,
    predictive_failures,
)
from ml.labeling import FailureEvent


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _experiments() -> pd.DataFrame:
    rows = [
        # induced scheduled outage - excluded, never a label
        {"experiment_id": "EXP-WANDOWN-20260928T030000Z", "scenario": "WAN_FAILURE",
         "start_timestamp": _ts("2026-09-28 03:00:00"), "end_timestamp": _ts("2026-09-28 06:00:00"),
         "failure_timestamp": _ts("2026-09-28 03:00:00"), "notes": "trigger=scheduled | induced=WAN_DOWN"},
        # congestion ramp - precursor kept, saturated tail excluded
        {"experiment_id": "EXP-CONG-20260928T120000Z", "scenario": "CONGESTION",
         "start_timestamp": _ts("2026-09-28 12:00:00"), "end_timestamp": _ts("2026-09-28 12:10:00"),
         "failure_timestamp": _ts("2026-09-28 12:06:00"), "notes": "trigger=scheduled | method=shaper"},
        # organic controlled failure - kept as a label
        {"experiment_id": "EXP-WAN-001", "scenario": "WAN_FAILURE",
         "start_timestamp": _ts("2026-09-13 02:00:00"), "end_timestamp": _ts("2026-09-13 02:35:00"),
         "failure_timestamp": _ts("2026-09-13 02:05:00"), "notes": None},
    ]
    return pd.DataFrame(rows)


def _speed_tests() -> pd.DataFrame:
    return pd.DataFrame([
        {"started_at": _ts("2026-09-28 10:30:00"), "finished_at": _ts("2026-09-28 10:30:18")},
    ])


def test_plan_classifies_each_kind() -> None:
    plan = build_induced_plan(_experiments(), _speed_tests())
    assert plan.n_outages == 1
    assert plan.n_congestion_tails == 1
    assert plan.n_speedtests == 1
    assert plan.induced_outage_ids == {"EXP-WANDOWN-20260928T030000Z"}
    # outage window, congestion tail (T_f..end), speed test (padded) = 3 windows
    assert len(plan.exclude_windows) == 3


def test_drop_excluded_rows_removes_outage_and_tail_and_speedtest() -> None:
    plan = build_induced_plan(_experiments(), _speed_tests())
    ts = pd.date_range("2026-09-28 00:00:00", "2026-09-28 13:00:00", freq="1min", tz="UTC")
    df = pd.DataFrame({"timestamp_utc": ts})
    kept = drop_excluded_rows(df, plan.exclude_windows)

    k = kept["timestamp_utc"]
    assert not ((k >= _ts("2026-09-28 03:00:00")) & (k <= _ts("2026-09-28 06:00:00"))).any()  # outage
    assert not ((k >= _ts("2026-09-28 12:06:00")) & (k <= _ts("2026-09-28 12:10:00"))).any()  # tail
    assert (_ts("2026-09-28 10:30:00") not in set(k))  # speed test sample gone
    # pre-T_f ramp-up (12:00-12:05) is kept - that is the precursor to learn
    assert (_ts("2026-09-28 12:03:00") in set(k))


def test_predictive_failures_keeps_precursors_drops_induced_outage() -> None:
    plan = build_induced_plan(_experiments(), _speed_tests())
    events = pd.DataFrame(columns=["timestamp_utc", "event_type", "experiment_id"])
    fails = predictive_failures(_experiments(), events, [], plan)
    ids = {f.experiment_id for f in fails}
    assert "EXP-CONG-20260928T120000Z" in ids          # congestion precursor kept
    assert "EXP-WAN-001" in ids                         # organic failure kept
    assert "EXP-WANDOWN-20260928T030000Z" not in ids    # induced outage dropped


def test_sla_failed_event_during_induced_outage_is_dropped() -> None:
    plan = build_induced_plan(_experiments(), _speed_tests())
    # An SLA_FAILED emitted *because* we cut the link on purpose (03:01, inside the outage)
    events = pd.DataFrame([
        {"timestamp_utc": _ts("2026-09-28 03:01:00"), "event_type": "SLA_FAILED", "experiment_id": None},
    ])
    fails = predictive_failures(_experiments(), events, [], plan)
    assert all(f.failure_timestamp != _ts("2026-09-28 03:01:00") for f in fails)


def test_filter_failures_no_windows_is_identity() -> None:
    f = [FailureEvent(_ts("2026-09-28 12:00:00"))]
    assert filter_failures(f, []) == f


def test_missing_speed_tests_frame_is_safe() -> None:
    plan = build_induced_plan(_experiments(), pd.DataFrame(columns=["started_at", "finished_at"]))
    assert plan.n_speedtests == 0
    assert plan.n_outages == 1
