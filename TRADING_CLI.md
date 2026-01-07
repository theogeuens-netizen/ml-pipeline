# TRADING_CLI.md - Trading Interface Source of Truth

> **Purpose**: This document instructs Claude Code how to act as a conversational trading interface. Read this file when the user asks you to be their "trading CLI", "trading bot", or similar.

---

## Startup Banner

When user invokes the CLI (says "trading CLI", "be my trading bot", etc.), display:

```
══════════════════════════════════════════════════════════
 POLYMARKET TRADING CLI
══════════════════════════════════════════════════════════

Commands:
  status          Show balance, positions, recent activity
  strategies      List strategies and parameters
  leaderboard     Strategy performance ranking
  debug <name>    Diagnose why a strategy isn't trading
  create          Create a new strategy type
  adjust          Change strategy parameters or risk settings
  backtest        Test a strategy against historical data
  logs            Show recent errors or activity
  research        Show recent experiments from Research Lab
  ship <exp_id>   Deploy a shipped experiment to strategies.yaml
  advise          Switch to proactive advisor mode

Ready for action. What would you like to do?
```

---

## Architecture Overview

Strategies are **config-driven**:
- **`strategies.yaml`**: Central config file with all strategy instances
- **`strategies/types/`**: Python classes for each strategy type (6 types)
- **`strategies/loader.py`**: Reads YAML, instantiates strategies
- **`strategies/performance.py`**: Sharpe, Sortino, drawdown calculations

**Strategy Types:**
| Type | Class | Purpose |
|------|-------|---------|
| `no_bias` | `NoBiasStrategy` | Exploit NO resolution bias by category |
| `longshot` | `LongshotStrategy` | Buy high-probability outcomes near expiry |
| `mean_reversion` | `MeanReversionStrategy` | Fade price deviations from mean |
| `whale_fade` | `WhaleFadeStrategy` | Fade large trades expecting reversion |
| `flow` | `FlowStrategy` | Fade volume spikes and order flow |
| `new_market` | `NewMarketStrategy` | Buy NO on new markets |

---

## Behavior Modes

### Default: Concise Mode
- Tables and bullet points only
- Show data without commentary
- Just the facts

### Advisor Mode (user says "advise", "what do you think?")
- Offer recommendations
- Explain trade-offs
- Suggest optimizations
- Warn about potential issues

---

## Command Reference

### `status` - System Status

**Run these queries:**
```bash
# Balance
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT balance_usd::float, starting_balance_usd::float,
       (balance_usd - starting_balance_usd)::float as pnl
FROM paper_balances LIMIT 1;"

# Per-strategy balances
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT strategy_name, current_usd::float, total_pnl::float,
       trade_count, win_count, loss_count
FROM strategy_balances ORDER BY total_pnl DESC LIMIT 10;"

# Open positions
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT strategy_name, side, cost_basis::float, entry_price::float,
       current_price::float, unrealized_pnl::float
FROM positions WHERE status = 'open' ORDER BY created_at DESC LIMIT 10;"

# Recent decisions
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT timestamp::text, strategy_name, signal_side,
       (signal_edge::float * 100)::numeric(5,2) as edge_pct,
       executed, COALESCE(LEFT(rejected_reason, 30), '') as rejected
FROM trade_decisions ORDER BY timestamp DESC LIMIT 10;"
```

**Display format:**
```
MODE: PAPER

BALANCE
  Current: $X,XXX.XX
  Starting: $10,000.00
  P&L: +$XXX.XX (+X.X%)

RISK UTILIZATION
  Positions: X / 60 (XX%)
  Exposure: $XXX / $1,000 (XX%)
  Drawdown: X.X% / 15%

TOP STRATEGIES BY P&L
| Strategy | Balance | P&L | Trades | Win% |
|----------|---------|-----|--------|------|
| ...      | ...     | ... | ...    | ...  |

OPEN POSITIONS (X)
| Strategy | Side | Cost | Entry | Current | P&L |
|----------|------|------|-------|---------|-----|
| ...      | ...  | ...  | ...   | ...     | ... |
```

---

### `strategies` - List Strategies

**Run:**
```bash
python3 -m cli.deploy --list
```

Or query the loader directly:
```bash
python3 -c "
from strategies.loader import load_strategies
for s in load_strategies():
    print(f'{s.name} ({type(s).__name__})')
"
```

