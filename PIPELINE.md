# Trading Pipeline: End-to-End Flow

> **Purpose**: Documents how markets are discovered, tracked, traded, and settled.

---

## 1. Market Discovery

**Celery Task**: `discover_markets` (every hour at :00)

- Fetches all markets from Polymarket Gamma API
- Filters: $100+ volume, within 2-week lookahead, not resolved
- Stores in `markets` table with `yes_token_id`, `no_token_id`
- Assigns initial TIER based on time to resolution

### Tier System (data collection frequency)

| Tier | Time to Close | Snapshot Interval |
|------|---------------|-------------------|
| T0 | > 48h | Every hour |
| T1 | 12-48h | Every 5 min |
| T2 | 4-12h | Every 1 min |
| T3 | 1-4h | Every 30 sec |
| T4 | < 1h | Every 15 sec |

---

## 2. Data Collection

**Celery Tasks**: `snapshot_tier_X` (per tier schedule above)

Each snapshot captures:
- `price` (YES probability 0-1)
- `best_bid`, `best_ask`, `spread`
- `volume_24h`, `liquidity`
- `trade_count_1h`, `price_change_1h`

Stored in `snapshots` table → used by executor scanner

### WebSocket (Real-time trades)
- 4 parallel connections
- ~380 trades/min captured
- Stored in `trades` table

---

## 3. Executor Scan Cycle

**Interval**: Every 30 seconds (`config.yaml` → `settings.scan_interval_seconds`)

### Step A: Scanner fetches eligible markets

```sql
SELECT * FROM markets
WHERE active=true AND resolved=false AND yes_token_id IS NOT NULL
JOIN latest snapshot for each market
```

Filters out:
- Excluded keywords (celebrity, influencer)
- Low liquidity (< $1000)

### Step B: Run each deployed strategy

```python
for strategy in deployed_strategies.yaml:
    signals = strategy.scan(markets)
```

### Strategy Filter Example (longshot_yes_v1)

```python
if bet_side == "YES":
    prob = market.price
else:
    prob = 1 - market.price  # NO probability

# Check criteria
if prob >= 0.92 and prob <= 0.99:
    if hours_to_close <= 72:
        if liquidity >= 5000:
            → EMIT SIGNAL
```

---

## 4. Risk Check

For each signal, `RiskManager` checks (in order):

| Check | Limit | Reject If |
|-------|-------|-----------|
| Position count | 20 | >= max_positions |
| Duplicate market | — | Already have position |
| Total exposure | $1,000 | Would exceed max |
| Balance | > 0 | Balance <= 0 |
| Drawdown | 15% | Drawdown >= max |

- If ANY fail → REJECT signal (logged to `trade_decisions` with reason)
- If ALL pass → APPROVE signal

---

## 5. Position Sizing

### Methods

| Method | Formula |
|--------|---------|
| **fixed** (default) | `size = fixed_amount_usd` ($25) |
| **kelly** | `size = edge × confidence × kelly_fraction × capital` |
| **volatility_scaled** | `size = target_vol × capital / market_volatility` |

### Constraints

Final size = `min(calculated_size, available_capital, max_position_usd)`

---

## 6. Order Execution

### Order Types

| Type | Behavior | Pros | Cons |
|------|----------|------|------|
| **MARKET** | Cross spread immediately | Guaranteed fill | Pays spread |
| **LIMIT** (default) | Post at mid ± offset | Better price | May not fill |
| **SPREAD** | Post limit, cross after timeout | Best of both | 30s delay |

### Limit Order Pricing

```
BUY:  limit_price = mid_price - (limit_offset_bps / 10000)
SELL: limit_price = mid_price + (limit_offset_bps / 10000)

Example (limit_offset_bps = 50):
  Mid = $0.95
  BUY limit = $0.95 - 0.005 = $0.945
```

### Slippage Model (Paper Trading)

```
base_slippage = 0.1%
size_slippage = (order_size / orderbook_depth) × 0.5%
total_slippage = min(base + size, 2%)  ← capped at 2%
```

---

## 7. Position Tracking

On successful execution:
1. Create `Position` record (strategy, market, entry_price, size, side)
2. Deduct from paper balance
3. Send Telegram alert
4. Log to `trade_decisions` with `executed=true`

