# Experiment: exp-001

## Metadata
- **Created**: 2024-12-22T08:30:00Z
- **Friction Bucket**: behavioral
- **Status**: pending

## Hypothesis

Markets priced in the 40-60% "uncertain zone" exhibit a systematic NO bias: YES outcomes are overpriced by 15-17%, and betting NO yields positive expected value.

## Evidence from Historical Analysis

Analysis of 351,410 resolved outcomes from the futarchy database (385K markets, 7.4M price snapshots):

### Overall Favorite-Longshot Bias
| Price Bucket | N | Actual Win % | Implied % | Edge |
|--------------|---|--------------|-----------|------|
| 0.00-0.05 | 66,411 | 0.33% | 2.5% | -2.17% |
| 0.45-0.50 | 27,449 | 37.33% | 47.5% | **-10.17%** |
| 0.50-0.55 | 56,291 | 55.50% | 52.5% | +3.00% |
| 0.90-0.95 | 7,101 | 95.04% | 92.5% | +2.54% |
| 0.95-1.00 | 64,730 | 99.63% | 97.5% | +2.13% |

### YES vs NO Bias (Key Finding)
| Price Range | Side | N | Actual Win % | Implied % | Edge |
|-------------|------|---|--------------|-----------|------|
| 0.40-0.50 | YES | 4,375 | 27.57% | 45% | **-17.43%** |
| 0.40-0.50 | NO | 2,643 | 46.20% | 45% | +1.20% |
| 0.50-0.60 | YES | 5,893 | 39.96% | 55% | **-15.04%** |
| 0.50-0.60 | NO | 6,669 | 72.00% | 55% | **+17.00%** |
| 0.60-0.70 | YES | 1,510 | 60.13% | 65% | -4.87% |
| 0.60-0.70 | NO | 3,082 | 66.87% | 65% | +1.87% |

**Interpretation**: In the uncertain zone (40-60%), YES outcomes are systematically overpriced. This may reflect:
- Optimism bias (people prefer betting on things happening)
- Narrative preference (YES is the "story" outcome)
- Anchoring on initial YES-favoring prices

## Universe Filter
- **Categories**: all (analyze by category in robustness)
- **Min volume 24h**: $500
- **Min liquidity**: $1,000
- **Hours to expiry**: 1-168 (1 hour to 1 week)

## Entry Criteria
- YES price between 0.40 and 0.60 (equivalently, NO price between 0.40 and 0.60)
- Bet on NO side

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
| Parameter | Values | Rationale |
|-----------|--------|-----------|
| yes_price_min | [0.35, 0.40, 0.45] | Lower bound of entry zone |
| yes_price_max | [0.55, 0.60, 0.65] | Upper bound of entry zone |
| min_hours_to_expiry | [1, 4, 12] | Filter out near-expiry noise |

**Variants**: 9 total (3 x 3 grid on price range, test min_hours separately)

## Data Requirements
- `price` (YES price at entry)
- `hours_to_close` (time to expiry)
- `volume_24h` (liquidity filter)
- `liquidity` (depth filter)
- Market resolution outcome (YES/NO)

## Prior Art
None - first experiment in behavioral bucket.

## Expected Edge
Based on historical analysis:
- Entry at YES 0.40-0.60, betting NO
- Expected win rate: ~60-72% (vs implied 40-60%)
- Expected edge: 10-17%

## Notes
- The 0.45-0.50 bucket shows -10% edge for YES, suggesting markets near 50/50 have the strongest NO bias
- Consider splitting by category in robustness checks (crypto vs politics vs sports may differ)
- The sample size (5,893 YES, 6,669 NO in 0.50-0.60 bucket) is statistically significant
