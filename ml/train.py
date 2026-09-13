#!/usr/bin/env python3
"""End-to-end training CLI (§3.16.39 flow, top to bottom).

    python -m ml.train --db data/fortigate.db --data-source REAL
    python -m ml.train --synthetic-demo          # pipeline smoke-test, no real data

Never run on the Raspberry Pi - point --db at a LOCAL COPY of fortigate.db
(scp/rsync it from the collector first). Requires requirements-ml.txt.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ml.baselines import random_forest_baseline, threshold_baseline
from ml.config import DEFAULT_CONFIG, FAILURE_EVENT_TYPES
from ml.data_loading import load_dataset, load_experiments, load_network_events
from ml.evaluate import evaluate_predictions, lead_times, select_threshold, summarise_lead_times
from ml.labeling import (
    apply_labels,
    dedupe_failures,
    failures_from_events,
    failures_from_experiments,
    failures_from_manual,
    labeling_summary,
)
from ml.preprocessing import SequenceScaler, temporal_split
from ml.segments import continuous_segments, segment_stats
from ml.sequences import build_sequences
from ml.synthetic import make_synthetic_dataset
from ml.variables import input_columns

DEFAULT_THRESHOLDS = {
    "latency": {"warning": 80, "critical": 150},
    "jitter": {"warning": 20, "critical": 50},
    "packet_loss": {"warning": 1, "critical": 5},
}


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
            timeout=5, check=False, cwd=Path(__file__).resolve().parents[1],
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _load_manual_failures(path: str | None) -> list:
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    return failures_from_manual(data["failure_timestamps"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", help="path to a LOCAL COPY of fortigate.db")
    p.add_argument("--data-source", default="REAL", choices=["REAL", "MOCK"])
    p.add_argument("--failures-json", help='JSON file: {"failure_timestamps": ["2026-...Z", ...]}')
    p.add_argument("--output-dir", default="data/models")
    p.add_argument("--synthetic-demo", action="store_true",
                    help="pipeline smoke-test with injected synthetic failures - NOT thesis data")
    p.add_argument("--synthetic-minutes", type=int, default=20_000)
    p.add_argument("--max-epochs", type=int, default=None, help="override config for quick runs/tests")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = DEFAULT_CONFIG
    if args.max_epochs is not None:
        from dataclasses import replace

        cfg = replace(cfg, max_epochs=args.max_epochs)

    if not args.synthetic_demo and not args.db:
        print("ERROR: pass --db <path to a local copy of fortigate.db>, or --synthetic-demo.")
        return 2

    synthetic = bool(args.synthetic_demo)
    if synthetic:
        print("=" * 78)
        print("SYNTHETIC DEMO MODE - injected fake failures. This validates the")
        print("pipeline mechanics ONLY. These are NOT real results and must never be")
        print("cited in the thesis. Real training requires labeled real data.")
        print("=" * 78)
        df = make_synthetic_dataset(n_minutes=args.synthetic_minutes, seed=cfg.seed)
        failures = failures_from_manual(df.attrs["synthetic_failure_timestamps"])
        data_source = "SYNTHETIC"
    else:
        df = load_dataset(args.db, args.data_source)
        experiments_df = load_experiments(args.db)
        events_df = load_network_events(args.db, FAILURE_EVENT_TYPES)
        failures = dedupe_failures(
            failures_from_experiments(experiments_df)
            + failures_from_events(events_df)
            + _load_manual_failures(args.failures_json)
        )
        data_source = args.data_source

    print(f"loaded {len(df):,} rows ({data_source}), {len(failures)} candidate failure instant(s)")

    labeled = apply_labels(df, failures, cfg.horizon_minutes)
    lsum = labeling_summary(labeled)
    print(f"labeling: {lsum}")

    if lsum["positive_rows"] == 0 and not synthetic:
        print()
        print("ABORTED: zero positive (failure) examples in this dataset.")
        print("The LSTM cannot learn a precursor pattern from zero examples of the")
        print("thing it must predict. To get real labels:")
        print("  1. Run a controlled experiment (scenario WAN_FAILURE/PACKET_LOSS/...)")
        print("     via POST /api/experiments, then POST /api/experiments/<id>/failure")
        print("     with the failure instant, while the collector keeps recording.")
        print("  2. Or confirm a real WAN_DOWN/SLA_FAILED network_event as genuine and")
        print("     re-run (it is picked up automatically from network_events).")
        print("  3. Or pass --failures-json with operator-confirmed timestamps that")
        print("     have clean telemetry in the 60 minutes beforehand.")
        print("Run with --synthetic-demo to validate the pipeline mechanics meanwhile.")
        return 1

    segments = continuous_segments(labeled, cfg.gap_tolerance_seconds)
    print(f"segments: {segment_stats(segments)}")

    feature_cols = input_columns()
    seqset = build_sequences(segments, feature_cols, cfg.window_steps)
    print(f"sequences built: {len(seqset)} (positives: {int(seqset.y.sum())})")
    if len(seqset) < 50:
        print("ABORTED: fewer than 50 usable sequences - not enough continuous, "
              "gap-free telemetry to train on yet.")
        return 1

    split = temporal_split(seqset, cfg.train_frac, cfg.val_frac)
    for name, s in (("train", split.train), ("val", split.val), ("test", split.test)):
        print(f"  {name}: n={len(s)} positives={int(s.y.sum())} "
              f"({s.y.mean()*100:.2f}%)" if len(s) else f"  {name}: n=0")

    if split.train.y.sum() == 0 or (len(split.val) and split.val.y.sum() == 0):
        print("WARNING: a split has zero positives - metrics on it will be degenerate. "
              "You need more distinct failure events spread across the timeline.")

    scaler = SequenceScaler().fit(split.train.X)
    X_train = scaler.transform(split.train.X)
    X_val = scaler.transform(split.val.X) if len(split.val) else split.val.X
    X_test = scaler.transform(split.test.X) if len(split.test) else split.test.X

    from sklearn.utils.class_weight import compute_class_weight

    classes = np.unique(split.train.y)
    weights = compute_class_weight("balanced", classes=classes, y=split.train.y)
    class_weight = dict(zip(classes.tolist(), weights.tolist(), strict=True))
    print(f"class_weight (TRAIN only): {class_weight}")

    import tensorflow as tf

    from ml.model import build_model

    model = build_model(cfg)
    n_params = int(np.sum([np.prod(v.shape) for v in model.trainable_variables]))
    print(f"model params: {n_params:,}")

    early_stop = tf.keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=cfg.early_stopping_patience, restore_best_weights=True
    )
    history = model.fit(
        X_train, split.train.y,
        validation_data=(X_val, split.val.y) if len(split.val) else None,
        epochs=cfg.max_epochs, batch_size=cfg.batch_size,
        class_weight=class_weight, callbacks=[early_stop], verbose=2,
    )

    proba_val = model.predict(X_val, verbose=0).ravel() if len(split.val) else np.array([])
    threshold = select_threshold(split.val.y, proba_val, cfg.candidate_thresholds) if len(split.val) else 0.5
    proba_test = model.predict(X_test, verbose=0).ravel() if len(split.test) else np.array([])

    span_days = None
    if len(split.test) > 1:
        span_days = max(
            1.0,
            (pd.Timestamp(split.test.end_timestamps.max()) - pd.Timestamp(split.test.end_timestamps.min()))
            .total_seconds() / 86400,
        )
    test_metrics = (
        evaluate_predictions(split.test.y, proba_test, threshold, span_days) if len(split.test) else None
    )
    lt = (
        lead_times(split.test.end_timestamps, split.test.y, proba_test, threshold)
        if len(split.test) else []
    )
    lt_summary = summarise_lead_times(lt)

    baseline_results = {}
    if len(split.test):
        thr_pred = threshold_baseline(split.test.X, feature_cols, DEFAULT_THRESHOLDS)
        baseline_results["threshold_rule"] = evaluate_predictions(
            split.test.y, thr_pred.astype(float), 0.5, span_days
        ).to_dict()
        if len(split.train) and split.train.y.sum() > 0:
            _, rf_proba = random_forest_baseline(split.train, split.test, seed=cfg.seed)
            baseline_results["random_forest"] = evaluate_predictions(
                split.test.y, rf_proba, threshold, span_days
            ).to_dict()

    run_id = ("demo_" if synthetic else "run_") + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    model.save(out_dir / "model.keras")
    (out_dir / "scaler.json").write_text(json.dumps(scaler.to_dict()))
    history_payload = {k: [float(x) for x in v] for k, v in history.history.items()}
    (out_dir / "history.json").write_text(json.dumps(history_payload))

    metadata = {
        "run_id": run_id,
        "synthetic": synthetic,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "python_version": platform.python_version(),
        "tensorflow_version": tf.__version__,
        "platform": f"{platform.system()} {platform.machine()}",
        "data_source": data_source,
        "config": asdict(cfg),
        "feature_columns": feature_cols,
        "dataset_period": {
            "start": df["timestamp_utc"].min().isoformat() if len(df) else None,
            "end": df["timestamp_utc"].max().isoformat() if len(df) else None,
            "total_rows": len(df),
        },
        "labeling": lsum,
        "failure_instants": [f.failure_timestamp.isoformat() for f in failures],
        "segments": segment_stats(segments),
        "sequences": {
            "total": len(seqset),
            "train": len(split.train), "val": len(split.val), "test": len(split.test),
            "train_positives": int(split.train.y.sum()),
            "val_positives": int(split.val.y.sum()) if len(split.val) else 0,
            "test_positives": int(split.test.y.sum()) if len(split.test) else 0,
        },
        "split_cutoffs": {
            "train_end": str(split.train_cutoff) if split.train_cutoff is not None else None,
            "val_end": str(split.val_cutoff) if split.val_cutoff is not None else None,
        },
        "class_weight": class_weight,
        "model_params": n_params,
        "selected_threshold": threshold,
    }
    (out_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    metrics_payload = {
        "lstm_test": test_metrics.to_dict() if test_metrics else None,
        "lead_time": lt_summary,
        "baselines": baseline_results,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics_payload, indent=2, default=str))

    print()
    print(f"saved run to {out_dir}")
    if test_metrics:
        print(f"TEST metrics @ threshold={threshold}: {test_metrics.to_dict()}")
    print(f"lead time: {lt_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
