---
description: Generate a trading hypothesis for systematic testing
---

# Hypothesis Generation Mode

Generate a testable trading hypothesis based on structural friction analysis.

## Path Registry

Read `configs/paths.py` for all path definitions. Key paths:
- **experiments**: `/home/theo/polymarket-ml/experiments`
- **ledger**: `/home/theo/polymarket-ml/ledger/insights.jsonl`
- **strategies.yaml**: `/home/theo/polymarket-ml/strategies.yaml`

**Strategy types** (from `STRATEGY_PARAMS`):
- `uncertain_zone`, `no_bias`, `longshot`, `mean_reversion`, `whale_fade`, `flow`, `new_market`

## Database Location

**Project root**: `/home/theo/polymarket-ml`
**Database**: `polymarket_ml` (Polymarket only - NO Kalshi data)

**Data sources**:
- `historical_markets` / `historical_price_snapshots` - Migrated historical data for backtesting
- `markets` / `snapshots` - Live operational data (65+ features, actively collected)

**IMPORTANT**: All CLI commands MUST be run from `/home/theo/polymarket-ml`.
Do NOT access any other database or project directory.

Read /home/theo/polymarket-ml/RESEARCH_LAB.md for full context.

## Argument

`$ARGUMENTS` should be a friction bucket:
- `timing` - Information arrives gradually; markets update slowly
- `liquidity` - Thin markets, volume clustering, overreactions
- `behavioral` - Anchoring, favorite/underdog bias, narrative preference
- `mechanism` - Resolution rules traders misunderstand
- `cross-market` - Same outcome priced in multiple markets

If no argument provided, ask which friction bucket to explore.

## Process

### Step 1: Check the Ledger

```bash
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.ledger search $ARGUMENTS
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.ledger stats
```

Review prior experiments in this friction bucket:
- Don't repeat killed hypotheses
- Build on shipped insights
- Look for unexplored parameter spaces

### Step 2: Generate Experiment ID

```bash
cd /home/theo/polymarket-ml && DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 -m cli.ledger next-id
```

### Step 3: Create spec.md

Create `experiments/<exp_id>/spec.md` using this structure:

```markdown
# Experiment: <exp_id>

## Metadata
- **Created**: <ISO timestamp>
- **Friction Bucket**: <bucket>
- **Status**: pending

## Hypothesis
<One-sentence testable claim with exact parameters>

## Universe Filter
- **Categories**: <list or "all">
- **Min volume 24h**: <USD>
- **Min liquidity**: <USD>
- **Hours to expiry**: <range>

## Holding Period
<exact duration or exit condition>

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
| <param1> | [a, b, c] | <why> |
| <param2> | [x, y, z] | <why> |

**Variants**: <N> total (max 9 recommended)

## Execution Config

Prompt user for these values or use defaults:

### Sizing
- **Method**: kelly | fixed_pct | fixed_amount (default: fixed_pct)
- **Size**: 1% of capital (or $10 fixed)

### Execution
- **Order type**: market | spread | limit (default: market)
- **Min edge after spread**: 3% (default)
- **Max spread**: null (no limit) or specify (e.g., 4%)

### Deployment
- **Allocated USD**: $400 (default)
- **Paper trade first**: Yes (default)
- **Categories**: All or specify list

## Data Requirements
<Which features from the 65+ available are needed>

## Prior Art
<Reference related experiments from ledger>
```

### Step 4: Confirm Next Step

After creating spec.md, output:

```
Created: experiments/<exp_id>/spec.md

Hypothesis: <one-sentence summary>
Friction: <bucket>
Variants: <N>

Kill criteria: Sharpe>0.5, WinRate>51%, Trades>50, PF>1.1, TimeSplit

Next: /test <exp_id>
```

## Hypothesis Guidance by Bucket

**Timing:**
- What information arrives gradually, not discretely?
- Which markets depend on slow aggregation (polls, court cases)?
- Where do prices underreact before official confirmation?

**Liquidity:**
- Where is liquidity thin?
- Does volume cluster at specific times?
- Are there overreactions in thin markets?

**Behavioral:**
- Where do people anchor on round numbers or initial prices?
- Where do they prefer narratives over math?
- Where is there favorite/underdog bias?

**Mechanism:**
- What exactly resolves the market?
- What edge cases exist in resolution criteria?
- Where do traders misunderstand the rules?

**Cross-market:**
- Which markets reference the same outcome indirectly?
- Which market updates first?
- Are there proxy relationships?

## Rules

- NEVER create config.yaml — that's `/test`
- NEVER run any backtest — that's `/test`
- Kill criteria MUST be quantitative, not vague
- If you can't write a one-sentence hypothesis, ask for clarification
- Check the ledger FIRST — don't repeat killed ideas
- **PROMPT for Execution Config** if not specified by user:
  - Sizing method (kelly/fixed_pct/fixed_amount)
  - Order type (market/spread/limit)
  - Allocated USD (default $400)
  - Min edge after spread (default 3%)
- Store execution config in spec.md for `/test` to use
