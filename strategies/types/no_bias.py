"""NO Bias Strategy - Exploit tendency for markets to resolve NO."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class NoBiasStrategy(Strategy):
    """Buy NO based on historical resolution rates by category."""

    def __init__(
        self,
        name: str,
        category: str,
        historical_no_rate: float,
        min_hours: float = 0,
        max_hours: float = 168,
        min_liquidity: float = 0,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "2.0.0"
        self.category = category
        self.historical_no_rate = historical_no_rate
        self.min_hours = min_hours
        self.max_hours = max_hours
        self.min_liquidity = min_liquidity
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Category filter
            if m.category_l1 != self.category:
                continue

            # Token check
            if not m.no_token_id:
                continue

            # Time window - must be positive and within range
            if m.hours_to_close is None or m.hours_to_close <= 0:
                continue
            if m.hours_to_close < self.min_hours or m.hours_to_close > self.max_hours:
                continue

            # Liquidity
            if self.min_liquidity and m.liquidity and m.liquidity < self.min_liquidity:
                continue

            # Calculate edge
            no_price = 1 - m.price
            if no_price <= 0:
                continue
            edge = (self.historical_no_rate - no_price) / no_price
            if edge <= 0:
                continue

            # Confidence scales with time to expiry
            confidence = 0.4 + (0.2 * (1 - m.hours_to_close / self.max_hours))

            yield Signal(
                token_id=m.no_token_id,
                side=Side.BUY,
                reason=f"{self.category} NO: {no_price:.1%} vs historical {self.historical_no_rate:.1%}",
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
                decision_inputs={"no_price": no_price, "hours": m.hours_to_close},
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        """Return debug info about why strategy isn't trading."""
        total = len(markets)
        by_category = sum(1 for m in markets if m.category_l1 == self.category)
        in_time = sum(1 for m in markets if m.category_l1 == self.category
                     and m.hours_to_close and self.min_hours <= m.hours_to_close <= self.max_hours)
        with_edge = 0
        for m in markets:
            if m.category_l1 != self.category:
                continue
            if not m.hours_to_close or not (self.min_hours <= m.hours_to_close <= self.max_hours):
                continue
            no_price = 1 - m.price
            if no_price > 0 and (self.historical_no_rate - no_price) / no_price > 0:
                with_edge += 1

        return {
            "total_markets": total,
            "by_category": by_category,
            "in_time_window": in_time,
            "with_positive_edge": with_edge,
            "funnel": f"{total} → {by_category} ({self.category}) → {in_time} (time) → {with_edge} (edge)",
        }
