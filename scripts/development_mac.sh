#!/usr/bin/env bash
# Set up and run the project locally on macOS (development only).
# Usage:  ./scripts/development_mac.sh [--no-run]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CHART_URL="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"
CHART_DEST="app/static/vendor/chart.umd.min.js"

echo "==> Project: $PROJECT_ROOT"
"$PYTHON_BIN" --version

if [[ ! -d .venv ]]; then
  echo "==> Creating virtual environment (.venv)"
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements-dev.txt

if [[ ! -f .env ]]; then
  echo "==> Creating .env from .env.example (MOCK_MODE stays true)"
  cp .env.example .env
fi

mkdir -p data/exports data/backups logs
if [[ ! -f "$CHART_DEST" ]]; then
  echo "==> Fetching Chart.js -> $CHART_DEST"
  curl -fsSL -o "$CHART_DEST" "$CHART_URL" || echo "   (offline? dashboard will still work without charts)"
fi

echo "==> Applying database migrations"
alembic upgrade head

if [[ "${1:-}" == "--no-run" ]]; then
  echo "==> Done. Start later with:  uvicorn app.main:app --reload"
  exit 0
fi

echo "==> Starting dev server on http://127.0.0.1:8000  (Ctrl+C to stop)"
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
