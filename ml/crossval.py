#!/usr/bin/env python3
"""Walk-forward (expanding-window) evaluation by event - the thesis-grade
contrast the reviewer asked for (Bloque 1 & 3).

Instead of a single 70/15/15 split (which leaves one fold of test positives and
makes the threshold a lottery), this runs K chronological folds: fold k trains
on everything up to time t_k and tests on the next time block, with an
EXCLUSION MARGIN of (window + horizon) minutes between train and test so no
sequence straddles the boundary. For every fold it evaluates the LSTM against
three baselines on the SAME test sequences - the threshold rule, Random Forest,
and the NAIVE "time since last failure" calendar baseline - and reports
per-event recall with a bootstrap CI, false alarms per day over the real test
span, Lead Time, and a paired McNemar test LSTM-vs-baseline. Results are given
per fold and pooled.

    python -m ml.crossval --db data/fortigate.db --folds 5

Never run on the Raspberry Pi - point --db at a LOCAL COPY of fortigate.db.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ml.baselines import random_forest_baseline, threshold_baseline, time_since_failure_baseline
from ml.config import DEFAULT_CONFIG, FAILURE_EVENT_TYPES
from ml.data_loading import (
    load_dataset,
    load_experiments,
    load_network_events,
    load_speed_tests,
)
from ml.evaluate import (
    bootstrap_event_recall_ci,
    evaluate_predictions,
    lead_times,
    mcnemar_test,
    select_threshold,
    summarise_lead_times,
)
from ml.induced import build_induced_plan, drop_excluded_rows, predictive_failures
from ml.labeling import apply_labels, labeling_summary
from ml.preprocessing import SequenceScaler
from ml.segments import continuous_segments
from ml.sequences import SequenceSet, build_sequences
from ml.variables import input_columns

DEFAULT_THRESHOLDS = {
    "latency": {"warning": 80, "critical": 150},
    "jitter": {"warning": 20, "critical": 50},
    "packet_loss": {"warning": 1, "critical": 5},
}


def build_walk_forward_folds(
    seqset: SequenceSet, n_folds: int, margin_minutes: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds by TIME with an exclusion margin between train and
    test. Returns (train_idx, test_idx) per fold (indices into seqset)."""
    order = np.argsort(seqset.end_timestamps)
    ts = pd.to_datetime(seqset.end_timestamps[order])
    t0, t1 = ts.min(), ts.max()
    margin = pd.Timedelta(minutes=margin_minutes)
    edges = [t0 + (t1 - t0) * (i + 1) / (n_folds + 1) for i in range(n_folds + 1)]

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(n_folds):
        train_end = edges[i]
        test_lo, test_hi = edges[i] + margin, edges[i + 1]
        train_mask = ts <= train_end
        test_mask = (ts > test_lo) & (ts <= test_hi)
        tr = order[np.asarray(train_mask)]
        te = order[np.asarray(test_mask)]
        if len(tr) and len(te):
            folds.append((tr, te))
    return folds


def _subset(seqset: SequenceSet, idx: np.ndarray) -> SequenceSet:
    return SequenceSet(
        X=seqset.X[idx], y=seqset.y[idx],
        end_timestamps=seqset.end_timestamps[idx], experiment_ids=seqset.experiment_ids[idx],
    )


def _train_lstm_proba(cfg, train: SequenceSet, val: SequenceSet, test: SequenceSet) -> np.ndarray:
    import tensorflow as tf
    from sklearn.utils.class_weight import compute_class_weight

    from ml.model import build_model

    scaler = SequenceScaler().fit(train.X)
    x_tr, x_va, x_te = scaler.transform(train.X), scaler.transform(val.X), scaler.transform(test.X)
    classes = np.unique(train.y)
    if len(classes) < 2:
        return np.full(len(test.y), float(train.y.mean()))
    weights = compute_class_weight("balanced", classes=classes, y=train.y)
    cw = dict(zip(classes.tolist(), weights.tolist(), strict=True))
    model = build_model(cfg)
    es = tf.keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=cfg.early_stopping_patience, restore_best_weights=True
    )
    model.fit(
        x_tr, train.y, validation_data=(x_va, val.y) if len(val) else None,
        epochs=cfg.max_epochs, batch_size=cfg.batch_size, class_weight=cw,
        callbacks=[es], verbose=0,
    )
    return model.predict(x_te, verbose=0).ravel()


