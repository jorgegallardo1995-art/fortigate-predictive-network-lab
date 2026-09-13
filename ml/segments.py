"""Split the dataset into temporally-continuous segments (§3.16.9).

An LSTM window must never be built across a collection gap - doing so would let
the network "learn" a collector outage as if it were network behaviour. A
segment is a maximal run of consecutive samples whose spacing never exceeds the
configured tolerance.
"""

from __future__ import annotations

import pandas as pd


def continuous_segments(df: pd.DataFrame, tolerance_seconds: int) -> list[pd.DataFrame]:
    """Split ``df`` (sorted by timestamp_utc) into segments with no internal gap.

    Each returned segment has a fresh RangeIndex and is itself sorted by time.
    """
    if df.empty:
        return []

    ts = df["timestamp_utc"].reset_index(drop=True)
    delta = ts.diff().dt.total_seconds()
    # A new segment starts wherever the gap to the previous sample is too large
    # (delta is NaN for the very first row, which also starts segment 0).
    new_segment = (delta.isna()) | (delta > tolerance_seconds)
    segment_id = new_segment.cumsum()

    df = df.reset_index(drop=True)
    return [
        group.reset_index(drop=True)
        for _, group in df.groupby(segment_id.values)
    ]


def segment_stats(segments: list[pd.DataFrame]) -> dict[str, object]:
    lengths = [len(s) for s in segments]
    return {
        "n_segments": len(segments),
        "total_rows": sum(lengths),
        "longest_segment_rows": max(lengths) if lengths else 0,
        "segments_long_enough": sum(1 for n in lengths if n > 0),
    }
