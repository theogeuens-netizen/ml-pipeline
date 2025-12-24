---
description: Enter trading CLI mode
---

## Database Location

**Project root**: `/home/theo/polymarket-ml`
**Database**: `polymarket_ml` (Polymarket only - NO Kalshi data)

**Data sources**:
- `historical_markets` / `historical_price_snapshots` - Migrated historical data
- `markets` / `snapshots` - Live operational data (65+ features, actively collected)

**IMPORTANT**: All CLI commands MUST be run from `/home/theo/polymarket-ml`.
Do NOT access any other database or project directory.

Read /home/theo/polymarket-ml/TRADING_CLI.md and follow its instructions exactly.

Enter Trading CLI mode:
1. Display the startup banner with all commands
2. Say "Ready for action. What would you like to do?"
3. Wait for user input
4. For each command, run the actual queries and display results
5. Always check proactive insights (errors, warnings, opportunities) when showing status
6. Stay in CLI mode until user says "exit" or changes topic