def _event_detected(test: SequenceSet, proba: np.ndarray, threshold: float) -> list[bool]:
    lts = lead_times(test.end_timestamps, test.y, proba, threshold)
    return [r.lead_time_minutes is not None for r in lts]


def _test_span_days(test: SequenceSet) -> float:
    if len(test) < 2:
        return 1.0
    ts = pd.to_datetime(test.end_timestamps)
    return max(1.0, (ts.max() - ts.min()).total_seconds() / 86400)


def evaluate_fold(cfg, seqset, tr_idx, te_idx, failures, feature_cols) -> dict:
    full_tr = _subset(seqset, tr_idx)
    test = _subset(seqset, te_idx)
    # Carve a validation tail (last 20%) out of train to pick the threshold on
    # data WITH positives, never on test.
    order = np.argsort(full_tr.end_timestamps)
    cut = int(len(order) * 0.8)
    train, val = _subset(full_tr, order[:cut]), _subset(full_tr, order[cut:])
    span = _test_span_days(test)

    proba = _train_lstm_proba(cfg, train, val, test)
    val_proba = _train_lstm_proba(cfg, train, val, val) if val.y.sum() > 0 else np.array([])
    threshold = (
        select_threshold(val.y, val_proba, cfg.candidate_thresholds)
        if len(val_proba) and val.y.sum() > 0 else 0.5
    )

    lstm_pred = (proba >= threshold).astype(int)
    out = {
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "test_positives": int(test.y.sum()),
        "threshold": threshold,
        "lstm": evaluate_predictions(test.y, proba, threshold, span).to_dict(),
        "lstm_event_recall": bootstrap_event_recall_ci(_event_detected(test, proba, threshold)),
        "lstm_lead_time": summarise_lead_times(lead_times(test.end_timestamps, test.y, proba, threshold)),
        "baselines": {},
        "paired_mcnemar_vs_lstm": {},
    }
    # Threshold rule
    thr_pred = threshold_baseline(test.X, feature_cols, DEFAULT_THRESHOLDS)
    out["baselines"]["threshold_rule"] = evaluate_predictions(
        test.y, thr_pred.astype(float), 0.5, span).to_dict()
    out["paired_mcnemar_vs_lstm"]["threshold_rule"] = mcnemar_test(test.y, lstm_pred, thr_pred)
    # Random forest
    if train.y.sum() > 0:
        _, rf_proba = random_forest_baseline(train, test, seed=cfg.seed)
        out["baselines"]["random_forest"] = evaluate_predictions(test.y, rf_proba, threshold, span).to_dict()
        out["paired_mcnemar_vs_lstm"]["random_forest"] = mcnemar_test(
            test.y, lstm_pred, (rf_proba >= threshold).astype(int))
    # Naive "time since last failure" calendar baseline
    _, naive_proba = time_since_failure_baseline(train, test, failures, seed=cfg.seed)
    out["baselines"]["naive_time_since_failure"] = evaluate_predictions(
        test.y, naive_proba, threshold, span).to_dict()
    out["baselines"]["naive_time_since_failure"]["event_recall"] = bootstrap_event_recall_ci(
        _event_detected(test, naive_proba, threshold))
    out["paired_mcnemar_vs_lstm"]["naive_time_since_failure"] = mcnemar_test(
        test.y, lstm_pred, (naive_proba >= threshold).astype(int))
    return out


