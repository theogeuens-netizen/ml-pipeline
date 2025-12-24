# Polymarket Trading System - User Guide

> Complete workflow from strategy research to live paper trading.

---

## Quick Start

```
/hypothesis timing     # Generate a strategy hypothesis
/test exp-001          # Run backtest with robustness checks
/verdict exp-001       # Make KILL/ITERATE/SHIP decision
/trading               # Deploy and monitor strategies
```

---

## The Big Picture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         STRATEGY LIFECYCLE                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   RESEARCH PHASE                    TRADING PHASE                        │
│   ──────────────                    ─────────────                        │
│                                                                          │
│   /hypothesis ───► /test ───► /verdict                                  │
│        │              │           │                                      │
│        ▼              ▼           ▼                                      │
│   spec.md      config.yaml   verdict.md                                 │
│                results.json   + ledger                                  │
│                                   │                                      │
│                                   ├── KILL → Learn, move on             │
│                                   ├── ITERATE → Refine hypothesis       │
│                                   └── SHIP ───────────────┐             │
│                                                           ▼             │
│                                                    /trading ship        │
│                                                           │             │
│                                                           ▼             │
│                                                   strategies.yaml       │
│                                                           │             │
│                                                           ▼             │
│                                                   Paper Trading         │
│                                                           │             │
│                                                           ▼             │
│                                                   Monitor & Refine      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Research Lab

The Research Lab is where you develop and validate trading strategies before risking any capital.

### Step 1: Generate a Hypothesis

```
/hypothesis <friction_bucket>
```

**Friction buckets** are categories of market inefficiencies:

| Bucket | What It Means | Example |
|--------|---------------|---------|
| `timing` | Information arrives gradually | "Markets 1-4h from close underprice NO" |
| `liquidity` | Thin markets misprice | "Low liquidity markets overreact to trades" |
| `behavioral` | Human biases | "Weekend prices are inflated 3-5%" |
| `mechanism` | Resolution confusion | "Post-UMA-proposal markets have patterns" |
| `cross-market` | Arbitrage | "Crypto markets lag Bitcoin spot" |

**What happens:**
1. Claude checks the ledger for prior experiments (no repeating dead ideas)
2. Generates next experiment ID (exp-001, exp-002, etc.)
3. Creates `experiments/<exp_id>/spec.md` with:
   - Testable hypothesis
   - Universe filter (which markets)
   - Kill criteria (when to abandon)
   - Parameters to test

**Example output:**
```
Created: experiments/exp-001/spec.md

Hypothesis: ESPORTS NO in 1-4h window has 5-10% edge
Friction: timing
Variants: 9

Kill criteria: Sharpe>0.5, WinRate>51%, Trades>50, PF>1.1, TimeSplit

Next: /test exp-001
```

---

### Step 2: Run the Backtest

```
/test <exp_id>
```

**What happens:**
1. Reads your hypothesis from spec.md
2. Creates `config.yaml` with backtest parameters
3. Runs backtest for each parameter variant
4. Runs robustness checks:
   - **Time split**: Does edge exist in both first and second half?
   - **Liquidity split**: Does it work in both liquid and illiquid markets?
   - **Category split**: Does it work across categories?
5. Saves results to `results.json`

**Example output:**
```
=== TEST RESULTS: exp-001 ===

Variant v1 (min_hours=1, threshold=0.65):
  Sharpe: 0.82 | Win: 58% | Trades: 67 | PF: 1.34
  Time split: PASS (0.75 / 0.89)
  Liquidity split: PASS (0.91 / 0.65)
  Kill criteria: 4/4 PASS

Best variant: v3 (Sharpe=0.95, Win=62%)

Next: /verdict exp-001
```

---

### Step 3: Make the Decision

```
/verdict <exp_id>
```

**What happens:**
1. Evaluates results against kill criteria
2. Makes a decision:

