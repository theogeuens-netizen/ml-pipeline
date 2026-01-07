# Strategy Research Lab

> Systematic strategy research through friction analysis, rigorous backtesting, and knowledge compounding.

## Philosophy

Every viable Polymarket strategy exploits **structural friction**, not cleverness. The research process systematically enumerates frictions, not brainstorms ideas.

**The 5 friction buckets:**

| Bucket | Description | Example Hypotheses |
|--------|-------------|-------------------|
| **timing** | Information arrives gradually; markets update slowly | "Markets 1-4h from close have systematically mispriced NO" |
| **liquidity** | Thin markets, volume clustering, overreactions | "Orderbooks <$1k depth lag price discovery by 5-15min" |
| **behavioral** | Anchoring, narrative preference, favorite/underdog bias | "Weekend YES prices are inflated 3-5% vs weekday" |
| **mechanism** | Resolution rules traders misunderstand | "Markets post-UMA-proposal have reversion patterns" |
| **cross-market** | Same outcome priced in multiple markets | "Crypto markets lag Bitcoin spot by 10-30min" |

---

## Slash Commands

### `/hypothesis <friction_bucket>`

Generate a testable strategy hypothesis.

**Process:**
1. Check `ledger/insights.jsonl` for prior learnings (don't repeat killed ideas)
2. Generate next experiment ID (`exp-NNN`)
3. Create `experiments/<exp_id>/spec.md` with hypothesis, kill criteria, parameters

**Usage:**
```
/hypothesis timing
/hypothesis behavioral
```

---

### `/test <exp_id>`

Run backtest with robustness checks.

**Process:**
1. Read `experiments/<exp_id>/spec.md`
2. Create `experiments/<exp_id>/config.yaml`
3. Run backtest for each parameter variant
4. Run robustness checks (time-split, liquidity-split, category-split)
5. Generate `experiments/<exp_id>/results.json`

**Usage:**
```
/test exp-001
```

---

### `/verdict <exp_id>`

Evaluate results and record learnings.

**Process:**
1. Read spec.md and results.json
2. Evaluate against kill criteria (ANY fail = KILL)
3. Decision: KILL / ITERATE / SHIP
4. Create `experiments/<exp_id>/verdict.md`
5. Append to `ledger/insights.jsonl`

**Usage:**
```
/verdict exp-001
```

---

## Folder Structure

```
experiments/
└── exp-001/
    ├── spec.md       # Hypothesis + kill criteria (BEFORE any test)
    ├── config.yaml   # Backtest configuration
    ├── results.json  # Backtest + robustness output
    └── verdict.md    # KILL / ITERATE / SHIP decision

ledger/
└── insights.jsonl    # Accumulated learnings (append-only)
```

---

## Kill Criteria

These are the minimum bars for a strategy to be considered viable:

| Metric | Kill if below | Rationale |
|--------|---------------|-----------|
| Sharpe (annualized) | 0.5 | Risk-adjusted return too low |
| Win rate | 51% | Must beat random |
| Sample size | 50 trades | Statistical significance |
| Profit factor | 1.1 | Gross profit / gross loss |
| Time-split consistency | Both halves positive | Overfitting detection |

A strategy that clears these bars is NOT necessarily good—it's just not obviously dead.

---

## Data Schemas

### spec.md Template

```markdown
# Experiment: exp-001

## Metadata
- **Created**: 2024-12-21T15:30:00Z
- **Friction Bucket**: timing
- **Status**: pending

## Hypothesis
[One-sentence testable claim with exact parameters. Not "momentum works" but
"If price increases by X% within Y hours AND volume exceeds Z, expected return
over next N hours is positive."]

## Universe Filter
- **Categories**: [ESPORTS] or "all"
- **Min volume 24h**: 500
- **Min liquidity**: 1000
- **Hours to expiry**: 1-4

## Holding Period
Hold until resolution (expiry-based strategy)

## Kill Criteria
| Metric | Threshold |
|--------|-----------|
| Sharpe | < 0.5 |
| Win Rate | < 51% |
| Trades | < 50 |
| Profit Factor | < 1.1 |
| Time Consistency | Fail |

## Parameters to Test
| Parameter | Values | Rationale |
|-----------|--------|-----------|
| min_hours | [1, 2, 3] | Test timing sensitivity |
| threshold | [0.65, 0.70, 0.75] | Test edge threshold |

**Variants**: 3 x 3 = 9 total (max recommended)

## Data Requirements
Features needed from 65+ available:
- price, hours_to_close, liquidity, volume_24h, category_l1

## Prior Art
- exp-000: Similar hypothesis killed due to sample size
- Ledger insight: "ESPORTS has 71.5% historical NO rate"
```

---

### config.yaml Schema

```yaml
experiment_id: exp-001
created_at: 2024-12-21T15:35:00Z

backtest:
  initial_capital: 1000
  stake_mode: fixed
  stake_per_bet: 10
  cost_per_bet: 0.01
  max_position_pct: 0.25

filters:
  categories: [ESPORTS]
  min_volume_24h: 500
  min_liquidity: 1000
  hours_to_expiry:
    min: 1
    max: 4

strategy:
  type: no_bias
  side: NO

variants:
  - id: v1
    min_hours: 1
    historical_no_rate: 0.65
  - id: v2
    min_hours: 1
    historical_no_rate: 0.70
  # ... up to 9 variants

robustness:
  time_split: true
  category_split: false
  liquidity_split: true
```

---

### results.json Schema

```json
{
  "experiment_id": "exp-001",
  "run_at": "2024-12-21T16:00:00Z",
  "data_range": {
    "start": "2024-10-01",
    "end": "2024-12-20",
    "markets_tested": 342,
    "resolved_markets": 287
  },
  "variants": [
    {
      "id": "v1",
      "params": {"min_hours": 1, "threshold": 0.65},
      "metrics": {
        "sharpe": 0.82,
        "win_rate": 0.58,
        "profit_factor": 1.34,
        "total_trades": 67,
        "total_pnl": 142.30,
        "max_drawdown_pct": 8.2
      },
      "robustness": {
        "time_split": {
          "first_half": {"sharpe": 0.75, "win_rate": 0.56, "trades": 31},
          "second_half": {"sharpe": 0.89, "win_rate": 0.61, "trades": 36},
          "pass": true
        },
        "liquidity_split": {
          "high_liquidity": {"sharpe": 0.91, "trades": 42},
          "low_liquidity": {"sharpe": 0.65, "trades": 25},
          "pass": true
        }
      },
      "kill_criteria": {
        "sharpe": {"threshold": 0.5, "actual": 0.82, "pass": true},
        "win_rate": {"threshold": 0.51, "actual": 0.58, "pass": true},
        "trades": {"threshold": 50, "actual": 67, "pass": true},
        "time_split": {"pass": true}
      }
    }
  ],
  "best_variant": "v3",
  "summary": {
    "all_passed_kill_criteria": true,
    "robustness_pass_rate": 0.89
  }
}
```

---

### Ledger Entry (insights.jsonl)

```json
{
  "id": "exp-001",
  "timestamp": "2024-12-21T16:30:00Z",
  "friction_bucket": "timing",
  "status": "ship",
  "hypothesis": "ESPORTS NO 1-4h window has 5-10% edge",
  "result": {
    "sharpe": 0.82,
    "win_rate": 0.58,
    "sample_size": 67,
    "robustness": "passed"
  },
  "learnings": [
    "Edge strongest at 1h, decays to 4h",
    "High liquidity markets have stronger edge",
    "historical_no_rate=0.75 threshold optimal"
  ],
  "kill_reason": null,
  "action": "Add esports_no_4h to strategies.yaml",
  "tags": ["esports", "no_bias", "timing"]
}
```

---

## CLI Tools

```bash
# Ledger queries
python3 -m cli.ledger stats                    # Summary statistics
python3 -m cli.ledger search timing            # By friction bucket
python3 -m cli.ledger search --status ship     # By status
python3 -m cli.ledger recent 10                # Last N entries
python3 -m cli.ledger next-id                  # Get next exp ID

# Robustness checks
python3 -m cli.robustness experiments/exp-001/config.yaml --all
python3 -m cli.robustness --strategy esports_no_1h --days 30 --all
python3 -m cli.robustness experiments/exp-001/config.yaml --time-split --liquidity-split

# Backtesting
python3 -m cli.backtest esports_no_1h --days 30
python3 -m cli.backtest --category ESPORTS --side NO --days 60
```

---

## Available Features (65+)

When designing hypotheses, these features are available in snapshot data:

**Price (5)**
- `price` - Current YES price (0-1)
- `best_bid`, `best_ask` - Orderbook quotes
- `spread` - Bid-ask spread
- `last_trade_price` - Most recent trade

**Momentum (3)**
- `price_change_1d`, `price_change_1w`, `price_change_1m`

**Volume (4)**
- `volume_total`, `volume_24h`, `volume_1w`
- `liquidity` - Current market liquidity

**Orderbook Depth (8)**
- `bid_depth_5/10/20/50` - Bid depth at various levels
- `ask_depth_5/10/20/50` - Ask depth at various levels

**Orderbook Derived (7)**
- `bid_levels`, `ask_levels` - Number of price levels
- `book_imbalance` - (bid_depth - ask_depth) / total
- `bid_wall_price/size`, `ask_wall_price/size` - Largest orders

**Trade Flow (9)**
- `trade_count_1h`, `buy_count_1h`, `sell_count_1h`
- `volume_1h`, `buy_volume_1h`, `sell_volume_1h`
- `avg_trade_size_1h`, `max_trade_size_1h`
- `vwap_1h` - Volume-weighted average price

**Whale Metrics (8)**
- `whale_count_1h`, `whale_volume_1h`
- `whale_buy_volume_1h`, `whale_sell_volume_1h`
- `whale_net_flow_1h` - Net whale buying
- `whale_buy_ratio_1h` - Whale buys / total whale volume
- `time_since_whale` - Seconds since last whale trade
- `pct_volume_from_whales` - Whale % of total volume

**Context (3)**
- `hours_to_close` - Hours until market resolution
- `day_of_week` - 0=Monday, 6=Sunday
- `hour_of_day` - 0-23 UTC

---

## Anti-Overfit Rules

1. **Kill criteria before looking at results** — no exceptions
2. **No parameter fishing** — define parameter ranges in spec.md, test once
3. **Robustness is mandatory** — time/category/liquidity splits before any verdict
4. **Track multiple testing** — ledger shows how many variants you've tried per friction bucket
5. **Sharpe > 2.0 is suspicious** — likely overfit or survivorship bias
6. **9 variants max** — 2 params × 3 values prevents excessive exploration

---

## Behavioral Notes

- Never say "this looks promising" without numbers
- Failed experiments are valuable—always extract learnings
- Prefer ugly tests that run fast over precise tests that take days
- If you can't define the signal in one sentence, you're not ready to test
- When in doubt, kill the idea and move on
- Every experiment ends with a verdict—no abandoned experiments

---

## Verdict Criteria

### KILL
- At least one kill criterion failed
- Robustness fundamentally broken (edge only in one time half)
- Extract the learning (why did it fail?)
- Note what adjacent idea might work

### ITERATE
- Kill criteria passed but robustness weak
- Clear improvement is obvious
- Define the SPECIFIC next experiment (narrow, testable change)
- Must have clear rationale, not "try more parameters"

### SHIP
- All kill criteria passed
- Robustness solid (time-split, liquidity-split pass)
- Define risk limits for paper trading
- Set review criteria (when to re-evaluate)
- Add to `strategies.yaml` with appropriate allocation

---

## Example Workflow

```
# 1. Generate hypothesis
/hypothesis timing

# Creates: experiments/exp-001/spec.md
# Review the spec, adjust if needed

# 2. Run backtest with robustness
/test exp-001

# Creates: experiments/exp-001/config.yaml
# Creates: experiments/exp-001/results.json
# Review results

# 3. Make decision
/verdict exp-001

# Creates: experiments/exp-001/verdict.md
# Appends to: ledger/insights.jsonl

# If SHIP: Add to strategies.yaml
# If ITERATE: /hypothesis <bucket> with learnings
# If KILL: Move on, learnings recorded
```
