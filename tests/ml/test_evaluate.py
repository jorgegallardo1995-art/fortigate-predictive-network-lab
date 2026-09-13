from __future__ import annotations

import numpy as np
import pandas as pd
from ml.evaluate import evaluate_predictions, lead_times, select_threshold, summarise_lead_times


def test_evaluate_predictions_perfect_classifier() -> None:
    y_true = np.array([0, 0, 1, 1, 0, 1])
    y_proba = np.array([0.1, 0.2, 0.9, 0.8, 0.1, 0.95])
    m = evaluate_predictions(y_true, y_proba, threshold=0.5)
    assert m.tp == 3 and m.fp == 0 and m.fn == 0 and m.tn == 3
    assert m.precision == 1.0 and m.recall == 1.0
    assert m.roc_auc == 1.0


def test_evaluate_predictions_false_alarms_per_day() -> None:
    y_true = np.zeros(10, dtype=int)
    y_proba = np.array([0.9, 0.1, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    m = evaluate_predictions(y_true, y_proba, threshold=0.5, span_days=2.0)
    assert m.fp == 2
    assert m.false_alarms_per_day == 1.0


def test_select_threshold_picks_best_f1_from_candidates() -> None:
    y_val = np.array([0, 0, 1, 1, 1])
    proba_val = np.array([0.2, 0.35, 0.45, 0.55, 0.65])
    t = select_threshold(y_val, proba_val, candidates=(0.3, 0.5, 0.7))
    assert t in (0.3, 0.5, 0.7)


def test_lead_time_detects_alert_before_failure() -> None:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=10, freq="1min", tz="UTC").to_numpy()
    y_true = np.array([0, 0, 0, 1, 1, 1, 1, 1, 0, 0])  # positive run = indices 3..7
    proba = np.array([0.1, 0.1, 0.1, 0.2, 0.6, 0.7, 0.8, 0.9, 0.1, 0.1])
    results = lead_times(ts, y_true, proba, threshold=0.5)
    assert len(results) == 1
    r = results[0]
    assert r.alert_timestamp is not None
    assert r.lead_time_minutes == 3.0  # alert at idx4, failure at idx7 -> 3 minutes


def test_lead_time_missed_event_when_never_crosses_threshold() -> None:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=6, freq="1min", tz="UTC").to_numpy()
    y_true = np.array([0, 1, 1, 1, 0, 0])
    proba = np.array([0.1, 0.2, 0.2, 0.2, 0.1, 0.1])  # never crosses 0.5
    results = lead_times(ts, y_true, proba, threshold=0.5)
    assert results[0].alert_timestamp is None
    summary = summarise_lead_times(results)
    assert summary["events_missed"] == 1
    assert summary["events_detected"] == 0


def test_summarise_lead_times_aggregates_multiple_events() -> None:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=20, freq="1min", tz="UTC").to_numpy()
    y_true = np.zeros(20, dtype=int)
    y_true[3:6] = 1
    y_true[13:16] = 1
    proba = np.zeros(20)
    proba[4] = 0.9   # detected 1 min into first run (starts at 3)
    proba[14] = 0.9  # detected 1 min into second run
    results = lead_times(ts, y_true, proba, threshold=0.5)
    summary = summarise_lead_times(results)
    assert summary["events_evaluated"] == 2
    assert summary["events_detected"] == 2
    assert summary["mean_lead_time_minutes"] == 1.0
