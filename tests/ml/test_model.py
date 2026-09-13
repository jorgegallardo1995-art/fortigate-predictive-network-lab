from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("tensorflow")

from ml.config import ModelConfig
from ml.model import build_model


def test_model_architecture_matches_spec() -> None:
    cfg = ModelConfig()
    model = build_model(cfg)

    assert model.output_shape == (None, 1)
    layer_types = [type(layer).__name__ for layer in model.layers]
    assert layer_types == ["LSTM", "Dropout", "LSTM", "Dropout", "Dense", "Dropout", "Dense"]
    assert model.layers[0].units == 64
    assert model.layers[2].units == 32
    assert model.layers[4].units == 16
    assert model.layers[4].activation.__name__ == "relu"
    assert model.layers[-1].activation.__name__ == "sigmoid"


def test_model_param_count_matches_thesis_figure() -> None:
    """§3.16.21: 35,489 trainable parameters for the default 23-variable config."""
    model = build_model(ModelConfig())
    n_params = int(np.sum([np.prod(v.shape) for v in model.trainable_variables]))
    assert n_params == 35_489


def test_model_forward_pass_shape() -> None:
    cfg = ModelConfig()
    model = build_model(cfg)
    X = np.random.default_rng(0).normal(size=(4, cfg.window_steps, cfg.n_features)).astype("float32")
    proba = model.predict(X, verbose=0)
    assert proba.shape == (4, 1)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_seed_gives_reproducible_initial_weights() -> None:
    cfg = ModelConfig(seed=7)
    m1 = build_model(cfg)
    m2 = build_model(cfg)
    w1 = m1.get_weights()[0]
    w2 = m2.get_weights()[0]
    np.testing.assert_allclose(w1, w2)
