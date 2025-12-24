# Experiment: exp-003

## Metadata
- **Created**: 2024-12-23T14:00:00Z
- **Friction Bucket**: liquidity
- **Status**: pending

## Hypothesis
Markets exhibiting price spikes > 2 standard deviations from their rolling 24h mean will revert toward the mean within 4 hours, creating a profitable fade opportunity.

## Rationale
In thin prediction markets:
1. Large orders hit insufficient depth, causing outsized price impact
2. The "pop" overshoots fair value due to mechanical slippage
3. Liquidity providers and arbitrageurs restore equilibrium
4. The faster we detect and fade the spike, the more edge we capture

Z-score based detection captures:
- Statistically unusual moves relative to each market's own volatility
- Self-calibrating threshold (a 5% move in a stable market vs volatile market)
- No dependency on volume data (which is sparse in historical dataset)

## Universe Filter
- **Categories**: all
- **Min snapshots**: 20 (enough data for volatility calculation)
- **Price range**: 0.10 - 0.90 (avoid near-resolved markets)
- **Hours to expiry**: > 24h (avoid resolution mechanics)

## Signal Generation
1. **Baseline calculation** (per market, rolling 24h window):
   - Rolling mean price
   - Rolling standard deviation of price

2. **Spike detection** (entry trigger):
   - Z-score = |current_price - rolling_mean| / rolling_std
   - Entry when Z-score > threshold (2.0, 2.5, or 3.0)

3. **Direction**:
   - If price spiked UP (above mean): bet NO (fade the spike)
   - If price spiked DOWN (below mean): bet YES (fade the spike)

## Holding Period
- **Base case**: 4 hours after entry
- **Early exit**: If price reverts past the rolling mean (take profit)
- **Stop loss**: None in base case (test impact as variant)

## Kill Criteria
| Metric | Threshold |
|--------|-----------|
| Sharpe | < 0.5 |
| Win Rate | < 51% |
| Trades | < 50 |
| Profit Factor | < 1.1 |
| Time Consistency | Fail (any 3-month period negative) |

## Parameters to Test
| Parameter | Values | Rationale |
|-----------|--------|-----------|
| price_std_threshold | [2.0, 2.5, 3.0] | Higher = fewer but stronger signals |
| holding_period_hours | [1, 4, 8] | Short vs letting reversion complete |

**Variants**: 9 total (3 × 3)

## Secondary Parameters (Fixed for exp-003)
- Rolling window: 24 hours
- Stop loss: None
- Position sizing: Equal weight ($10 per trade)
- Volume filter: None (min snapshots filter is sufficient)

## Data Requirements
From futarchy.price_snapshots:
- `price`: Current market price
- `timestamp`: For calculating intervals and rolling windows
- `market_id`: For per-market baseline calculation

From futarchy.markets:
- `winner`: Resolution outcome for P&L calculation
- `close_date`: To filter out near-resolution markets

Derived features:
- Rolling 24h mean price (per market)
- Rolling 24h price standard deviation (per market)
- Price z-score: (current - mean) / std

## Expected Edge Source
1. **Mechanical**: Large orders in thin books cause temporary mispricings
2. **Behavioral**: Retail traders chase momentum, creating overshoot
3. **Information**: Genuine information arrives but is initially overweighted

## Prior Art
- No prior experiments in liquidity bucket
- exp-001, exp-002 explored behavioral biases (NO resolution bias) with success
- Mean reversion is well-documented in traditional markets but untested here

## Risks
1. **False positives**: Price spikes near resolution are NOT mean reverting
2. **Adverse selection**: We fade informed traders with real information
3. **Execution**: Spread costs may eat edge in thin markets

Mitigations:
- Filter for >24h to expiry (avoids resolution mechanics)
- Z-score threshold filters noise (only trade significant deviations)
- Test with realistic spread assumptions in backtest

## Success Criteria for Shipping
If multiple variants pass kill criteria:
1. Ship the most conservative (highest threshold) variant
2. Prefer longer holding periods (fewer trades, less execution risk)
3. Require positive performance across all 3-month splits
