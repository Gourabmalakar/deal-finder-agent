#!/usr/bin/env bash
# Runs check_uptime.py; on failure, fires a native macOS notification and
# appends to a local log. On success, does nothing (a healthy check should
# be silent, not a recurring "all good" message) — matches the same
# alert discipline the family-office project uses for its own reports.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/../uptime.log"

OUTPUT="$("$DIR/check_uptime.py" 2>&1)"
STATUS=$?

if [ $STATUS -ne 0 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) DOWN: $OUTPUT" >> "$LOG"
  osascript -e "display notification \"$OUTPUT\" with title \"Deal Finder is down\" sound name \"Basso\"" 2>/dev/null || true
fi

exit 0
