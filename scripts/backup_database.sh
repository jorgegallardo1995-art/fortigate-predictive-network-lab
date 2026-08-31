#!/usr/bin/env bash
# Manual on-demand database backup (the collector also does this daily at 03:00).
# Wraps the same consistent SQLite online-backup used by the app.
#
#   ./scripts/backup_database.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

"$PY" -c "from app.services.backup_service import run_backup; import json; print(json.dumps(run_backup(), indent=2))"
