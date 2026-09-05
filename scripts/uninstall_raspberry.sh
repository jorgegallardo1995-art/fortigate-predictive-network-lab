#!/usr/bin/env bash
# Remove the systemd service. The database, exports, backups and logs are KEPT
# unless --purge is passed.
#
#   sudo ./scripts/uninstall_raspberry.sh [--purge]
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="fortigate-collector"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ $EUID -ne 0 ]]; then echo "ERROR: run with sudo." >&2; exit 1; fi

echo "==> Stopping and disabling $SERVICE_NAME (+ watchdog)"
systemctl disable --now "${SERVICE_NAME}-watchdog.timer" 2>/dev/null || true
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SERVICE_FILE" \
      "/etc/systemd/system/${SERVICE_NAME}-watchdog.service" \
      "/etc/systemd/system/${SERVICE_NAME}-watchdog.timer"
systemctl daemon-reload
echo "==> Service removed."

if [[ "${1:-}" == "--purge" ]]; then
  # shellcheck disable=SC1091
  set -a; [[ -f "$APP_DIR/.env" ]] && source "$APP_DIR/.env"; set +a
  DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
  [[ "$DATA_DIR" = /* ]] || DATA_DIR="$APP_DIR/$DATA_DIR"
  echo "==> --purge: deleting $DATA_DIR and $APP_DIR/logs and .venv"
  read -r -p "    Type YES to confirm: " ok
  [[ "$ok" == "YES" ]] || { echo "aborted."; exit 1; }
  rm -rf "$DATA_DIR" "$APP_DIR/logs" "$APP_DIR/.venv" "$APP_DIR/research_metadata.json"
  echo "==> Purged."
else
  echo "==> Data kept. Re-run with --purge to also delete the dataset."
fi
