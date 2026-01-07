# Experiment: exp-004

## Metadata
- **Created**: 2024-12-25T10:00:00Z
- **Friction Bucket**: behavioral
- **Status**: pending
- **Prior Art**: exp-001 (Uncertain Zone NO Bias), exp-002 (Timing Optimization)

## Hypothesis

Markets where YES is priced between 0.55 and 0.95 systematically overestimate the probability of YES outcomes. Betting NO on these markets yields positive expected value because ~65-70% of all markets resolve NO regardless of the YES price.

## Theoretical Basis

**Observation**: If markets were calibrated, a market priced at YES=0.70 should resolve YES 70% of the time. Empirically:
- Polymarket claims ~80% of markets resolve NO
- User observation: ~65-70% resolve NO
- This suggests YES outcomes are systematically overpriced

**Behavioral Drivers**:
- **Action bias**: People prefer betting on things happening (YES) over status quo (NO)
- **Narrative preference**: YES is the "story" outcome - more exciting to bet on
- **Optimism bias**: Retail traders overweight positive outcomes
- **Salience**: Markets are created when something *might* happen, biasing attention to YES

**Key Difference from exp-001/002**: Prior experiments focused on the "uncertain zone" (40-60%). This tests whether the NO bias extends to markets where YES is the **favorite**.

**Upper bound (0.95)**: Markets with YES > 0.95 are near-certain outcomes where the NO bias doesn't apply - these are typically resolved events or extreme favorites where YES pricing is accurate.

## Universe Filter
- **Categories**: all (analyze by category in robustness)
- **Min volume 24h**: varies by variant
- **Min liquidity**: varies by variant
- **Hours to expiry**: 12-168 (avoiding <12h per exp-002 learnings)

## Entry Criteria
- YES price between configured min threshold (0.55-0.70) and 0.95
- Bet on NO side
- Hours to close within configured window

## Holding Period
Hold until resolution (binary outcome)

## Kill Criteria
| Metric | Threshold |
|--------|-----------|
| Sharpe | < 0.5 |
| Win Rate | < 51% |
| Trades | < 50 |
| Profit Factor | < 1.1 |
| Time Consistency | Fail (edge must exist in both halves of data) |

## Parameters to Test

### YES Price Threshold Variants
| Variant | yes_min | yes_max | Rationale |
|---------|---------|---------|-----------|
| v1 | 0.55 | 0.95 | Broadest range (most trades) |
| v2 | 0.60 | 0.95 | Moderate favorite |
| v3 | 0.65 | 0.95 | Clear favorite |
| v4 | 0.70 | 0.95 | Strong favorite (fewer trades, higher potential edge) |

### Time Window Variants (per exp-002 learnings)
| Variant | min_hours | max_hours | Rationale |
|---------|-----------|-----------|-----------|
| v5 | 12 | 48 | Medium-term (best from exp-002) |
| v6 | 24 | 96 | Extended medium-term |
| v7 | 48 | 168 | Long-term (1-week) |

### Volume Threshold Variants
| Variant | min_volume_24h | Rationale |
|---------|----------------|-----------|
| v8 | 100 | Maximum opportunity set |
| v9 | 500 | Moderate filter |
| v10 | 2000 | Higher quality markets |
| v11 | 5000 | Institutional-grade |

### Liquidity Threshold Variants
| Variant | min_liquidity | Rationale |
|---------|---------------|-----------|
| v12 | 500 | Low threshold, max trades |
| v13 | 1000 | Moderate depth |
| v14 | 2500 | Good execution |
| v15 | 5000 | Deep markets only |

**Total Variants**: 15 primary (can cross-test promising combinations)

## Execution Config

### Sizing
- **Method**: fixed_pct
- **Size**: 1% of capital per trade

### Execution
- **Order type**: market
- **Min edge after spread**: 3%
- **Max spread**: null (no limit)

### Deployment
- **Allocated USD**: $400
- **Paper trade first**: Yes
- **Categories**: All

## Data Requirements
- `price` (YES price at entry)
- `hours_to_close` (time to expiry)
- `volume_24h` (liquidity filter)
- `liquidity` (depth filter)
- `l1_category` (for category robustness)
- Market resolution outcome (YES/NO)

## Prior Art

**exp-001**: Found +17% edge betting NO in the 50-60% YES zone. This experiment tests if edge persists at higher YES prices.

**exp-002**: Found 24-48h window optimal; <12h fails due to market efficiency near expiry. We adopt 12h minimum.

## Expected Outcomes

| Threshold | Expected WR | Rationale |
|-----------|-------------|-----------|
| YES 0.55-0.95 | ~55-60% | Marginal edge, high volume |
| YES 0.60-0.95 | ~58-63% | Moderate edge |
| YES 0.65-0.95 | ~60-65% | Good edge, fewer trades |
| YES 0.70-0.95 | ~62-68% | Strongest edge, lowest volume |

If ~65% of markets resolve NO across all prices, then:
- Betting NO at YES=0.60 pays 0.60 on win, loses 0.40 on loss
- With 65% WR: EV = 0.65 * 0.60 - 0.35 * 0.40 = 0.39 - 0.14 = +0.25 per dollar

## Risk Factors

1. **Selection bias**: Markets created are biased toward "newsworthy" outcomes which may have different base rates
2. **Category dependence**: Sports/crypto may differ from politics
3. **Time decay**: Edge may erode as market approaches resolution
4. **Sample size at high thresholds**: Fewer markets with YES > 0.70
5. **Adverse selection**: High YES prices may reflect genuine information

## Notes

- This is complementary to exp-001/002 - tests the "favorite" side rather than uncertain zone
- If successful, could deploy alongside uncertain_zone strategies for broader coverage
- Consider category-specific deployment if robustness shows significant differences
- Volume/liquidity thresholds may interact with edge (thin markets = more mispricing but worse execution)
