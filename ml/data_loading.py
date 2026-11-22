"""Load telemetry_raw + probe_results (+ availability_timeline, when present)
straight from the SQLite file with plain SQL - no dependency on the app's
SQLAlchemy models, so this runs unmodified on a copy of the DB, in Colab, or
anywhere else.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from ml.variables import AGGREGATE_VARIABLES, ICMP_DESTINATIONS, LEAKAGE_RESERVED_COLUMNS

_RAW_COLUMNS = (
    "id",
    "timestamp_utc",
    "data_source",
    "collection_success",
    "experiment_id",
    "label",
    "failure_timestamp",
    "minutes_before_failure",
    "scenario_id",
    *AGGREGATE_VARIABLES,
    *LEAKAGE_RESERVED_COLUMNS,
)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            f"SQLite database not found at {path}. Copy the collector's "
            "fortigate.db locally first (e.g. scp from the Raspberry Pi)."
        )
    # Read-only connection: this pipeline must never write to the operational DB.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_raw(
    db_path: str | Path, data_source: str = "REAL", since: str | None = None,
    until: str | None = None,
) -> pd.DataFrame:
    """telemetry_raw for one data_source, ordered by time, only successful cycles.

    `since` (ISO8601) restricts to rows at or after that instant - used to scope
    a training run to a short pilot window (e.g. a single day of controlled
    experiments) instead of the full historical dataset.
    """
    cols_sql = ", ".join(_RAW_COLUMNS)
    query = f"""
        SELECT {cols_sql} FROM telemetry_raw
        WHERE data_source = ? AND collection_success = 1
    """
    params: list[str] = [data_source]
    if since:
        # timestamp_utc is stored as naive "YYYY-MM-DD HH:MM:SS.ffffff" (no "T"/"Z"),
        # so an ISO8601 `since` must be reformatted to match or string comparison
        # sorts wrong (" " < "T" lexicographically excludes every real row).
        ts = pd.Timestamp(since)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        query += " AND timestamp_utc >= ?"
        params.append(ts.strftime("%Y-%m-%d %H:%M:%S"))
    if until:
        # Upper cutoff (inclusive), applied at LOAD time so every downstream
        # artefact (labels, failure instants, dataset_period, metadata) reflects
        # only data at or before the cutoff - no post-cutoff rows enter the run.
        tu = pd.Timestamp(until)
        tu = tu.tz_localize("UTC") if tu.tzinfo is None else tu.tz_convert("UTC")
        query += " AND timestamp_utc <= ?"
        params.append(tu.strftime("%Y-%m-%d %H:%M:%S"))
    query += " ORDER BY timestamp_utc"
    with _connect(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp_utc"])
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


def load_probe_results(db_path: str | Path, data_source: str = "REAL") -> pd.DataFrame:
    """probe_results pivoted wide: one row per timestamp, columns per destination."""
    query = """
        SELECT raw_id, timestamp_utc, label, rtt_avg_ms, rtt_mdev_ms, packet_loss_pct
        FROM probe_results WHERE data_source = ?
        ORDER BY timestamp_utc
    """
    with _connect(db_path) as conn:
        probes = pd.read_sql_query(query, conn, params=(data_source,))
    if probes.empty:
        return pd.DataFrame(columns=["raw_id"])

    wide = probes.pivot_table(index="raw_id", columns="label",
                               values=["rtt_avg_ms", "rtt_mdev_ms", "packet_loss_pct"])
    wide.columns = [f"{dest}_{metric}" for metric, dest in wide.columns]
    rename = {}
    for dest in ICMP_DESTINATIONS:
        rename[f"{dest}_rtt_avg_ms"] = f"{dest}_latency_ms"
        rename[f"{dest}_rtt_mdev_ms"] = f"{dest}_jitter_ms"
        rename[f"{dest}_packet_loss_pct"] = f"{dest}_loss_pct"
    wide = wide.rename(columns=rename).reset_index()
    return wide


def load_experiments(db_path: str | Path) -> pd.DataFrame:
    query = """
        SELECT experiment_id, name, scenario, start_timestamp, end_timestamp,
               failure_timestamp, operator, notes
        FROM experiments ORDER BY start_timestamp
    """
    with _connect(db_path) as conn:
        date_cols = ["start_timestamp", "end_timestamp", "failure_timestamp"]
        df = pd.read_sql_query(query, conn, parse_dates=date_cols)
    for col in ("start_timestamp", "end_timestamp", "failure_timestamp"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def load_speed_tests(db_path: str | Path, data_source: str = "REAL") -> pd.DataFrame:
    """[started_at, finished_at] of every active speed test - the self-inflicted
    saturation windows to exclude from the organic baseline. Returns an empty
    frame if the table does not exist (older DB copies)."""
    query = """
        SELECT started_at, finished_at, trigger FROM speed_tests
        WHERE data_source = ? ORDER BY started_at
    """
    try:
        with _connect(db_path) as conn:
            df = pd.read_sql_query(
                query, conn, params=(data_source,), parse_dates=["started_at", "finished_at"]
            )
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=["started_at", "finished_at", "trigger"])
    for col in ("started_at", "finished_at"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def load_network_events(db_path: str | Path, event_types: tuple[str, ...]) -> pd.DataFrame:
    placeholders = ",".join("?" * len(event_types))
    query = f"""
        SELECT timestamp_utc, event_type, severity, description, experiment_id
        FROM network_events WHERE event_type IN ({placeholders})
        ORDER BY timestamp_utc
    """
    with _connect(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=event_types, parse_dates=["timestamp_utc"])
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


def load_dataset(
    db_path: str | Path, data_source: str = "REAL", since: str | None = None,
    until: str | None = None,
) -> pd.DataFrame:
    """Merge telemetry_raw + pivoted ICMP into the wide frame the pipeline works on.

    One row per successful acquisition cycle, columns = the 23 input variables +
    the leakage-reserved status fields + labeling/bookkeeping columns.
    """
    raw = load_raw(db_path, data_source, since=since, until=until)
    probes = load_probe_results(db_path, data_source)
    if not probes.empty:
        df = raw.merge(probes, left_on="id", right_on="raw_id", how="left")
        df = df.drop(columns=["raw_id"])
    else:
        df = raw.copy()
        for col in ICMP_DESTINATIONS:
            for suffix in ("latency_ms", "jitter_ms", "loss_pct"):
                df[f"{col}_{suffix}"] = pd.NA
    return df.sort_values("timestamp_utc").reset_index(drop=True)
