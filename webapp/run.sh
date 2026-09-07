#!/usr/bin/env bash
# Runs the local test webapp regardless of the caller's working directory.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR/backend"
exec "$DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 --reload
