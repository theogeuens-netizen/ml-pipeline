#!/bin/bash
# Cron script for daily CSGO BigQuery sync
# - Prevents overlap with flock
# - Writes logs to /tmp/csgo-bq-sync.log
# - Full sync (replaces tables)

set -o pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
cd /home/theomlmachine/polymarket-ml

LOG_FILE="/tmp/csgo-bq-sync.log"
LOCK_FILE="/tmp/csgo-bq-sync.lock"

# Database connection
export DATABASE_URL="postgresql://postgres:postgres@localhost:5433/polymarket_ml"
export GOOGLE_APPLICATION_CREDENTIALS="/home/theomlmachine/polymarket-ml/gcp-credentials.json"

# Activate venv if present
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# Acquire non-blocking lock to avoid overlapping runs
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] csgo-bq-sync: previous run still active, skipping." >> "$LOG_FILE"
  exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"
echo "Starting CSGO BigQuery sync..." >> "$LOG_FILE"

bash scripts/export/sync_csgo_to_bq.sh >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "Sync completed successfully." >> "$LOG_FILE"
else
  echo "Sync failed with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
exit $EXIT_CODE
