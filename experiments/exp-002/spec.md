# Experiment: exp-002

## Metadata
- **Created**: 2024-12-23T14:00:00Z
- **Friction Bucket**: behavioral
- **Status**: pending
- **Prior Art**: exp-001 (Uncertain Zone NO Bias)

## Hypothesis

The uncertain zone NO bias (exp-001) is real but timing-dependent. Short windows (1-4h) fail in live trading due to market efficiency near expiry, while medium windows (24-48h) work well. We can improve returns by:
1. Eliminating the failing short-window variant
2. Optimizing the entry window around the 24-48h sweet spot
3. Testing category-specific variants (behavioral biases may differ by domain)
4. Testing tighter price bands around the 50% center

## Evidence from Live Performance (exp-001 Deployed)

| Strategy | Window | Win Rate | Return | Trades | Status |
|----------|--------|----------|--------|--------|--------|
| uncertain_zone_1h | 1-4h | 50.0% | -13.9% | ~10 | **KILL** |
| uncertain_zone_24h | 24-48h | ~65% | +33.7% | ~15 | **WORKING** |
| uncertain_zone_5d | 120-168h | ~52% | +1.8% | ~8 | **MARGINAL** |

### Key Observations
1. **1h variant is failing**: 50% win rate matches random chance. Live markets near expiry are more efficient than backtest suggested.
2. **24h variant is crushing**: +33.7% return validates the core hypothesis at medium time horizons.
3. **5d variant is marginal**: Long horizon may allow price to drift out of zone before resolution.

### Backtest vs Live Discrepancy Analysis
The exp-001 backtest showed 65.7% WR for 1-4h window, but live shows 50%. Likely causes:
- Backtest used "last snapshot" often <5 minutes before resolution (not executable)
- Near-expiry markets attract professional/informed traders
- Slippage higher in fast-moving close-to-resolution markets
- Small sample size in live (need more trades for statistical significance)

## Improvements to Test

### 1. Full Time Window Sweep
Re-test all time windows including 1-4h (small sample may explain poor live performance):
- **v1**: 1-4h (re-test, may be bad luck)
- **v2**: 4-12h (gap filler)
- **v3**: 12-36h (earlier entry)
- **v4**: 24-72h (expand working window)
- **v5**: 36-96h (2-4 day horizon)
- **v6**: 72-168h (full week)

### 2. Price Band Sweep
Test the full range from tight to wide:
- **v7**: 0.48-0.52 (pure 50/50 center)
- **v8**: 0.47-0.53 (slight expansion)
- **v9**: 0.46-0.54 (medium band)
- **v10**: 0.45-0.55 (current production)
- **v11**: 0.43-0.57 (wider)
- **v12**: 0.40-0.60 (original exp-001)

### 3. Category-Specific Variants
Behavioral biases may differ by domain:
- **v13**: CRYPTO only (high volatility, retail dominated)
- **v14**: SPORTS only (outcome anchoring common)
- **v15**: POLITICS only (narrative preference strong)

## Universe Filter
- **Categories**: All (with category variants for subset testing)
- **Min volume 24h**: Varies by variant ($100 - $10,000)
- **No min liquidity filter** (volume is sufficient proxy)
- **Time window**: Varies by variant (1h - 168h)

## Entry Criteria
- YES price between configured min/max
- Bet on NO side
- Hours to close within configured window

## Holding Period
Hold until resolution (binary outcome)

## Kill Criteria
| Metric | Threshold |
|--------|-----------|
| Sharpe | < 0.5 |
| Win Rate | < 52% (raised from 51%) |
| Trades | < 30 (lower for category variants) |
| Profit Factor | < 1.15 (raised from 1.1) |
| Time Consistency | Fail (edge must exist in both halves) |

## Parameters to Test

### Time Window Variants
| Variant | min_hours | max_hours | Rationale |
|---------|-----------|-----------|-----------|
| v1 | 1 | 4 | Re-test short window (small sample, may be bad luck) |
| v2 | 4 | 12 | Gap between 1h and 24h variants |
| v3 | 12 | 36 | Earlier entry than 24h |
| v4 | 24 | 72 | Expand the working window |
| v5 | 36 | 96 | Test 2-4 day horizon |
| v6 | 72 | 168 | Long horizon (1-week) |

### Price Band Variants
| Variant | yes_price_min | yes_price_max | Rationale |
|---------|---------------|---------------|-----------|
| v7 | 0.48 | 0.52 | Pure center zone (tightest) |
| v8 | 0.47 | 0.53 | Slight expansion |
| v9 | 0.46 | 0.54 | Medium band |
| v10 | 0.45 | 0.55 | Current production (baseline) |
| v11 | 0.43 | 0.57 | Wider band |
| v12 | 0.40 | 0.60 | Original exp-001 range |

### Category Variants
| Variant | category | Rationale |
|---------|----------|-----------|
| v13 | CRYPTO | High volatility, retail bias |
| v14 | SPORTS | Outcome anchoring |
| v15 | POLITICS | Narrative preference |

### Volume Variants
| Variant | min_volume | Rationale |
|---------|------------|-----------|
| v16 | 100 | Minimum threshold, max opportunities |
| v17 | 500 | Original exp-001 threshold |
| v18 | 2000 | Current production |
| v19 | 5000 | Higher quality filter |
| v20 | 10000 | Institutional-grade only |

## Data Requirements
- `price` (YES price at entry)
- `hours_to_close` (time to expiry)
- `volume_24h` (liquidity filter)
- `liquidity` (depth filter)
- `l1_category` (for category filtering)
- Market resolution outcome (YES/NO)

## Expected Outcomes

Based on exp-001 learnings:

1. **Time Window**: Expect 12-72h (v1, v2) to outperform 1-4h and 5d variants
2. **Price Band**: Tighter bands (v4, v5) may have higher win rate but fewer trades
3. **Categories**: Expect CRYPTO and POLITICS to show stronger NO bias than SPORTS

## Success Criteria

Experiment is successful if:
- At least 2 variants pass all kill criteria
- At least 1 variant shows Sharpe > 1.5
- Category analysis provides actionable segmentation

## Deployment Plan (If Successful)

1. **Replace** uncertain_zone_1h with best-performing 12-72h variant
2. **Keep** uncertain_zone_24h if still best
3. **Kill** uncertain_zone_5d if no improvement found
4. **Add** category-specific variants if edge differs by >20%

## Notes

- Prioritize time window optimization (v1-v3) over other variants
- Category variants require categorized markets - check availability
- Consider adding volume-weighted entry (bias toward higher liquidity)
- The liquidity asymmetry from exp-001 (Sharpe 8.88 low-vol vs 1.67 high-vol) suggests edge is partially from inefficiency - live results will track high-vol
