"""Predictive Network Lab - LSTM training pipeline.

Deliberately **decoupled** from the ``app`` package (the 24/7 collector): this
module talks to the SQLite file directly with plain SQL, has no FastAPI/
SQLAlchemy/APScheduler dependency, and is meant to run on a development
machine or Colab - never on the Raspberry Pi. Install its extra dependencies
with ``pip install -r requirements-ml.txt``.

See ``ml/README.md`` for the end-to-end workflow and
``Capitulo_3_16_Implementacion_LSTM_Tesis`` for the methodological source this
package implements section by section.
"""
