---
description: Run backtest with robustness checks on an experiment
---

# Test Execution Mode

Run comprehensive backtest on a hypothesis specification.

## Path Registry

Read `configs/paths.py` for all path definitions. Key paths:
- **experiments**: `/home/theo/polymarket-ml/experiments`
- **strategies.yaml**: `/home/theo/polymarket-ml/strategies.yaml`
- **config schema**: `configs/schemas.py`

**Strategy types** (from `STRATEGY_PARAMS`):
- `uncertain_zone`, `no_bias`, `longshot`, `mean_reversion`, `whale_fade`, `flow`, `new_market`

## Database Location

**Project root**: `/home/theo/polymarket-ml`
**Database**: `polymarket_ml` (Polymarket only - NO Kalshi data)

**Data sources**:
- `historical_markets` / `historical_price_snapshots` - Migrated historical data (default for backtesting)
- `markets` / `snapshots` - Live operational data with 65+ features (use `--live` flag when available)

**IMPORTANT**: All CLI commands MUST be run from `/home/theo/polymarket-ml`.
Do NOT access any other database or project directory.

Read /home/theo/polymarket-ml/RESEARCH_LAB.md for full context.

## Argument

`$ARGUMENTS` should be an experiment ID (e.g., `exp-001`).

If no argument provided, list available experiments:
```bash
ls -la /home/theo/polymarket-ml/experiments/
```

## Prerequisites

- `experiments/$ARGUMENTS/spec.md` MUST exist
- If not, refuse and explain: "Run /hypothesis first to create spec.md"

## Process

### Step 1: Read Spec

Read `experiments/<exp_id>/spec.md` and extract:
- Hypothesis
- Universe filter (categories, volume, time-to-expiry)
- Kill criteria thresholds
- Parameters to test

### Step 2: Generate config.yaml

Read the Execution Config section from spec.md to populate the deployment section.
If spec.md lacks Execution Config, use defaults from `configs/paths.py:DEPLOYMENT_FIELDS`.

Create `experiments/<exp_id>/config.yaml`:

```yaml
experiment_id: <exp_id>
created_at: <ISO timestamp>

strategy_type: <inferred from hypothesis>
strategy_side: <YES or NO>

backtest:
  initial_capital: 1000
  stake_mode: fixed
  stake_per_bet: 10
  cost_per_bet: 0.0
  max_position_pct: 0.25

# NEW: Deployment section - flows to live trading
deployment:
  allocated_usd: 400           # From spec.md Execution Config
  order_type: market           # From spec.md Execution Config
  size_pct: 0.01               # From spec.md Execution Config
  min_edge_after_spread: 0.03  # From spec.md Execution Config
  max_spread: null             # From spec.md Execution Config
  paper_trade: true            # From spec.md Execution Config

filters:
  categories: <from spec>
  min_volume_24h: <from spec>
  min_liquidity: <from spec>
  hours_min: <from spec>
  hours_max: <from spec>

variants:
  - id: v1
    name: <descriptive_name>
    <param1>: <value1>
    <param2>: <value1>
  # ... generate cartesian product of parameters

robustness:
  time_split: true
  category_split: <true if multi-category>
  liquidity_split: true

kill_criteria:
  sharpe: 0.5
  win_rate: 0.51
  trades: 50
  profit_factor: 1.1
```

### Step 2b: Validate config.yaml

After generating config.yaml, validate it:

```bash
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m configs.validate experiments/<exp_id>/config.yaml
```

If validation fails, fix the errors before proceeding.

### Step 3: Run Backtest

For each variant, run backtest using the existing CLI:

```bash
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.backtest --category <cat> --side <side> --days 60 --capital 1000
```

Collect metrics for each variant:
- Sharpe ratio
- Win rate
- Profit factor
- Total trades
- Total P&L
- Max drawdown

### Step 4: Run Robustness Checks

```bash
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.robustness experiments/<exp_id>/config.yaml --all
```

Or run specific checks:
```bash
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.robustness experiments/<exp_id>/config.yaml --time-split
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.robustness experiments/<exp_id>/config.yaml --liquidity-split
```

### Step 5: Generate results.json

Create `experiments/<exp_id>/results.json`:

```json
{
  "experiment_id": "<exp_id>",
  "run_at": "<ISO timestamp>",
  "data_range": {
    "start": "<date>",
    "end": "<date>",
    "markets_tested": <N>,
    "resolved_markets": <N>
  },
  "variants": [
    {
      "id": "v1",
      "params": {...},
      "metrics": {
        "sharpe": <N>,
        "win_rate": <N>,
        "profit_factor": <N>,
        "total_trades": <N>,
        "total_pnl": <N>,
        "max_drawdown_pct": <N>
      },
      "robustness": {
        "time_split": {"first_half": {...}, "second_half": {...}, "pass": <bool>},
        "liquidity_split": {"high": {...}, "low": {...}, "pass": <bool>}
      },
      "kill_criteria": {
        "sharpe": {"threshold": 0.5, "actual": <N>, "pass": <bool>},
        "win_rate": {"threshold": 0.51, "actual": <N>, "pass": <bool>},
        "trades": {"threshold": 50, "actual": <N>, "pass": <bool>},
        "time_split": {"pass": <bool>}
      }
    }
  ],
  "best_variant": "<id>",
  "summary": {
    "all_passed_kill_criteria": <bool>,
    "robustness_pass_rate": <N>
  }
}
```

### Step 6: Output Summary

```
=== TEST RESULTS: <exp_id> ===

Variant v1 (param1=X, param2=Y):
  Sharpe: 0.82 | Win: 58% | Trades: 67 | PF: 1.34
  Time split: PASS (0.75 / 0.89)
  Liquidity split: PASS (0.91 / 0.65)
  Kill criteria: 4/4 PASS

[... all variants ...]

Best variant: v3 (Sharpe=0.95, Win=62%)
Results saved: experiments/<exp_id>/results.json

Next: /verdict <exp_id>
```

## Rules

- NEVER skip robustness checks
- NEVER modify spec.md (that's the pre-registered hypothesis)
- Run ALL parameter variants, not just the first one
- If backtest fails due to insufficient data, note in results.json
- Time split is MANDATORY for any strategy to proceed
- **ALWAYS include deployment section** in config.yaml (read from spec.md Execution Config)
- **ALWAYS validate config.yaml** using `python3 -m configs.validate` before proceeding
- Store complete config for reproducibility (backtest + deployment + filters)
