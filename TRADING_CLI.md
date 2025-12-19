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
  strategies      List deployed strategies and parameters
  create          Create a new strategy from description
  adjust          Change strategy parameters or risk settings
  deploy          Deploy/undeploy a strategy
  backtest        Test a strategy against historical data
  logs            Show recent errors or activity
  advise          Switch to proactive advisor mode

Ready for action. What would you like to do?
```

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

# Position count and exposure
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT COUNT(*) as positions, COALESCE(SUM(cost_basis::float), 0) as exposure
FROM positions WHERE status = 'open';"
```

**Display format:**
```
MODE: PAPER

BALANCE
  Current: $X,XXX.XX
  Starting: $10,000.00
  P&L: +$XXX.XX (+X.X%)

RISK UTILIZATION
  Positions: X / 20 (XX%)
  Exposure: $XXX / $1,000 (XX%)
  Drawdown: X.X% / 15%

OPEN POSITIONS (X)
| Strategy | Side | Cost | Entry | Current | P&L |
|----------|------|------|-------|---------|-----|
| ...      | ...  | ...  | ...   | ...     | ... |

RECENT DECISIONS (last 24h)
| Time | Strategy | Side | Edge | Status |
|------|----------|------|------|--------|
| ...  | ...      | ...  | ...  | ...    |

[Any insights from proactive checks - see below]
```

---

### `strategies` - List Strategies

**Run:**
```bash
cat /home/theo/polymarket-ml/deployed_strategies.yaml
```

**For each strategy, also show parameters:**
```bash
cat /home/theo/polymarket-ml/strategies/<filename>.py
```

**Display format:**
```
DEPLOYED STRATEGIES

[ON] longshot_yes_v1
     File: strategies/longshot_yes_v1.py
     Parameters:
       min_probability: 0.92
       max_probability: 0.99
       max_hours_to_expiry: 72
       min_liquidity_usd: 5000
       size_usd: 25
```

---

### `create` - Create New Strategy

When user describes a strategy in natural language:

1. **Understand the intent** - Ask clarifying questions if needed
2. **Write the strategy file** to `strategies/<name>.py`
3. **Validate it** - Check syntax and required methods
4. **Offer to backtest** before deploying

**Template:**
```python
"""
<Strategy description>
"""
from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData

class <ClassName>(Strategy):
    name = "<snake_case_name>"
    version = "1.0.0"

    # Parameters
    <param1> = <value>
    <param2> = <value>

    def filter(self, market: MarketData) -> bool:
        # Quick filter
        return True

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            if not self.filter(m):
                continue
            # Logic here
            yield Signal(
                token_id=m.yes_token_id,  # or m.no_token_id
                side=Side.BUY,
                reason="<reason>",
                market_id=m.id,
                price_at_signal=m.price,
                edge=<edge>,
                confidence=<confidence>,
                size_usd=self.<size_param>,
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
            )

strategy = <ClassName>()
```

---

### `adjust` - Change Settings

**Strategy parameters** - Edit the strategy file:
```bash
# Show current
cat /home/theo/polymarket-ml/strategies/<name>.py

# Edit using Edit tool to change class attributes
```

**Risk settings** - Edit config.yaml:
```bash
cat /home/theo/polymarket-ml/config.yaml
```

| Setting | Location | How to Change |
|---------|----------|---------------|
| Bet size | `strategies/<name>.py` → `size_usd` | Edit strategy file |
| Max position | `config.yaml` → `risk.max_position_usd` | Edit config |
| Max exposure | `config.yaml` → `risk.max_total_exposure_usd` | Edit config |
| Max positions | `config.yaml` → `risk.max_positions` | Edit config |
| Max drawdown | `config.yaml` → `risk.max_drawdown_pct` | Edit config |
| Sizing method | `config.yaml` → `sizing.method` | Edit config |
| Order type | `config.yaml` → `execution.default_order_type` | Edit config |

After editing config.yaml, restart API:
```bash
docker-compose restart api
```

---

### `deploy` / `undeploy` - Manage Deployments

**Deploy:**
```bash
# Validate first
python3 -c "
import sys
sys.path.insert(0, '/home/theo/polymarket-ml')
from strategies import load_strategy
s = load_strategy('/home/theo/polymarket-ml/strategies/<name>.py')
print(f'Valid: {s.name} v{s.version}')
"

# Add to deployed_strategies.yaml
```

**Undeploy:**
Edit `/home/theo/polymarket-ml/deployed_strategies.yaml`:
- Set `enabled: false` to disable
- Or remove the entry entirely

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

