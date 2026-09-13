"""Temporal split + scaling (§3.16.10 / §3.16.27).

Two rules that must never be violated:
  1. The split is by TIME, train = oldest, val/test = most recent - never random.
  2. The scaler is fit ONLY on TRAIN; val/test are transformed with those
     parameters. Fitting on the whole dataset would leak future statistics into
     training.

A same experiment's sequences are kept together on one side of a boundary
(§3.16.8: "un evento completo debe permanecer dentro de una única partición"),
which can shift the effective split ratios slightly - that shift is logged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ml.sequences import SequenceSet


@dataclass
class SplitResult:
    train: SequenceSet
    val: SequenceSet
    test: SequenceSet
    train_cutoff: object
    val_cutoff: object


def _push_boundary_past_experiment(experiment_ids_sorted: np.ndarray, idx: int) -> int:
    """If index idx falls inside a run of the same non-null experiment_id, advance
    idx to the first row of the NEXT experiment (or end of array)."""
    if idx <= 0 or idx >= len(experiment_ids_sorted):
        return idx
    boundary_exp = experiment_ids_sorted[idx - 1]
    if boundary_exp is None:
        return idx
    while idx < len(experiment_ids_sorted) and experiment_ids_sorted[idx] == boundary_exp:
        idx += 1
    return idx


def temporal_split(seqset: SequenceSet, train_frac: float, val_frac: float) -> SplitResult:
    n = len(seqset)
    order = np.argsort(seqset.end_timestamps)
    exp_sorted = seqset.experiment_ids[order]

    train_end = _push_boundary_past_experiment(exp_sorted, round(n * train_frac))
    val_end = _push_boundary_past_experiment(exp_sorted, round(n * (train_frac + val_frac)))
    val_end = max(val_end, train_end)

    def subset(idx: np.ndarray) -> SequenceSet:
        return SequenceSet(
            X=seqset.X[idx], y=seqset.y[idx],
            end_timestamps=seqset.end_timestamps[idx], experiment_ids=seqset.experiment_ids[idx],
        )

    train_idx, val_idx, test_idx = order[:train_end], order[train_end:val_end], order[val_end:]
    return SplitResult(
        train=subset(train_idx), val=subset(val_idx), test=subset(test_idx),
        train_cutoff=seqset.end_timestamps[order[train_end - 1]] if train_end else None,
        val_cutoff=seqset.end_timestamps[order[val_end - 1]] if val_end else None,
    )


class SequenceScaler:
    """StandardScaler applied per-feature across the (sample, timestep) axes,
    fit once on TRAIN and reused for val/test/inference."""

    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, X_train: np.ndarray) -> SequenceScaler:
        flat = X_train.reshape(-1, X_train.shape[-1])
        self.mean_ = np.nanmean(flat, axis=0)
        std = np.nanstd(flat, axis=0)
        self.scale_ = np.where(std < 1e-8, 1.0, std)  # guard against constant columns
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("SequenceScaler must be fit before transform")
        return (X - self.mean_) / self.scale_

    def to_dict(self) -> dict[str, list[float]]:
        return {"mean": self.mean_.tolist(), "scale": self.scale_.tolist()}

    @classmethod
    def from_dict(cls, d: dict[str, list[float]]) -> SequenceScaler:
        s = cls()
        s.mean_ = np.array(d["mean"])
        s.scale_ = np.array(d["scale"])
        return s
