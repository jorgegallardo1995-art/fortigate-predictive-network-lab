"""Baseline models (§3.16.31) - the LSTM is only meaningful in comparison to these.

  * threshold_baseline: the "conventional" reactive rule (fires once a raw metric
    already crosses a bad value) - representative of what the dashboard alarms do
    today.
  * random_forest_baseline: a non-sequential ML baseline, fed the SAME windows
    flattened to summary statistics (mean/last per feature), so the comparison
    isolates the value of modelling the sequence, not just having more features.
"""

from __future__ import annotations

import numpy as np

from ml.sequences import SequenceSet


def threshold_baseline(
    X: np.ndarray,
    feature_columns: list[str],
    thresholds: dict[str, dict[str, float]],
) -> np.ndarray:
    """Predict 1 if the LAST step of the window already breaches a critical
    threshold on latency, jitter or packet loss (mirrors config.yaml's
    dashboard thresholds - visual-only there, used here only as a baseline
    comparator, never as ground truth)."""
    last_step = X[:, -1, :]
    pred = np.zeros(len(X), dtype=int)
    checks = (("latency_ms", "latency"), ("jitter_ms", "jitter"), ("packet_loss_pct", "packet_loss"))
    for var, crit_key in checks:
        if var not in feature_columns or crit_key not in thresholds:
            continue
        idx = feature_columns.index(var)
        crit = thresholds[crit_key].get("critical")
        if crit is None:
            continue
        pred |= (last_step[:, idx] >= crit).astype(int)
    return pred


def _flatten(X: np.ndarray) -> np.ndarray:
    """(N, T, F) -> (N, 2F): mean and last value per feature over the window."""
    mean = X.mean(axis=1)
    last = X[:, -1, :]
    return np.concatenate([mean, last], axis=1)


def random_forest_baseline(train: SequenceSet, test: SequenceSet, seed: int = 42):
    from sklearn.ensemble import RandomForestClassifier

    clf = RandomForestClassifier(
        n_estimators=200, max_depth=8, class_weight="balanced", random_state=seed, n_jobs=-1
    )
    clf.fit(_flatten(train.X), train.y)
    proba = clf.predict_proba(_flatten(test.X))[:, 1]
    return clf, proba