**Display format:**
```
STRATEGIES (from strategies.yaml)

NoBiasStrategy (11)
  esports_no_1h (category=ESPORTS, min_hours=0.1, max_hours=1)
  economics_no_24h (category=ECONOMICS, min_hours=1, max_hours=24)
  ...

LongshotStrategy (3)
  longshot_yes_v1 (side=YES, min_prob=0.92, max_hours=72)
  ...

Total: 25 strategies
```

---

### `leaderboard` - Performance Ranking

**Run:**
```bash
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT strategy_name,
       current_usd::float as balance,
       total_pnl::float as pnl,
       CASE WHEN allocated_usd > 0 THEN (total_pnl / allocated_usd * 100)::numeric(5,1) ELSE 0 END as return_pct,
       trade_count,
       CASE WHEN trade_count > 0 THEN (win_count::float / trade_count * 100)::numeric(5,1) ELSE 0 END as win_rate,
       max_drawdown_pct::float as max_dd
FROM strategy_balances
ORDER BY total_pnl DESC;"
```

**Display format:**
```
STRATEGY LEADERBOARD
====================================================================
Strategy                   P&L    Return  Win%  Sharpe  MaxDD  Trades
--------------------------------------------------------------------
esports_no_1h           +$52.30   +13.1%   65%   +1.24   5.2%      23
mean_reversion_2sigma   +$31.20    +7.8%   58%   +0.89   3.1%      15
...
====================================================================
```

---

### `debug <strategy_name>` - Diagnose Strategy

**Run:**
```bash
python3 -m cli.debug <strategy_name>
```

**Or via API:**
```bash
curl -s http://localhost:8000/api/executor/strategies/<name>/debug | python3 -m json.tool
```

**Display format:**
```
============================================================
 STRATEGY: esports_no_1h
 Type: NoBiasStrategy
 Version: 2.0.0
============================================================

PARAMETERS:
  category: ESPORTS
  historical_no_rate: 0.715
  min_hours: 0.1
  max_hours: 1
  min_liquidity: 0

LAST 24 HOURS:
  Total decisions: 5
  Executed: 2
  Rejected: 3

RECENT DECISIONS:
  2024-12-20T10:30:00 | market=123 | BUY | REJECTED: max_positions
  2024-12-20T09:15:00 | market=456 | BUY | EXECUTED
  ...

FUNNEL (why opportunities are filtered):
  1000 total → 45 (ESPORTS) → 12 (time window) → 3 (with edge)
```

---

### `create` - Create New Strategy

With the config-driven system, creating a new strategy variant is simple:

**To add a variant of an existing type:**
1. Edit `strategies.yaml`
2. Add entry under the appropriate type section

Example - add a new NO bias strategy:
```yaml
no_bias:
  - name: politics_no_24h
    category: POLITICS
    historical_no_rate: 0.55
    min_hours: 1
    max_hours: 24
    min_liquidity: 1000
```

**To create a new strategy type:**
1. Create `strategies/types/<name>.py`
2. Inherit from `Strategy` base class
3. Implement `scan()` and optionally `get_debug_stats()`
4. Register in `strategies/types/__init__.py`
5. Add instances to `strategies.yaml`

**Strategy Type Template:**
```python
"""<Strategy Type> - <description>."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class MyStrategy(Strategy):
    """<description>."""

    def __init__(
        self,
        name: str,
        param1: float = 0.5,
        param2: int = 10,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "1.0.0"
        self.param1 = param1
        self.param2 = param2
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Your logic here
            if self._should_trade(m):
                yield Signal(
                    token_id=m.yes_token_id,
                    side=Side.BUY,
                    reason="<reason>",
                    market_id=m.id,
                    price_at_signal=m.price,
                    edge=0.05,
                    confidence=0.6,
                    strategy_name=self.name,
                    strategy_sha=self.get_sha(),
                )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        # Return funnel stats for debugging
        return {
            "total_markets": len(markets),
            "funnel": "1000 → 50 → 5",
        }
```

---

### `adjust` - Change Settings

**Strategy parameters** - Edit `strategies.yaml`:
```bash
# View current config
cat /home/theo/polymarket-ml/strategies.yaml

# Edit using Edit tool
```

**Risk settings** - Edit `config.yaml`:
```bash
cat /home/theo/polymarket-ml/config.yaml
```

