---
description: Run backtest with robustness checks on an experiment
---

# Test Execution Mode

Run comprehensive backtest on a hypothesis specification.

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

Create `experiments/<exp_id>/config.yaml`:

```yaml
experiment_id: <exp_id>
created_at: <ISO timestamp>

backtest:
  initial_capital: 1000
  stake_mode: fixed
  stake_per_bet: 10
  cost_per_bet: 0.0
  max_position_pct: 0.25

filters:
  categories: <from spec>
  min_volume_24h: <from spec>
  min_liquidity: <from spec>
  hours_to_expiry:
    min: <from spec>
    max: <from spec>

strategy:
  type: <inferred from hypothesis>
  side: <YES or NO>

variants:
  - id: v1
    <param1>: <value1>
    <param2>: <value1>
  # ... generate cartesian product of parameters

robustness:
  time_split: true
  category_split: <true if multi-category>
  liquidity_split: true
```

### Step 3: Run Backtest

For each variant, run backtest using the existing CLI:

```bash
cd /home/theo/polymarket-ml && python3 -m cli.backtest --category <cat> --side <side> --days 60 --capital 1000
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
cd /home/theo/polymarket-ml && python3 -m cli.robustness experiments/<exp_id>/config.yaml --all
```

Or run specific checks:
```bash
cd /home/theo/polymarket-ml && python3 -m cli.robustness experiments/<exp_id>/config.yaml --time-split
cd /home/theo/polymarket-ml && python3 -m cli.robustness experiments/<exp_id>/config.yaml --liquidity-split
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
