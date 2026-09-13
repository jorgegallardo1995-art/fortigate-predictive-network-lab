"""Build (N, window_steps, n_features) sequences from continuous segments (§3.16.3).

A sample X_t = [x_(t-w+1), ..., x_t] is only built when all w steps come from the
SAME continuous segment (see ml/segments.py) - this is what "never cross a
collector_up=0 tramo" means in practice. The label y_t is the ``label`` column's
value at the last step (t).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SequenceSet:
    X: np.ndarray                 # (N, window_steps, n_features)
    y: np.ndarray                 # (N,)
    end_timestamps: np.ndarray    # (N,) - timestamp of the last step in each window
    experiment_ids: np.ndarray    # (N,) - for split-integrity checks; may contain None

    def __len__(self) -> int:
        return len(self.y)


def build_sequences(
    segments: list[pd.DataFrame],
    feature_columns: list[str],
    window_steps: int,
) -> SequenceSet:
    X_list: list[np.ndarray] = []
    y_list: list[int] = []
    ts_list: list[pd.Timestamp] = []
    exp_list: list[object] = []

    for seg in segments:
        if len(seg) < window_steps:
            continue
        feats = seg[feature_columns].to_numpy(dtype="float64")
        labels = seg["label"].to_numpy()
        timestamps = seg["timestamp_utc"].to_numpy()
        experiment_ids = seg["experiment_id"].to_numpy() if "experiment_id" in seg else [None] * len(seg)

        for end in range(window_steps - 1, len(seg)):
            start = end - window_steps + 1
            window = feats[start : end + 1]
            if np.isnan(window).any():
                continue  # e.g. the first sample after a restart has no rx_mbps yet
            X_list.append(window)
            y_list.append(int(labels[end]))
            ts_list.append(timestamps[end])
            exp_list.append(experiment_ids[end])

    if not X_list:
        return SequenceSet(
            np.empty((0, window_steps, len(feature_columns))),
            np.empty((0,), dtype=int),
            np.empty((0,), dtype="datetime64[ns]"),
            np.empty((0,), dtype=object),
        )

    return SequenceSet(
        X=np.stack(X_list),
        y=np.array(y_list, dtype=int),
        end_timestamps=np.array(ts_list),
        experiment_ids=np.array(exp_list, dtype=object),
    )
