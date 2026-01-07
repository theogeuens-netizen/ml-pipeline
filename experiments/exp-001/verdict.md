# Verdict: exp-001

## Decision: SHIP

## Kill Criteria Results

| Criterion | Threshold | Actual (v7) | Status |
|-----------|-----------|-------------|--------|
| Sharpe | >= 0.5 | 6.12 | **PASS** |
| Win Rate | >= 51% | 68.2% | **PASS** |
| Trades | >= 50 | 7,817 | **PASS** |
| Profit Factor | >= 1.1 | 2.12 | **PASS** |
| Time Split | consistent | H1: 1.66 / H2: 6.26 | **PASS** |

**All 9 variants passed all kill criteria.** Best variant (v7) has the highest Sharpe, win rate, and profit factor.

## Robustness Assessment

**Time split:** Edge exists in both time periods. First half (Jul-Mar 2025) shows Sharpe 1.66 with 55.7% win rate on 221 trades. Second half (Mar-Dec 2025) shows Sharpe 6.26 with 68.6% win rate on 7,596 trades. The asymmetry suggests either improving data quality over time or genuine market evolution, but critically, the edge is positive in both periods.

**Liquidity split:** Edge exists in both high and low volume markets, but is dramatically stronger in low-volume (Sharpe 8.88 vs 1.67). This is important for live trading - we should expect the realized edge to be closer to the high-volume figures (56% WR, Sharpe 1.67) since those are executable. Still profitable, but more modest than backtest suggests.

**Category split:** Not tested (futarchy database lacks category labels). Recommend monitoring performance by category in paper trading.

## Reasoning

The hypothesis that YES outcomes in the 40-60% "uncertain zone" are systematically overpriced is strongly confirmed. Across 10,268 trades (v5: 0.40-0.60), the strategy achieved a 65.3% win rate against an implied fair rate of 40-60%, yielding Sharpe 4.92 and profit factor 1.85. The effect is consistent across time periods and liquidity levels, though significantly stronger in illiquid markets.

The best risk-adjusted variant (v7: 0.45-0.55) achieves 68.2% win rate with Sharpe 6.12, but has fewer trading opportunities. For paper trading, I recommend v5 (0.40-0.60) as it provides more trades and still exceeds all thresholds comfortably.

## Deployment Plan

**Strategy name:** `no_bias_uncertain_zone`

**Parameters:**
- Side: NO
- YES price range: 0.40 - 0.60
- Min liquidity: $1,000 (to ensure execution quality)
- Min volume 24h: $500
- **Entry window: 1-4 hours before close** (best Sharpe: 5.09, win rate: 65.7%)
- Alternative entry: 24-48 hours before close (Sharpe: 5.60, win rate: 67.6%)

**Important timing note:** The original backtest used "last snapshot" which was typically <5 minutes before resolution (not executable). Re-analysis using all snapshots shows the edge persists at tradeable time windows:
- 1-4h: 65.7% WR, Sharpe 5.09 (25,505 entries)
- 4-12h: 62.6% WR, Sharpe 3.88
- 12-24h: 61.3% WR, Sharpe 3.35
- 24-48h: 67.6% WR, Sharpe 5.60
- 48h-1w: 62.5% WR, Sharpe 3.76

**Risk limits:**
- Max position size: $25 per trade
- Max daily exposure: $200
- Max concurrent positions: 10
- Categories: all (monitor by category)

**Paper trade duration:** 4 weeks

**Review triggers:**
- Re-evaluate if win rate drops below 55% over 50+ trades
- Re-evaluate if Sharpe drops below 1.0
- Re-evaluate if any category shows negative edge
- Scale up if metrics hold after 100 trades

## Notes

1. The liquidity asymmetry (Sharpe 8.88 low-vol vs 1.67 high-vol) suggests the edge may be partially explained by market inefficiency in thin markets. Live performance will likely track closer to high-volume figures.

2. The first-half sample size (397 trades for v5) is smaller than ideal. Continue monitoring time consistency as more data accumulates.

3. Consider testing category-specific variants once we have category data - the behavioral bias may be stronger in certain market types (e.g., politics vs crypto).
