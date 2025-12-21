"""Mean Reversion Strategy - Fade price deviations from mean."""

from typing import Iterator
import statistics
from strategies.base import Strategy, Signal, Side, MarketData


class MeanReversionStrategy(Strategy):
    """Fade price deviations beyond N standard deviations."""

    def __init__(
        self,
        name: str,
        std_threshold: float = 2.0,
        min_deviation_pct: float = 0.05,
        lookback_hours: float = 24,
        min_history_points: int = 10,
        min_liquidity: float = 5000,
        category: str = None,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "2.0.0"
        self.std_threshold = std_threshold
        self.min_deviation_pct = min_deviation_pct
        self.lookback_hours = lookback_hours
        self.min_history_points = min_history_points
        self.min_liquidity = min_liquidity
        self.category = category
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Skip expired markets
            if m.hours_to_close is not None and m.hours_to_close <= 0:
                continue

            # Category filter
            if self.category and m.category_l1 != self.category:
                continue

            # Token check
            if not m.yes_token_id or not m.no_token_id:
                continue

            # History check
            if not m.price_history or len(m.price_history) < self.min_history_points:
                continue

            # Liquidity
            if m.liquidity and m.liquidity < self.min_liquidity:
                continue

            # For short lookback, use tail of history
            if self.lookback_hours <= 1:
                prices = m.price_history[-12:]  # ~1 hour assuming 5-min intervals
            else:
                prices = m.price_history

            if len(prices) < self.min_history_points:
                continue

            # Calculate stats
            mean_price = statistics.mean(prices)
            std_price = statistics.stdev(prices) if len(prices) > 1 else 0
            if std_price == 0:
                continue

            z_score = (m.price - mean_price) / std_price
            deviation_pct = abs(m.price - mean_price) / mean_price if mean_price else 0

            # Check thresholds
            if abs(z_score) < self.std_threshold:
                continue
            if deviation_pct < self.min_deviation_pct:
                continue

            # Determine direction
            if z_score > 0:
                token_id, side_desc = m.no_token_id, "NO"
            else:
                token_id, side_desc = m.yes_token_id, "YES"

            edge = deviation_pct * 0.5
            confidence = 0.4 + min(0.4, abs(z_score) / 5)

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"{z_score:+.1f}σ from mean → buy {side_desc}",
                market_id=m.id,
                price_at_signal=m.price,
                edge=edge,
                confidence=confidence,
                size_usd=None,
                best_bid=m.best_bid,
                best_ask=m.best_ask,
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={"z_score": z_score, "mean": mean_price, "std": std_price},
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        total = len(markets)
        with_history = sum(1 for m in markets if m.price_history and len(m.price_history) >= self.min_history_points)
        with_deviation = 0
        for m in markets:
            if not m.price_history or len(m.price_history) < self.min_history_points:
                continue
            prices = m.price_history[-12:] if self.lookback_hours <= 1 else m.price_history
            if len(prices) < 2:
                continue
            mean_p = statistics.mean(prices)
            std_p = statistics.stdev(prices)
            if std_p > 0:
                z = abs((m.price - mean_p) / std_p)
                if z >= self.std_threshold:
                    with_deviation += 1

        return {
            "total_markets": total,
            "with_sufficient_history": with_history,
            "with_deviation_above_threshold": with_deviation,
            "funnel": f"{total} → {with_history} (history) → {with_deviation} (>{self.std_threshold}σ)",
        }
