# Predictive Network Lab — FortiGate Predictive Network Collector

*FortiGate Telemetry & Network Continuity Research Platform*

A 24/7 telemetry collector that builds a **traceable, reproducible, multivariate
time-series dataset** from a FortiGate 50E, for the experimental phase of a
doctoral thesis on early prediction of failures in telecommunications networks
(LSTM). **This software does not train models.** It captures, stores, validates,
visualises, documents and exports the experimental data.

- **Development:** macOS (Apple Silicon), VS Code — MOCK mode, no FortiGate needed.
- **Production:** Raspberry Pi (ARM64, Raspberry Pi OS 64-bit) — autonomous, `systemd`, 24/7.

---

## 1. Objective

Build, over weeks/months, a dataset that later feeds LSTM training/evaluation.
Priorities, in order: **data integrity → traceability → reproducibility →**
visualisation. Raw observations are immutable; derived features are always
recomputable; gaps are recorded, never back-filled; MOCK and REAL data never mix.

## 2. Architecture

```
FortiGate 50E ──HTTPS/GET──► Raspberry Pi (one process)
                              ├─ APScheduler  (1 cycle / 60 s, no overlap)
                              ├─ TelemetryCollector → FortiGateClient (GET only)
                              │                     └ MockProvider (MOCK_MODE)
                              ├─ ICMP prober (Pi → targets, independent cross-check)
                              ├─ SQLite + WAL  (SQLAlchemy 2.x, Alembic-ready)
                              ├─ GapDetector · EventService · FeatureEngineering
                              ├─ BackupService (daily)
                              └─ FastAPI + Jinja2 + Chart.js dashboard
                                     ▲ http://IP_RASPBERRY:8000  (LAN only)
                                  Mac browser · CSV export · LSTM analysis (elsewhere)
```

One process serves the dashboard **and** runs the scheduler (FastAPI `lifespan`).
One `systemd` service. See [app/](app/) for the module layout.

## 3. Requirements

- Python **3.11+** (3.12 recommended).
- macOS Apple Silicon *or* Linux ARM64. No macOS-only dependencies.
- FortiGate 50E with a **read-only REST API admin** (token) and, for network-quality
  metrics, **SD-WAN + Performance SLA health-checks** configured in measurement-only
  mode. See §5–6.

Dependencies: FastAPI, Uvicorn, httpx, Pydantic v2, SQLAlchemy 2, Alembic,
APScheduler, pandas, NumPy, PyYAML, Jinja2, tenacity. Full list in
[requirements.txt](requirements.txt).

## 4. Development on the Mac

```bash
git clone <repo> fortigate_predictive_collector
cd fortigate_predictive_collector
./scripts/development_mac.sh          # venv + deps + .env + DB + dev server
# or manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                  # MOCK_MODE stays true
uvicorn app.main:app --reload         # http://127.0.0.1:8000
```

`MOCK_MODE=true` generates synthetic data (`data_source=MOCK`) so you can build and
test the dashboard, database, scheduler and feature pipeline with no FortiGate.

## 5. FortiGate configuration

### 5.1 Read-only REST API admin

`config system accprofile` → profile with `sysgrp/netgrp/fwgrp/loggrp = read`,
everything else `none`. Then **System → Administrators → Create New → REST API
Admin**: assign that profile, set **Trusted Hosts** to the Raspberry Pi IP `/32`,
and copy the generated **API key once** into `.env` (`FORTIGATE_API_TOKEN=`).
Never paste the token anywhere else.

### 5.2 SD-WAN + Performance SLA (measurement-only)

Enable SD-WAN with the single WAN interface as the only member, create 2–3
health-checks (`1.1.1.1`, `8.8.8.8`, `9.9.9.9`, protocol `ping`), and **do not
create SD-WAN service rules** — routing is unchanged, only the SLA probes run.
Keep console access during the change. Map the health-check names in
[config/config.yaml](config/config.yaml) → `sla_targets`.

Without SD-WAN, `latency/jitter/packet_loss` come only from the Pi's ICMP prober
(documented as a separate measurement point).

## 6. Identify the endpoints for *your* FortiOS (ETAPA 6)

```bash
python scripts/probe_fortios_endpoints.py
```

Issues **GET only** against every catalogued endpoint, prints which respond, and
saves the JSON under `data/exports/fortios_probe_*/`. Adjust
`config.yaml → fortios_endpoints` with the working paths. A missing endpoint →
its metrics are stored `NULL`, the cycle continues.

## 7. `.env` variables

| Variable | Meaning |
|---|---|
| `FORTIGATE_HOST` / `FORTIGATE_PORT` | management IP/FQDN and HTTPS port |
| `FORTIGATE_API_TOKEN` | read-only REST API key (**secret**) |
| `FORTIGATE_VDOM` | `root` |
| `VERIFY_SSL` | `false` for the self-signed cert |
| `MOCK_MODE` | `true` (dev) / `false` (production) |
| `DATA_DIR` | base dir for DB, exports, backups (see §13) |
| `DATABASE_URL` | optional — set to a PostgreSQL URL to migrate off SQLite |
| `DASHBOARD_HOST` / `DASHBOARD_PORT` | `127.0.0.1:8000` dev, `0.0.0.0:8000` Pi |
| `TIMEZONE` | IANA tz for local timestamps (UTC is always primary) |
| `RUN_SCHEDULER` | `false` to run the dashboard only |

