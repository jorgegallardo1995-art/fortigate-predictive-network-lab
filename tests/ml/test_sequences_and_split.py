from __future__ import annotations

import numpy as np
import pandas as pd
from ml.preprocessing import SequenceScaler, temporal_split
from ml.sequences import SequenceSet, build_sequences
from ml.variables import input_columns


def _segment(n: int, start_minute: int = 0, label_at: int | None = None) -> pd.DataFrame:
    base = pd.date_range("2026-01-01T00:00:00Z", periods=n, freq="1min", tz="UTC")
    ts = base + pd.Timedelta(minutes=start_minute)
    cols = input_columns()
    data = {c: np.linspace(0, 1, n) + i * 0.01 for i, c in enumerate(cols)}
    df = pd.DataFrame(data)
    df["timestamp_utc"] = ts
    df["label"] = 0
    df["experiment_id"] = None
    if label_at is not None:
        df.loc[label_at, "label"] = 1
    return df


def test_build_sequences_respects_window_and_labels() -> None:
    seg = _segment(20, label_at=19)
    seqset = build_sequences([seg], input_columns(), window_steps=15)
    assert len(seqset) == 20 - 15 + 1
    assert seqset.X.shape == (len(seqset), 15, 23)
    assert seqset.y[-1] == 1  # last window ends exactly on the labeled row
    assert seqset.y[:-1].sum() == 0


def test_build_sequences_skips_short_segments() -> None:
    seg = _segment(5)  # shorter than window_steps
    seqset = build_sequences([seg], input_columns(), window_steps=15)
    assert len(seqset) == 0


def test_build_sequences_drops_windows_with_nan() -> None:
    seg = _segment(20)
    seg.loc[3, input_columns()[0]] = np.nan
    seqset = build_sequences([seg], input_columns(), window_steps=15)
    # every window covering index 3 must be dropped: windows ending at 14..19 that include row 3
    # (end-14 <= 3 <= end) -> end in [3, 17], all overlap since window_steps=15 spans indices end-14..end
    assert len(seqset) == 20 - 15 + 1 - len([e for e in range(14, 20) if e - 14 <= 3 <= e])


def test_temporal_split_is_chronological_and_covers_all() -> None:
    n = 100
    X = np.random.default_rng(0).normal(size=(n, 15, 23))
    y = np.zeros(n, dtype=int)
    y[10:15] = 1
    ts = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC").to_numpy()
    exp_ids = np.array([None] * n, dtype=object)
    seqset = SequenceSet(X, y, ts, exp_ids)

    split = temporal_split(seqset, train_frac=0.7, val_frac=0.15)
    assert len(split.train) + len(split.val) + len(split.test) == n
    assert split.train.end_timestamps.max() <= split.val.end_timestamps.min() if len(split.val) else True
    assert split.val.end_timestamps.max() <= split.test.end_timestamps.min() if len(split.test) else True


def test_temporal_split_keeps_experiment_together() -> None:
    n = 100
    X = np.zeros((n, 15, 23))
    y = np.zeros(n, dtype=int)
    ts = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC").to_numpy()
    exp_ids = np.array([None] * n, dtype=object)
    # experiment spans the naive 70% boundary (index 68..72)
    exp_ids[68:73] = "EXP_1"
    seqset = SequenceSet(X, y, ts, exp_ids)

    split = temporal_split(seqset, train_frac=0.7, val_frac=0.15)
    in_train = "EXP_1" in split.train.experiment_ids
    in_val = "EXP_1" in split.val.experiment_ids
    assert not (in_train and in_val)  # never straddles the boundary


def test_scaler_fits_only_on_train_and_transforms_consistently() -> None:
    rng = np.random.default_rng(1)
    X_train = rng.normal(loc=5, scale=2, size=(50, 15, 23))
    X_other = rng.normal(loc=50, scale=10, size=(10, 15, 23))  # very different distribution

    scaler = SequenceScaler().fit(X_train)
    scaled_train = scaler.transform(X_train)
    assert abs(scaled_train.mean()) < 0.2

    # transforming an unrelated distribution must use TRAIN's parameters, not re-fit
    scaled_other = scaler.transform(X_other)
    assert scaled_other.mean() > 5  # far from zero because it's not centered on itself

    restored = SequenceScaler.from_dict(scaler.to_dict())
    np.testing.assert_allclose(restored.transform(X_train), scaled_train)


def test_scaler_guards_against_constant_columns() -> None:
    X = np.ones((10, 15, 23))
    scaler = SequenceScaler().fit(X)
    out = scaler.transform(X)
    assert np.isfinite(out).all()
