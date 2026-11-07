"""Reviewer corrections in the ML pipeline: undefined metrics (0/0), the naive
calendar baseline, the paired McNemar test, bootstrap event-recall CIs, the
recovery-window exclusion and the walk-forward folds."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.baselines import minutes_since_last_failure, time_since_failure_baseline
from ml.crossval import build_walk_forward_folds
from ml.evaluate import (
    bootstrap_event_recall_ci,
    evaluate_predictions,
    mcnemar_test,
)
from ml.induced import build_induced_plan
from ml.sequences import SequenceSet


def _ts(s):
    return pd.Timestamp(s, tz="UTC")


# --- Bloque 3: undefined metrics ------------------------------------------
def test_undefined_metrics_are_none_not_zero() -> None:
    # No positives in truth AND model predicts all-negative: precision/recall/F1
    # are 0/0 = undefined, must be None (the V42 defect reported them as 0).
    y = np.zeros(10, dtype=int)
    proba = np.zeros(10)
    m = evaluate_predictions(y, proba, threshold=0.5, span_days=1.0)
    assert m.recall is None  # 0 real positives
    assert m.precision is None  # 0 predicted positives
    assert m.f1 is None
    assert m.false_alarms_per_day == 0.0


def test_defined_metrics_still_numeric() -> None:
    y = np.array([0, 0, 1, 1])
    proba = np.array([0.1, 0.4, 0.6, 0.9])
    m = evaluate_predictions(y, proba, threshold=0.5, span_days=2.0)
    assert m.precision == 1.0 and m.recall == 1.0 and m.f1 == 1.0


def test_false_alarms_use_real_span() -> None:
    y = np.zeros(4, dtype=int)
    proba = np.array([0.9, 0.9, 0.1, 0.1])  # 2 false positives
    m = evaluate_predictions(y, proba, threshold=0.5, span_days=4.0)
    assert m.false_alarms_per_day == 0.5  # 2 FP / 4 days, not extrapolated


# --- Bloque 3: paired test + bootstrap ------------------------------------
def test_mcnemar_detects_difference() -> None:
    y = np.array([1, 1, 1, 1, 0, 0])
    good = y.copy()  # perfect
    bad = 1 - y  # always wrong
    r = mcnemar_test(y, good, bad)
    assert r["b"] == 0 and r["c"] == 6 and r["p_value"] is not None


def test_bootstrap_event_recall_ci() -> None:
    ci = bootstrap_event_recall_ci([True] * 8 + [False] * 2, seed=1)
    assert ci["n_events"] == 10
    assert ci["point"] == 0.8
    assert 0.0 <= ci["ci_low"] <= 0.8 <= ci["ci_high"] <= 1.0


# --- Bloque 2: naive calendar baseline ------------------------------------
def test_minutes_since_last_failure() -> None:
    fails = [_ts("2026-11-07 10:00"), _ts("2026-11-07 12:00")]
    ends = np.array([_ts("2026-11-07 10:30"), _ts("2026-11-07 12:05"), _ts("2026-11-07 09:00")])
    out = minutes_since_last_failure(ends, fails)
    assert out[0] == 30.0  # 30 min after the 10:00 failure
    assert out[1] == 5.0   # 5 min after the 12:00 failure
    assert out[2] == 1e6   # none before 09:00


def test_naive_baseline_runs_and_predicts() -> None:
    n = 60
    base = _ts("2026-11-07 00:00")
    ends = np.array([base + pd.Timedelta(minutes=i) for i in range(n)])
    # failures every 20 min; label = within 5 min before a failure
    fails = [base + pd.Timedelta(minutes=m) for m in (20, 40)]
    y = np.array([1 if any(0 < (f - ends[i]).total_seconds() / 60 <= 5 for f in fails) else 0
                  for i in range(n)])
    X = np.zeros((n, 3, 2))
    tr = SequenceSet(X[:40], y[:40], ends[:40], np.array([None] * 40))
    te = SequenceSet(X[40:], y[40:], ends[40:], np.array([None] * 20))
    _, proba = time_since_failure_baseline(tr, te, fails)
    assert proba.shape == (20,)
    assert proba.min() >= 0 and proba.max() <= 1


# --- Bloque 2: recovery-window exclusion ----------------------------------
def test_recovery_window_extends_exclusion() -> None:
    exps = pd.DataFrame([{
        "experiment_id": "EXP-CONG-20261107T120000Z", "scenario": "CONGESTION",
        "start_timestamp": _ts("2026-11-07 12:00"), "end_timestamp": _ts("2026-11-07 12:10"),
        "failure_timestamp": _ts("2026-11-07 12:06"), "notes": "method=shaper",
    }])
    plan = build_induced_plan(exps, None, recovery_minutes=15)
    # tail window is (T_f, end + 15 min) = (12:06, 12:25)
    start, end = plan.exclude_windows[0]
    assert start == _ts("2026-11-07 12:06")
    assert end == _ts("2026-11-07 12:25")


# --- Bloque 1: walk-forward folds -----------------------------------------
def test_walk_forward_folds_have_margin_and_are_chronological() -> None:
    base = _ts("2026-11-01 00:00")
    n = 200
    ends = np.array([base + pd.Timedelta(hours=i) for i in range(n)])
    seqset = SequenceSet(
        X=np.zeros((n, 3, 2)), y=np.zeros(n, dtype=int),
        end_timestamps=ends, experiment_ids=np.array([None] * n),
    )
    folds = build_walk_forward_folds(seqset, n_folds=4, margin_minutes=30)
    assert len(folds) == 4
    for tr, te in folds:
        # every train sequence ends before every test sequence (walk-forward)
        assert ends[tr].max() < ends[te].min()
        # the margin gap is respected
        assert (ends[te].min() - ends[tr].max()) >= pd.Timedelta(minutes=30)
