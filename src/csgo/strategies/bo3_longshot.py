"""
CSGO BO3 Longshot Rebound Strategy.

Exploits the fact that BO3 matches can swing heavily - losing map 1
doesn't mean losing the series. Buys when price crashes, sells partial
on rebound, holds remainder to resolution.

Entry:
- BO3 only (not BO1/BO5)
- First 90 minutes after game_start_time
- Tier 1: Price < 20% (and >= 5%) -> $20
- Tier 2: Price < 10% (and >= 5%) -> $30 (stacks with Tier 1)
- Both YES and NO sides tracked
- Max spread: 10%
- 5-min cooldown between positions on same side

Exit:
- Sell 70% when price doubles (100% profit from avg entry)
- Hold remaining 30% to resolution
- After partial exit, can re-enter on fresh crash (after cooldown)
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple

from src.csgo.engine.strategy import CSGOStrategy, Tick, Action, ActionType

logger = logging.getLogger(__name__)


class CSGOB03LongshotStrategy(CSGOStrategy):
    """
    BO3 longshot rebound strategy.

    Buys crashed prices in BO3 matches, takes 70% profit on double,
    holds 30% to resolution.
    """

    name = "csgo_bo3_longshot"
    version = "1.0.0"

    # Market filters - BO3 only
    formats = ["BO3"]
    market_types = ["moneyline"]

    # Timing
    entry_window_minutes = 90.0  # Only enter in first 1.5 hours

    # Entry tiers (price level triggers)
    tier1_threshold = 0.20  # Buy $20 when price < 20%
    tier1_size = 20.0
    tier2_threshold = 0.10  # Buy $30 when price < 10% (stacks)
    tier2_size = 30.0

    # Price limits
    min_price = 0.05  # Don't buy below 5%
    max_spread = 0.10  # Max 10% spread

    # Exit parameters
    profit_target_pct = 1.00  # 100% profit = price doubled
    partial_close_pct = 0.70  # Sell 70% at target

    # Cooldown
    cooldown_minutes = 5.0  # 5 min between exits and re-entries

    # Position limits
    max_position_usd = 100.0
    max_positions = 10

    def __init__(self, state_manager=None):
        """Initialize BO3 longshot strategy."""
        super().__init__(state_manager)
        # Track state per (market_id, token_type)
        # Each entry: {
        #   "entries": [(price, size_usd), ...],  # Entry levels hit
        #   "tiers_hit": set(),  # {1, 2} - which tiers entered
        #   "partial_closed": bool,  # Already took 70% profit?
        #   "last_exit_time": datetime,  # For cooldown
        #   "holding_remainder": bool,  # Holding 30% to resolution
        # }
        self._positions: Dict[Tuple[int, str], dict] = {}
        # Stats
        self._total_entries = 0
        self._total_partial_exits = 0

    def on_tick(self, tick: Tick) -> Optional[Action]:
        """
        Check for entry opportunities on both YES and NO sides.
        """
        market_id = tick.market_id

        # Check timing - must be within entry window
        mins = tick.minutes_since_start
        if mins is None or mins < 0:
            return None  # Game hasn't started

        if mins > self.entry_window_minutes:
            return None  # Past entry window

        # Need price data
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Check spread
        if tick.spread is not None and tick.spread > self.max_spread:
            return None

        # Need token IDs
        if not tick.yes_token_id or not tick.no_token_id:
            return None

        # Check YES side
        yes_action = self._check_entry(tick, "YES", yes_price)
        if yes_action:
            return yes_action

        # Check NO side (use actual NO price from order book)
        no_price = tick.no_price or (1 - yes_price)
        no_action = self._check_entry(tick, "NO", no_price)
        if no_action:
            return no_action

        return None

    def _check_entry(
        self, tick: Tick, token_type: str, current_price: float
    ) -> Optional[Action]:
        """Check if we should enter on this side."""
        market_id = tick.market_id
        key = (market_id, token_type)
        now = datetime.now(timezone.utc)

        # Get or create position state
        state = self._positions.get(key)

        # If holding remainder to resolution, no new entries on this side
        if state and state.get("holding_remainder"):
            return None

        # Check cooldown (if we recently exited)
        if state and state.get("last_exit_time"):
            elapsed = (now - state["last_exit_time"]).total_seconds() / 60.0
            if elapsed < self.cooldown_minutes:
                return None
            # Cooldown passed - can start fresh
            if state.get("partial_closed"):
                # Reset state for fresh entries
                state = None
                self._positions.pop(key, None)

        # Price must be in valid range
        if current_price < self.min_price:
            return None  # Too cheap, probably dead

        # Determine which tier(s) to enter
        tiers_to_enter = []

        if current_price < self.tier1_threshold:
            # Check if tier 1 already entered
            tiers_hit = state["tiers_hit"] if state else set()
            if 1 not in tiers_hit:
                tiers_to_enter.append((1, self.tier1_size))

        if current_price < self.tier2_threshold:
            # Check if tier 2 already entered
            tiers_hit = state["tiers_hit"] if state else set()
            if 2 not in tiers_hit:
                tiers_to_enter.append((2, self.tier2_size))

        if not tiers_to_enter:
            return None

        # Enter the first available tier (will get second on next tick if applicable)
        tier_num, size_usd = tiers_to_enter[0]

        # Check capital
        if not self.state.has_capacity(self.name, size_usd):
            return None

        # Initialize or update state
        if not state:
            state = {
                "entries": [],
                "tiers_hit": set(),
                "partial_closed": False,
                "last_exit_time": None,
                "holding_remainder": False,
            }
            self._positions[key] = state

        # Record entry
        state["entries"].append((current_price, size_usd))
        state["tiers_hit"].add(tier_num)
        self._total_entries += 1

        tier_label = f"T{tier_num}"
        logger.info(
            f"[{self.name}] ENTRY {tier_label}: {tick.team_yes} vs {tick.team_no} - "
            f"Buy {token_type} @ {current_price:.2%}, ${size_usd:.0f} "
            f"(mins={tick.minutes_since_start:.1f})"
        )

        return Action(
            action_type=ActionType.OPEN_LONG,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type=token_type,
            size_usd=size_usd,
            strategy_name=self.name,
            reason=f"{self.name}: {tier_label} entry @ {current_price:.2%}",
            trigger_price=tick.yes_price,
        )

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        Check for exit on 100% profit.

        Sells 70% at target, holds 30% to resolution.
        """
        market_id = tick.market_id

        # Get token type from position
        token_type = getattr(position, "token_type", None)
        if not token_type:
            return None

        key = (market_id, token_type)
        state = self._positions.get(key)

        if not state:
            return None

        # If already took partial profit, just hold to resolution
        if state.get("partial_closed") or state.get("holding_remainder"):
            return None

        # Get actual avg entry price from DB (includes slippage from fills)
        db_position = self.state.get_position(self.name, market_id, token_type)
        if db_position and db_position.get("avg_entry_price"):
            avg_entry_price = float(db_position["avg_entry_price"])
        else:
            # Fallback to internal tracking if DB not available
            entries = state.get("entries", [])
            if not entries:
                return None
            total_cost = sum(price * size for price, size in entries)
            total_size = sum(size for _, size in entries)
            if total_size == 0:
                return None
            avg_entry_price = total_cost / total_size

        # Get current price
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Get current price for our token (use actual NO price from order book)
        if token_type == "YES":
            current_price = yes_price
        else:
            current_price = tick.no_price or (1 - yes_price)

        # Check if profit target hit (100% = price doubled)
        target_price = avg_entry_price * (1 + self.profit_target_pct)

        if current_price >= target_price:
            profit_pct = (current_price - avg_entry_price) / avg_entry_price
            tiers_str = ",".join(f"T{t}" for t in sorted(state["tiers_hit"]))

            logger.info(
                f"[{self.name}] PARTIAL EXIT: {tick.team_yes} vs {tick.team_no} - "
                f"Sell 70% {token_type} @ {current_price:.2%} "
                f"(+{profit_pct:.0%} from avg {avg_entry_price:.2%}, tiers={tiers_str})"
            )

            # Update state
            state["partial_closed"] = True
            state["holding_remainder"] = True
            state["last_exit_time"] = datetime.now(timezone.utc)
            self._total_partial_exits += 1

            return Action(
                action_type=ActionType.PARTIAL_CLOSE,
                market_id=market_id,
                condition_id=tick.condition_id,
                token_type=token_type,
                close_pct=self.partial_close_pct,
                strategy_name=self.name,
                reason=f"{self.name}: +{profit_pct:.0%} hit, sell 70%",
                trigger_price=yes_price,
            )

        return None

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        base = super().get_state()

        active_positions = sum(
            1 for s in self._positions.values()
            if s.get("entries") and not s.get("partial_closed")
        )
        holding_remainder = sum(
            1 for s in self._positions.values()
            if s.get("holding_remainder")
        )

        base.update({
            "entry_window_minutes": self.entry_window_minutes,
            "tier1_threshold": self.tier1_threshold,
            "tier1_size": self.tier1_size,
            "tier2_threshold": self.tier2_threshold,
            "tier2_size": self.tier2_size,
            "min_price": self.min_price,
            "max_spread": self.max_spread,
            "profit_target_pct": self.profit_target_pct,
            "partial_close_pct": self.partial_close_pct,
            "cooldown_minutes": self.cooldown_minutes,
            "active_positions": active_positions,
            "holding_remainder": holding_remainder,
            "total_entries": self._total_entries,
            "total_partial_exits": self._total_partial_exits,
        })
        return base
