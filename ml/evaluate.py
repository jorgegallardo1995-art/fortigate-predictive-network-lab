"""Evaluation metrics (§3.16.32 / §3.16.33 / §3.16.34).

Never reports accuracy alone - a rare-failure dataset makes accuracy trivially
high for a useless always-NORMAL classifier. Confusion matrix, PR-AUC, false
alarms/day and Lead Time are the metrics that actually matter here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ClassificationMetrics:
    threshold: float
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float | None
    pr_auc: float | None
    tn: int
    fp: int
    fn: int
    tp: int
    false_alarms_per_day: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "roc_auc": self.roc_auc,
            "pr_auc": self.pr_auc,
            "confusion_matrix": {"tn": self.tn, "fp": self.fp, "fn": self.fn, "tp": self.tp},
            "false_alarms_per_day": self.false_alarms_per_day,
        }


def evaluate_predictions(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    threshold: float,
    span_days: float | None = None,
) -> ClassificationMetrics:
    from sklearn.metrics import (
        average_precision_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y_pred = (y_proba >= threshold).astype(int)
    labels_present = len(set(y_true.tolist())) > 1

    tn, fp, fn, tp = (0, 0, 0, 0)
    if len(y_true):
        cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
        tn, fp, fn, tp = cm.ravel()

    false_alarms_per_day = (fp / span_days) if span_days else None

    return ClassificationMetrics(
        threshold=threshold,
        accuracy=float((y_pred == y_true).mean()) if len(y_true) else float("nan"),
        precision=float(precision_score(y_true, y_pred, zero_division=0)),
        recall=float(recall_score(y_true, y_pred, zero_division=0)),
        f1=float(f1_score(y_true, y_pred, zero_division=0)),
        roc_auc=float(roc_auc_score(y_true, y_proba)) if labels_present else None,
        pr_auc=float(average_precision_score(y_true, y_proba)) if labels_present else None,
        tn=int(tn), fp=int(fp), fn=int(fn), tp=int(tp),
        false_alarms_per_day=false_alarms_per_day,
    )


def select_threshold(y_val: np.ndarray, proba_val: np.ndarray, candidates: tuple[float, ...]) -> float:
    """Pick the threshold maximising F1 on VALIDATION ONLY (§3.16.34) - TEST is
    never touched during this search."""
    best_t, best_f1 = candidates[0], -1.0
    for t in candidates:
        m = evaluate_predictions(y_val, proba_val, t)
        if m.f1 > best_f1:
            best_t, best_f1 = t, m.f1
    return best_t


@dataclass
class LeadTimeResult:
    failure_timestamp: object
    alert_timestamp: object | None
    lead_time_minutes: float | None


def lead_times(
    end_timestamps: np.ndarray,
    y_true: np.ndarray,
    y_proba: np.ndarray,
    threshold: float,
) -> list[LeadTimeResult]:
    """For each contiguous run of true positives (one real failure's horizon
    window), find the first timestamp where the model's probability crosses the
    threshold, and report T_f - T_alert (§3.16.33)."""
    results: list[LeadTimeResult] = []
    in_run = False
    run_start = None
    run_end_idx = None
    for i, y in enumerate(y_true):
        if y == 1 and not in_run:
            in_run, run_start = True, i
        if y == 0 and in_run:
            run_end_idx = i - 1
            results.append(_lead_time_for_run(end_timestamps, y_proba, threshold, run_start, run_end_idx))
            in_run = False
    if in_run:
        results.append(_lead_time_for_run(end_timestamps, y_proba, threshold, run_start, len(y_true) - 1))
    return results


def _lead_time_for_run(end_timestamps, y_proba, threshold, start, end) -> LeadTimeResult:
    failure_ts = end_timestamps[end]  # the run's last positive step is closest to T_f
    for i in range(start, end + 1):
        if y_proba[i] >= threshold:
            import pandas as pd

            alert_ts = end_timestamps[i]
            lead_minutes = (pd.Timestamp(failure_ts) - pd.Timestamp(alert_ts)).total_seconds() / 60.0
            return LeadTimeResult(failure_ts, alert_ts, lead_minutes)
    return LeadTimeResult(failure_ts, None, None)


def summarise_lead_times(results: list[LeadTimeResult]) -> dict[str, object]:
    valid = [r.lead_time_minutes for r in results if r.lead_time_minutes is not None]
    return {
        "events_evaluated": len(results),
        "events_detected": len(valid),
        "events_missed": len(results) - len(valid),
        "mean_lead_time_minutes": round(float(np.mean(valid)), 2) if valid else None,
        "min_lead_time_minutes": round(float(np.min(valid)), 2) if valid else None,
        "max_lead_time_minutes": round(float(np.max(valid)), 2) if valid else None,
    }
