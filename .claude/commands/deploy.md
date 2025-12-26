---
description: Deploy SHIP-verdicted experiments to strategies.yaml
---

# Deploy Mode

Deploy one or more SHIP-verdicted experiments to live trading.

## Path Registry

Read `configs/paths.py` for all path definitions. Key paths:
- **experiments**: `/home/theo/polymarket-ml/experiments`
- **strategies.yaml**: `/home/theo/polymarket-ml/strategies.yaml`
- **ledger**: `/home/theo/polymarket-ml/ledger/insights.jsonl`

**Strategy types** (from `STRATEGY_PARAMS`):
- `uncertain_zone`, `no_bias`, `longshot`, `mean_reversion`, `whale_fade`, `flow`, `new_market`

**IMPORTANT**: All CLI commands MUST be run from `/home/theo/polymarket-ml`.

## Arguments

`$ARGUMENTS` should be one or more of:
- `exp-001` - Deploy best variant from experiment
- `exp-002:v3` - Deploy specific variant
- `exp-002:v3,v6` - Deploy multiple variants from same experiment

Examples:
```
/deploy exp-001
/deploy exp-002:v3 exp-002:v6
/deploy exp-001 exp-002:v3
```

If no arguments provided, list recent SHIP-verdicted experiments.

## Process

### Step 1: List Recent Experiments (if no args)

```bash
cd /home/theo/polymarket-ml && python3 -m cli.ledger search --status SHIP
```

### Step 2: Validate Experiments

For each experiment specified:

1. Check `experiments/<exp_id>/verdict.md` exists
2. Verify verdict is SHIP (not KILL or ITERATE)
3. Load `experiments/<exp_id>/config.yaml`
4. Load `experiments/<exp_id>/results.json` for metrics

```bash
cd /home/theo/polymarket-ml && cat experiments/<exp_id>/verdict.md | head -20
```

If verdict is not SHIP, refuse and explain why.

### Step 3: Extract Deployment Configs

For each experiment/variant:

1. Read `config.yaml` deployment section
2. Extract variant params from config.yaml or results.json
3. Generate strategy name: `<type>_<category>_<time_window>_<exp_id>`

Example strategy name: `uncertain_zone_12h_exp002_v3`

### Step 4: Validate Strategy Params

```bash
cd /home/theo/polymarket-ml && python3 -c "
from configs.paths import validate_strategy_params
errors = validate_strategy_params('<strategy_type>', {
    'name': '<generated_name>',
    # ... params from config.yaml
})
for e in errors:
    print(f'ERROR: {e}')
if not errors:
    print('Validation passed')
"
```

### Step 5: Prompt for Customization

Ask user to confirm or customize:

1. **Allocated USD per strategy?** (default from config.yaml, typically $400)
2. **Paper trade or live?** (default: paper)
3. **Any category restrictions?** (default from config.yaml)

### Step 6: Generate strategies.yaml Entries

For each strategy to deploy, generate YAML entry:

```yaml
<strategy_type>:
  - name: <generated_name>
    # Variant params from config.yaml
    yes_price_min: 0.45
    yes_price_max: 0.55
    min_hours: 12
    max_hours: 36
    # Deployment params
    min_edge_after_spread: 0.03
    order_type: market
    size_pct: 0.01
    allocated_usd: 400
    # Metadata (as comments)
    # Experiment: exp-002:v3
    # Deployed: 2024-12-24
    # Backtest Sharpe: 8.57
```

### Step 7: Apply Changes

Use cli/ship.py with the `--apply` flag:

```bash
cd /home/theo/polymarket-ml && python3 -m cli.ship <exp_id> --apply
```

For multiple experiments, apply each in sequence. If ANY fails validation, abort ALL.

### Step 8: Create Wallet Entries

After successful deployment, create wallet entries:

```bash
cd /home/theo/polymarket-ml && docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
INSERT INTO strategy_balances (strategy_name, allocated_usd, current_usd)
VALUES ('<strategy_name>', 400, 400)
ON CONFLICT (strategy_name) DO UPDATE SET allocated_usd = 400, updated_at = NOW();
"
```

### Step 9: Output Summary

```
=== DEPLOYMENT COMPLETE ===

Deployed 2 strategies:

1. uncertain_zone_12h_exp002_v3 (from exp-002:v3)
   - Allocated: $400
   - Mode: paper
   - Backtest Sharpe: 8.57
   - Win Rate: 57.8%

2. uncertain_zone_72h_exp002_v6 (from exp-002:v6)
   - Allocated: $400
   - Mode: paper
   - Backtest Sharpe: 7.54
   - Win Rate: 55.2%

Executor will auto-reload within 30 seconds.

To verify:
  python3 -m cli.deploy --list
  python3 -m cli.debug
```

## Multi-Deploy

When deploying multiple experiments at once:

1. Validate ALL experiments first
2. Show combined summary for user confirmation
3. Apply atomically: if any fails, abort all
4. Create all wallet entries in one transaction

## Rules

- **NEVER deploy without SHIP verdict** - refuse if verdict is KILL or ITERATE
- **NEVER overwrite existing strategy** without confirmation - ask first
- **ALWAYS create wallet entry** in strategy_balances after deployment
- **ALWAYS validate params** before deployment using `configs.validate`
- **ALWAYS show summary** and ask for confirmation before applying
- Read deployment config from `config.yaml`, not guessing from spec.md
- Include experiment ID in strategy name for traceability
- Document backtest metrics in deployment summary

## Rollback

If user wants to rollback a deployment:

1. Remove strategy from `strategies.yaml`
2. Delete wallet entry (optional):
   ```sql
   DELETE FROM strategy_balances WHERE strategy_name = '<name>';
   ```
3. Executor will auto-reload and stop trading the removed strategy
