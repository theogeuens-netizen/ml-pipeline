"""
Mean Reversion Strategy.

Fade large price spikes assuming they will revert to recent average.
Uses price history from snapshots to detect sudden moves.

The market often overreacts to news, creating temporary mispricings.
This strategy captures alpha by betting against extreme moves.
"""

import statistics
from typing import Iterator

from ..base import Strategy, Signal, Side, MarketData


class MeanReversionStrategy(Strategy):
    """Fade large price spikes expecting reversion to mean."""

    name = "mean_reversion"
    description = "Fade large price moves expecting mean reversion"
    version = "1.0.0"

    # Default parameters
    DEFAULT_MIN_HISTORY_POINTS = 10
    DEFAULT_LOOKBACK_HOURS = 24
    DEFAULT_STD_THRESHOLD = 2.0  # Standard deviations from mean
    DEFAULT_MIN_DEVIATION_PCT = 0.05  # Minimum 5% move
    DEFAULT_MIN_LIQUIDITY_USD = 10000

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """
        Scan for mean reversion opportunities.

        Yields signals when:
        1. Price has deviated significantly from recent mean
        2. Sufficient price history exists
        3. Liquidity meets minimum threshold
        """
        min_points = self.get_param("min_history_points", self.DEFAULT_MIN_HISTORY_POINTS)
        std_threshold = self.get_param("std_threshold", self.DEFAULT_STD_THRESHOLD)
        min_deviation = self.get_param("min_deviation_pct", self.DEFAULT_MIN_DEVIATION_PCT)
        min_liquidity = self.get_param("min_liquidity_usd", self.DEFAULT_MIN_LIQUIDITY_USD)

        for market in markets:
            # Need price history for mean reversion
            if len(market.price_history) < min_points:
                continue

            # Check liquidity
            if market.liquidity is not None and market.liquidity < min_liquidity:
                continue

            # Calculate statistics
            prices = market.price_history
            mean_price = statistics.mean(prices)
            if len(prices) > 1:
                std_price = statistics.stdev(prices)
            else:
                continue

            # Avoid division by zero
            if std_price < 0.001:
                continue

            current_price = market.price
            deviation = current_price - mean_price
            z_score = deviation / std_price
            deviation_pct = abs(deviation) / mean_price if mean_price > 0 else 0

            # Check if deviation is significant
            if abs(z_score) < std_threshold or deviation_pct < min_deviation:
                continue

            # Determine direction - fade the move
            if z_score > std_threshold:
                # Price spiked up, bet it comes down (buy NO)
                if not market.no_token_id:
                    continue
                token_id = market.no_token_id
                side = Side.BUY
                reason = f"Price spike up: {current_price:.3f} vs mean {mean_price:.3f} ({z_score:.1f}σ)"
            else:
                # Price dropped, bet it comes up (buy YES)
                if not market.yes_token_id:
                    continue
                token_id = market.yes_token_id
                side = Side.BUY
                reason = f"Price drop: {current_price:.3f} vs mean {mean_price:.3f} ({z_score:.1f}σ)"

            # Calculate edge - expect reversion to mean
            expected_reversion = abs(deviation) * 0.5  # Expect 50% reversion
            edge = expected_reversion / current_price if side == Side.BUY else expected_reversion / (1 - current_price)

            # Confidence based on historical pattern and liquidity
            history_factor = min(1.0, len(prices) / 50)  # More history = more confidence
            liquidity_factor = min(1.0, (market.liquidity or 0) / (min_liquidity * 5))
            confidence = 0.4 + 0.3 * history_factor + 0.3 * liquidity_factor

            self.logger.info(
                f"Mean reversion signal: {market.question[:50]}... "
                f"z={z_score:.2f} dev={deviation_pct:.1%} edge={edge:.3f}"
            )

            yield Signal(
                token_id=token_id,
                side=side,
                reason=reason,
                edge=edge,
                confidence=confidence,
                market_id=market.id,
                price_at_signal=current_price,
                best_bid=market.best_bid,
                best_ask=market.best_ask,
                strategy_name=self.name,
                metadata={
                    "question": market.question,
                    "mean_price": mean_price,
                    "std_price": std_price,
                    "z_score": z_score,
                    "deviation_pct": deviation_pct,
                    "history_length": len(prices),
                    "volatility": std_price,  # For volatility-scaled sizing
                },
            )

    def filter(self, market: MarketData) -> bool:
        """Pre-filter to exclude markets without history."""
        min_points = self.get_param("min_history_points", self.DEFAULT_MIN_HISTORY_POINTS)

        # Must have price history
        if len(market.price_history) < min_points:
            return False

        # Must have at least one token
        if not market.yes_token_id and not market.no_token_id:
            return False

        return True

    def should_exit(self, position, market: MarketData) -> Signal | None:
        """
        Exit when price reverts to mean or moves further against.

        Exit conditions:
        1. Price reverted past the mean (take profit)
        2. Z-score exceeded 3σ against us (stop loss)
        """
        if len(market.price_history) < 5:
            return None

        mean_price = statistics.mean(market.price_history)
        std_price = statistics.stdev(market.price_history) if len(market.price_history) > 1 else 0.01

        current_z = (market.price - mean_price) / std_price if std_price > 0 else 0

        # Determine original direction from position
        was_betting_up = position.token_id == market.yes_token_id

        # Take profit: price reverted past mean
        if was_betting_up and market.price >= mean_price + std_price * 0.5:
            return Signal(
                token_id=position.token_id,
                side=Side.SELL,
                reason=f"Mean reversion complete: {market.price:.3f} reverted to {mean_price:.3f}",
                edge=0,
                confidence=0.8,
                market_id=market.id,
                price_at_signal=market.price,
                strategy_name=self.name,
            )
        elif not was_betting_up and market.price <= mean_price - std_price * 0.5:
            return Signal(
                token_id=position.token_id,
                side=Side.SELL,
                reason=f"Mean reversion complete: {market.price:.3f} reverted to {mean_price:.3f}",
                edge=0,
                confidence=0.8,
                market_id=market.id,
                price_at_signal=market.price,
                strategy_name=self.name,
            )

        # Stop loss: moved 3σ against us
        if was_betting_up and current_z < -3:
            return Signal(
                token_id=position.token_id,
                side=Side.SELL,
                reason=f"Stop loss: price at {current_z:.1f}σ below mean",
                edge=0,
                confidence=0.9,
                market_id=market.id,
                price_at_signal=market.price,
                strategy_name=self.name,
            )
        elif not was_betting_up and current_z > 3:
            return Signal(
                token_id=position.token_id,
                side=Side.SELL,
                reason=f"Stop loss: price at {current_z:.1f}σ above mean",
                edge=0,
                confidence=0.9,
                market_id=market.id,
                price_at_signal=market.price,
                strategy_name=self.name,
            )

        return None
