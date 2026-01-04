#!/bin/bash
# Cron script for daily GCS export
# - Prevents overlap with flock
# - Writes logs to /tmp/polymarket-gcs-export.log
# - Runs incremental export (only new data since last export)

set -o pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
cd /home/theo/polymarket-ml

LOG_FILE="/tmp/polymarket-gcs-export.log"
LOCK_FILE="/tmp/polymarket-gcs-export.lock"

# Database connection
export DATABASE_URL="postgresql://postgres:postgres@localhost:5433/polymarket_ml"
export GOOGLE_APPLICATION_CREDENTIALS="/home/theo/polymarket-ml/gcp-credentials.json"

# Activate venv if present
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# Acquire non-blocking lock to avoid overlapping runs
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] gcs-export: previous run still active, skipping." >> "$LOG_FILE"
  exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"
echo "Starting incremental GCS export..." >> "$LOG_FILE"

python3 scripts/export_to_gcs.py --incremental >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "Export completed successfully." >> "$LOG_FILE"
else
  echo "Export failed with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
exit $EXIT_CODE
