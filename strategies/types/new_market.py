"""New Market Strategy - Buy NO on new markets (convergence hypothesis)."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class NewMarketStrategy(Strategy):
    """Buy NO on markets early when probability is in sweet spot."""

    def __init__(
        self,
        name: str,
        min_no_probability: float = 0.50,
        max_no_probability: float = 0.90,
        min_hours_to_expiry: float = 168,
        min_liquidity: float = 500,
        assumed_no_rate: float = 0.60,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "2.0.0"
        self.min_no_probability = min_no_probability
        self.max_no_probability = max_no_probability
        self.min_hours_to_expiry = min_hours_to_expiry
        self.min_liquidity = min_liquidity
        self.assumed_no_rate = assumed_no_rate
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            if not m.no_token_id:
                continue

            # Time check (want early markets)
            if m.hours_to_close is None or m.hours_to_close < self.min_hours_to_expiry:
                continue

            # Liquidity
            if m.liquidity and m.liquidity < self.min_liquidity:
                continue

            # NO probability range
            no_prob = 1 - m.price
            if no_prob < self.min_no_probability or no_prob > self.max_no_probability:
                continue

            # Edge vs assumed rate
            edge = (self.assumed_no_rate - no_prob) / no_prob if no_prob > 0 else 0
            if edge <= 0:
                continue

            confidence = 0.3 + min(0.3, edge)

            yield Signal(
                token_id=m.no_token_id,
                side=Side.BUY,
                reason=f"New market NO: {no_prob:.1%} < {self.assumed_no_rate:.0%} base rate",
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
                decision_inputs={"no_prob": no_prob, "hours": m.hours_to_close},
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        total = len(markets)
        early = sum(1 for m in markets if m.hours_to_close and m.hours_to_close >= self.min_hours_to_expiry)
        in_range = 0
        with_edge = 0
        for m in markets:
            if not m.hours_to_close or m.hours_to_close < self.min_hours_to_expiry:
                continue
            no_prob = 1 - m.price
            if self.min_no_probability <= no_prob <= self.max_no_probability:
                in_range += 1
                if (self.assumed_no_rate - no_prob) / no_prob > 0:
                    with_edge += 1

        return {
            "total_markets": total,
            "early_enough": early,
            "in_probability_range": in_range,
            "with_positive_edge": with_edge,
            "funnel": f"{total} → {early} (early) → {in_range} (prob) → {with_edge} (edge)",
        }
