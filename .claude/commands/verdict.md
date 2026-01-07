---
description: Evaluate experiment results and record learnings to ledger
---

# Verdict Mode

Force a decision on an experiment and update the ledger.

## Database Location

**Project root**: `/home/theo/polymarket-ml`
**Database**: `polymarket_ml` (Polymarket only - NO Kalshi data)

**Data sources**:
- `historical_markets` / `historical_price_snapshots` - Migrated historical data
- `markets` / `snapshots` - Live operational data (65+ features, actively collected)

**IMPORTANT**: All CLI commands MUST be run from `/home/theo/polymarket-ml`.
Do NOT access any other database or project directory.

Read /home/theo/polymarket-ml/RESEARCH_LAB.md for full context.

## Argument

`$ARGUMENTS` should be an experiment ID (e.g., `exp-001`).

## Prerequisites

Both files MUST exist:
- `experiments/$ARGUMENTS/spec.md`
- `experiments/$ARGUMENTS/results.json`

If missing, refuse and explain what's needed.

## Process

### Step 1: Load Context

Read:
- `experiments/<exp_id>/spec.md` (hypothesis, kill criteria)
- `experiments/<exp_id>/results.json` (performance, robustness)
- `ledger/insights.jsonl` (prior learnings in same friction bucket)

### Step 2: Evaluate Kill Criteria

For each kill criterion in spec.md, check the best variant's results.

**The rule:** If ANY kill criterion failed, verdict is KILL. No exceptions.
No "but the Sharpe is close" or "maybe with different parameters."
Dead is dead.

Create evaluation table:
```
| Criterion | Threshold | Actual | Status |
|-----------|-----------|--------|--------|
| Sharpe | >= 0.5 | X.XX | PASS/FAIL |
| Win rate | >= 51% | XX.X% | PASS/FAIL |
| Trades | >= 50 | NNN | PASS/FAIL |
| Profit Factor | >= 1.1 | X.XX | PASS/FAIL |
| Time split | consistent | yes/no | PASS/FAIL |
```

### Step 3: Assess Robustness

Even if kill criteria pass, evaluate:
- **Time split**: Does edge exist in BOTH halves?
- **Liquidity split**: Does it only work in illiquid markets?
- **Category split**: Is it driven by one category only?

Robustness failures don't auto-kill but heavily inform the verdict.

### Step 4: Make the Decision

**KILL** — At least one kill criterion failed OR robustness fundamentally broken
- Extract the learning (why did it fail?)
- Note what adjacent idea might work
- Record in ledger with kill_reason

**ITERATE** — Kill criteria passed but robustness weak OR clear improvement obvious
- Define the SPECIFIC next experiment
- Must be a narrow, testable change
- Not "try more parameters"

**SHIP** — Kill criteria passed AND robustness solid
- Define risk limits for paper trading
- Set review criteria (when to re-evaluate)
- Specify addition to strategies.yaml

### Step 5: Create verdict.md

Create `experiments/<exp_id>/verdict.md`:

```markdown
# Verdict: <exp_id>

## Decision: <KILL | ITERATE | SHIP>

## Kill Criteria Results

| Criterion | Threshold | Actual | Status |
|-----------|-----------|--------|--------|
| Sharpe | >= 0.5 | X.XX | PASS/FAIL |
| Win rate | >= 51% | XX.X% | PASS/FAIL |
| Trades | >= 50 | NNN | PASS/FAIL |
| Profit Factor | >= 1.1 | X.XX | PASS/FAIL |
| Time split | consistent | yes/no | PASS/FAIL |

## Robustness Assessment

**Time split:** <1-2 sentence assessment>
**Liquidity split:** <1-2 sentence assessment>
**Category split:** <1-2 sentence assessment if applicable>

## Reasoning

<2-4 sentences explaining the decision. Be specific, cite numbers.>

## <If KILL: Learning>

**Why it failed:** <specific reason>

**What this tells us:** <generalizable insight>

**Adjacent idea to test:** <if any>

## <If ITERATE: Next Experiment>

**Next Experiment ID:** exp-XXX

**Change:** <specific, narrow change>

**Why this might help:** <reasoning>

## <If SHIP: Deployment Plan>

**Strategy name:** <for strategies.yaml>

**Risk limits:**
- Max position size: $XX
- Categories: <allowed list>

**Paper trade duration:** <N weeks>

**Review trigger:** Re-evaluate if <condition>
```

### Step 6: Update Ledger

Append to `ledger/insights.jsonl`:

```json
{
  "id": "<exp_id>",
  "timestamp": "<ISO date>",
  "friction_bucket": "<from spec>",
  "status": "<kill|iterate|ship>",
  "hypothesis": "<one-sentence summary>",
  "result": {
    "sharpe": <number>,
    "win_rate": <number>,
    "sample_size": <number>,
    "robustness": "<passed|partial|failed>"
  },
  "learnings": [
    "<insight 1>",
    "<insight 2>"
  ],
  "kill_reason": "<if KILL, why; else null>",
  "action": "<next step or 'paper trade' or null>",
  "tags": ["<tag1>", "<tag2>"]
}
```

### Step 7: Output Summary

```
=== VERDICT: <exp_id> ===

Decision: <KILL | ITERATE | SHIP>

Kill criteria: X/Y PASS
Robustness: <pass rate>%

<2-3 sentence summary of reasoning>

<If KILL>
Learning: <key insight>
Logged to ledger.

<If ITERATE>
Next: Create exp-XXX with <specific change>

<If SHIP>
Ready for paper trading:
- Add to strategies.yaml as: <strategy_name>
- Max position: $XX
- Review after: <N weeks>
```

## Rules

- NEVER soften a KILL — if kill criteria failed, it's dead
- NEVER SHIP without time-split passing
- NEVER ITERATE without defining the specific next experiment
- ALWAYS update the ledger — this is how knowledge compounds
- Be honest about uncertainty but still make a decision
- Extract learnings even from killed experiments
