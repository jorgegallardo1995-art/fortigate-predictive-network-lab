"""Hyperparameters and structural constants - a 1:1 mirror of the table in
Cap. 3.16.24 of the thesis methodology document. Change values here, not in
scattered literals, so every run stays traceable to a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    # --- data / windowing (§3.16.3) ---
    sampling_interval_seconds: int = 60
    window_minutes: int = 15          # input sequence length, in 1-min steps
    horizon_minutes: int = 15         # prediction horizon: failure in (t, t+horizon]
    n_features: int = 23              # §3.16.4 - see ml/variables.py

    # --- architecture (§3.16.12) ---
    lstm1_units: int = 64
    lstm2_units: int = 32
    dense_units: int = 16
    dropout: float = 0.20
    dense_activation: str = "relu"
    output_activation: str = "sigmoid"

    # --- training (§3.16.22-3.16.24) ---
    loss: str = "binary_crossentropy"
    learning_rate: float = 0.001
    batch_size: int = 32
    max_epochs: int = 100
    early_stopping_patience: int = 10
    seed: int = 42

    # --- split (§3.16.27) ---
    train_frac: float = 0.70
    val_frac: float = 0.15
    test_frac: float = 0.15

    # --- gap handling (§3.16.9) ---
    # A window may only be built from samples spaced within this tolerance;
    # matches the collector's own gap_detector (interval + tolerance).
    gap_tolerance_seconds: int = 90

    # --- threshold search (§3.16.34) ---
    candidate_thresholds: tuple[float, ...] = field(
        default_factory=lambda: (0.30, 0.40, 0.50, 0.60, 0.70)
    )

    @property
    def window_steps(self) -> int:
        return int(self.window_minutes * 60 / self.sampling_interval_seconds)

    @property
    def horizon_steps(self) -> int:
        return int(self.horizon_minutes * 60 / self.sampling_interval_seconds)


DEFAULT_CONFIG = ModelConfig()

# Event types treated as real (non-simulated) WAN failure ground truth when
# scanning network_events automatically. Kept narrow on purpose - an operator
# must confirm/curate before training, per §3.16.7 ("no debe haber asignación
# automática... hasta disponer de evidencia").
FAILURE_EVENT_TYPES = ("WAN_DOWN", "SLA_FAILED", "INTERFACE_DOWN")

# --- induced (self-inflicted) load, see ml/induced.py -------------------------
# Experiments the collector injects on purpose are marked so the predictive
# pipeline can treat them correctly instead of mixing them with organic traffic.
CONGESTION_EXPERIMENT_PREFIX = "EXP-CONG-"       # bandwidth ramps (precursor-bearing)
INDUCED_OUTAGE_EXPERIMENT_PREFIX = "EXP-WANDOWN-"  # scheduled abrupt outages
INDUCED_OUTAGE_NOTE = "induced=WAN_DOWN"          # notes marker for the same
# Fallback duration for an induced outage whose experiment row has no end yet.
INDUCED_OUTAGE_MAX_HOURS = 3
# A speed test's window is padded by this on each side so the 1-min sample that
# overlaps its ~18 s burst is reliably excluded from the "normal" baseline.
SPEEDTEST_PAD_SECONDS = 60
# Minutes after an induced fault's restoration to also exclude from BOTH classes
# (one prediction horizon): the link's recovery transient is neither a clean
# "normal" baseline nor a precursor (reviewer Bloque 2).
RECOVERY_EXCLUSION_MINUTES = 15