### Position Fields

| Field | Description |
|-------|-------------|
| `cost_basis` | What you paid |
| `entry_price` | Price at entry |
| `current_price` | Updated each scan cycle |
| `unrealized_pnl` | (current - entry) × shares |
| `status` | open / closed / expired |

---

## 8. Resolution & Settlement

**Celery Task**: `check_resolutions` (every 15 minutes)

### Resolution Detection

```python
# Query Gamma API for resolved markets
if market.yes_price >= 0.99:
    outcome = "YES"
elif market.yes_price <= 0.01:
    outcome = "NO"
```

### Settlement (Paper Mode)

| Outcome | Your Side | Result |
|---------|-----------|--------|
| YES | Bought YES | Win: get $1.00/share |
| YES | Bought NO | Lose: get $0.00/share |
| NO | Bought YES | Lose: get $0.00/share |
| NO | Bought NO | Win: get $1.00/share |

### Example P&L

```
Bought YES @ $0.95 for $25 → 26.3 shares

If resolves YES:
  Receive: 26.3 × $1.00 = $26.30
  P&L: +$1.30 (5.2% return)

If resolves NO:
  Receive: 26.3 × $0.00 = $0.00
  P&L: -$25.00 (100% loss)
```

---

## 9. Refresh Summary

| Interval | What Happens |
|----------|--------------|
| Every 15 sec | T4 markets (<1h) get new snapshot |
| Every 30 sec | T3 markets (1-4h) get new snapshot |
| Every 30 sec | **EXECUTOR** scans all markets, runs strategies |
| Every 1 min | T2 markets (4-12h) get new snapshot |
| Every 5 min | T1 markets (12-48h) get new snapshot |
| Every 15 min | Check for market resolutions |
| Every hour | Discover new markets, update tiers |
| Daily 9 AM UTC | Telegram daily summary |

---

## 10. Configuration Reference

### config.yaml

```yaml
mode: paper  # or live

settings:
  scan_interval_seconds: 30

risk:
  max_position_usd: 100
  max_total_exposure_usd: 1000
  max_positions: 20
  max_drawdown_pct: 0.15

sizing:
  method: fixed  # fixed | kelly | volatility_scaled
  fixed_amount_usd: 25
  kelly_fraction: 0.25

execution:
  default_order_type: limit  # market | limit | spread
  limit_offset_bps: 50
  spread_timeout_seconds: 30

filters:
  min_liquidity_usd: 1000
  excluded_keywords:
    - celebrity
    - influencer
```

### Strategy Parameters (strategies/*.py)

```python
class MyStrategy(Strategy):
    bet_side = "YES"          # "YES" or "NO"
    min_probability = 0.92
    max_probability = 0.99
    max_hours_to_expiry = 72
    min_liquidity_usd = 5000
    size_usd = 25
```

---

## 11. Database Tables

| Table | Purpose |
|-------|---------|
| `markets` | Market metadata, token IDs, tier |
| `snapshots` | Price/volume snapshots per market |
| `trades` | Real-time trades from WebSocket |
| `positions` | Open/closed trading positions |
| `trade_decisions` | Audit trail of all signals |
| `paper_balances` | Paper trading balance |
| `signals` | Generated signals |
| `executor_trades` | Executed trades |

---

## 12. Flow Diagram

```
Gamma API                    Polymarket CLOB
    │                              │
    ▼                              ▼
┌─────────┐                 ┌─────────────┐
│Discovery│                 │  WebSocket  │
│ (hourly)│                 │  (realtime) │
└────┬────┘                 └──────┬──────┘
     │                             │
     ▼                             ▼
┌─────────────────────────────────────────┐
│              PostgreSQL                  │
│  markets │ snapshots │ trades │ ...     │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│           EXECUTOR (every 30s)          │
│                                         │
│  Scanner → Strategies → Risk → Sizing   │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│         Order Execution                  │
│   Paper: Simulated with slippage        │
│   Live: Real CLOB orders                │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│         Position Tracking               │
│   → Telegram alerts                     │
│   → trade_decisions audit               │
│   → paper_balances update               │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│      Resolution Check (every 15min)     │
│   → Settle positions                    │
│   → Calculate P&L                       │
│   → Update balance                      │
└─────────────────────────────────────────┘
```