def _pool(folds: list[dict], path: list[str]) -> dict:
    """Mean of a numeric metric across folds (ignoring None)."""
    vals = []
    for f in folds:
        cur = f
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if isinstance(cur, (int, float)):
            vals.append(cur)
    return {"mean": round(float(np.mean(vals)), 4) if vals else None, "n_folds": len(vals)}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, help="path to a LOCAL COPY of fortigate.db")
    p.add_argument("--data-source", default="REAL", choices=["REAL", "MOCK"])
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--margin-minutes", type=int, default=None,
                   help="exclusion margin between train and test (default: window+horizon)")
    p.add_argument("--max-epochs", type=int, default=None)
    p.add_argument("--variant", choices=["reference", "bidirectional"], default=None,
                   help="LSTM architecture variant (default: config's 'reference')")
    p.add_argument("--output", default=None, help="write the JSON report here")
    p.add_argument("--since", default=None,
                   help="ISO date; restrict to telemetry at/after it (e.g. 2026-11-07 "
                        "for the randomised-schedule regime only, excluding the fixed-calendar history)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = DEFAULT_CONFIG
    from dataclasses import replace
    if args.max_epochs is not None:
        cfg = replace(cfg, max_epochs=args.max_epochs)
    if args.variant is not None:
        cfg = replace(cfg, variant=args.variant)
    margin = args.margin_minutes if args.margin_minutes is not None else (
        cfg.window_minutes + cfg.horizon_minutes)

    df = load_dataset(args.db, args.data_source, since=args.since)
    if args.since:
        print(f"SINCE MODE: only telemetry at/after {args.since} (randomised regime).")
    experiments_df = load_experiments(args.db)
    events_df = load_network_events(args.db, FAILURE_EVENT_TYPES)
    speed_tests_df = load_speed_tests(args.db, args.data_source)
    plan = build_induced_plan(experiments_df, speed_tests_df)
    df = drop_excluded_rows(df, plan.exclude_windows)
    failures = predictive_failures(experiments_df, events_df, [], plan)
    failure_instants = [f.failure_timestamp for f in failures]

    labeled = apply_labels(df, failures, cfg.horizon_minutes)
    print(f"labeling: {labeling_summary(labeled)}")
    segments = continuous_segments(labeled, cfg.gap_tolerance_seconds)
    feature_cols = input_columns()
    seqset = build_sequences(segments, feature_cols, cfg.window_steps)
    print(f"sequences: {len(seqset)} (positives {int(seqset.y.sum())})")

    folds = build_walk_forward_folds(seqset, args.folds, margin)
    print(f"walk-forward folds: {len(folds)} (margin={margin} min)")
    if not folds:
        print("ABORTED: no usable folds (need more data spread over time).")
        return 1

    fold_results = []
    for i, (tr, te) in enumerate(folds, 1):
        print(f"  fold {i}/{len(folds)}: train={len(tr)} test={len(te)} ...", flush=True)
        fold_results.append(evaluate_fold(cfg, seqset, tr, te, failure_instants, feature_cols))

    pooled = {
        "lstm_pr_auc": _pool(fold_results, ["lstm", "pr_auc"]),
        "lstm_event_recall": _pool(fold_results, ["lstm_event_recall", "point"]),
        "lstm_false_alarms_per_day": _pool(fold_results, ["lstm", "false_alarms_per_day"]),
        "naive_pr_auc": _pool(fold_results, ["baselines", "naive_time_since_failure", "pr_auc"]),
        "random_forest_pr_auc": _pool(fold_results, ["baselines", "random_forest", "pr_auc"]),
        "mean_lead_time_minutes": _pool(fold_results, ["lstm_lead_time", "mean_lead_time_minutes"]),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {"folds": len(folds), "margin_minutes": margin, "variant": cfg.variant,
                   "window_minutes": cfg.window_minutes, "horizon_minutes": cfg.horizon_minutes},
        "pooled": pooled,
        "folds": fold_results,
    }
    out_path = Path(args.output) if args.output else Path(
        f"data/models/crossval_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nsaved cross-validation report to {out_path}")
    print(f"POOLED: LSTM PR-AUC={pooled['lstm_pr_auc']['mean']} | "
          f"naive PR-AUC={pooled['naive_pr_auc']['mean']} | "
          f"RF PR-AUC={pooled['random_forest_pr_auc']['mean']} | "
          f"LSTM event-recall={pooled['lstm_event_recall']['mean']} | "
          f"lead={pooled['mean_lead_time_minutes']['mean']} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
