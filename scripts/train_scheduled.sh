#!/usr/bin/env bash
# Scheduled LSTM training run (Mac only - never the Raspberry Pi).
#
# Pulls a CONSISTENT snapshot of the collector's live database from the Pi
# (SQLite online backup, safe under WAL), trains with ml/train.py, and
# publishes the resulting run/ to the dashboard so it shows on /model. Every
# run writes data/models/<run_id>/ - that directory IS the history.
#
#   ./scripts/train_scheduled.sh
#
# Overridable: RPI_SSH, RPI_DIR, WORK, LOG. Run 3x/day by launchd -
# see scripts/install_mac_training_schedule.sh.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
RPI_USER="${RPI_USER:-aaa}"
# Reach the Pi at its LAN IP first (fast, when the Mac is at home), then fall
# back to its Tailscale IP (works from anywhere the Mac has Tailscale up).
RPI_HOSTS="${RPI_HOSTS:-10.254.0.10 100.123.191.35}"
RPI_DIR="${RPI_DIR:-fortigate_predictive_collector}"
WORK="${WORK:-$HOME/fortigate_rpi_archive/work}"
LOG="${LOG:-$HOME/fortigate_rpi_archive/training.log}"
PY="$APP_DIR/.venv/bin/python"

mkdir -p "$WORK" "$(dirname "$LOG")"
log() { echo "$(date '+%Y-%m-%dT%H:%M:%S%z') | $*" | tee -a "$LOG"; }

# Never let two training runs overlap (a hung one must not stack on the next).
# A stale lock older than 40 min is reclaimed.
LOCKDIR="$WORK/.train.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    if [[ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +40 2>/dev/null)" ]]; then
        rmdir "$LOCKDIR" 2>/dev/null || true
        mkdir "$LOCKDIR" 2>/dev/null || { echo "lock busy" ; exit 0 ; }
    else
        log "another training run is still active - skipping this run"
        exit 0
    fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

log "=== scheduled training run ==="
# Pick the first host that answers: LAN IP, then Tailscale IP.
RPI_SSH=""
for h in $RPI_HOSTS; do
    if ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new \
           "$RPI_USER@$h" true 2>/dev/null; then
        RPI_SSH="$RPI_USER@$h"
        log "Pi reachable at $h"
        break
    fi
done
if [[ -z "$RPI_SSH" ]]; then
    log "Raspberry Pi unreachable on any of: $RPI_HOSTS - skipping this run"
    exit 0
fi

# Light snapshot: a PASSIVE WAL checkpoint (non-blocking) folds recent rows into
# the main DB file, then rsync the file itself (sequential read). This avoids
# sqlite3 .backup, whose random-access page copy + write locks thrash the Pi 3's
# SD card and, when runs stack, starve the collector into a watchdog restart
# loop. A flock on the Pi serialises the checkpoint; timeouts stop a hung copy.
SNAP="$WORK/train_snapshot.db"
# Bound every remote step with ssh/rsync's own timeouts (portable: macOS has no
# `timeout` command). ServerAlive* kills a hung ssh in ~60 s.
SSH_BOUND=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
if ! ssh "${SSH_BOUND[@]}" "$RPI_SSH" \
    "flock -n /tmp/pnl_snapshot.lock -c \"sqlite3 '$RPI_DIR/data/fortigate.db' 'PRAGMA wal_checkpoint(PASSIVE);'\"" \
    >/dev/null 2>&1; then
    log "checkpoint busy or Pi unreachable - skipping this run (no heavy copy attempted)"
    exit 0
fi
if ! rsync -a --timeout=180 -e "ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new" \
    "$RPI_SSH:$RPI_DIR/data/fortigate.db" "$SNAP"; then
    log "snapshot copy failed or timed out - skipping this run"
    exit 0
fi
log "snapshot pulled ($(du -h "$SNAP" | cut -f1))"

set +e
OUT="$("$PY" -m ml.train --db "$SNAP" --data-source REAL 2>&1)"
rc=$?
set -e
printf '%s\n' "$OUT" >> "$LOG"

if [[ $rc -ne 0 ]]; then
    log "training FAILED (rc=$rc) - see the captured output above in $LOG"
    exit $rc
fi

run_id="$(printf '%s\n' "$OUT" | sed -n 's#.*saved run to data/models/\(run_[0-9A-Za-z]*\).*#\1#p' | tail -1)"
metrics="$(printf '%s\n' "$OUT" | sed -n 's/.*TEST metrics[^{]*\({.*\)/\1/p' | tail -1)"
log "training OK: ${run_id:-?}  ${metrics:0:160}"

# Publish to the dashboard's /model page (best effort).
if [[ -n "$run_id" && -d "data/models/$run_id" ]]; then
    if rsync -a "data/models/$run_id/" "$RPI_SSH:$RPI_DIR/data/models/$run_id/"; then
        log "published $run_id to the dashboard"
    else
        log "could not publish $run_id (kept locally)"
    fi
fi
