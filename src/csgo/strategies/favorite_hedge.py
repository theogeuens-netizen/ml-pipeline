"""
CSGO Favorite Hedge Strategy.

Buys the favorite 3 minutes after game start, with optional hedge.

Entry:
- Wait until 3-8 minutes after game_start_time
- Buy the favorite (whichever side is 55-65%)
- Size: $10 at 55%, scaling linearly to $50 at 65%
- BO3/BO5 only

Hedge:
- Triggered when favorite reaches 85%
- Hedge size: 1/4 of original entry

Max trades: 2 per market (entry + hedge)
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict

from src.csgo.engine.strategy import CSGOStrategy, Tick, Action, ActionType

logger = logging.getLogger(__name__)


class CSGOFavoriteHedgeStrategy(CSGOStrategy):
    """
    Buy favorite at 3 mins, hedge if it rises 25 points.
    """

    name = "csgo_favorite_hedge"
    version = "1.0.0"

    # Format filter (BO3/BO5 only)
    formats = ["BO3", "BO5"]
    market_types = ["moneyline"]

    # Entry timing
    entry_minutes_min = 3.0  # Earliest entry
    entry_minutes_max = 8.0  # Latest entry

    # Position sizing (linear interpolation)
    min_fav_price = 0.55  # Don't enter below this (not clear favorite)
    max_fav_price = 0.65  # Don't enter above this (too lopsided)
    min_size_usd = 10.0   # Size at min_fav_price
    max_size_usd = 50.0   # Size at max_fav_price
    max_entry_spread = 0.10  # Don't enter if spread > 10%
    max_exit_spread = 0.05   # Don't exit if spread > 5%

    # Hedge parameters
    hedge_trigger_price = 0.85  # Hedge when favorite reaches 85%
    hedge_size_ratio = 1 / 4    # Hedge = 1/4 of entry size

    # Position limits
    max_position_usd = 50.0
    max_positions = 10

    def __init__(self, state_manager=None):
        """Initialize favorite hedge strategy."""
        super().__init__(state_manager)
        # In-memory state tracking per market
        # market_id -> {stage, entry_price, entry_size_usd, entry_token, hedge_triggered}
        self._market_states: Dict[int, dict] = {}

    def on_tick(self, tick: Tick) -> Optional[Action]:
        """
        Check for entry opportunity at 3 mins after game start.
        """
        market_id = tick.market_id

        # Get or create market state
        state = self._market_states.get(market_id)
        if state and state.get("stage") != "WAITING":
            # Already entered or done with this market
            return None

        # Check timing - must be 3-8 mins after start
        mins = tick.minutes_since_start
        if mins is None:
            return None

        # Entry window: 3 to 8 minutes
        if mins < self.entry_minutes_min or mins > self.entry_minutes_max:
            return None

        # Determine favorite and price
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Favorite is whichever side is >50%
        if yes_price >= 0.5:
            favorite_token = "YES"
            favorite_price = yes_price
        else:
            favorite_token = "NO"
            favorite_price = 1 - yes_price  # NO price

        # Must be clear favorite (>= min_fav_price) but not too lopsided (<= max_fav_price)
        if favorite_price < self.min_fav_price:
            logger.debug(
                f"[{self.name}] {tick.team_yes} vs {tick.team_no}: "
                f"Favorite at {favorite_price:.2%} < {self.min_fav_price:.2%}, skipping"
            )
            return None

        if favorite_price > self.max_fav_price:
            logger.debug(
                f"[{self.name}] {tick.team_yes} vs {tick.team_no}: "
                f"Favorite at {favorite_price:.2%} > {self.max_fav_price:.2%}, too lopsided"
            )
            return None

        # Check spread
        if tick.spread and tick.spread > self.max_entry_spread:
            logger.info(
                f"[{self.name}] SKIP: {tick.team_yes} vs {tick.team_no} - "
                f"spread {tick.spread:.1%} > {self.max_entry_spread:.0%}"
            )
            return None

        # Calculate position size (linear interpolation)
        size_usd = self._calculate_size(favorite_price)

        # Check capital
        if not self.state.has_capacity(self.name, size_usd):
            logger.debug(f"[{self.name}] Insufficient capital for ${size_usd:.2f}")
            return None

        # Need token IDs
        if not tick.yes_token_id or not tick.no_token_id:
            logger.debug(f"[{self.name}] Missing token IDs for market {market_id}")
            return None

        # Entry signal!
        logger.info(
            f"[{self.name}] ENTRY: {tick.team_yes} vs {tick.team_no} - "
            f"Buy {favorite_token} @ {favorite_price:.2%}, ${size_usd:.2f}"
        )

        # Initialize market state
        self._market_states[market_id] = {
            "stage": "ENTERED",
            "entry_price": favorite_price,
            "entry_size_usd": size_usd,
            "entry_token": favorite_token,
            "hedge_triggered": False,
        }

        return Action(
            action_type=ActionType.OPEN_LONG,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type=favorite_token,
            size_usd=size_usd,
            strategy_name=self.name,
            reason=f"{self.name}: Buy favorite {favorite_token} @ {favorite_price:.2%}",
            trigger_price=yes_price,
        )

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        Check for hedge opportunity when favorite rises 25 points.
        """
        market_id = tick.market_id
        state = self._market_states.get(market_id)

        if not state:
            # No tracked state, can't manage
            return None

        if state.get("stage") != "ENTERED":
            # Not in entered stage (already hedged or done)
            return None

        if state.get("hedge_triggered"):
            # Already triggered hedge
            return None

        entry_price = state.get("entry_price")
        entry_token = state.get("entry_token")
        entry_size = state.get("entry_size_usd")

        if not entry_price or not entry_token or not entry_size:
            return None

        # Get current favorite price
        yes_price = tick.yes_price
        if not yes_price:
            return None

        if entry_token == "YES":
            current_fav_price = yes_price
        else:
            current_fav_price = 1 - yes_price  # NO price

        # Check if favorite has reached hedge trigger price (85%)
        if current_fav_price < self.hedge_trigger_price:
            # Not at trigger yet
            return None

        # Check spread before hedging
        if tick.spread and tick.spread > self.max_exit_spread:
            logger.debug(
                f"[{self.name}] Skip hedge: spread {tick.spread:.1%} > {self.max_exit_spread:.0%}"
            )
            return None

        # Hedge trigger! Buy the underdog
        underdog_token = "NO" if entry_token == "YES" else "YES"
        hedge_size = entry_size * self.hedge_size_ratio

        logger.info(
            f"[{self.name}] HEDGE: {tick.team_yes} vs {tick.team_no} - "
            f"Buy {underdog_token} @ {1-current_fav_price:.2%}, ${hedge_size:.2f} "
            f"(favorite hit {current_fav_price:.0%})"
        )

        # Mark hedge as triggered
        state["hedge_triggered"] = True
        state["stage"] = "HEDGED"

        return Action(
            action_type=ActionType.OPEN_LONG,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type=underdog_token,
            size_usd=hedge_size,
            strategy_name=self.name,
            reason=f"{self.name}: Hedge with {underdog_token}, fav hit {current_fav_price:.0%}",
            trigger_price=yes_price,
        )

    def _calculate_size(self, favorite_price: float) -> float:
        """
        Calculate position size based on favorite price.

        Linear interpolation:
        - Price 0.55 -> $10
        - Price 0.75 -> $50
        - Below 0.55 -> skip (handled elsewhere)
        - Above 0.75 -> $50 (capped)
        """
        if favorite_price >= self.max_fav_price:
            return self.max_size_usd

        # Linear interpolation
        price_range = self.max_fav_price - self.min_fav_price
        size_range = self.max_size_usd - self.min_size_usd

        price_pct = (favorite_price - self.min_fav_price) / price_range
        size = self.min_size_usd + (price_pct * size_range)

        return round(size, 2)

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        base = super().get_state()
        base.update({
            "entry_minutes_min": self.entry_minutes_min,
            "entry_minutes_max": self.entry_minutes_max,
            "min_fav_price": self.min_fav_price,
            "max_fav_price": self.max_fav_price,
            "min_size_usd": self.min_size_usd,
            "max_size_usd": self.max_size_usd,
            "max_entry_spread": self.max_entry_spread,
            "max_exit_spread": self.max_exit_spread,
            "hedge_trigger_price": self.hedge_trigger_price,
            "hedge_size_ratio": self.hedge_size_ratio,
            "tracked_markets": len(self._market_states),
            "markets_entered": sum(
                1 for s in self._market_states.values()
                if s.get("stage") in ["ENTERED", "HEDGED"]
            ),
            "markets_hedged": sum(
                1 for s in self._market_states.values()
                if s.get("stage") == "HEDGED"
            ),
        })
        return base
