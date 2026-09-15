#!/usr/bin/env bash
# Restart the local dashboard: stop any running instance, self-heal any
# ingestion run orphaned by that stop (see README "Reliability lessons" /
# CLAUDE.md), start a fresh uvicorn in the background, print the link.
#
# Usage:
#   ./scripts/restart_dashboard.sh              # port 8099
#   ./scripts/restart_dashboard.sh 8100          # custom port
#
# Safe to run repeatedly — killing a non-running server and cleaning zero
# stale rows are both no-ops, not errors.

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8099}"
LOG_FILE="/tmp/dash.log"

echo "Stopping any running dashboard..."
pkill -f "uvicorn deliverability.web.app" 2>/dev/null || true
sleep 1

echo "Clearing ingestion runs orphaned by that stop..."
.venv/bin/python -c "
from deliverability.storage.database import Database
from deliverability.storage.repositories import IngestionRunRepository
from deliverability.config import Settings
settings = Settings.from_env()
database = Database(settings.database_url)
repo = IngestionRunRepository(database, settings.project_id)
print('  cleaned up:', repo.fail_stale_running(older_than_minutes=0))
"

echo "Starting dashboard on port $PORT..."
nohup .venv/bin/python -m uvicorn deliverability.web.app:app --port "$PORT" --host 127.0.0.1 \
  > "$LOG_FILE" 2>&1 & disown
sleep 2
tail -n 10 "$LOG_FILE"

echo
echo "Dashboard: http://127.0.0.1:$PORT"
echo "Logs:      tail -f $LOG_FILE"
