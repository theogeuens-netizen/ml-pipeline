---
description: Categorize Polymarket markets using L1/L2/L3 taxonomy
---

# Market Categorization Mode

You are categorizing Polymarket prediction markets into a 3-level taxonomy.

## Available Commands

| Command | Description |
|---------|-------------|
| `stats` | Show categorization statistics |
| `batch [n]` | Categorize n uncategorized markets (default: 100) |
| `validate [n]` | Validate n random rule-categorized markets from today |
| `rules` | Show rule performance stats |
| `run-rules` | Run rule engine on uncategorized markets |

## How to Start

1. Run `python -m cli.categorize_helpers stats --json` to get current stats
2. Display stats to user in a nice format
3. Ask what they want to do, or proceed with the command they specified

## Taxonomy Reference

**L1 Categories**: CRYPTO, SPORTS, ESPORTS, POLITICS, ECONOMICS, BUSINESS, ENTERTAINMENT, WEATHER, SCIENCE, TECH, LEGAL, OTHER

**Common L2/L3 patterns**:
- CRYPTO: BITCOIN, ETHEREUM, SOLANA, XRP, ALTCOIN, MEMECOIN
  - L3: DIRECTION_15MIN, DIRECTION_DAILY, ABOVE_THRESHOLD, BELOW_THRESHOLD, REACH_TARGET
- SPORTS: NFL, NBA, EPL, MLB, NHL, MMA_UFC, TENNIS, GOLF, CRICKET, etc.
  - L3: MATCH_WINNER, SPREAD, OVER_UNDER, PROP_PLAYER, FIRST_SCORER, FUTURES_*
- ESPORTS: CSGO, LOL, VALORANT, DOTA2, ROCKET_LEAGUE
  - L3: MATCH_WINNER, MAP_WINNER, MAPS_TOTAL, TOURNAMENT_WINNER
- POLITICS: US_PRESIDENTIAL, US_CONGRESSIONAL, TRUMP_ACTIONS, GEOPOLITICS, etc.
  - L3: WINNER, STATE_WINNER, TARIFF, EXECUTIVE_ORDER, WAR_OUTCOME, CEASEFIRE
- BUSINESS: STOCK_PRICE, EARNINGS, CORPORATE_ACTIONS, ELON_MUSK
  - L3: ABOVE_THRESHOLD, BEAT_ESTIMATE, MERGER, TWEET_COUNT

For complete taxonomy, read `/home/theo/polymarket-ml/src/models/taxonomy.py`

## Batch Categorization Workflow

When user says `batch [n]`:

1. Run: `python -m cli.categorize_helpers uncategorized --limit [n] --json`
2. For each market, determine L1/L2/L3 based on the question and description
3. Output results as JSON array:
```json
[
  {"id": 123, "l1": "CRYPTO", "l2": "BITCOIN", "l3": "DIRECTION_DAILY"},
  {"id": 124, "l1": "SPORTS", "l2": "NFL", "l3": "SPREAD"}
]
```
4. Ask user to confirm, then save via Python:
```python
import sys; sys.path.insert(0, '/home/theo/polymarket-ml')
from cli.categorize_helpers import save_categories
results = [{"id": 123, "l1": "CRYPTO", "l2": "BITCOIN", "l3": "DIRECTION_DAILY"}, ...]
saved = save_categories(results)
print(f"Saved {saved} categories")
```

## Validation Workflow

When user says `validate [n]`:

1. Run: `python -m cli.categorize_helpers fetch-validation --limit [n] --json`
   (Note: this needs to be added or use Python directly)
2. For each market, check if rule_l1/l2/l3 is correct
3. Output validation results:
```json
{
  "validated": 50,
  "correct": 47,
  "mismatches": [
    {"id": 123, "rule_l1": "SPORTS", "rule_l2": "NBA", "rule_l3": "MATCH_WINNER",
     "correct_l1": "SPORTS", "correct_l2": "NBA", "correct_l3": "PROP_PLAYER",
     "reason": "Question asks about player triple-double, not match winner"}
  ]
}
```
4. Ask if user wants to fix mismatches

## Key Guidelines

1. **Be specific with L3**: MATCH_WINNER vs PROP_PLAYER vs SPREAD matters
2. **Use context**: Event title often clarifies the sport/league
3. **15-minute crypto**: Questions with time ranges like "12:00 to 12:15" are DIRECTION_15MIN
4. **Spreads**: Look for (-X.5) patterns indicating point spreads
5. **Props**: Player names + stats (yards, points, rebounds) = PROP_PLAYER
6. **When uncertain**: Ask the user or use OTHER/MISCELLANEOUS/UNCLASSIFIED

## Quick Reference Commands

```bash
# Get stats
python -m cli.categorize_helpers stats

# Get uncategorized markets as JSON
python -m cli.categorize_helpers uncategorized --limit 50 --json

# Run rules on uncategorized
python -m cli.categorize_helpers run-rules --limit 500

# Show rule performance
python -m cli.categorize_helpers rules --json
```

## Docker Context

These commands should be run from within the api container or locally with the virtualenv:
```bash
docker-compose exec api python -m cli.categorize_helpers stats
```

Or if running locally, ensure you're in the project directory with dependencies installed.
