"""
CSGO Map Longshot Strategy.

Buys map winner markets after rapid price drops.

Entry:
- Market type: moneyline, child_moneyline (series + map winners)
- Price drops rapidly (15+ points in 5 min window)
- Current price between 0.05 and 0.20
- Spread < 10%
- No open position on that side (can re-enter after selling)

Exit:
- Sell when price jumps 20 points from entry
- E.g., buy at 0.15, sell at 0.35

This captures "comeback" scenarios where a team loses early rounds
but the market overreacts, creating value on the underdog.

Can profit from multiple swings in the same match - after selling
a position at 100% profit, can re-enter if price drops again.
"""

import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from src.csgo.engine.strategy import CSGOStrategy, Tick, Action, ActionType

logger = logging.getLogger(__name__)


class CSGOMapLongshotStrategy(CSGOStrategy):
    """
    Buy map winners after rapid price crashes.

    Detects when a price drops quickly (panic selling) vs slow bleed,
    and buys if the price is cheap enough for a potential comeback.
    """

    name = "csgo_map_longshot"
    version = "1.0.0"

    # Market filters - match and map winners
    formats = ["BO3", "BO5"]
    market_types = ["moneyline", "child_moneyline"]  # Both series and map winners

    # Entry parameters
    entry_price_min = 0.05  # Don't buy below 5% (likely dead)
    entry_price_max = 0.20  # Don't buy above 20% (not cheap enough)
    max_spread = 0.10       # Max 10% spread

    # Drop detection
    drop_threshold = 0.15   # 15 point drop required
    lookback_minutes = 5.0  # Window to detect drop
    entry_cooldown_minutes = 1.0  # Wait 1 min after crash detected (let volume settle)

    # Exit parameters
    exit_jump_points = 0.20  # Sell on 20-point rise (e.g., 15% -> 35%)

    # Position sizing
    position_size = 15.0     # USD per trade
    max_position_usd = 50.0
    max_positions = 10       # Across all markets

    def __init__(self, state_manager=None):
        """Initialize map longshot strategy."""
        super().__init__(state_manager)
        # Track price history per market: market_id -> deque[(timestamp, yes_price)]
        self._price_history: Dict[int, deque] = {}
        # Track OPEN positions per market: market_id -> set of token_types
        # Removed when position is closed, allowing re-entry on same side
        self._open_positions: Dict[int, set] = {}
        # Track entry prices for exit calculation: (market_id, token_type) -> entry_price
        self._entry_prices: Dict[tuple, float] = {}
        # Track trade count per side for stats: (market_id, token_type) -> count
        self._trade_counts: Dict[tuple, int] = {}
        # Track when a drop was first detected: (market_id, token_type) -> timestamp
        # Entry only allowed after cooldown period (let volume settle)
        self._drop_detected_at: Dict[tuple, datetime] = {}

    def on_tick(self, tick: Tick) -> Optional[Action]:
        """
        Check for entry on rapid price drop.

        Only enters if:
        1. Price dropped 15+ points in last 5 minutes
        2. Current price is between 0.05 and 0.20
        3. Spread is < 10%
        4. Haven't already traded this side
        """
        market_id = tick.market_id

        # Update price history
        self._update_price_history(market_id, tick)

        # Need price data
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Check spread
        if tick.spread and tick.spread > self.max_spread:
            return None

        # Need token IDs for trading
        if not tick.yes_token_id or not tick.no_token_id:
            return None

        # Check YES side opportunity
        yes_action = self._check_entry_opportunity(
            tick, "YES", yes_price, tick.yes_token_id
        )
        if yes_action:
            return yes_action

        # Check NO side opportunity
        no_price = 1 - yes_price
        no_action = self._check_entry_opportunity(
            tick, "NO", no_price, tick.no_token_id
        )
        if no_action:
            return no_action

        return None

    def _check_entry_opportunity(
        self,
        tick: Tick,
        token_type: str,
        current_price: float,
        token_id: str,
    ) -> Optional[Action]:
        """Check if we should enter on this side."""
        market_id = tick.market_id
        drop_key = (market_id, token_type)
        now = datetime.now(timezone.utc)

        # Check if we already have an OPEN position on this side
        # (can re-enter after previous position was sold)
        open_sides = self._open_positions.get(market_id, set())
        if token_type in open_sides:
            return None

        # Check price range
        if not (self.entry_price_min <= current_price <= self.entry_price_max):
            # Price out of range - clear any pending drop detection
            self._drop_detected_at.pop(drop_key, None)
            return None

        # Check for rapid drop
        drop = self._calculate_drop(market_id, token_type)
        if drop is None or drop < self.drop_threshold:
            # No drop or not big enough - clear pending detection
            self._drop_detected_at.pop(drop_key, None)
            return None

        # Drop detected! Check cooldown
        if drop_key not in self._drop_detected_at:
            # First time detecting this drop - start cooldown timer
            self._drop_detected_at[drop_key] = now
            logger.info(
                f"[{self.name}] CRASH DETECTED: {tick.team_yes} vs {tick.team_no} "
                f"({tick.market_type}) - {token_type} @ {current_price:.2%} "
                f"(dropped {drop:.0%}) - waiting {self.entry_cooldown_minutes} min..."
            )
            return None

        # Check if cooldown has passed
        detected_at = self._drop_detected_at[drop_key]
        elapsed_minutes = (now - detected_at).total_seconds() / 60.0

        if elapsed_minutes < self.entry_cooldown_minutes:
            # Still in cooldown - wait for volume to settle
            return None

        # Cooldown passed - ready to enter!

        # Check capital
        if not self.state.has_capacity(self.name, self.position_size):
            return None

        # Track trade count for this side
        trade_num = self._trade_counts.get(drop_key, 0) + 1
        self._trade_counts[drop_key] = trade_num

        # Entry signal!
        logger.info(
            f"[{self.name}] ENTRY #{trade_num}: {tick.team_yes} vs {tick.team_no} "
            f"({tick.market_type}) - Buy {token_type} @ {current_price:.2%} "
            f"(dropped {drop:.0%}, waited {elapsed_minutes:.1f} min)"
        )

        # Clear drop detection (will need new drop to re-enter after exit)
        self._drop_detected_at.pop(drop_key, None)

        # Track that we have an open position on this side
        if market_id not in self._open_positions:
            self._open_positions[market_id] = set()
        self._open_positions[market_id].add(token_type)

        # Track entry price for exit
        self._entry_prices[drop_key] = current_price

        return Action(
            action_type=ActionType.OPEN_LONG,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type=token_type,
            size_usd=self.position_size,
            strategy_name=self.name,
            reason=f"{self.name}: {token_type} dropped {drop:.0%}, entry #{trade_num} @ {current_price:.2%}",
            trigger_price=tick.yes_price,
        )

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        Check for exit when position doubles.

        Sells when current price >= 2x entry price.
        """
        market_id = tick.market_id

        # Update price history
        self._update_price_history(market_id, tick)

        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Determine which token type this position is
        token_type = getattr(position, 'token_type', None)
        if not token_type:
            return None

        # Get entry price
        entry_price = self._entry_prices.get((market_id, token_type))
        if not entry_price:
            # Try to get from position
            entry_price = getattr(position, 'avg_entry_price', None)
            if not entry_price:
                return None
            entry_price = float(entry_price)

        # Calculate current price for our token
        if token_type == "YES":
            current_price = yes_price
        else:
            current_price = 1 - yes_price

        # Check if price has jumped 20 points from entry
        price_jump = current_price - entry_price

        if price_jump >= self.exit_jump_points:
            profit_pct = (current_price - entry_price) / entry_price
            trade_num = self._trade_counts.get((market_id, token_type), 1)

            logger.info(
                f"[{self.name}] EXIT #{trade_num}: {tick.team_yes} vs {tick.team_no} - "
                f"Sell {token_type} @ {current_price:.2%} "
                f"(+{price_jump:.0%} jump from {entry_price:.2%})"
            )

            # Clear entry price tracking
            self._entry_prices.pop((market_id, token_type), None)

            # Remove from open positions - ALLOWS RE-ENTRY on next drop!
            if market_id in self._open_positions:
                self._open_positions[market_id].discard(token_type)

            return Action(
                action_type=ActionType.CLOSE,
                market_id=market_id,
                condition_id=tick.condition_id,
                token_type=token_type,
                strategy_name=self.name,
                reason=f"{self.name}: +{price_jump:.0%} jump hit (trade #{trade_num})",
                trigger_price=yes_price,
            )

        return None

    def _update_price_history(self, market_id: int, tick: Tick) -> None:
        """Add current price to history."""
        if not tick.yes_price:
            return

        now = datetime.now(timezone.utc)

        if market_id not in self._price_history:
            self._price_history[market_id] = deque(maxlen=300)

        self._price_history[market_id].append((now, tick.yes_price))

    def _calculate_drop(self, market_id: int, token_type: str) -> Optional[float]:
        """
        Calculate how much the price dropped in the lookback window.

        Returns the drop amount (e.g., 0.15 for 15 points) or None if
        insufficient data.
        """
        history = self._price_history.get(market_id)
        if not history or len(history) < 2:
            return None

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=self.lookback_minutes)

        # Get prices within window
        prices_in_window = [
            (ts, price) for ts, price in history if ts >= cutoff
        ]

        if len(prices_in_window) < 2:
            return None

        # For YES token, we want to detect YES price dropping
        # For NO token, we want to detect NO price dropping (YES price rising)
        if token_type == "YES":
            # YES dropped = max - current (positive value)
            max_price = max(p for _, p in prices_in_window)
            current_price = prices_in_window[-1][1]
            drop = max_price - current_price
        else:
            # NO dropped = (1-min_yes) - (1-current_yes) = current_yes - min_yes
            min_yes = min(p for _, p in prices_in_window)
            current_yes = prices_in_window[-1][1]
            drop = current_yes - min_yes

        return drop if drop > 0 else None

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        base = super().get_state()
        total_trades = sum(self._trade_counts.values())
        base.update({
            "entry_price_min": self.entry_price_min,
            "entry_price_max": self.entry_price_max,
            "max_spread": self.max_spread,
            "drop_threshold": self.drop_threshold,
            "lookback_minutes": self.lookback_minutes,
            "entry_cooldown_minutes": self.entry_cooldown_minutes,
            "exit_jump_points": self.exit_jump_points,
            "position_size": self.position_size,
            "markets_tracked": len(self._price_history),
            "open_positions": len(self._entry_prices),
            "pending_cooldowns": len(self._drop_detected_at),  # Crashes detected, waiting to enter
            "total_trades": total_trades,  # Includes re-entries
            "markets_with_multiple_trades": sum(
                1 for count in self._trade_counts.values() if count > 1
            ),
        })
        return base
