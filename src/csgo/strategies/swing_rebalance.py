"""
CSGO Swing Rebalance Strategy.

Buys both sides around game start, rebalances on 15-point swings.

Entry:
- 30 minutes before to 15 minutes after game start
- YES price between 40-60% (skip if outside)
- BO3/BO5 only

Rebalancing:
- Compare current price to oldest price in 5-min window
- If moved +15 points AND YES is 15+ points above cost: sell 50% YES, buy NO with cost basis
- If moved -15 points AND NO is 15+ points above cost: sell 50% NO, buy YES with cost basis
- 3 min cooldown between trades
- Don't let either side drop below 30% of total holdings

Exit:
- None - ride to resolution
"""

import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple

from src.csgo.engine.strategy import CSGOStrategy, Tick, Action, ActionType

logger = logging.getLogger(__name__)


class CSGOSwingRebalanceStrategy(CSGOStrategy):
    """
    Swing trading with profit extraction.

    Triggers on 15 absolute point moves within 5-min window.
    Only sells if profitable. Extracts profit, reinvests cost basis.
    """

    name = "csgo_swing_rebalance"
    version = "3.0.0"

    # Filters
    formats = ["BO3", "BO5"]
    market_types = ["moneyline"]

    # Entry parameters
    entry_minutes_min = -30.0  # 30 min before game start
    entry_minutes_max = 15.0   # 15 min after game start
    entry_price_min = 0.40   # Skip if outside this range
    entry_price_max = 0.60
    max_spread = 0.05            # Only enter if spread <= 5%
    extreme_price_min = 0.10     # Skip if YES below 10% (near resolution)
    extreme_price_max = 0.90     # Skip if YES above 90% (near resolution)
    position_size = 20.0             # USD per leg

    # Trigger threshold
    trigger_points = 0.15  # 15 absolute points move in 5-min window
    min_profit_points = 0.15  # Need 15 points above cost basis to sell

    # Rebalance parameters
    sell_pct = 0.50              # Sell 50% of winning side
    min_holding_pct = 0.30       # Don't let either side drop below 30%
    cooldown_minutes = 3.0       # 3 min between trades
    lookback_minutes = 5.0       # Window for price comparison

    # Position limits
    max_position_usd = 100.0
    max_positions = 5

    def __init__(self, state_manager=None):
        """Initialize swing rebalance strategy."""
        super().__init__(state_manager)
        self._market_states: Dict[int, dict] = {}

    def _get_db_positions(self, market_id: int) -> Tuple[Optional[dict], Optional[dict]]:
        """
        Get actual YES and NO positions from database.

        Returns (yes_pos, no_pos) dicts with remaining_shares, avg_entry_price, cost_basis.
        Returns (None, None) if no positions found.
        """
        yes_pos = self.state.get_position(self.name, market_id, "YES")
        no_pos = self.state.get_position(self.name, market_id, "NO")
        return yes_pos, no_pos

    def on_tick(self, tick: Tick) -> Optional[Action]:
        """Check for entry at 5 min after game start."""
        market_id = tick.market_id

        # Already tracking this market - update price history
        if market_id in self._market_states:
            self._update_price_history(market_id, tick)
            return None

        # Check timing - must be within entry window relative to game start
        mins = tick.minutes_since_start
        if mins is None:
            return None

        # Entry window: -30 min (before) to +15 min (after) game start
        if mins < self.entry_minutes_min or mins > self.entry_minutes_max:
            return None

        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Skip if market is near resolution (extreme prices)
        if yes_price < self.extreme_price_min or yes_price > self.extreme_price_max:
            logger.info(
                f"[{self.name}] SKIP: {tick.team_yes} vs {tick.team_no} @ {yes_price:.2%} "
                f"(extreme price - near resolution)"
            )
            self._market_states[market_id] = {"skipped": True}
            return None

        # Skip if spread is too wide or unknown
        if tick.spread is None or tick.spread > self.max_spread:
            spread_str = f"{tick.spread:.1%}" if tick.spread is not None else "unknown"
            logger.info(
                f"[{self.name}] SKIP: {tick.team_yes} vs {tick.team_no} - "
                f"spread {spread_str} > {self.max_spread:.0%}"
            )
            self._market_states[market_id] = {"skipped": True}
            return None

        # Skip if price outside balanced range
        if not (self.entry_price_min <= yes_price <= self.entry_price_max):
            logger.info(
                f"[{self.name}] SKIP: {tick.team_yes} vs {tick.team_no} @ {yes_price:.2%} "
                f"(outside {self.entry_price_min:.0%}-{self.entry_price_max:.0%})"
            )
            # Mark as skipped so we don't keep checking
            self._market_states[market_id] = {"skipped": True}
            return None

        # Need token IDs
        if not tick.yes_token_id or not tick.no_token_id:
            return None

        # Check capital
        required = self.position_size * 2
        if not self.state.has_capacity(self.name, required):
            return None

        # Use actual NO price from order book
        no_price = tick.no_price or (1 - yes_price)

        logger.info(
            f"[{self.name}] ENTRY: {tick.team_yes} vs {tick.team_no} @ {yes_price:.2%}"
        )

        # Initialize market state
        # NOTE: We do NOT track shares/cost internally - we read from DB
        # This avoids desync between strategy's view and executor's actual fills
        now = datetime.now(timezone.utc)
        self._market_states[market_id] = {
            "skipped": False,
            # Price tracking
            "price_history": deque(maxlen=300),
            # Trade tracking
            "last_trade_time": now,
            "rebalance_count": 0,
            "total_profit_extracted": 0.0,
            # Pending action
            "pending_buy": None,
        }

        # Add initial price
        self._market_states[market_id]["price_history"].append((now, yes_price))

        return Action(
            action_type=ActionType.OPEN_SPREAD,
            market_id=market_id,
            condition_id=tick.condition_id,
            yes_size_usd=self.position_size,
            no_size_usd=self.position_size,
            strategy_name=self.name,
            reason=f"{self.name}: Entry @ {yes_price:.2%}",
            trigger_price=yes_price,
        )

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        Check for rebalance triggers.

        Compare current price to oldest price in 5-min window.
        Only sell if that side is profitable.
        """
        market_id = tick.market_id
        state = self._market_states.get(market_id)

        if not state or state.get("skipped"):
            return None

        yes_price = tick.yes_price
        if not yes_price:
            return None

        # Use actual NO price from order book
        no_price = tick.no_price or (1 - yes_price)
        now = datetime.now(timezone.utc)

        # Update price history
        self._update_price_history(market_id, tick)

        # Handle pending buy from previous rebalance
        pending_buy = state.get("pending_buy")
        if pending_buy:
            return self._execute_pending_buy(market_id, tick, state, pending_buy)

        # Check cooldown
        last_trade = state["last_trade_time"]
        cooldown = timedelta(minutes=self.cooldown_minutes)
        if now - last_trade < cooldown:
            return None

        # Get oldest price in window
        oldest_price = self._get_oldest_price(state["price_history"])
        if oldest_price is None:
            return None

        # Get actual positions from DB (not internal tracking)
        yes_pos, no_pos = self._get_db_positions(market_id)

        if not yes_pos or not no_pos:
            # Positions not found in DB - might still be processing
            logger.debug(f"[{self.name}] Positions not found in DB for market {market_id}")
            return None

        # Extract actual shares and cost from DB
        yes_shares = float(yes_pos.get("remaining_shares", 0))
        no_shares = float(no_pos.get("remaining_shares", 0))
        yes_cost_basis = float(yes_pos.get("avg_entry_price", 0))
        no_cost_basis = float(no_pos.get("avg_entry_price", 0))

        price_change = yes_price - oldest_price

        # Check for spike (+15 points)
        if price_change >= self.trigger_points:
            # Only sell YES if 15+ points above cost basis
            min_sell_price = yes_cost_basis + self.min_profit_points
            if yes_price < min_sell_price:
                logger.debug(
                    f"[{self.name}] Spike but YES {yes_price:.2f} < {min_sell_price:.2f} (cost+15), skipping"
                )
                return None
            return self._handle_spike(
                market_id, tick, state, yes_price, oldest_price,
                yes_shares, no_shares, yes_cost_basis, no_price
            )

        # Check for crash (-15 points)
        if price_change <= -self.trigger_points:
            # Only sell NO if 15+ points above cost basis
            min_sell_price = no_cost_basis + self.min_profit_points
            if no_price < min_sell_price:
                logger.debug(
                    f"[{self.name}] Crash but NO {no_price:.2f} < {min_sell_price:.2f} (cost+15), skipping"
                )
                return None
            return self._handle_crash(
                market_id, tick, state, yes_price, oldest_price,
                yes_shares, no_shares, no_cost_basis, no_price
            )

        return None

    def _execute_pending_buy(
        self, market_id: int, tick: Tick, state: dict, pending_buy: dict
    ) -> Optional[Action]:
        """Execute the pending buy from previous rebalance.

        Note: We don't track shares internally anymore - the executor will update
        the DB positions when this action is executed.
        """
        token_type = pending_buy["token_type"]
        size_usd = pending_buy["size_usd"]
        state["pending_buy"] = None

        # Skip if size too small
        if size_usd < 1.0:
            logger.debug(f"[{self.name}] Pending buy ${size_usd:.2f} too small, skipping")
            return None

        yes_price = tick.yes_price
        if not yes_price or yes_price <= 0.01 or yes_price >= 0.99:
            logger.debug(f"[{self.name}] Invalid price {yes_price}, skipping pending buy")
            return None

        no_price = tick.no_price or (1 - yes_price)

        # Get price for logging
        if token_type == "YES":
            buy_price = yes_price
        else:
            buy_price = no_price

        # Safety check
        if buy_price <= 0.01:
            logger.warning(f"[{self.name}] Buy price {buy_price} too low, skipping")
            return None

        logger.info(
            f"[{self.name}] BUY {token_type}: ${size_usd:.2f} @ {buy_price:.4f}"
        )

        return Action(
            action_type=ActionType.ADD,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type=token_type,
            add_size_usd=size_usd,
            strategy_name=self.name,
            reason=f"{self.name}: Add {token_type} (cost basis reinvest)",
            trigger_price=yes_price,
        )

    def _handle_spike(
        self, market_id: int, tick: Tick, state: dict, yes_price: float, oldest_price: float,
        yes_shares: float, no_shares: float, yes_cost: float, no_price: float
    ) -> Optional[Action]:
        """Handle YES spike - sell YES, buy NO.

        All position data (shares, cost) comes from DB via parameters.
        """
        # Validate shares are positive
        if yes_shares <= 0:
            logger.debug(f"[{self.name}] No YES shares to sell")
            return None

        # Calculate current values
        yes_value = yes_shares * yes_price
        no_value = no_shares * no_price

        # Calculate max sellable respecting 30% floor
        max_sell_shares = self._calc_max_sell(
            yes_shares, yes_price, yes_cost, no_value, self.min_holding_pct
        )

        if max_sell_shares <= 0:
            logger.debug(f"[{self.name}] Can't sell YES - would breach 30% floor")
            return None

        # Sell up to 50%, respecting floor
        sell_shares = min(yes_shares * self.sell_pct, max_sell_shares)

        # Skip if sell amount too small
        if sell_shares < 0.1:
            logger.debug(f"[{self.name}] Sell amount {sell_shares:.4f} too small, skipping")
            return None

        profit = sell_shares * (yes_price - yes_cost)
        reinvest_amount = sell_shares * yes_cost

        # Skip if reinvest amount too small
        if reinvest_amount < 1.0:
            logger.debug(f"[{self.name}] Reinvest ${reinvest_amount:.2f} too small, skipping")
            return None

        price_move = yes_price - oldest_price

        logger.info(
            f"[{self.name}] SPIKE +{price_move:.0%}: "
            f"Sell {sell_shares:.2f} YES @ {yes_price:.4f} | "
            f"Profit: ${profit:.2f} | Reinvest: ${reinvest_amount:.2f}"
        )

        # Update state (only non-position tracking fields)
        state["last_trade_time"] = datetime.now(timezone.utc)
        state["rebalance_count"] += 1
        state["total_profit_extracted"] += profit

        # Queue buy of NO
        state["pending_buy"] = {
            "token_type": "NO",
            "size_usd": reinvest_amount,
        }

        # Calculate close_pct with safety bounds
        close_pct = sell_shares / yes_shares
        close_pct = max(0.01, min(0.99, close_pct))

        return Action(
            action_type=ActionType.PARTIAL_CLOSE,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type="YES",
            close_pct=close_pct,
            strategy_name=self.name,
            reason=f"{self.name}: Spike +{price_move:.0%}, profit ${profit:.2f}",
            trigger_price=yes_price,
        )

    def _handle_crash(
        self, market_id: int, tick: Tick, state: dict, yes_price: float, oldest_price: float,
        yes_shares: float, no_shares: float, no_cost: float, no_price: float
    ) -> Optional[Action]:
        """Handle YES crash - sell NO, buy YES.

        All position data (shares, cost) comes from DB via parameters.
        """
        # Validate shares are positive
        if no_shares <= 0:
            logger.debug(f"[{self.name}] No NO shares to sell")
            return None

        # Calculate current values
        yes_value = yes_shares * yes_price
        no_value = no_shares * no_price

        # Calculate max sellable respecting 30% floor
        max_sell_shares = self._calc_max_sell(
            no_shares, no_price, no_cost, yes_value, self.min_holding_pct
        )

        if max_sell_shares <= 0:
            logger.debug(f"[{self.name}] Can't sell NO - would breach 30% floor")
            return None

        # Sell up to 50%, respecting floor
        sell_shares = min(no_shares * self.sell_pct, max_sell_shares)

        # Skip if sell amount too small
        if sell_shares < 0.1:
            logger.debug(f"[{self.name}] Sell amount {sell_shares:.4f} too small, skipping")
            return None

        profit = sell_shares * (no_price - no_cost)
        reinvest_amount = sell_shares * no_cost

        # Skip if reinvest amount too small
        if reinvest_amount < 1.0:
            logger.debug(f"[{self.name}] Reinvest ${reinvest_amount:.2f} too small, skipping")
            return None

        price_move = yes_price - oldest_price

        logger.info(
            f"[{self.name}] CRASH {price_move:.0%}: "
            f"Sell {sell_shares:.2f} NO @ {no_price:.4f} | "
            f"Profit: ${profit:.2f} | Reinvest: ${reinvest_amount:.2f}"
        )

        # Update state (only non-position tracking fields)
        state["last_trade_time"] = datetime.now(timezone.utc)
        state["rebalance_count"] += 1
        state["total_profit_extracted"] += profit

        # Queue buy of YES
        state["pending_buy"] = {
            "token_type": "YES",
            "size_usd": reinvest_amount,
        }

        # Calculate close_pct with safety bounds
        close_pct = sell_shares / no_shares
        close_pct = max(0.01, min(0.99, close_pct))

        return Action(
            action_type=ActionType.PARTIAL_CLOSE,
            market_id=market_id,
            condition_id=tick.condition_id,
            token_type="NO",
            close_pct=close_pct,
            strategy_name=self.name,
            reason=f"{self.name}: Crash {price_move:.0%}, profit ${profit:.2f}",
            trigger_price=yes_price,
        )

    def _calc_max_sell(
        self,
        sell_side_shares: float,
        sell_side_price: float,
        sell_side_cost: float,
        other_side_value: float,
        min_pct: float,
    ) -> float:
        """Calculate max shares sellable while keeping >= min_pct of total."""
        # Safeguard for invalid inputs
        if sell_side_shares <= 0 or sell_side_price <= 0:
            return 0.0

        S = sell_side_shares
        P = sell_side_price
        C = max(0.01, sell_side_cost)  # Ensure cost basis is at least 0.01
        O = max(0.0, other_side_value)

        # Binary search for max sellable
        low, high = 0.0, S
        for _ in range(20):
            X = (low + high) / 2
            new_sell_value = (S - X) * P
            new_other_value = O + X * C
            new_total = new_sell_value + new_other_value
            sell_pct = new_sell_value / new_total if new_total > 0 else 0

            if sell_pct >= min_pct:
                low = X
            else:
                high = X

        return low * 0.95  # 5% safety buffer

    def _update_price_history(self, market_id: int, tick: Tick) -> None:
        """Add current price to history."""
        state = self._market_states.get(market_id)
        if not state or state.get("skipped") or not tick.yes_price:
            return

        now = datetime.now(timezone.utc)
        state["price_history"].append((now, tick.yes_price))

    def _get_oldest_price(self, price_history: deque) -> Optional[float]:
        """Get oldest price within the lookback window."""
        if not price_history:
            return None

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=self.lookback_minutes)

        # Find oldest price that's within window
        for ts, price in price_history:
            if ts >= cutoff:
                return price

        # If all prices are older than window, use oldest available
        return price_history[0][1] if price_history else None

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        base = super().get_state()
        active_markets = {k: v for k, v in self._market_states.items() if not v.get("skipped")}
        base.update({
            "entry_minutes_min": self.entry_minutes_min,
            "entry_minutes_max": self.entry_minutes_max,
            "entry_price_min": self.entry_price_min,
            "entry_price_max": self.entry_price_max,
            "trigger_points": self.trigger_points,
            "min_profit_points": self.min_profit_points,
            "sell_pct": self.sell_pct,
            "min_holding_pct": self.min_holding_pct,
            "cooldown_minutes": self.cooldown_minutes,
            "lookback_minutes": self.lookback_minutes,
            "tracked_markets": len(active_markets),
            "skipped_markets": len(self._market_states) - len(active_markets),
            "total_rebalances": sum(
                s.get("rebalance_count", 0) for s in active_markets.values()
            ),
            "total_profit_extracted": sum(
                s.get("total_profit_extracted", 0) for s in active_markets.values()
            ),
        })
        return base
