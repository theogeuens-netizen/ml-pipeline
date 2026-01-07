"""
CSGO Scalping Strategy.

Buys both sides at 50/50 odds, swings on price movements.
The quintessential in-play trading strategy for close matches.

Entry:
- YES price between 45-55%
- Spread < 5%
- Game has started (in-play)
- BO3+ only (more time for swings)

Management:
- Sell winning side when it jumps 10%+
- Continue swinging until match ends

Exit:
- At extreme (>90%): Sell remaining WINNER shares only (good fills)
- NEVER force-sell the loser at extreme (terrible fills)
- Let loser go to 0 at resolution

IMPORTANT: This strategy holds the losing side to resolution rather than
force-selling at extreme prices where spreads are 30-50%+. The winner
captures profit, the loser goes to 0 naturally.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from src.csgo.engine.strategy import CSGOStrategy, Tick, Action, ActionType

logger = logging.getLogger(__name__)


class CSGOScalpStrategy(CSGOStrategy):
    """
    Scalping strategy for in-play CSGO markets.

    Opens spread positions when prices are near 50/50,
    sells winners and rebalances on price swings.
    """

    name = "csgo_scalp"
    version = "2.0.0"  # Updated: resolution mode for loser side

    # Filters (inherited from base)
    formats = ["BO3", "BO5"]  # Skip BO1 (too short)
    market_types = ["moneyline"]  # Match winner only

    # Position limits
    max_position_usd = 50.0  # Max per leg
    max_positions = 5  # Max concurrent spreads

    # Entry parameters
    entry_price_min = 0.45  # Min YES price for entry
    entry_price_max = 0.55  # Max YES price for entry
    max_entry_spread = 0.05  # Max bid-ask spread for entry (5%)
    max_exit_spread = 0.15   # Max bid-ask spread for partial exit (15%)
    position_size = 20.0  # USD per leg

    # Exit parameters
    jump_threshold = 0.10  # 10% price move triggers action
    partial_close_pct = 0.5  # Sell 50% on first jump
    extreme_threshold = 0.90  # Sell remaining winner at 90%+
    min_hold_seconds = 30.0  # Don't exit within 30s of entry (avoid noise)

    # Resolution mode: loser side is held to resolution, not force-sold
    # This avoids terrible fills when spreads are 30-50%+ at extreme prices

    def __init__(self, state_manager=None):
        """Initialize scalp strategy."""
        super().__init__(state_manager)
        # Track per-market state with SEPARATE baselines for YES and NO
        # This prevents cascading partial closes - each side tracks its own baseline
        self._yes_baselines = {}  # market_id -> YES price baseline for triggers
        self._no_baselines = {}   # market_id -> NO price baseline for triggers
        self._entry_prices = {}   # market_id -> original entry_yes_price (for reference)
        self._entry_times = {}    # market_id -> entry_timestamp

    def on_tick(self, tick: Tick) -> Optional[Action]:
        """
        Check for entry opportunity.

        Called when we have NO position on this market.
        """
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Must be in entry range
        if not (self.entry_price_min <= yes_price <= self.entry_price_max):
            return None

        # Check spread - reject if spread is too wide
        # Spread data now comes from match cache (CLOB snapshots) if tick spread is garbage
        if tick.spread is not None and tick.spread > self.max_entry_spread:
            return None

        # Game must have started
        if not tick.is_in_play:
            return None

        # Need token IDs
        if not tick.yes_token_id or not tick.no_token_id:
            logger.debug(f"Missing token IDs for market {tick.market_id}")
            return None

        # Check capital
        required = self.position_size * 2
        if not self.state.has_capacity(self.name, required):
            logger.debug(f"Insufficient capital for spread entry")
            return None

        # Entry signal!
        logger.info(
            f"[{self.name}] Entry signal: {tick.team_yes} vs {tick.team_no} @ {yes_price:.2%}"
        )

        # Store entry price and time for exit logic
        # Initialize SEPARATE baselines for YES and NO sides
        self._entry_prices[tick.market_id] = yes_price
        self._yes_baselines[tick.market_id] = yes_price      # YES baseline starts at entry
        # Use actual NO price from order book (not derived from YES)
        self._no_baselines[tick.market_id] = tick.no_price or (1 - yes_price)
        self._entry_times[tick.market_id] = datetime.now(timezone.utc)

        return Action(
            action_type=ActionType.OPEN_SPREAD,
            market_id=tick.market_id,
            condition_id=tick.condition_id,
            yes_size_usd=self.position_size,
            no_size_usd=self.position_size,
            strategy_name=self.name,
            reason=f"{self.name}: Scalp entry {tick.team_yes} vs {tick.team_no} @ {yes_price:.2%}",
            trigger_price=yes_price,
        )

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        Manage existing spread position.

        Called when we have a position on this market and receive a tick.
        Position can be CSGOPosition or CSGOSpread.
        """
        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Get entry price and initialize baselines if needed
        entry_price = self._entry_prices.get(tick.market_id)
        if not entry_price:
            # Try to get from spread (now returns dict)
            spread = self.state.get_spread(self.name, tick.market_id)
            if spread and spread.get("entry_yes_price"):
                entry_price = float(spread["entry_yes_price"])
                self._entry_prices[tick.market_id] = entry_price
            else:
                # Can't manage without knowing entry
                return None

        # Initialize baselines if not set (for existing positions)
        if tick.market_id not in self._yes_baselines:
            self._yes_baselines[tick.market_id] = entry_price
        if tick.market_id not in self._no_baselines:
            # Get actual NO entry price from DB instead of deriving from YES
            no_pos = self.state.get_position(self.name, tick.market_id, "NO")
            if no_pos and no_pos.get("avg_entry_price"):
                self._no_baselines[tick.market_id] = float(no_pos["avg_entry_price"])
            else:
                # Fallback to derived if NO position not found
                self._no_baselines[tick.market_id] = 1 - entry_price

        # Check minimum hold time - don't exit too quickly (avoid noise)
        entry_time = self._entry_times.get(tick.market_id)
        if entry_time:
            elapsed = (datetime.now(timezone.utc) - entry_time).total_seconds()
            if elapsed < self.min_hold_seconds:
                return None  # Still in hold period

        # Check spread before any exit action
        spread_ok = tick.spread is None or tick.spread <= self.max_exit_spread

        # Check for extreme - ONLY close the WINNER side, hold loser to resolution
        # This avoids terrible fills on the loser side (30-50% spreads at extremes)
        if yes_price >= self.extreme_threshold:
            # YES is winning (90%+), NO is losing (10%-)
            # Close remaining YES shares (good fills at 90%+)
            # Hold NO to resolution (will go to 0)
            yes_pos = self.state.get_position(self.name, tick.market_id, "YES")
            if yes_pos and float(yes_pos.get("remaining_shares", 0)) > 0:
                logger.info(
                    f"[{self.name}] YES extreme ({yes_price:.2%}): closing remaining YES (winner)"
                )
                return Action(
                    action_type=ActionType.PARTIAL_CLOSE,
                    market_id=tick.market_id,
                    condition_id=tick.condition_id,
                    token_type="YES",
                    close_pct=1.0,  # Close all remaining
                    strategy_name=self.name,
                    reason=f"{self.name}: Extreme exit YES @ {yes_price:.2%}",
                    trigger_price=yes_price,
                )
            # NO position will be held to resolution (goes to 0)
            return None

        if yes_price <= (1 - self.extreme_threshold):
            # NO is winning (90%+), YES is losing (10%-)
            # Close remaining NO shares (good fills at 90%+)
            # Hold YES to resolution (will go to 0)
            no_pos = self.state.get_position(self.name, tick.market_id, "NO")
            if no_pos and float(no_pos.get("remaining_shares", 0)) > 0:
                logger.info(
                    f"[{self.name}] NO extreme ({1-yes_price:.2%}): closing remaining NO (winner)"
                )
                return Action(
                    action_type=ActionType.PARTIAL_CLOSE,
                    market_id=tick.market_id,
                    condition_id=tick.condition_id,
                    token_type="NO",
                    close_pct=1.0,  # Close all remaining
                    strategy_name=self.name,
                    reason=f"{self.name}: Extreme exit NO @ {1-yes_price:.2%}",
                    trigger_price=yes_price,
                )
            # YES position will be held to resolution (goes to 0)
            return None

        # Skip partial exits if spread too wide
        if not spread_ok:
            logger.debug(
                f"[{self.name}] Skip exit: spread {tick.spread:.1%} > {self.max_exit_spread:.0%}"
            )
            return None

        # Use SEPARATE baselines for YES and NO to prevent cascading
        # Each side only triggers when it moves 10pt from ITS OWN baseline
        # Note: baselines should already be initialized above, but provide safe fallback
        yes_baseline = self._yes_baselines.get(tick.market_id, entry_price)
        no_baseline = self._no_baselines.get(tick.market_id)

        # If NO baseline still not set, skip NO side checks until it's initialized
        if no_baseline is None:
            logger.debug(f"[{self.name}] NO baseline not initialized for market {tick.market_id}")
            no_baseline = 1 - entry_price  # Temporary fallback to avoid None errors

        # IMPORTANT: Use actual NO price from order book, not derived!
        # YES and NO have separate order books - prices don't sum to 100%
        no_price = tick.no_price
        if no_price is None:
            # Fallback to derived if no actual NO price available
            no_price = 1 - yes_price

        yes_change = yes_price - yes_baseline  # Positive = YES jumped up
        no_change = no_price - no_baseline     # Positive = NO jumped up (YES dropped)

        # Check YES side - did YES jump up from YES baseline?
        if yes_change >= self.jump_threshold:
            logger.info(
                f"[{self.name}] YES jumped +{yes_change:.2%} (from baseline {yes_baseline:.2%}), selling partial"
            )
            # Reset YES baseline to current price - next trigger needs another 10pt move
            self._yes_baselines[tick.market_id] = yes_price
            return Action(
                action_type=ActionType.PARTIAL_CLOSE,
                market_id=tick.market_id,
                condition_id=tick.condition_id,
                token_type="YES",
                close_pct=self.partial_close_pct,
                strategy_name=self.name,
                reason=f"{self.name}: YES jump +{yes_change:.2%} from {yes_baseline:.2%}",
                trigger_price=yes_price,
            )

        # Check NO side - did NO jump up from NO baseline?
        if no_change >= self.jump_threshold:
            logger.info(
                f"[{self.name}] NO jumped +{no_change:.2%} (from baseline {no_baseline:.2%}), selling partial"
            )
            # Reset NO baseline to current price - next trigger needs another 10pt move
            self._no_baselines[tick.market_id] = no_price
            return Action(
                action_type=ActionType.PARTIAL_CLOSE,
                market_id=tick.market_id,
                condition_id=tick.condition_id,
                token_type="NO",
                close_pct=self.partial_close_pct,
                strategy_name=self.name,
                reason=f"{self.name}: NO jump +{no_change:.2%} from {no_baseline:.2%}",
                trigger_price=yes_price,
            )

        return None

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        base = super().get_state()
        base.update({
            "entry_price_min": self.entry_price_min,
            "entry_price_max": self.entry_price_max,
            "max_entry_spread": self.max_entry_spread,
            "max_exit_spread": self.max_exit_spread,
            "position_size": self.position_size,
            "jump_threshold": self.jump_threshold,
            "partial_close_pct": self.partial_close_pct,
            "extreme_threshold": self.extreme_threshold,
            "tracked_entries": len(self._entry_prices),
            "yes_baselines": len(self._yes_baselines),
            "no_baselines": len(self._no_baselines),
        })
        return base
