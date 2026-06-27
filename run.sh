#!/usr/bin/env bash
# Convenience launcher for local development.
set -e
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements.txt
fi
export SPORTS_SECRET_KEY="${SPORTS_SECRET_KEY:-local-dev-secret}"
export SPORTS_PORT="${SPORTS_PORT:-3003}"
echo "Starting Sports Meet on http://127.0.0.1:${SPORTS_PORT}  (Ctrl+C to stop)"
exec ./venv/bin/python app.py
