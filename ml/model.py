"""The LSTM architecture, exactly as specified in §3.16.12:

    Input (window_steps x n_features)
      -> LSTM(64, return_sequences=True)
      -> Dropout(0.20)
      -> LSTM(32, return_sequences=False)
      -> Dropout(0.20)
      -> Dense(16, activation=ReLU)
      -> Dropout(0.20)
      -> Dense(1, activation=Sigmoid)
      -> P(failure in next `horizon_minutes` minutes)

35,489 trainable parameters for the default config (§3.16.21) - verified by
tests/ml/test_model.py.
"""

from __future__ import annotations

from ml.config import ModelConfig


def build_model(cfg: ModelConfig):
    # Imported lazily: TensorFlow is a heavy, ml-only dependency that must never
    # be required to import the rest of this package (e.g. for labeling/reporting
    # tooling that runs without it installed).
    import tensorflow as tf
    from tensorflow.keras import Sequential
    from tensorflow.keras.layers import LSTM, Bidirectional, Dense, Dropout, Input
    from tensorflow.keras.metrics import AUC, Precision, Recall
    from tensorflow.keras.optimizers import Adam

    tf.keras.utils.set_random_seed(cfg.seed)

    if cfg.variant == "bidirectional":
        # Improved variant: sees the window in both directions and uses recurrent
        # dropout; more capacity to beat the non-sequential Random Forest baseline.
        model = Sequential(
            [
                Input(shape=(cfg.window_steps, cfg.n_features)),
                Bidirectional(LSTM(cfg.lstm1_units, return_sequences=True,
                                   recurrent_dropout=cfg.recurrent_dropout)),
                Dropout(cfg.dropout),
                Bidirectional(LSTM(cfg.lstm2_units, return_sequences=False,
                                   recurrent_dropout=cfg.recurrent_dropout)),
                Dropout(cfg.dropout),
                Dense(cfg.dense_units, activation=cfg.dense_activation),
                Dropout(cfg.dropout),
                Dense(1, activation=cfg.output_activation),
            ],
            name="predictive_network_lab_bilstm",
        )
    else:
        model = Sequential(
            [
                Input(shape=(cfg.window_steps, cfg.n_features)),
                LSTM(cfg.lstm1_units, return_sequences=True),
                Dropout(cfg.dropout),
                LSTM(cfg.lstm2_units, return_sequences=False),
                Dropout(cfg.dropout),
                Dense(cfg.dense_units, activation=cfg.dense_activation),
                Dropout(cfg.dropout),
                Dense(1, activation=cfg.output_activation),
            ],
            name="predictive_network_lab_lstm",
        )
    model.compile(
        optimizer=Adam(learning_rate=cfg.learning_rate),
        loss=cfg.loss,
        metrics=[
            "accuracy",
            Precision(name="precision"),
            Recall(name="recall"),
            AUC(name="auc"),
        ],
    )
    return model
