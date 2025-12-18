"""
Volatility Hedge Strategy.

Two-phase strategy for capturing volatility premium:
1. Entry phase: Buy the favorite (YES or NO depending on price)
2. Hedge phase: After price moves, hedge by buying the opposite side

The strategy profits from:
- Collecting "volatility premium" by being long both outcomes
- The hedge locks in profit from the initial move
- Resolution gives back $1 regardless of outcome (minus spread costs)

Best suited for markets with:
- High uncertainty (price near 50%)
- High liquidity
- Expected news/catalyst events
"""

from typing import Iterator, Optional
from datetime import datetime, timezone

from ..base import Strategy, Signal, Side, MarketData


class VolatilityHedgeStrategy(Strategy):
    """Two-phase volatility capture strategy."""

    name = "volatility_hedge"
    description = "Capture volatility with initial position + hedge"
    version = "1.0.0"

    # Default parameters
    DEFAULT_MIN_PRICE = 0.35
    DEFAULT_MAX_PRICE = 0.65  # Near 50/50 markets
    DEFAULT_HEDGE_MOVE_PCT = 0.10  # Hedge after 10% move
    DEFAULT_MIN_LIQUIDITY_USD = 15000  # Need good liquidity for 2 trades
    DEFAULT_MAX_SPREAD = 0.04  # Max 4% spread

    def __init__(self):
        super().__init__()
        # Track positions waiting for hedge
        self._pending_hedges: dict[int, dict] = {}

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """
        Scan for volatility entry opportunities.

        Yields entry signals for markets where:
        1. Price is near 50% (high uncertainty)
        2. Good liquidity on both sides
        3. Reasonable spread
        """
        min_price = self.get_param("min_price", self.DEFAULT_MIN_PRICE)
        max_price = self.get_param("max_price", self.DEFAULT_MAX_PRICE)
        min_liquidity = self.get_param("min_liquidity_usd", self.DEFAULT_MIN_LIQUIDITY_USD)
        max_spread = self.get_param("max_spread", self.DEFAULT_MAX_SPREAD)

        for market in markets:
            # Must have both tokens
            if not market.yes_token_id or not market.no_token_id:
                continue

            # Check price range (near 50/50)
            if market.price < min_price or market.price > max_price:
                continue

            # Check liquidity
            if market.liquidity is not None and market.liquidity < min_liquidity:
                continue

            # Check spread
            if market.spread is not None and market.spread > max_spread:
                continue

            # Skip if already have pending hedge for this market
            if market.id in self._pending_hedges:
                continue

            # Calculate expected profit from volatility
            # If we buy YES at 50c and price moves to 60c, then buy NO at 40c
            # Total cost: $0.50 + $0.40 = $0.90
            # Payout at resolution: $1.00
            # Profit: $0.10 (minus spreads)
            spread_cost = (market.spread or 0.02) * 2  # Two trades
            expected_move = 0.10  # Expect 10% move
            expected_profit = expected_move - spread_cost
            edge = expected_profit / 0.50  # Edge relative to initial investment

            if edge <= 0:
                continue

            # Determine initial side - slightly favor the favorite
            if market.price >= 0.50:
                token_id = market.yes_token_id
                initial_side = "YES"
            else:
                token_id = market.no_token_id
                initial_side = "NO"

            # Confidence based on liquidity and proximity to 50%
            distance_from_50 = abs(market.price - 0.5)
            uncertainty_factor = 1 - (distance_from_50 / 0.15)  # Max at exactly 50%
            liquidity_factor = min(1.0, (market.liquidity or 0) / (min_liquidity * 3))
            confidence = 0.4 + 0.3 * uncertainty_factor + 0.3 * liquidity_factor

            self.logger.info(
                f"Volatility entry: {market.question[:50]}... "
                f"price={market.price:.3f} side={initial_side} "
                f"edge={edge:.3f} spread={market.spread or 'N/A'}"
            )

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"Volatility entry: {initial_side} @ {market.price:.1%}, spread={market.spread or 0:.1%}",
                edge=edge,
                confidence=confidence,
                market_id=market.id,
                price_at_signal=market.price,
                best_bid=market.best_bid,
                best_ask=market.best_ask,
                strategy_name=self.name,
                metadata={
                    "question": market.question,
                    "phase": "entry",
                    "initial_side": initial_side,
                    "entry_price": market.price,
                    "target_hedge_price": market.price + (0.10 if initial_side == "YES" else -0.10),
                },
            )

    def on_signal_executed(self, signal: Signal, result):
        """Track executed entries for hedging."""
        if signal.metadata.get("phase") == "entry":
            self._pending_hedges[signal.market_id] = {
                "initial_side": signal.metadata.get("initial_side"),
                "entry_price": signal.metadata.get("entry_price"),
                "entry_time": datetime.now(timezone.utc),
                "token_id": signal.token_id,
            }
            self.logger.info(
                f"Volatility position opened: market={signal.market_id} "
                f"side={signal.metadata.get('initial_side')} "
                f"entry={signal.metadata.get('entry_price'):.3f}"
            )

    def filter(self, market: MarketData) -> bool:
        """Pre-filter markets."""
        min_price = self.get_param("min_price", self.DEFAULT_MIN_PRICE)
        max_price = self.get_param("max_price", self.DEFAULT_MAX_PRICE)

        # Must have both tokens
        if not market.yes_token_id or not market.no_token_id:
            return False

        # Quick price check
        if market.price < min_price or market.price > max_price:
            return False

        return True

    def should_exit(self, position, market: MarketData) -> Optional[Signal]:
        """
        Check if hedge should be executed.

        Hedge triggers when:
        1. Price has moved favorably by hedge_move_pct
        2. Or stop loss if moved against by more than hedge_move_pct
        """
        pending = self._pending_hedges.get(market.id)
        if not pending:
            return None

        hedge_move = self.get_param("hedge_move_pct", self.DEFAULT_HEDGE_MOVE_PCT)
        entry_price = pending["entry_price"]
        initial_side = pending["initial_side"]
        current_price = market.price

        # Calculate move since entry
        move = current_price - entry_price

        # Check for hedge trigger
        hedge_signal = None

        if initial_side == "YES":
            # Bought YES, hedge by buying NO when YES goes up
            if move >= hedge_move:
                # YES went up, buy NO to lock in profit
                hedge_signal = Signal(
                    token_id=market.no_token_id,
                    side=Side.BUY,
                    reason=f"Volatility hedge: YES moved +{move:.1%}, buying NO @ {1-current_price:.1%}",
                    edge=move / 2,  # Edge is half the move (spread costs)
                    confidence=0.8,
                    market_id=market.id,
                    price_at_signal=current_price,
                    best_bid=market.best_bid,
                    best_ask=market.best_ask,
                    strategy_name=self.name,
                    metadata={
                        "phase": "hedge",
                        "initial_side": initial_side,
                        "entry_price": entry_price,
                        "hedge_price": current_price,
                        "move": move,
                    },
                )
            elif move <= -hedge_move:
                # YES went down, exit the position (stop loss)
                hedge_signal = Signal(
                    token_id=position.token_id,
                    side=Side.SELL,
                    reason=f"Volatility stop: YES moved {move:.1%}, exiting",
                    edge=0,
                    confidence=0.9,
                    market_id=market.id,
                    price_at_signal=current_price,
                    strategy_name=self.name,
                    metadata={"phase": "stop_loss"},
                )
        else:
            # Bought NO, hedge by buying YES when NO goes up (YES goes down)
            if move <= -hedge_move:
                # YES went down, NO went up, buy YES to lock in profit
                hedge_signal = Signal(
                    token_id=market.yes_token_id,
                    side=Side.BUY,
                    reason=f"Volatility hedge: NO moved +{-move:.1%}, buying YES @ {current_price:.1%}",
                    edge=abs(move) / 2,
                    confidence=0.8,
                    market_id=market.id,
                    price_at_signal=current_price,
                    best_bid=market.best_bid,
                    best_ask=market.best_ask,
                    strategy_name=self.name,
                    metadata={
                        "phase": "hedge",
                        "initial_side": initial_side,
                        "entry_price": entry_price,
                        "hedge_price": current_price,
                        "move": move,
                    },
                )
            elif move >= hedge_move:
                # YES went up, NO went down, exit the position (stop loss)
                hedge_signal = Signal(
                    token_id=position.token_id,
                    side=Side.SELL,
                    reason=f"Volatility stop: NO moved {-move:.1%}, exiting",
                    edge=0,
                    confidence=0.9,
                    market_id=market.id,
                    price_at_signal=current_price,
                    strategy_name=self.name,
                    metadata={"phase": "stop_loss"},
                )

        if hedge_signal:
            # Clean up pending hedge tracker
            if hedge_signal.metadata.get("phase") in ["hedge", "stop_loss"]:
                del self._pending_hedges[market.id]

        return hedge_signal

    def on_position_closed(self, position, pnl: float):
        """Clean up tracking when position closes."""
        if position.market_id in self._pending_hedges:
            del self._pending_hedges[position.market_id]