| Decision | When | What Next |
|----------|------|-----------|
| **KILL** | Any kill criterion failed | Extract learning, move on |
| **ITERATE** | Passed but robustness weak | Define specific next experiment |
| **SHIP** | All passed + robust | Deploy to paper trading |

3. Creates `verdict.md` documenting the decision
4. Appends learning to `ledger/insights.jsonl`

**Kill Criteria (strategy is dead if ANY fail):**
- Sharpe ratio < 0.5
- Win rate < 51%
- Sample size < 50 trades
- Profit factor < 1.1
- Time-split inconsistent (edge only in one half)

---

## Phase 2: Deployment

Once a strategy is shipped, deploy it to paper trading.

### Option A: Via Trading CLI

```
/trading
> ship exp-001
```

### Option B: Via Command Line

```bash
python3 -m cli.ship exp-001 --preview   # See what will be added
python3 -m cli.ship exp-001 --apply     # Apply to strategies.yaml
```

**What happens:**
1. Reads experiment files (spec.md, results.json, verdict.md)
2. Extracts best variant parameters
3. Generates YAML entry
4. Appends to `strategies.yaml`
5. Executor auto-reloads (within 30 seconds)

**Example deployment:**
```yaml
# Added to strategies.yaml:
no_bias:
  - name: esports_no_4h          # from exp-001
    category: ESPORTS
    historical_no_rate: 0.75
    min_hours: 1
    max_hours: 4
    min_liquidity: 1000
    # Experiment: exp-001
    # Shipped: 2024-12-21
```

---

## Phase 3: Paper Trading

### Entering Trading Mode

```
/trading
```

**Available commands:**

| Command | What It Does |
|---------|--------------|
| `status` | Balance, positions, P&L, recent decisions |
| `strategies` | List all deployed strategies |
| `leaderboard` | Strategy performance ranking |
| `debug <name>` | Why isn't a strategy trading? |
| `research` | View recent experiments |
| `ship <exp_id>` | Deploy a shipped experiment |
| `adjust` | Change strategy parameters |
| `logs` | View recent errors |
| `exit` | Leave trading mode |

### Monitoring Performance

**View current status:**
```
> status

MODE: PAPER

BALANCE
  Current: $10,142.30
  Starting: $10,000.00
  P&L: +$142.30 (+1.4%)

TOP STRATEGIES BY P&L
| Strategy          | P&L     | Trades | Win% |
|-------------------|---------|--------|------|
| esports_no_1h     | +$52.30 | 23     | 65%  |
| longshot_yes_v1   | +$31.20 | 15     | 58%  |
```

**View leaderboard:**
```
> leaderboard

Strategy                   P&L    Return  Win%  Sharpe  MaxDD  Trades
--------------------------------------------------------------------
esports_no_1h           +$52.30   +13.1%   65%   +1.24   5.2%      23
mean_reversion_2sigma   +$31.20    +7.8%   58%   +0.89   3.1%      15
```

**Debug a strategy:**
```
> debug esports_no_1h

PARAMETERS:
  category: ESPORTS
  min_hours: 0.1
  max_hours: 1
  historical_no_rate: 0.715

FUNNEL (why opportunities are filtered):
  1000 total → 45 (ESPORTS) → 12 (time window) → 3 (with edge)

RECENT DECISIONS:
  2024-12-20T10:30:00 | BUY | REJECTED: max_positions
  2024-12-20T09:15:00 | BUY | EXECUTED
```

---

## File Structure

```
polymarket-ml/
├── experiments/                    # Strategy research
│   └── exp-001/
│       ├── spec.md                # Hypothesis (BEFORE testing)
│       ├── config.yaml            # Backtest parameters
│       ├── results.json           # Test results
│       └── verdict.md             # KILL/ITERATE/SHIP decision
│
├── ledger/
│   └── insights.jsonl             # Accumulated learnings
│
├── strategies.yaml                # Deployed strategies
│
├── .claude/commands/              # Slash commands
│   ├── hypothesis.md              # /hypothesis
│   ├── test.md                    # /test
│   ├── verdict.md                 # /verdict
│   └── trading.md                 # /trading
│
└── cli/                           # Command-line tools
    ├── ledger.py                  # Query the ledger
    ├── robustness.py              # Run robustness checks
    ├── ship.py                    # Deploy experiments
    └── debug.py                   # Debug strategies
```

