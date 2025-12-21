"""Longshot Strategy - Buy high-probability outcomes near expiry."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class LongshotStrategy(Strategy):
    """Buy tokens when probability is very high and expiry is near."""

    def __init__(
        self,
        name: str,
        side: str,  # YES or NO
        min_probability: float = 0.85,
        max_probability: float = 0.99,
        max_hours: float = 72,
        min_liquidity: float = 0,
        excluded_categories: list = None,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "2.0.0"
        self.side = side
        self.min_probability = min_probability
        self.max_probability = max_probability
        self.max_hours = max_hours
        self.min_liquidity = min_liquidity
        self.excluded_categories = excluded_categories or []
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Category exclusion
            if m.category_l1 in self.excluded_categories:
                continue

            # Token check
            if self.side == "YES" and not m.yes_token_id:
                continue
            if self.side == "NO" and not m.no_token_id:
                continue

            # Calculate probability for chosen side
            prob = m.price if self.side == "YES" else 1 - m.price

            # Probability range
            if prob < self.min_probability or prob > self.max_probability:
                continue

            # Time window - must be between 0 and max_hours
            if m.hours_to_close is None or m.hours_to_close <= 0 or m.hours_to_close > self.max_hours:
                continue

            # Liquidity
            if self.min_liquidity and m.liquidity and m.liquidity < self.min_liquidity:
                continue

            # Edge: assume true prob slightly higher due to opportunity cost
            estimated_true = min(prob + 0.02, 0.995)
            edge = (estimated_true - prob) / prob

            # Confidence
            time_factor = 1 - (m.hours_to_close / self.max_hours)
            confidence = 0.5 + (time_factor * 0.3)

            token_id = m.yes_token_id if self.side == "YES" else m.no_token_id

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"High {self.side} ({prob:.1%}) near expiry ({m.hours_to_close:.1f}h)",
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
                decision_inputs={"prob": prob, "hours": m.hours_to_close},
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        total = len(markets)
        after_exclusion = sum(1 for m in markets if m.category_l1 not in self.excluded_categories)
        in_prob_range = 0
        in_time = 0
        for m in markets:
            if m.category_l1 in self.excluded_categories:
                continue
            prob = m.price if self.side == "YES" else 1 - m.price
            if self.min_probability <= prob <= self.max_probability:
                in_prob_range += 1
                if m.hours_to_close and m.hours_to_close <= self.max_hours:
                    in_time += 1

        return {
            "total_markets": total,
            "after_category_filter": after_exclusion,
            "in_probability_range": in_prob_range,
            "in_time_window": in_time,
            "funnel": f"{total} → {after_exclusion} → {in_prob_range} (prob) → {in_time} (time)",
        }
