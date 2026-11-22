#!/usr/bin/env bash
# Clean retraining + walk-forward on the RANDOMISED-SCHEDULE regime only.
#
# Trains and cross-validates using ONLY telemetry at/after SINCE (default
# 2026-11-07, when randomised ramps + netem latency/loss replaced the fixed
# :06 calendar). This isolates the unbiased regime from the fixed-schedule
# history the UNADE reviewer criticised, for a fair "before vs after" result.
#
# Usage:  scripts/train_randomized_regime.sh [SINCE_DATE]
#         scripts/train_randomized_regime.sh 2026-11-07
#
# NOTE: needs enough accumulated randomised data (aim for ~1-2 weeks: the
# reviewer suggested ~30-40 events/scenario and 8-10 independent events in
# val/test). It ABORTS cleanly if there are too few sequences yet.
set -euo pipefail

SINCE="${1:-2026-11-07}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

PY="$APP_DIR/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
SNAP="${SNAP:-$HOME/fortigate_rpi_archive/work/train_snapshot.db}"
RPI_SSH="${RPI_SSH:-aaa@10.254.0.10}"
RPI_DB="${RPI_DB:-/home/aaa/fortigate_predictive_collector/data/fortigate.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# Refresh the local snapshot from the Pi (light PASSIVE checkpoint + rsync).
# If the Pi is unreachable, fall back to the existing snapshot with a warning.
echo ">> refrescando snapshot desde el Pi ..."
if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$RPI_SSH" \
      "flock -n /tmp/pnl_snapshot.lock -c \"sqlite3 '$RPI_DB' 'PRAGMA wal_checkpoint(PASSIVE);'\"" 2>/dev/null \
   && rsync -a --timeout=180 -e "ssh -o ConnectTimeout=10" "$RPI_SSH:$RPI_DB" "$SNAP"; then
  echo ">> snapshot actualizado: $SNAP"
else
  echo "!! no se pudo refrescar desde el Pi; uso el snapshot existente: $SNAP"
fi
[ -f "$SNAP" ] || { echo "ERROR: no existe el snapshot $SNAP"; exit 1; }

echo ">> ===== ENTRENAMIENTO (desde $SINCE) ====="
"$PY" -m ml.train --db "$SNAP" --since "$SINCE" --output-dir data/models || {
  echo "!! el entrenamiento abortó (probablemente aún hay pocos datos del régimen nuevo)."; exit 2; }

echo ">> ===== WALK-FORWARD (desde $SINCE) ====="
"$PY" -m ml.crossval --db "$SNAP" --since "$SINCE" --folds 4 \
  --output "data/models/crossval_randomized_${STAMP}.json" || {
  echo "!! el walk-forward abortó (pocos eventos independientes por pliegue todavía)."; exit 3; }

echo ">> listo. Resultados en data/models/ (run_* y crossval_randomized_${STAMP}.json)"
