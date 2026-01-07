#!/bin/bash
# Cron script for market categorization (rule pass + Codex/Claude fallback)
# - Prevents overlap with flock
# - Writes structured logs to /tmp/polymarket-categorize.log
# - Keeps rule stage intact; replaces Claude with Codex by default

set -o pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
cd /home/theo/polymarket-ml

LOG_FILE="/tmp/polymarket-categorize.log"
LOCK_FILE="/tmp/polymarket-categorize.lock"
USE_CODEX=${USE_CODEX:-1}
USE_CLAUDE_FALLBACK=${USE_CLAUDE_FALLBACK:-1}
# DB connection for host-based scripts (rule run uses container networking separately)
export DB_HOST=${DB_HOST:-127.0.0.1}
export DB_PORT=${DB_PORT:-5433}
export DB_USER=${DB_USER:-postgres}
export DB_PASSWORD=${DB_PASSWORD:-postgres}
export DB_NAME=${DB_NAME:-polymarket_ml}

# Activate venv if present
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# Acquire non-blocking lock to avoid overlapping runs
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Iseconds)] categorize: previous run still active, skipping." >> "$LOG_FILE"
  exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"

# Step 1: Run rule engine first (instant, free)
docker-compose exec -T api python3 -c "
from src.services.rule_categorizer import get_rule_categorizer
from src.db.database import get_session
from src.db.models import Market

categorizer = get_rule_categorizer()
with get_session() as session:
    markets = session.query(Market).filter(
        Market.category_l1 == None,
        Market.active == True
    ).limit(2000).all()
    market_dicts = [
        {'id': m.id, 'question': m.question, 'description': m.description, 'event_title': m.event_title}
        for m in markets
    ]
if market_dicts:
    matched, unmatched = categorizer.categorize_batch(market_dicts)
    if matched:
        categorizer.save_results(matched)
        print(f'Rules: categorized {len(matched)} markets')
    print(f'Remaining for LLM: {len(unmatched)}')
else:
    print('No uncategorized markets')
" >> "$LOG_FILE" 2>&1

# Step 2: Codex categorization (default) with Claude fallback if enabled
if [ "$USE_CODEX" -eq 1 ]; then
  echo "Codex batch..." >> "$LOG_FILE"
  if ! python3 scripts/categorize_with_codex.py --batch 30 >> "$LOG_FILE" 2>&1; then
    echo "Codex failed." >> "$LOG_FILE"
    if [ "$USE_CLAUDE_FALLBACK" -eq 1 ]; then
      echo "Fallback to Claude batch..." >> "$LOG_FILE"
      python3 scripts/categorize_with_claude.py --batch 30 >> "$LOG_FILE" 2>&1
    fi
  fi
else
  echo "Codex disabled, running Claude..." >> "$LOG_FILE"
  python3 scripts/categorize_with_claude.py --batch 30 >> "$LOG_FILE" 2>&1
fi

echo "Done." >> "$LOG_FILE"
