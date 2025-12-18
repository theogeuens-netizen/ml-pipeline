"""
Longshot NO Strategy.

Buy NO against overpriced longshots (YES < 8%).
Tail risks are systematically overestimated in prediction markets.

The market overprices longshots because:
1. Gamblers and speculators buy cheap options hoping for big wins
2. Headline risk makes people overweight unlikely but vivid scenarios
3. Illiquidity in the NO side (most want to buy YES)

This strategy captures that edge by buying NO on events that are
very unlikely to happen, betting against the overpriced longshots.

Effectively, if YES is at 5%, we buy NO at 95% (1 - 0.05 = 0.95).
If the event doesn't happen (likely), we profit from the 5% edge.
"""

from typing import Iterator

from ..base import Strategy, Signal, Side, MarketData


class LongshotNoStrategy(Strategy):
    """Buy NO against overpriced longshots."""

    name = "longshot_no"
    description = "Buy NO against overpriced longshots (YES < 8%)"
    version = "1.0.0"

    # Default parameters
    DEFAULT_MAX_PROBABILITY = 0.08  # Max YES price to trigger
    DEFAULT_MIN_HOURS_TO_EXPIRY = 24  # Minimum time to expiry
    DEFAULT_MIN_LIQUIDITY_USD = 5000

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """
        Scan markets for longshot NO opportunities.

        Yields signals for markets where:
        1. YES price is below max probability (i.e., unlikely to happen)
        2. Time to expiry is above minimum (not too close to resolution)
        3. Liquidity meets minimum threshold
        """
        max_prob = self.get_param("max_probability", self.DEFAULT_MAX_PROBABILITY)
        min_hours = self.get_param("min_hours_to_expiry", self.DEFAULT_MIN_HOURS_TO_EXPIRY)
        min_liquidity = self.get_param("min_liquidity_usd", self.DEFAULT_MIN_LIQUIDITY_USD)

        for market in markets:
            # Skip if no NO token
            if not market.no_token_id:
                continue

            # Check YES price is low enough (longshot)
            yes_price = market.price
            if yes_price > max_prob or yes_price <= 0.01:
                continue

            # Check time to expiry (not too close - need time for position to work)
            if market.hours_to_close is None:
                continue
            if market.hours_to_close < min_hours:
                continue

            # Check liquidity
            if market.liquidity is not None and market.liquidity < min_liquidity:
                continue

            # Calculate NO price (inverse of YES)
            no_price = 1 - yes_price

            # Calculate edge: overestimation of tail risk
            # If YES is at 5%, market implies 5% chance of happening
            # But true probability might be 3%, so NO is worth 97% not 95%
            # We estimate true probability is lower than market price
            estimated_true_yes_prob = max(yes_price * 0.7, 0.005)  # Assume overpriced by ~30%
            estimated_true_no_value = 1 - estimated_true_yes_prob
            edge = (estimated_true_no_value - no_price) / no_price

            # Calculate confidence based on how extreme the longshot is
            # Lower YES price = more overpriced = higher confidence
            price_factor = 1 - (yes_price / max_prob)
            liquidity_factor = min(1, (market.liquidity or 0) / (min_liquidity * 5))
            confidence = 0.5 + 0.3 * price_factor + 0.2 * liquidity_factor

            self.logger.info(
                f"Signal: {market.question[:50]}... "
                f"YES={yes_price:.3f} NO={no_price:.3f} "
                f"edge={edge:.3f} confidence={confidence:.2f}"
            )

            yield Signal(
                token_id=market.no_token_id,
                side=Side.BUY,
                reason=f"Overpriced longshot (YES={yes_price:.1%}), buying NO",
                edge=edge,
                confidence=confidence,
                market_id=market.id,
                price_at_signal=no_price,
                best_bid=1 - market.best_ask if market.best_ask else None,
                best_ask=1 - market.best_bid if market.best_bid else None,
                strategy_name=self.name,
                metadata={
                    "question": market.question,
                    "yes_price": yes_price,
                    "hours_to_close": market.hours_to_close,
                    "liquidity": market.liquidity,
                },
            )

    def filter(self, market: MarketData) -> bool:
        """Pre-filter to exclude obviously unsuitable markets."""
        # Must have NO token
        if not market.no_token_id:
            return False

        # Must have reasonable time to expiry
        min_hours = self.get_param("min_hours_to_expiry", self.DEFAULT_MIN_HOURS_TO_EXPIRY)
        if market.hours_to_close is None or market.hours_to_close < min_hours:
            return False

        # Quick price check (YES must be low)
        max_prob = self.get_param("max_probability", self.DEFAULT_MAX_PROBABILITY)
        if market.price > max_prob:
            return False

        return True
