"""
CSGO Comeback Buy Strategy.

Buys YES tokens when fallen favorites drop into the 20-35% range.

Hypothesis:
- team_yes tends to be the favorite (78% win rate when starting >60%)
- When favorites drop to 20-35%, market overreacts
- Actual win rate ~40% vs implied 25% = edge
- NO tokens at 30% have NO edge (weak underdogs staying weak)

Entry:
- YES price between 20-35%
- YES must have been >40% earlier (fallen favorite, not always-underdog)
- Game is in-play
- Spread < 5%
- BO3/BO5 only

Sizing:
- $30 at 35% (higher confidence)
- $15 at 20% (riskier, less size)
- Linear interpolation between

Exit:
- Pure hold to resolution (edge is in the resolution)
- No stop loss, no partial exits
"""

import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from src.csgo.engine.strategy import CSGOStrategy, Tick, Action, ActionType

logger = logging.getLogger(__name__)


class CSGOComebackBuyStrategy(CSGOStrategy):
    """
    Buy fallen favorites when YES drops to 20-35%.

    Detects when a favorite (was >40%) drops into value territory,
    then holds to resolution betting on comeback/market overreaction.
    """

    name = "csgo_comeback_buy"
    version = "1.0.0"

    # Market filters
    formats = ["BO3", "BO5"]  # More time for comebacks
    market_types = ["moneyline"]  # Match winner only

    # Entry parameters
    entry_price_min = 0.20  # Don't buy below 20%
    entry_price_max = 0.35  # Don't buy above 35%
    was_favorite_threshold = 0.40  # Must have been >40% to qualify as "fallen"
    max_spread = 0.05  # Max 5% spread

    # Lookback for drop detection
    lookback_minutes = 60.0  # Check if was >40% in last hour

    # Position sizing (linear: $30 at 35%, $15 at 20%)
    size_at_max_price = 30.0  # $30 when YES = 35%
    size_at_min_price = 15.0  # $15 when YES = 20%

    # Position limits
    max_position_usd = 30.0
    max_positions = 10

    def __init__(self, state_manager=None):
        """Initialize comeback buy strategy."""
        super().__init__(state_manager)
        # Track price history: market_id -> deque[(timestamp, yes_price)]
        self._price_history: Dict[int, deque] = {}
        # Track entered markets (max 1 per market)
        self._entered_markets: set = set()

    def on_tick(self, tick: Tick) -> Optional[Action]:
        """
        Check for entry when fallen favorite hits 20-35%.

        Only enters if:
        1. YES price is 20-35%
        2. YES was >40% earlier (fallen favorite)
        3. Game is in-play
        4. Spread < 5%
        5. Haven't already entered this market
        """
        market_id = tick.market_id

        # Update price history
        self._update_price_history(market_id, tick)

        # Already entered this market
        if market_id in self._entered_markets:
            return None

        # Must be in-play
        if not tick.is_in_play:
            return None

        # Need YES price
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Check spread
        if tick.spread is not None and tick.spread > self.max_spread:
            return None

        # Check price range
        if not (self.entry_price_min <= yes_price <= self.entry_price_max):
            return None

        # Check if was a favorite earlier (fallen favorite check)
        if not self._was_favorite(market_id):
            return None

        # Need token IDs
        if not tick.yes_token_id:
            return None

        # Check capital
        size_usd = self._calculate_size(yes_price)
        if not self.state.has_capacity(self.name, size_usd):
            return None

        # Entry signal!
        logger.info(
            f"[{self.name}] ENTRY: {tick.team_yes} vs {tick.team_no} - "
            f"Buy YES @ {yes_price:.2%}, ${size_usd:.2f} (fallen favorite)"
        )

        # Mark as entered
        self._entered_markets.add(market_id)

        return Action(
            action_type=ActionType.OPEN_LONG,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type="YES",
            size_usd=size_usd,
            strategy_name=self.name,
            reason=f"{self.name}: Fallen favorite YES @ {yes_price:.2%}",
            trigger_price=yes_price,
        )

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        No exit logic - pure hold to resolution.

        The edge is in the resolution, not in trading out.
        """
        # Update price history for tracking
        self._update_price_history(tick.market_id, tick)

        # No exits - hold to resolution
        return None

    def _calculate_size(self, yes_price: float) -> float:
        """
        Calculate position size based on YES price.

        Linear interpolation:
        - $30 at 35% (entry_price_max)
        - $15 at 20% (entry_price_min)

        Higher price = more size (more confidence in comeback).
        """
        # Linear interpolation
        price_range = self.entry_price_max - self.entry_price_min
        price_pct = (yes_price - self.entry_price_min) / price_range

        size_range = self.size_at_max_price - self.size_at_min_price
        size = self.size_at_min_price + (price_pct * size_range)

        return round(size, 2)

    def _update_price_history(self, market_id: int, tick: Tick) -> None:
        """Add current YES price to history."""
        if not tick.yes_price:
            return

        now = datetime.now(timezone.utc)

        if market_id not in self._price_history:
            self._price_history[market_id] = deque(maxlen=600)  # ~10 min at 1 tick/sec

        self._price_history[market_id].append((now, tick.yes_price))

    def _was_favorite(self, market_id: int) -> bool:
        """
        Check if YES was >40% at any point in the lookback window.

        Returns True if this is a "fallen favorite" scenario.
        """
        history = self._price_history.get(market_id)
        if not history:
            return False

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=self.lookback_minutes)

        # Check if any price in window was above threshold
        for ts, price in history:
            if ts >= cutoff and price >= self.was_favorite_threshold:
                return True

        return False

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        base = super().get_state()
        base.update({
            "entry_price_min": self.entry_price_min,
            "entry_price_max": self.entry_price_max,
            "was_favorite_threshold": self.was_favorite_threshold,
            "max_spread": self.max_spread,
            "lookback_minutes": self.lookback_minutes,
            "size_at_max_price": self.size_at_max_price,
            "size_at_min_price": self.size_at_min_price,
            "markets_tracked": len(self._price_history),
            "markets_entered": len(self._entered_markets),
        })
        return base
