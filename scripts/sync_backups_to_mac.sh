#!/usr/bin/env bash
# Pull the Raspberry Pi's daily backups into the Mac archive (the Pi only keeps
# the last backup.keep_days of them). Never deletes anything on either side;
# every newly copied file is checked with `gzip -t`.
#
#   ./scripts/sync_backups_to_mac.sh
#
# Overridable: RPI_SSH (aaa@10.254.0.10), RPI_BACKUPS_DIR, ARCHIVE_DIR.
# Run 4x/day by launchd - see scripts/install_mac_backup_sync.sh.
set -euo pipefail

RPI_SSH="${RPI_SSH:-aaa@10.254.0.10}"
RPI_BACKUPS_DIR="${RPI_BACKUPS_DIR:-fortigate_predictive_collector/data/backups}"
ARCHIVE_DIR="${ARCHIVE_DIR:-$HOME/fortigate_rpi_archive/backups}"

log() { echo "$(date '+%Y-%m-%dT%H:%M:%S%z') | $*"; }

mkdir -p "$ARCHIVE_DIR"

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$RPI_SSH" true 2>/dev/null; then
    log "Raspberry Pi $RPI_SSH unreachable - will retry on the next run"
    exit 0
fi

# Only finished backups (*.db.gz); .tmp files are never matched.
copied="$(rsync -a --partial --out-format='%n' \
    --include='fortigate_*.db.gz' --exclude='*' \
    "$RPI_SSH:$RPI_BACKUPS_DIR/" "$ARCHIVE_DIR/")"

# rsync also lists the directory itself ("./"); keep only backup files.
copied="$(grep -E '\.db\.gz$' <<< "$copied" || true)"

status=0
if [[ -z "$copied" ]]; then
    log "up to date ($(ls "$ARCHIVE_DIR" | wc -l | tr -d ' ') files in archive)"
fi
while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    if gzip -t "$ARCHIVE_DIR/$name" 2>/dev/null; then
        log "copied $name ($(du -h "$ARCHIVE_DIR/$name" | cut -f1)) - gzip OK"
    else
        log "ERROR: $name failed gzip -t, moving it aside"
        mv "$ARCHIVE_DIR/$name" "$ARCHIVE_DIR/$name.corrupt"
        status=1
    fi
done <<< "$copied"
exit $status