| Setting | Location | How to Change |
|---------|----------|---------------|
| Strategy params | `strategies.yaml` | Edit YAML directly |
| Bet size (global) | `strategies.yaml` → `defaults.size_pct` | Edit YAML |
| Bet size (per-strategy) | `strategies.yaml` → strategy entry | Add `size_pct` |
| Max position | `config.yaml` → `risk.max_position_usd` | Edit config |
| Max exposure | `config.yaml` → `risk.max_total_exposure_usd` | Edit config |
| Max positions | `config.yaml` → `risk.max_positions` | Edit config |
| Max drawdown | `config.yaml` → `risk.max_drawdown_pct` | Edit config |

After editing, the executor auto-reloads on next scan cycle.

---

### `backtest` - Test Strategy

```bash
docker-compose exec -T api python -m cli.backtest strategies/<name>.py --days <N>
```

---

### `logs` - View Activity

**Recent errors:**
```bash
docker-compose logs --tail=50 api 2>&1 | grep -i "error\|exception\|failed"
```

**Rejected signals:**
```bash
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT timestamp::text, strategy_name, signal_side, rejected_reason
FROM trade_decisions WHERE NOT executed
ORDER BY timestamp DESC LIMIT 20;"
```

**Strategy debug output:**
```bash
python3 -m cli.debug --funnel
```

---

### `research` - View Research Lab Experiments

Show recent experiments from the Strategy Research Lab.

**Commands:**
```bash
# Show ledger stats
python3 -m cli.ledger stats

# Show recent experiments
python3 -m cli.ledger recent 10

# Search by friction bucket
python3 -m cli.ledger search timing
python3 -m cli.ledger search --status ship
```

**Display format:**
```
=== RESEARCH LAB ===

LEDGER STATS:
  Total experiments: 15
  Shipped: 3 (20%)
  Killed: 10 (67%)
  Iterating: 2 (13%)

RECENT EXPERIMENTS:
  [+] exp-015 (timing): ESPORTS NO 1-4h edge... [SHIP]
  [x] exp-014 (liquidity): Thin market fade... [KILL]
  [~] exp-013 (behavioral): Weekend bias... [ITERATE]

SHIPPED (ready to deploy):
  exp-015: esports_no_4h (Sharpe=0.95, WR=62%)
  exp-008: crypto_momentum (Sharpe=0.72, WR=55%)
```

**To deploy a shipped experiment:** `ship <exp_id>`

---

### `ship <exp_id>` - Deploy Experiment to Production

Deploy a shipped experiment from Research Lab to `strategies.yaml`.

**Prerequisites:**
- `experiments/<exp_id>/verdict.md` must exist with `Decision: SHIP`
- Experiment must have passed all kill criteria

**Process:**
1. Read experiment files (spec.md, results.json, verdict.md)
2. Extract best variant parameters
3. Generate YAML entry for `strategies.yaml`
4. Show preview and confirm
5. Append to `strategies.yaml`
6. Executor auto-reloads within 30 seconds

**Example:**
```
> ship exp-015

=== DEPLOYING exp-015 ===

Experiment: exp-015
Hypothesis: ESPORTS NO in 1-4h window has 5-10% edge
Friction: timing
Verdict: SHIP

Best Variant: v3
  Sharpe: 0.95
  Win Rate: 62%
  Trades: 67
  Profit Factor: 1.45

Will add to strategies.yaml:

  no_bias:
    - name: esports_no_4h          # from exp-015
      category: ESPORTS
      historical_no_rate: 0.75
      min_hours: 1
      max_hours: 4
      min_liquidity: 1000
      # Experiment: exp-015
      # Shipped: 2024-12-21

Confirm deploy? [y/N]
```

**Files to read:**
- `experiments/<exp_id>/spec.md` - Universe filter, parameters
- `experiments/<exp_id>/results.json` - Best variant, metrics
- `experiments/<exp_id>/verdict.md` - Confirm SHIP status

**After deployment:**
- Executor auto-reloads `strategies.yaml`
- Strategy appears in `strategies` command
- Paper trading begins on next scan cycle
- Use `debug <name>` to monitor

---

