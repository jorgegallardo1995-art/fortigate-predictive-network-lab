"""Dashboard-side view of the LSTM training pipeline (``ml/``, trained on the
Mac/Colab - never here). This module has NO dependency on TensorFlow/sklearn:
it only reads the small JSON artefacts a training run writes
(``run_metadata.json`` / ``metrics.json``) and checks the DB for how many
labeled failures exist, so it works unmodified on the Raspberry Pi.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.models import Experiment, NetworkEvent

FAILURE_EVENT_TYPES = ("WAN_DOWN", "SLA_FAILED", "INTERFACE_DOWN")


def _models_dir() -> Path:
    return get_settings().data_dir / "models"


def training_readiness(session: Session) -> dict[str, Any]:
    """How close the dataset is to having usable ground truth for ml/train.py."""
    experiments_with_failure = session.execute(
        select(Experiment).where(Experiment.failure_timestamp.isnot(None))
    ).scalars().all()
    failure_events = session.execute(
        select(NetworkEvent).where(NetworkEvent.event_type.in_(FAILURE_EVENT_TYPES))
    ).scalars().all()
    total_experiments = session.execute(select(Experiment)).scalars().all()

    return {
        "labeled_experiments": len(experiments_with_failure),
        "total_experiments": len(total_experiments),
        "failure_events_logged": len(failure_events),
        "ready_to_train": bool(experiments_with_failure or failure_events),
        "experiments_missing_failure_timestamp": [
            e.experiment_id for e in total_experiments if e.failure_timestamp is None
        ],
    }


def list_runs() -> list[dict[str, Any]]:
    """Every training run found under data/models/, newest first. Empty list if
    the pipeline hasn't been run yet, or if a run was produced on another
    machine and never copied here (see ml/README.md)."""
    root = _models_dir()
    if not root.exists():
        return []
    runs = []
    for run_dir in sorted(root.iterdir(), reverse=True):
        meta_path = run_dir / "run_metadata.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        metrics_path = run_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else None
        runs.append({"run_id": meta.get("run_id", run_dir.name), "metadata": meta, "metrics": metrics})
    return runs


def get_run(run_id: str) -> dict[str, Any] | None:
    for run in list_runs():
        if run["run_id"] == run_id:
            return run
    return None
