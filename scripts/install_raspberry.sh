#!/usr/bin/env bash
# Deploy the collector on a Raspberry Pi (Raspberry Pi OS 64-bit / Debian, ARM64).
# Idempotent: safe to re-run after a "git pull".
#
#   sudo ./scripts/install_raspberry.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="fortigate-collector"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CHART_URL="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"

echo "==============================================================="
echo " Predictive Network Lab - Raspberry Pi installer"
echo " app dir : $APP_DIR"
echo " user    : $RUN_USER"
echo "==============================================================="

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run with sudo." >&2; exit 1
fi

# 1. architecture ----------------------------------------------------------
ARCH="$(uname -m)"
echo "==> [1/12] Architecture: $ARCH"
[[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]] || echo "   WARNING: expected ARM64 - continuing anyway."

# 2. python --------------------------------------------------------------
echo "==> [2/12] Checking Python"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
  || { echo "ERROR: Python >= 3.11 required."; exit 1; }
"$PYTHON_BIN" --version

# 3. system deps -------------------------------------------------------
echo "==> [3/12] Installing system packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential sqlite3 iputils-ping curl

# 4. virtualenv -------------------------------------------------------
echo "==> [4/12] Creating virtual environment"
sudo -u "$RUN_USER" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"

# 5. requirements ---------------------------------------------------
echo "==> [5/12] Installing Python requirements"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# 6. directories --------------------------------------------------
echo "==> [6/12] Preparing directories"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "   Created .env from template - EDIT IT before starting (host, token, MOCK_MODE=false, DATA_DIR)."
fi
# shellcheck disable=SC1091
set -a; source "$APP_DIR/.env"; set +a
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
[[ "$DATA_DIR" = /* ]] || DATA_DIR="$APP_DIR/$DATA_DIR"
mkdir -p "$DATA_DIR/exports" "$DATA_DIR/backups" "$APP_DIR/logs"
sudo -u "$RUN_USER" curl -fsSL -o "$APP_DIR/app/static/vendor/chart.umd.min.js" "$CHART_URL" \
  || echo "   (could not fetch Chart.js - dashboard works without it)"

# 7. permissions -------------------------------------------------
echo "==> [7/12] Setting permissions"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" "$DATA_DIR"
chmod 600 "$APP_DIR/.env"

# 8. database ----------------------------------------------------
echo "==> [8/12] Applying database migrations"
sudo -u "$RUN_USER" bash -c "cd '$APP_DIR' && .venv/bin/alembic upgrade head"

# 9. systemd unit ----------------------------------------------
echo "==> [9/12] Installing systemd unit"
sed -e "s|__APP_DIR__|$APP_DIR|g" \
    -e "s|__DATA_DIR__|$DATA_DIR|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    "$APP_DIR/systemd/fortigate-collector.service" > "$SERVICE_FILE"
systemctl daemon-reload

# 10. enable ---------------------------------------------------
echo "==> [10/12] Enabling service"
systemctl enable "$SERVICE_NAME"

# 11. start --------------------------------------------------
echo "==> [11/12] Starting service"
systemctl restart "$SERVICE_NAME"
sleep 3

# 12. status ------------------------------------------------
echo "==> [12/12] Status"
systemctl --no-pager --full status "$SERVICE_NAME" || true

IP="$(hostname -I | awk '{print $1}')"
echo
echo "==============================================================="
echo " Dashboard:  http://${IP}:${DASHBOARD_PORT:-8000}"
echo " Logs:       journalctl -u $SERVICE_NAME -f"
echo "             $APP_DIR/logs/collector.log"
echo " Manage:     sudo systemctl {status|restart|stop|start} $SERVICE_NAME"
echo "==============================================================="
echo " If MOCK_MODE is still true, edit $APP_DIR/.env and run:"
echo "   sudo systemctl restart $SERVICE_NAME"
echo "==============================================================="
