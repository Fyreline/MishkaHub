#!/usr/bin/env sh
# Start both Mishka Hub dev servers together. Press Ctrl-C to stop both.
set -e
# This script lives in scripts/; the project root is one level up.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting backend on http://127.0.0.1:8000 ..."
( cd "$ROOT/apps/server" && .venv/bin/python -m uvicorn app.main:app --reload --port 8000 ) &
BACK=$!

echo "Starting web on http://127.0.0.1:5173 ..."
( cd "$ROOT/apps/web" && npm run dev ) &
FRONT=$!

trap 'kill $BACK $FRONT 2>/dev/null' INT TERM
wait