`.env` is git-ignored. The token is redacted from all logs.

## 8. MOCK mode

`MOCK_MODE=true` → every row `data_source=MOCK`. `MOCK_MODE=false` → `data_source=REAL`.
Exports default to `REAL` and never mix the two.

## 9. Local tests

```bash
pytest                 # 23 tests, no FortiGate access (httpx mocked with respx)
ruff check app tests   # lint
```

## 10. Deployment on the Raspberry Pi

```bash
git clone <repo> ~/fortigate_predictive_collector
cd ~/fortigate_predictive_collector
sudo ./scripts/install_raspberry.sh    # checks arch/python, venv, deps, dirs,
                                       # permissions, systemd unit, enable, start
nano .env                              # set host, token, MOCK_MODE=false, DATA_DIR
sudo systemctl restart fortigate-collector
```

## 11. systemd

Unit: [systemd/fortigate-collector.service](systemd/fortigate-collector.service)
(`After=network-online.target`, `Restart=always`, `RestartSec=10`, WAL checkpoint
on stop).

```bash
sudo systemctl status  fortigate-collector
sudo systemctl restart fortigate-collector
sudo systemctl stop    fortigate-collector
sudo systemctl start   fortigate-collector
journalctl -u fortigate-collector -f
```

## 12. Dashboard

`http://IP_RASPBERRY:8000` — LAN only, do **not** expose to the internet.
Pages: **Dashboard** (KPIs, current values, 8 charts, 1h–30d, 30 s auto-refresh),
**Dataset**, **Eventos**, **Research**, **Sistema**. API docs at `/docs`.
Chart.js is vendored locally ([app/static/vendor/](app/static/vendor/)); the
install scripts fetch it — the dashboard degrades to tables if it is absent.

## 13. Dataset & database

SQLite (`WAL`) at `${DATA_DIR}/fortigate.db`. `DATA_DIR` defaults to `./data` on
the Mac and should point to a **USB SSD** on the Pi (e.g. `/mnt/data/fortigate`)
to avoid SD-card wear and power-loss corruption. The Mac's dev DB is never copied
to the Pi — the Pi starts a clean `REAL`-only dataset on first run.

Tables: `telemetry_raw` (immutable, 1 row/cycle + full API snapshot),
`telemetry_sla`, `probe_results`, `telemetry_features`, `network_events`,
`collection_gaps`, `experiments`, `system_status`, `research_metadata`.
Rough size: ~200 MB/month, ~1.3 GB in 6 months.

Migrate to PostgreSQL later: set `DATABASE_URL`, run `alembic upgrade head`
(models are portable, no code change).

## 14. Export

`/dataset` page or `GET /api/export/{raw|features|events|gaps|experiments|icmp|dictionary}`
with `start`, `end`, `experiment_id`, `data_source` filters. CSV, UTF-8, ISO-8601
UTC; RAW is wide with per-target SLA columns pivoted. Ready for pandas / NumPy /
scikit-learn / TensorFlow-Keras.

## 15. Backups

Daily at 03:00 UTC (configurable) via the SQLite online-backup API, checked with
`PRAGMA quick_check` and gzipped → `${DATA_DIR}/backups/fortigate_YYYY-MM-DD.db.gz`
(~15x smaller). Keep-N configurable, logged, active DB untouched. A backup is skipped
(and logged as an error) when the disk cannot hold its temporary copy; corrupt or
half-written files are removed and never count towards keep-N. `/api/status` reports
`checks.backup` (error when the newest valid backup is older than 36 h). raw_json
retention never clears rows newer than the newest valid backup.
Manual: `./scripts/backup_database.sh`.

The Pi only keeps the last `keep_days` backups; the long-term archive lives on the
Mac. `./scripts/sync_backups_to_mac.sh` pulls new backups into
`~/fortigate_rpi_archive/backups/` and checks each one with `gzip -t`;
`./scripts/install_mac_backup_sync.sh` installs it as a launchd agent (4 times a day,
catching up after sleep). Restore / open one with
`gunzip -k fortigate_YYYY-MM-DD.db.gz`.

## 16. Logs

`logs/collector.log` (rotating, 10 MB × 10) + journald. Records startup/shutdown,
poll success/failure, API timeouts/errors, DB errors, gaps, restarts, backups,
exports, experiments. **Never logs tokens or credentials.**

## 17. Security

Read-only API admin · trusted-host = Pi · GET only (the client has no
POST/PUT/DELETE methods, enforced by a test) · token only in `.env`, redacted in
logs · dashboard bound to the LAN.

