"""
Streaming book imbalance strategy.

Evaluates orderbook updates for trading signals in real-time.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from .config import StreamingConfig
from .signals import StreamingSignal
from .state import MarketInfo, OrderbookState, StreamingStateManager

logger = logging.getLogger(__name__)


class StreamingBookImbalanceStrategy:
    """
    Evaluate book imbalance signals on orderbook updates.

    MOMENTUM strategy: Follow the imbalance direction
    - Bid-heavy (imbalance > 0): Buy YES expecting price to rise
    - Ask-heavy (imbalance < 0): Buy NO expecting price to fall

    This mirrors the logic from BookImbalanceMomentumStrategy but
    operates on streaming data instead of polled snapshots.
    """

    def __init__(self, config: StreamingConfig):
        """
        Initialize strategy with configuration.

        Args:
            config: Streaming configuration
        """
        self.config = config
        self.name = config.name
        self.version = "1.0.0"

    def evaluate(
        self,
        book: OrderbookState,
        market: MarketInfo,
        state: StreamingStateManager,
    ) -> Optional[StreamingSignal]:
        """
        Evaluate orderbook update for potential signal.

        This method is called on every orderbook update.
        It must be fast - all heavy operations should be deferred.

        Args:
            book: Current orderbook state
            market: Market information
            state: State manager for position/cooldown checks

        Returns:
            StreamingSignal if all conditions met, None otherwise
        """
        # Quick checks first (fastest to evaluate)

        # 1. Check imbalance threshold
        imbalance = book.imbalance
        if abs(imbalance) < self.config.min_imbalance:
            return None

        # 2. Check price zone
        mid_price = book.mid_price
        if mid_price is None:
            return None
        if mid_price < self.config.yes_price_min or mid_price > self.config.yes_price_max:
            return None

        # 3. Check spread
        spread = book.spread
        if spread is None or spread > self.config.max_spread:
            return None

        # 4. Check best bid/ask exist
        if book.best_bid is None or book.best_ask is None:
            return None

        # Slower checks (involve state lookups)

        # 5. Check position limit (only for LIVE mode, paper mode has no limit)
        if self.config.live:
            current_positions = state.get_position_count(self.name)
            if current_positions >= self.config.max_positions:
                logger.debug(
                    f"Position limit reached: {current_positions}/{self.config.max_positions}"
                )
                return None

        # 6. Check existing position on this market
        if state.has_open_position(self.name, market.id):
            return None

        # 7. Check cooldown
        if state.is_in_cooldown(self.name, market.id, self.config.cooldown_minutes):
            return None

        # 8. Check time to close
        min_hours = self.config.min_minutes_to_close / 60
        if market.hours_to_close < min_hours:
            logger.debug(
                f"Too close to resolution: {market.hours_to_close:.2f}h < {min_hours:.2f}h"
            )
            return None

        # All checks passed - generate signal

        # Determine which token's orderbook we're looking at
        is_yes_token = book.token_id == market.yes_token_id
        is_no_token = book.token_id == market.no_token_id

        # MOMENTUM strategy: Follow the imbalance direction
        # Bid-heavy (imbalance > 0) means buyers dominating on THIS token
        # Ask-heavy (imbalance < 0) means sellers dominating on THIS token
        if is_yes_token:
            if imbalance > 0:
                # Buyers want YES → buy YES
                token_id = market.yes_token_id
                token_side = "YES"
                execution_price = book.best_ask
            else:
                # Sellers want to sell YES → buy NO
                token_id = market.no_token_id
                token_side = "NO"
                execution_price = 1 - book.best_bid
        elif is_no_token:
            if imbalance > 0:
                # Buyers want NO → buy NO
                token_id = market.no_token_id
                token_side = "NO"
                execution_price = book.best_ask
            else:
                # Sellers want to sell NO → buy YES
                token_id = market.yes_token_id
                token_side = "YES"
                execution_price = 1 - book.best_bid
        else:
            # Unknown token, shouldn't happen
            logger.warning(f"Unknown token {book.token_id} for market {market.id}")
            return None

        # Build signal
        signal = StreamingSignal(
            strategy_name=self.name,
            market_id=market.id,
            token_id=token_id,
            condition_id=market.condition_id,
            side="BUY",
            token_side=token_side,
            price_at_signal=execution_price,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            imbalance=imbalance,
            spread=spread,
            hours_to_close=market.hours_to_close,
            size_usd=self.config.fixed_size_usd,
            reason=f"Streaming imbalance {imbalance:+.0%} → {token_side}",
            edge=abs(imbalance) * 0.1,  # Simple edge estimate
            confidence=0.6,
            market_question=market.question,
        )

        logger.info(
            f"Signal generated: {token_side} on market {market.id}, "
            f"imbalance={imbalance:+.0%}, price={execution_price:.4f}"
        )

        # Update stats
        state.increment_stat("signals_generated")

        return signal

    def get_debug_stats(self, state: StreamingStateManager) -> dict:
        """Get debug statistics for this strategy."""
        return {
            "name": self.name,
            "version": self.version,
            "config": {
                "min_imbalance": f"{self.config.min_imbalance:.0%}",
                "price_zone": f"{self.config.yes_price_min:.0%}-{self.config.yes_price_max:.0%}",
                "max_spread": f"{self.config.max_spread:.2f}",
                "categories": self.config.categories,
                "max_positions": self.config.max_positions,
                "cooldown_minutes": self.config.cooldown_minutes,
            },
            "state": {
                "markets_subscribed": len(state.market_info),
                "orderbooks_cached": len(state.orderbooks),
                "open_positions": state.get_position_count(self.name),
                "active_cooldowns": sum(
                    1
                    for k in state.cooldowns.keys()
                    if k.startswith(f"{self.name}:")
                ),
            },
        }
