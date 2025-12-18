"""
Longshot YES Strategy.

Buy YES on high-probability events (92-99%) near expiry.
Edge comes from slight underpricing of near-certain outcomes.

The market systematically underprices near-certainties because:
1. Opportunity cost of capital locked until resolution
2. Small chance of black swan events
3. Lack of sophisticated traders arbitraging small edges

This strategy captures that edge by buying YES on events that are
very likely to resolve YES, especially close to expiry when the
time value component is minimal.
"""

from typing import Iterator

from ..base import Strategy, Signal, Side, MarketData


class LongshotYesStrategy(Strategy):
    """Buy YES on high-probability events near expiry."""

    name = "longshot_yes"
    description = "Buy YES on 92-99% probability events near expiry"
    version = "1.0.0"

    # Default parameters
    DEFAULT_MIN_PROBABILITY = 0.92
    DEFAULT_MAX_PROBABILITY = 0.99
    DEFAULT_MAX_HOURS_TO_EXPIRY = 72
    DEFAULT_MIN_LIQUIDITY_USD = 5000

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """
        Scan markets for longshot YES opportunities.

        Yields signals for markets where:
        1. YES price is between min and max probability
        2. Time to expiry is within max hours
        3. Liquidity meets minimum threshold
        """
        min_prob = self.get_param("min_probability", self.DEFAULT_MIN_PROBABILITY)
        max_prob = self.get_param("max_probability", self.DEFAULT_MAX_PROBABILITY)
        max_hours = self.get_param("max_hours_to_expiry", self.DEFAULT_MAX_HOURS_TO_EXPIRY)
        min_liquidity = self.get_param("min_liquidity_usd", self.DEFAULT_MIN_LIQUIDITY_USD)

        for market in markets:
            # Skip if no YES token
            if not market.yes_token_id:
                continue

            # Check price range (YES price = probability of YES outcome)
            price = market.price
            if price < min_prob or price > max_prob:
                continue

            # Check time to expiry
            if market.hours_to_close is None:
                continue
            if market.hours_to_close > max_hours or market.hours_to_close <= 0:
                continue

            # Check liquidity
            if market.liquidity is not None and market.liquidity < min_liquidity:
                continue

            # Calculate edge: expected value - cost
            # At 95% probability, expected value is 0.95 * $1 = $0.95
            # If we pay $0.95, edge is 0
            # If we pay $0.94, edge is $0.01 per share
            # We estimate true probability slightly higher than market price
            estimated_true_prob = min(price + 0.02, 0.995)
            expected_value = estimated_true_prob * 1.0
            edge = (expected_value - price) / price  # Return on investment

            # Calculate confidence based on proximity to expiry and liquidity
            # Higher confidence for:
            # - Closer to expiry (less time for black swans)
            # - Higher liquidity (more informed pricing)
            time_factor = max(0, 1 - market.hours_to_close / max_hours)
            liquidity_factor = min(1, (market.liquidity or 0) / (min_liquidity * 5))
            confidence = 0.5 + 0.25 * time_factor + 0.25 * liquidity_factor

            self.logger.info(
                f"Signal: {market.question[:50]}... "
                f"price={price:.3f} hours={market.hours_to_close:.1f} "
                f"edge={edge:.3f} confidence={confidence:.2f}"
            )

            yield Signal(
                token_id=market.yes_token_id,
                side=Side.BUY,
                reason=f"High probability ({price:.1%}) near expiry ({market.hours_to_close:.1f}h)",
                edge=edge,
                confidence=confidence,
                market_id=market.id,
                price_at_signal=price,
                best_bid=market.best_bid,
                best_ask=market.best_ask,
                strategy_name=self.name,
                metadata={
                    "question": market.question,
                    "hours_to_close": market.hours_to_close,
                    "liquidity": market.liquidity,
                },
            )

    def filter(self, market: MarketData) -> bool:
        """Pre-filter to exclude obviously unsuitable markets."""
        # Must have YES token
        if not market.yes_token_id:
            return False

        # Must be active (has end date in future)
        if market.hours_to_close is None or market.hours_to_close <= 0:
            return False

        # Quick price check
        min_prob = self.get_param("min_probability", self.DEFAULT_MIN_PROBABILITY)
        if market.price < min_prob:
            return False

        return True