## 18. Troubleshooting

| Symptom | Check |
|---|---|
| `auth` errors | token in `.env`; trusted-host includes the Pi IP; profile is read-only |
| metrics all `NULL` | run `probe_fortios_endpoints.py`; fix `fortios_endpoints` in config.yaml |
| no SLA data | SD-WAN + health-checks configured? names match `sla_targets`? |
| dashboard unreachable from Mac | `DASHBOARD_HOST=0.0.0.0`; same LAN; firewall |
| charts missing | `curl` Chart.js into `app/static/vendor/chart.umd.min.js` |
| gaps after reboot | expected — `collection_gaps` records reason `SYSTEM_REBOOT` |

## 19. Scientific metadata

Auto-collected: device model / anonymisable serial / FortiOS version, Pi model /
arch / OS, Python & collector versions, dataset start, sampling interval,
timezone, requested vs available vs unavailable variables, expected / obtained /
lost samples, completeness %. Visible on `/research`, in `GET /api/research/metadata`.

## 20. Reproducibility

`POST /api/research/metadata/snapshot` (or the `/research` button) writes
`research_metadata.json` at the project root: `collector_version`, `git_commit`,
`fortios_version`, `sampling_interval`, `feature_windows`, variables, timezone,
`database_schema_version`, Python version, OS, hardware. No secrets. Use it to
document each experimental run in the thesis.

## 21. Continuity & availability

`GET /api/availability/summary` / `/timeline` and the `/availability` dashboard
page expose a **gap-free** minute-by-minute record (`availability_timeline`):
`collector_up`, `network_up` (NULL while the collector was down - never assumed),
and `reason` per slot. Export it with `GET /api/export/availability`. Gaps are
classified `POWER_FAILURE` / `SYSTEM_REBOOT` (host rebooted) vs
`COLLECTOR_FAILURE` / `FORTIGATE_UNREACHABLE` (host stayed up) automatically from
`/proc/stat` boot time; `PATCH /api/gaps/{id}` corrects a cause manually when the
operator knows more than the heuristic does.

## 22. LSTM model (`ml/`)

The deep-learning pipeline described in the thesis's methodology chapter lives in
a **separate package, `ml/`, that trains on your Mac or Colab - never on the
Raspberry Pi**. See [`ml/README.md`](ml/README.md) for the full workflow
(getting a local copy of the DB, registering controlled experiments with
`POST /api/experiments/<id>/failure` to create real ground truth, training,
evaluating against baselines). The dashboard's **Modelo LSTM** page (`/model`,
`GET /api/model/status`) shows whether the dataset has any labeled failures yet
and lists past training runs - it has no TensorFlow dependency, so it runs fine
on the Pi even though training never does.

---

### Project stages

1–4 (structure, config, DB, FortiGateClient) and 7–14 in MOCK: **done**.
5–6 (real FortiGate connection & endpoint validation): **done** (FGT-50E,
FortiOS 6.0.4). 15–17 (Raspberry deployment & 24/7 validation): **done**, running
in production with a watchdog. LSTM (`ml/`): pipeline **done**; real training is
blocked on ground truth - see §22.

---

## Reproducir resultados del Capítulo 4

Entorno exacto en `requirements-cap4.txt` (Python 3.12.14, TensorFlow 2.21.0, scikit-learn 1.9.1,
numpy 2.5.3, pandas 2.3.3, scipy 1.18.1, matplotlib 3.11.2).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-cap4.txt

# Dataset del estudio (Release, corte 2026-11-14) -> snap.db
gunzip fortigate_dataset_estudio.db.gz && mv fortigate_dataset_estudio.db snap.db
```

| Resultado | Comando | Salida |
|---|---|---|
| Modelo histórico | `python -m ml.train --db snap.db --data-source REAL --until 2026-11-14T23:59:59Z` | `data/models/run_*/` |
| Modelo régimen aleatorizado | `python -m ml.train --db snap.db --since 2026-11-07 --until 2026-11-14T23:59:59Z` | `data/models/run_*/` |
| Walk-forward | `python -m ml.crossval --db snap.db --since 2026-11-07 --until 2026-11-14T23:59:59Z --folds 4` | `data/models/crossval_*.json` |
| Bootstrap PR-AUC (§4.9) | `python -m scripts.bootstrap_prauc_diferencias --db snap.db` | `docs/resultados/bootstrap_prauc.{json,csv}`, `bootstrap_prauc_tabla.md` |
| Naive + IC de lead | `python -m scripts.audit_baseline_lead --db snap.db` | consola |
| Figura 10 (registro) | `python -m scripts.figura10_desde_registro` | `docs/graficos/figura10_registro_eventos.png` |

Artefactos de modelo ya entrenados: `docs/modelo/run_20261122T144340Z` (régimen aleatorizado) y
`run_20261122T144431Z` (histórico). Los hashes SHA-256 de los vectores de predicción usados por el
bootstrap están en `docs/resultados/bootstrap_prauc.json`.