**Executor activity:**
```bash
docker-compose logs --tail=100 api 2>&1 | grep -i "signal\|execute\|position"
```

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

**Surface if:** Any rejected signals or errors found

### Warnings (Yellow Flags)
```bash
# Check drawdown
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT
  (high_water_mark - balance_usd) / high_water_mark * 100 as drawdown_pct
FROM paper_balances LIMIT 1;"

# Check exposure utilization
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT COALESCE(SUM(cost_basis::float), 0) as exposure FROM positions WHERE status = 'open';"
```

**Surface if:**
- Drawdown > 10% (max is 15%)
- Exposure > $800 (max is $1,000)
- Position count > 16 (max is 20)

### Opportunities (Blue Insights)
```bash
# Last signal time per strategy
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT strategy_name, MAX(timestamp)::text as last_signal
FROM trade_decisions GROUP BY strategy_name;"

# Markets matching criteria but not traded
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT COUNT(*) FROM markets m
JOIN LATERAL (SELECT probability FROM snapshots WHERE market_id = m.id ORDER BY timestamp DESC LIMIT 1) s ON true
WHERE m.active AND s.probability BETWEEN 0.92 AND 0.99
AND m.id NOT IN (SELECT DISTINCT market_id FROM positions);"
```

**Surface if:**
- Strategy hasn't fired in 24+ hours
- Markets match criteria but aren't being traded

---

## Settings Reference

### Risk Settings (config.yaml → risk)

| Parameter | Current | Description |
|-----------|---------|-------------|
| `max_position_usd` | 100 | Max USD per single position |
| `max_total_exposure_usd` | 1000 | Max USD across all positions |
| `max_positions` | 20 | Max number of open positions |
| `max_drawdown_pct` | 0.15 | Stop trading at 15% drawdown |

### Sizing Settings (config.yaml → sizing)

| Parameter | Current | Description |
|-----------|---------|-------------|
| `method` | fixed | `fixed`, `kelly`, or `volatility_scaled` |
| `fixed_amount_usd` | 25 | Default bet size |
| `kelly_fraction` | 0.25 | Fraction of Kelly criterion to use |

**Sizing Methods:**
- **fixed**: Always bet `fixed_amount_usd`
- **kelly**: Bet based on edge × confidence, scaled by `kelly_fraction`
- **volatility_scaled**: Smaller bets in volatile markets

### Execution Settings (config.yaml → execution)

| Parameter | Current | Description |
|-----------|---------|-------------|
| `default_order_type` | limit | `market`, `limit`, or `spread` |
| `limit_offset_bps` | 50 | Basis points from mid (50 = 0.5%) |
| `spread_timeout_seconds` | 30 | Time before crossing spread |

**Order Types:**
- **market**: Cross spread immediately, guaranteed fill
- **limit**: Post at mid - offset, may not fill
- **spread**: Post to capture spread, cross after timeout

---

## Common User Requests

| User Says | Action |
|-----------|--------|
| "increase bet size to $50" | Edit strategy's `size_usd` parameter |
| "switch to kelly sizing" | Edit `config.yaml` → `sizing.method: kelly` |
| "pause trading" | Set `enabled: false` in `deployed_strategies.yaml` |
| "more aggressive" | Increase `max_position_usd`, `max_total_exposure_usd` |
| "more conservative" | Decrease limits, lower `max_drawdown_pct` |
| "use market orders" | Edit `config.yaml` → `execution.default_order_type: market` |
| "why no trades?" | Check strategy criteria, query matching markets |
| "reset paper balance" | Query to reset `paper_balances` table |

---

## Troubleshooting

| Problem | Diagnosis | Solution |
|---------|-----------|----------|
| No signals | Strategy criteria too strict | Check markets matching criteria |
| All signals rejected | Risk limits hit | Check exposure, position count, drawdown |
| Executor not running | Container issue | `docker-compose logs api`, restart if needed |
| Telegram not working | Missing env vars | Check `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

---

## File Locations

| File | Purpose |
|------|---------|
| `config.yaml` | Risk, sizing, execution settings |
| `deployed_strategies.yaml` | Which strategies are active |
| `strategies/*.py` | Strategy code files |
| `strategies/base.py` | Base class for strategies |

---

## Database Tables (Executor)

| Table | Purpose |
|-------|---------|
| `paper_balances` | Paper trading balance |
| `positions` | Open/closed positions |
| `trade_decisions` | Audit trail of all decisions |
| `signals` | Generated signals |
| `executor_trades` | Executed trades |