---

## CLI Reference

### Ledger Commands

```bash
python3 -m cli.ledger stats              # Summary statistics
python3 -m cli.ledger search timing      # Search by friction bucket
python3 -m cli.ledger search --status ship   # Find shipped experiments
python3 -m cli.ledger recent 10          # Last 10 entries
python3 -m cli.ledger next-id            # Get next experiment ID
```

### Robustness Commands

```bash
python3 -m cli.robustness exp-001 --all                    # All checks
python3 -m cli.robustness exp-001 --time-split             # Time split only
python3 -m cli.robustness --strategy esports_no_1h --days 30   # By strategy
```

### Deployment Commands

```bash
python3 -m cli.ship exp-001 --preview    # See what will be added
python3 -m cli.ship exp-001 --apply      # Apply to strategies.yaml
```

### Debug Commands

```bash
python3 -m cli.debug                     # Show leaderboard
python3 -m cli.debug esports_no_1h       # Debug specific strategy
python3 -m cli.debug --funnel            # Funnel analysis for all
```

---

## Best Practices

### Research Phase

1. **Check the ledger first** - Don't repeat killed ideas
2. **Define kill criteria BEFORE testing** - No moving goalposts
3. **Max 9 variants** - 2 params x 3 values prevents overfitting
4. **Time-split is mandatory** - Edge must exist in both halves
5. **Sharpe > 2.0 is suspicious** - Likely overfit

### Deployment Phase

1. **Start small** - Default $400 allocation per strategy
2. **Monitor the funnel** - Use `debug <name>` to see why signals filter
3. **Watch drawdown** - Max 15% before trading pauses
4. **Review shipped strategies** - Re-evaluate if conditions change

### Mental Models

- **Failed experiments are valuable** - Always extract learnings
- **Every idea needs a verdict** - No abandoned experiments
- **Numbers over intuition** - Never say "looks promising" without data
- **Kill quickly** - Dead ideas drain attention

---

## Common Workflows

### "I have a new trading idea"

```
/hypothesis timing          # Pick most relevant friction bucket
# Review spec.md, adjust if needed
/test exp-001               # Run backtest
/verdict exp-001            # Make decision
# If SHIP: /trading → ship exp-001
```

### "Why isn't my strategy trading?"

```
/trading
> debug <strategy_name>
# Look at funnel: where are opportunities filtering out?
# Check recent decisions: are signals being rejected?
```

### "How are my strategies performing?"

```
/trading
> leaderboard
# Or for details:
> status
```

### "I want to adjust a strategy"

```
/trading
> adjust
# Edit strategies.yaml directly
# Executor auto-reloads on next scan
```

---

## Quick Reference Card

| Task | Command |
|------|---------|
| Generate hypothesis | `/hypothesis <bucket>` |
| Run backtest | `/test <exp_id>` |
| Make decision | `/verdict <exp_id>` |
| Enter trading mode | `/trading` |
| Check status | `/trading` → `status` |
| View strategies | `/trading` → `strategies` |
| Deploy experiment | `/trading` → `ship <exp_id>` |
| Debug strategy | `/trading` → `debug <name>` |
| View research | `/trading` → `research` |
| Exit trading mode | `/trading` → `exit` |

---

## Related Documentation

| Document | Purpose |
|----------|---------|
| `RESEARCH_LAB.md` | Full research methodology reference |
| `TRADING_CLI.md` | Complete trading CLI reference |
| `CLAUDE.md` | AI working memory and session context |
| `ARCHITECTURE.md` | System architecture overview |
| `PIPELINE.md` | Trading pipeline flow |

---

*Happy trading!*
