# LSTM training pipeline (`ml/`)

Implements the architecture and protocol from
*Capítulo 3.16 — Implementación detallada de la arquitectura neuronal LSTM*.
Runs on your **Mac or Colab — never on the Raspberry Pi**. The collector
(`app/`, `requirements.txt`) has zero dependency on anything in this folder.

## Install

```bash
python -m venv .venv-ml && source .venv-ml/bin/activate
pip install -r requirements-ml.txt
```

## 1. Get a local copy of the data

```bash
scp aaa@<pi-ip>:/home/aaa/fortigate_predictive_collector/data/fortigate.db ./data/fortigate.db
```

The pipeline opens this file **read-only** — it never writes to the collector's
operational database.

## 2. Check whether you have any ground truth yet

```bash
python -m ml.train --db data/fortigate.db --data-source REAL
```

If there are zero labeled failures (experiments with `failure_timestamp` set,
or confirmed `WAN_DOWN`/`SLA_FAILED`/`INTERFACE_DOWN` events), it **aborts on
purpose** with instructions — it will not train a model that has never seen a
single example of the thing it's supposed to predict. This is the current
state of the platform's dataset (0 experiments, 0 failure events as of the
last check) — see §3.16.37 of the methodology document.

## 3. Get real ground truth: run a controlled experiment

With the collector **up and recording**, degrade the link on purpose and log it:

```bash
# 1. start the experiment
curl -X POST http://<pi-ip>:8000/api/experiments -H 'content-type: application/json' -d '{
  "experiment_id": "EXP001",
  "name": "Corte controlado de WAN",
  "scenario": "WAN_FAILURE",
  "operator": "jorge"
}'

# 2. ... perform the controlled degradation/outage now ...

# 3. mark exactly when the failure started (T_f)
curl -X POST http://<pi-ip>:8000/api/experiments/EXP001/failure -H 'content-type: application/json' -d '{
  "failure_timestamp": "2026-09-12T15:30:00Z"
}'

# 4. close the experiment once recovered
curl -X POST http://<pi-ip>:8000/api/experiments/EXP001/close
```

Repeat for `LATENCY_DEGRADATION`, `PACKET_LOSS`, `CONGESTION`, `RECOVERY` scenarios
(§3.16.8) to get a variety of precursor patterns. A handful of clean experiments
spread across different times of day is enough for a first real training run.

## 4. Train

```bash
python -m ml.train --db data/fortigate.db --data-source REAL
```

Or validate the pipeline mechanics right now, with no real data:

```bash
python -m ml.train --synthetic-demo
```

Every run writes `data/models/<run_id>/`:

| File | Contents |
|---|---|
| `model.keras` | the trained network |
| `scaler.json` | StandardScaler mean/scale, fit on TRAIN only |
| `history.json` | per-epoch loss/metrics |
| `run_metadata.json` | full reproducibility record (§3.16.38) |
| `metrics.json` | TEST metrics, lead time, baseline comparisons |

`demo_*` run IDs are synthetic-only and are also tagged `"synthetic": true`
inside `run_metadata.json` — never cite those numbers in the thesis.

## 5. See it in the dashboard

The dashboard's **Modelo LSTM** page (`/model`) reads `data/models/` — it has no
TensorFlow dependency, so it works on the Pi too. To publish a run there:

```bash
rsync -av data/models/<run_id>/ aaa@<pi-ip>:/home/aaa/fortigate_predictive_collector/data/models/<run_id>/
```

## Module map

| File | §  | Purpose |
|---|---|---|
| `config.py` | 3.16.24 | Hyperparameters - single source of truth |
| `variables.py` | 3.16.4 | The 23 input variables + why the rest are excluded |
| `data_loading.py` | 3.16.2 | Read telemetry_raw/probe_results/experiments straight from SQLite |
| `segments.py` | 3.16.9 | Split into gap-free continuous runs |
| `labeling.py` | 3.16.6-3.16.7 | y_t from operator-confirmed T_f only, never thresholds |
| `sequences.py` | 3.16.3 | Build (15, 23) windows within one segment |
| `preprocessing.py` | 3.16.10, 3.16.27 | Temporal split + TRAIN-only scaler |
| `model.py` | 3.16.12-3.16.24 | The exact architecture (35,489 params) |
| `baselines.py` | 3.16.31 | Threshold rule + Random Forest comparators |
| `evaluate.py` | 3.16.32-3.16.34 | Metrics, threshold search, Lead Time |
| `synthetic.py` | — | Pipeline smoke-test data only, never real results |
| `train.py` | 3.16.39 | CLI orchestrating the whole flow |

## What NOT to do

- Don't install `requirements-ml.txt` on the Raspberry Pi.
- Don't fit the scaler on anything but TRAIN.
- Don't label from dashboard thresholds - only from a confirmed `failure_timestamp`.
- Don't build a window that spans a `collector_up=0` gap.
- Don't report `metrics.json` from a `demo_*` run as a thesis result.