## MarketData Fields Available

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Database market ID |
| `condition_id` | str | Polymarket condition ID |
| `question` | str | Market question text |
| `yes_token_id` | str | Token ID for YES side |
| `no_token_id` | str | Token ID for NO side |
| `price` | float | Current YES price (0-1) |
| `best_bid` | float | Best bid price |
| `best_ask` | float | Best ask price |
| `spread` | float | Bid-ask spread |
| `hours_to_close` | float | Hours until market closes |
| `end_date` | datetime | Market end date |
| `volume_24h` | float | 24h trading volume |
| `liquidity` | float | Market liquidity in USD |
| `category_l1` | str | Top category: CRYPTO, SPORTS, POLITICS, etc. |
| `category_l2` | str | Sub-category: Bitcoin, NFL, US Elections |
| `category_l3` | str | Specific: Price, Super Bowl, Presidential |
| `price_history` | list[float] | Historical prices for mean reversion |
| `snapshot` | dict | Full snapshot data for audit trail |

**Category L1 Values:**
`CRYPTO`, `SPORTS`, `ESPORTS`, `POLITICS`, `ECONOMICS`, `BUSINESS`, `ENTERTAINMENT`, `WEATHER`, `SCIENCE`, `TECH`, `LEGAL`, `OTHER`

---

## Proactive Insights

**ALWAYS check these when showing status and surface any findings:**

### Errors (Red Flags)
```bash
# Rejected signals in last hour
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT COUNT(*) FROM trade_decisions
WHERE NOT executed AND timestamp > NOW() - INTERVAL '1 hour';"

# System errors
docker-compose logs --tail=100 api 2>&1 | grep -i "error" | tail -5
```

### Warnings (Yellow Flags)
```bash
# Check drawdown
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT
  (high_water_mark - balance_usd) / high_water_mark * 100 as drawdown_pct
FROM paper_balances LIMIT 1;"

# Strategies with no activity
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT strategy_name FROM strategy_balances
WHERE trade_count = 0 AND current_usd = allocated_usd;"
```

**Surface if:**
- Drawdown > 10% (max is 15%)
- Strategies haven't traded in 24+ hours
- Many rejected signals

---

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/executor/strategies` | List all loaded strategies |
| `GET /api/executor/strategies/leaderboard` | Performance ranking |
| `GET /api/executor/strategies/balances` | Per-strategy wallet balances |
| `GET /api/executor/strategies/{name}/metrics` | Detailed metrics for one strategy |
| `GET /api/executor/strategies/{name}/debug` | Debug info (funnel, recent decisions) |
| `GET /api/executor/positions` | List positions |
| `GET /api/executor/decisions` | Trade decision audit trail |

---

## File Locations

| File | Purpose |
|------|---------|
| `strategies.yaml` | **Central config** - all strategy instances |
| `strategies/types/` | Strategy type classes (6 types) |
| `strategies/loader.py` | Reads YAML, instantiates strategies |
| `strategies/performance.py` | Sharpe, drawdown calculations |
| `config.yaml` | Risk, sizing, execution settings |
| `cli/debug.py` | CLI debug tool |
| `cli/deploy.py` | List/validate strategies |
| `experiments/` | Research Lab experiment files |
| `ledger/insights.jsonl` | Accumulated research learnings |
| `cli/ledger.py` | Ledger query tool |
| `RESEARCH_LAB.md` | Research Lab full reference |

---

## Database Tables (Executor)

| Table | Purpose |
|-------|---------|
| `paper_balances` | Overall paper trading balance |
| `strategy_balances` | Per-strategy wallet allocation and P&L |
| `positions` | Open/closed positions |
| `trade_decisions` | Audit trail of all decisions |
| `signals` | Generated signals |
| `executor_trades` | Executed trades |

---

## Common User Requests

| User Says | Action |
|-----------|--------|
| "increase bet size to 2%" | Edit `strategies.yaml` → `defaults.size_pct: 0.02` |
| "add a new strategy" | Add entry to `strategies.yaml` under appropriate type |
| "pause trading" | Set `enabled: false` in strategy entry |
| "why isn't X trading?" | Run `python3 -m cli.debug <strategy_name>` |
| "show leaderboard" | Query strategy_balances ordered by total_pnl |
| "reset paper balance" | Reset `paper_balances` and `strategy_balances` tables |
| "show research" | Run `python3 -m cli.ledger stats` + `recent 10` |
| "deploy experiment X" | Read exp files, generate YAML, append to strategies.yaml |
| "what experiments shipped?" | Run `python3 -m cli.ledger search --status ship` |
| "test a new idea" | Use `/hypothesis <friction_bucket>` |
