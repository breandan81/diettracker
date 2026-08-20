#!/usr/bin/env bash
# Run τrend multi-user API on PORT (default 8511). Does not touch the live :8510 app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
# shellcheck disable=SC1091
source .venv/bin/activate
export PORT="${PORT:-8511}"
export HOST="${HOST:-0.0.0.0}"
mkdir -p data/photos
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
