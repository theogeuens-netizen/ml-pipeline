"""
CSGO Paper Trading Executor.

Executes strategy actions with realistic slippage simulation.
Records all trades for audit trail and P&L tracking.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.csgo.engine.models import CSGOTrade, CSGOPosition
from src.csgo.engine.positions import CSGOPositionManager
from src.csgo.engine.state import CSGOStateManager
from src.csgo.engine.strategy import Action, ActionType, Tick
from src.alerts.telegram import get_alerter

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing an action."""
    success: bool
    action: Action

    # Fill details
    fill_price: Optional[float] = None
    shares_filled: Optional[float] = None
    cost_usd: Optional[float] = None
    slippage: Optional[float] = None

    # Position details
    position_id: Optional[int] = None
    spread_id: Optional[int] = None

    # Error info
    error: Optional[str] = None

    def __repr__(self) -> str:
        if self.success:
            return (
                f"ExecutionResult(success=True, "
                f"price={self.fill_price:.4f}, shares={self.shares_filled:.2f}, "
                f"cost=${self.cost_usd:.2f}, slippage={self.slippage:.4f})"
            )
        return f"ExecutionResult(success=False, error={self.error})"


class CSGOExecutor:
    """
    Paper trading executor for CSGO strategies.

    Features:
    - Realistic slippage model based on orderbook state
    - Full audit trail in csgo_trades table
    - Telegram alerts on execution
    - Integration with position manager for lifecycle
    """

    # Slippage model parameters
    SIZE_IMPACT = 0.001   # 0.1% per $100 of size (market impact)

    def __init__(
        self,
        state_manager: Optional[CSGOStateManager] = None,
        position_manager: Optional[CSGOPositionManager] = None,
        enable_alerts: bool = True,
    ):
        """
        Initialize executor.

        Args:
            state_manager: State manager for position queries
            position_manager: Position manager for lifecycle operations
            enable_alerts: Whether to send Telegram alerts
        """
        self.state = state_manager or CSGOStateManager()
        self.positions = position_manager or CSGOPositionManager(self.state)
        self.enable_alerts = enable_alerts
        self._alerter = get_alerter() if enable_alerts else None

    def execute(self, action: Action, tick: Tick) -> ExecutionResult:
        """
        Execute a strategy action.

        Simulates fill with slippage, records trade, updates position.

        Args:
            action: Action from strategy
            tick: Current tick with orderbook state

        Returns:
            ExecutionResult with fill details or error
        """
        try:
            if action.action_type == ActionType.OPEN_LONG:
                return self._execute_open_long(action, tick)
            elif action.action_type == ActionType.OPEN_SPREAD:
                return self._execute_open_spread(action, tick)
            elif action.action_type == ActionType.CLOSE:
                return self._execute_close(action, tick)
            elif action.action_type == ActionType.PARTIAL_CLOSE:
                return self._execute_partial_close(action, tick)
            elif action.action_type == ActionType.ADD:
                return self._execute_add(action, tick)
            elif action.action_type == ActionType.REBALANCE:
                return self._execute_rebalance(action, tick)
            else:
                return ExecutionResult(
                    success=False,
                    action=action,
                    error=f"Unknown action type: {action.action_type}",
                )
        except Exception as e:
            logger.exception(f"Execution error: {e}")
            return ExecutionResult(
                success=False,
                action=action,
                error=str(e),
            )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _get_strategy_name(self, action: Action) -> str:
        """
        Get strategy name from action.

        Prefers explicit strategy_name field, falls back to parsing reason.
        This ensures consistent capital tracking across all operations.
        """
        if action.strategy_name:
            return action.strategy_name
        # Fallback: parse from reason (legacy support)
        if action.reason:
            # Format: "strategy_name: reason text" or just "strategy_name"
            return action.reason.split(":")[0].split()[0]
        return "unknown"

    # =========================================================================
    # Action Handlers
    # =========================================================================

    def _execute_open_long(self, action: Action, tick: Tick) -> ExecutionResult:
        """Execute OPEN_LONG action - buy a single token."""
        if not action.token_type:
            return ExecutionResult(
                success=False, action=action,
                error="token_type required for OPEN_LONG",
            )
        if not action.size_usd:
            return ExecutionResult(
                success=False, action=action,
                error="size_usd required for OPEN_LONG",
            )

        # Get token ID and price
        if action.token_type == "YES":
            token_id = tick.yes_token_id
            base_price = tick.yes_price
            best_ask = tick.best_ask if tick.token_type == "YES" else (1 - tick.best_bid if tick.best_bid else None)
        else:
            token_id = tick.no_token_id
            base_price = tick.no_price
            best_ask = tick.best_ask if tick.token_type == "NO" else (1 - tick.best_bid if tick.best_bid else None)

        if not token_id:
            return ExecutionResult(
                success=False, action=action,
                error=f"No token ID for {action.token_type}",
            )
        if not base_price:
            return ExecutionResult(
                success=False, action=action,
                error=f"No price available for {action.token_type}",
            )

        # Calculate fill price by crossing the spread
        fill_price, slippage, eff_spread, eff_bid, eff_ask = self._calculate_fill_price(
            base_price=base_price,
            best_ask=best_ask,
            best_bid=None,  # Not needed for BUY
            spread=tick.spread,
            size_usd=action.size_usd,
            side="BUY",
        )

        # Calculate shares
        shares = action.size_usd / fill_price
        cost_usd = shares * fill_price

        # Get strategy name
        strategy_name = self._get_strategy_name(action)

        # Check capacity
        if not self.state.has_capacity(strategy_name, cost_usd):
            return ExecutionResult(
                success=False, action=action,
                error="Insufficient capital",
            )

        # Open position
        with get_session() as db:

            position = self.positions.open_position(
                strategy_name=strategy_name,
                market_id=action.market_id,
                condition_id=action.condition_id,
                token_id=token_id,
                token_type=action.token_type,
                shares=shares,
                price=fill_price,
                tick=tick,
                db=db,
            )

            # Extract ID before session closes
            position_id = position.id

            # Record trade
            trade = self._record_trade(
                db=db,
                position_id=position_id,
                token_id=token_id,
                side="BUY",
                shares=shares,
                price=fill_price,
                cost_usd=cost_usd,
                tick=tick,
                slippage=slippage,
                effective_spread=eff_spread,
                effective_bid=eff_bid,
                effective_ask=eff_ask,
            )

            db.commit()

        # Send alert
        self._send_trade_alert(
            action=action,
            tick=tick,
            fill_price=fill_price,
            shares=shares,
            cost_usd=cost_usd,
            slippage=slippage,
        )

        logger.info(
            f"OPEN_LONG executed: {action.token_type} @ {fill_price:.4f}, "
            f"{shares:.2f} shares, ${cost_usd:.2f}, slippage={slippage:.4f}"
        )

        return ExecutionResult(
            success=True,
            action=action,
            fill_price=fill_price,
            shares_filled=shares,
            cost_usd=cost_usd,
            slippage=slippage,
            position_id=position_id,
        )

    def _execute_open_spread(self, action: Action, tick: Tick) -> ExecutionResult:
        """Execute OPEN_SPREAD action - buy both YES and NO."""
        if not action.yes_size_usd or not action.no_size_usd:
            return ExecutionResult(
                success=False, action=action,
                error="yes_size_usd and no_size_usd required for OPEN_SPREAD",
            )

        yes_token_id = tick.yes_token_id
        no_token_id = tick.no_token_id

        if not yes_token_id or not no_token_id:
            return ExecutionResult(
                success=False, action=action,
                error="Missing token IDs",
            )

        yes_price = tick.yes_price
        no_price = tick.no_price

        if not yes_price or not no_price:
            return ExecutionResult(
                success=False, action=action,
                error="Missing prices",
            )

        # Calculate fills by crossing the spread
        yes_fill, yes_slippage, yes_spread, yes_bid, yes_ask = self._calculate_fill_price(
            base_price=yes_price,
            best_ask=tick.best_ask if tick.token_type == "YES" else None,
            best_bid=None,  # Not needed for BUY
            spread=tick.spread,
            size_usd=action.yes_size_usd,
            side="BUY",
        )

        no_fill, no_slippage, no_spread, no_bid, no_ask = self._calculate_fill_price(
            base_price=no_price,
            best_ask=tick.best_ask if tick.token_type == "NO" else None,
            best_bid=None,  # Not needed for BUY
            spread=tick.spread,
            size_usd=action.no_size_usd,
            side="BUY",
        )

        yes_shares = action.yes_size_usd / yes_fill
        no_shares = action.no_size_usd / no_fill
        total_cost = (yes_shares * yes_fill) + (no_shares * no_fill)

        # Get strategy name
        strategy_name = self._get_strategy_name(action)

        # Check capacity
        if not self.state.has_capacity(strategy_name, total_cost):
            return ExecutionResult(
                success=False, action=action,
                error="Insufficient capital",
            )

        # Open spread
        with get_session() as db:
            spread = self.positions.open_spread(
                strategy_name=strategy_name,
                market_id=action.market_id,
                condition_id=action.condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                yes_shares=yes_shares,
                yes_price=yes_fill,
                no_shares=no_shares,
                no_price=no_fill,
                tick=tick,
                spread_type="scalp",
                db=db,
            )

            # Extract IDs before session closes
            spread_id = spread.id
            yes_position_id = spread.yes_position_id
            no_position_id = spread.no_position_id

            # Record YES trade
            self._record_trade(
                db=db,
                position_id=yes_position_id,
                token_id=yes_token_id,
                side="BUY",
                shares=yes_shares,
                price=yes_fill,
                cost_usd=yes_shares * yes_fill,
                tick=tick,
                slippage=yes_slippage,
                effective_spread=yes_spread,
                effective_bid=yes_bid,
                effective_ask=yes_ask,
            )

            # Record NO trade
            self._record_trade(
                db=db,
                position_id=no_position_id,
                token_id=no_token_id,
                side="BUY",
                shares=no_shares,
                price=no_fill,
                cost_usd=no_shares * no_fill,
                tick=tick,
                slippage=no_slippage,
                effective_spread=no_spread,
                effective_bid=no_bid,
                effective_ask=no_ask,
            )

            db.commit()

        # Send alert
        self._send_spread_alert(
            action=action,
            tick=tick,
            yes_price=yes_fill,
            yes_shares=yes_shares,
            no_price=no_fill,
            no_shares=no_shares,
            total_cost=total_cost,
        )

        avg_slippage = (yes_slippage + no_slippage) / 2

        logger.info(
            f"OPEN_SPREAD executed: YES@{yes_fill:.4f}x{yes_shares:.2f}, "
            f"NO@{no_fill:.4f}x{no_shares:.2f}, total=${total_cost:.2f}"
        )

        return ExecutionResult(
            success=True,
            action=action,
            fill_price=yes_fill,  # YES price as reference
            shares_filled=yes_shares + no_shares,
            cost_usd=total_cost,
            slippage=avg_slippage,
            spread_id=spread_id,
        )

    def _execute_close(self, action: Action, tick: Tick) -> ExecutionResult:
        """Execute CLOSE action - close entire position."""
        strategy_name = self._get_strategy_name(action)

        # If no token_type specified, check for spread first (spread close)
        # This ensures we close both legs of a spread simultaneously
        if not action.token_type:
            spread = self.state.get_spread(strategy_name, action.market_id)
            if spread:
                return self._close_spread_position(action, tick, spread)

        # Find open position for single-leg close
        position = self.state.get_position(
            strategy_name=strategy_name,
            market_id=action.market_id,
            token_type=action.token_type,
        )

        if not position:
            # No position found - maybe spread was already closed
            return ExecutionResult(
                success=False, action=action,
                error="No position found to close",
            )

        # If this position is part of a spread, close the whole spread
        if position.spread_id:
            spread = self.state.get_spread(strategy_name, action.market_id)
            if spread:
                return self._close_spread_position(action, tick, spread)

        # Get exit price
        if position.token_type == "YES":
            base_price = tick.yes_price
            best_bid = tick.best_bid if tick.token_type == "YES" else None
        else:
            base_price = tick.no_price
            best_bid = tick.best_bid if tick.token_type == "NO" else None

        if not base_price:
            return ExecutionResult(
                success=False, action=action,
                error="No price available for exit",
            )

        # Calculate fill by crossing the spread (selling)
        remaining_value = float(position.remaining_shares) * base_price
        fill_price, slippage, eff_spread, eff_bid, eff_ask = self._calculate_fill_price(
            base_price=base_price,
            best_ask=None,  # Not needed for SELL
            best_bid=best_bid,
            spread=tick.spread,
            size_usd=remaining_value,
            side="SELL",
        )

        # Close position
        with get_session() as db:
            closed = self.positions.close_position(
                position_id=position.id,
                price=fill_price,
                reason=action.reason or "strategy_close",
                db=db,
            )

            # Extract values before session closes
            pnl = float(closed.realized_pnl) if closed.realized_pnl else 0

            # Record trade
            self._record_trade(
                db=db,
                position_id=position.id,
                token_id=position.token_id,
                side="SELL",
                shares=float(position.remaining_shares),
                price=fill_price,
                cost_usd=float(position.remaining_shares) * fill_price,
                tick=tick,
                slippage=slippage,
                effective_spread=eff_spread,
                effective_bid=eff_bid,
                effective_ask=eff_ask,
            )

            db.commit()

        logger.info(
            f"CLOSE executed: {position.token_type} @ {fill_price:.4f}, "
            f"P&L: ${pnl:+.2f}"
        )

        return ExecutionResult(
            success=True,
            action=action,
            fill_price=fill_price,
            shares_filled=float(position.remaining_shares),
            cost_usd=float(position.remaining_shares) * fill_price,
            slippage=slippage,
            position_id=position.id,
        )

    def _execute_partial_close(self, action: Action, tick: Tick) -> ExecutionResult:
        """Execute PARTIAL_CLOSE action - close portion of position."""
        if not action.close_pct:
            return ExecutionResult(
                success=False, action=action,
                error="close_pct required for PARTIAL_CLOSE",
            )

        strategy_name = self._get_strategy_name(action)

        # Find position
        position = self.state.get_position(
            strategy_name=strategy_name,
            market_id=action.market_id,
            token_type=action.token_type,
        )

        if not position:
            return ExecutionResult(
                success=False, action=action,
                error="No position found for partial close",
            )

        # Get exit price
        if position.token_type == "YES":
            base_price = tick.yes_price
            best_bid = tick.best_bid if tick.token_type == "YES" else None
        else:
            base_price = tick.no_price
            best_bid = tick.best_bid if tick.token_type == "NO" else None

        if not base_price:
            return ExecutionResult(
                success=False, action=action,
                error="No price available for exit",
            )

        shares_to_close = float(position.remaining_shares) * action.close_pct
        exit_value = shares_to_close * base_price

        fill_price, slippage, eff_spread, eff_bid, eff_ask = self._calculate_fill_price(
            base_price=base_price,
            best_ask=None,  # Not needed for SELL
            best_bid=best_bid,
            spread=tick.spread,
            size_usd=exit_value,
            side="SELL",
        )

        # Partial close
        with get_session() as db:
            updated, leg = self.positions.partial_close(
                position_id=position.id,
                close_pct=action.close_pct,
                price=fill_price,
                reason=action.reason or "partial_exit",
                db=db,
            )

            # Extract values before session closes
            leg_id = leg.id
            pnl = float(leg.realized_pnl) if leg.realized_pnl else 0

            # Record trade
            self._record_trade(
                db=db,
                position_id=position.id,
                leg_id=leg_id,
                token_id=position.token_id,
                side="SELL",
                shares=shares_to_close,
                price=fill_price,
                cost_usd=shares_to_close * fill_price,
                tick=tick,
                slippage=slippage,
                effective_spread=eff_spread,
                effective_bid=eff_bid,
                effective_ask=eff_ask,
            )

            db.commit()

        logger.info(
            f"PARTIAL_CLOSE executed: {action.close_pct:.0%} of {position.token_type} @ {fill_price:.4f}, "
            f"P&L: ${pnl:+.2f}"
        )

        return ExecutionResult(
            success=True,
            action=action,
            fill_price=fill_price,
            shares_filled=shares_to_close,
            cost_usd=shares_to_close * fill_price,
            slippage=slippage,
            position_id=position.id,
        )

    def _execute_add(self, action: Action, tick: Tick) -> ExecutionResult:
        """Execute ADD action - add to existing position."""
        if not action.add_size_usd:
            return ExecutionResult(
                success=False, action=action,
                error="add_size_usd required for ADD",
            )

        strategy_name = self._get_strategy_name(action)

        # Find position
        position = self.state.get_position(
            strategy_name=strategy_name,
            market_id=action.market_id,
            token_type=action.token_type,
        )

        if not position:
            return ExecutionResult(
                success=False, action=action,
                error="No position found to add to",
            )

        # Get entry price
        if position.token_type == "YES":
            base_price = tick.yes_price
            best_ask = tick.best_ask if tick.token_type == "YES" else None
        else:
            base_price = tick.no_price
            best_ask = tick.best_ask if tick.token_type == "NO" else None

        if not base_price:
            return ExecutionResult(
                success=False, action=action,
                error="No price available for add",
            )

        fill_price, slippage, eff_spread, eff_bid, eff_ask = self._calculate_fill_price(
            base_price=base_price,
            best_ask=best_ask,
            best_bid=None,  # Not needed for BUY
            spread=tick.spread,
            size_usd=action.add_size_usd,
            side="BUY",
        )

        shares = action.add_size_usd / fill_price

        # Add to position
        with get_session() as db:
            updated, leg = self.positions.add_to_position(
                position_id=position.id,
                shares=shares,
                price=fill_price,
                db=db,
            )

            # Record trade
            self._record_trade(
                db=db,
                position_id=position.id,
                leg_id=leg.id,
                token_id=position.token_id,
                side="BUY",
                shares=shares,
                price=fill_price,
                cost_usd=shares * fill_price,
                tick=tick,
                slippage=slippage,
                effective_spread=eff_spread,
                effective_bid=eff_bid,
                effective_ask=eff_ask,
            )

            db.commit()

        logger.info(
            f"ADD executed: {shares:.2f} shares @ {fill_price:.4f} to {position.token_type}"
        )

        return ExecutionResult(
            success=True,
            action=action,
            fill_price=fill_price,
            shares_filled=shares,
            cost_usd=shares * fill_price,
            slippage=slippage,
            position_id=position.id,
        )

    def _execute_rebalance(self, action: Action, tick: Tick) -> ExecutionResult:
        """Execute REBALANCE action - adjust spread ratio."""
        # Rebalance is complex - sell some of one side, buy more of other
        # For now, return not implemented
        return ExecutionResult(
            success=False, action=action,
            error="REBALANCE not yet implemented",
        )

    def _close_spread_position(self, action: Action, tick: Tick, spread) -> ExecutionResult:
        """Close an entire spread position."""
        yes_price = tick.yes_price
        no_price = tick.no_price

        if not yes_price or not no_price:
            return ExecutionResult(
                success=False, action=action,
                error="Missing prices for spread close",
            )

        # Calculate fills by crossing the spread
        yes_fill, _, yes_spread, yes_bid, yes_ask = self._calculate_fill_price(
            base_price=yes_price,
            best_ask=None,  # Not needed for SELL
            best_bid=tick.best_bid if tick.token_type == "YES" else None,
            spread=tick.spread,
            size_usd=50,  # Estimate
            side="SELL",
        )

        no_fill, _, no_spread, no_bid, no_ask = self._calculate_fill_price(
            base_price=no_price,
            best_ask=None,  # Not needed for SELL
            best_bid=tick.best_bid if tick.token_type == "NO" else None,
            spread=tick.spread,
            size_usd=50,
            side="SELL",
        )

        with get_session() as db:
            closed = self.positions.close_spread(
                spread_id=spread.id,
                yes_price=yes_fill,
                no_price=no_fill,
                reason=action.reason or "spread_close",
                db=db,
            )

            # Extract values before session closes
            pnl = float(closed.total_realized_pnl) if closed.total_realized_pnl else 0

            db.commit()

        logger.info(f"Spread closed: P&L ${pnl:+.2f}")

        return ExecutionResult(
            success=True,
            action=action,
            fill_price=yes_fill,
            spread_id=spread.id,
        )

    # =========================================================================
    # Slippage Model
    # =========================================================================

    def _get_realistic_spread(self, price: float) -> float:
        """
        Get realistic spread based on price level.

        FALLBACK MODEL: Used only when CLOB API doesn't return bid/ask data.
        Primary spread data now comes from CLOB /price endpoint (ask - bid).

        Markets at extreme prices (near 0 or 1) have much wider spreads
        because liquidity providers don't want to take the other side.

        Based on empirical observation of Polymarket CSGO markets:
        - Near 50/50: 2-3% spread (competitive, liquid)
        - Near 70/30: 4-6% spread
        - Near 85/15: 8-12% spread
        - Near 92/8: 15-25% spread
        - Near 97/3: 30-45% spread (very illiquid)

        Args:
            price: Token price (0-1)

        Returns:
            Estimated bid-ask spread as decimal
        """
        # Distance from 50% (0 = at 50%, 0.5 = at 0% or 100%)
        distance_from_mid = abs(price - 0.5)

        if distance_from_mid <= 0.05:  # 45-55%
            return 0.025  # 2.5%
        elif distance_from_mid <= 0.15:  # 35-65%
            return 0.04  # 4%
        elif distance_from_mid <= 0.25:  # 25-75%
            return 0.06  # 6%
        elif distance_from_mid <= 0.32:  # 18-82%
            return 0.10  # 10%
        elif distance_from_mid <= 0.40:  # 10-90%
            return 0.18  # 18%
        elif distance_from_mid <= 0.45:  # 5-95%
            return 0.30  # 30%
        elif distance_from_mid <= 0.48:  # 2-98%
            return 0.45  # 45%
        else:  # < 2% or > 98%
            return 0.60  # 60% - nearly impossible to trade

    def _validate_spread(self, spread: Optional[float], price: float) -> Optional[float]:
        """
        Validate spread data from DB.

        Returns None if spread is obviously wrong, forcing use of price-based model.

        Args:
            spread: Spread value from database
            price: Current price (for sanity checking)

        Returns:
            Validated spread or None if invalid
        """
        if spread is None:
            return None

        # Spread can't be negative
        if spread < 0:
            return None

        # Spread can't be > 100%
        if spread > 1.0:
            return None

        # Spread of exactly 1.0 is clearly wrong (common bad value from DB)
        if spread == 1.0:
            return None

        # Spread shouldn't be dramatically higher than price-based estimate
        # (allows some flexibility but catches obviously wrong values)
        expected = self._get_realistic_spread(price)
        if spread > expected * 3:  # More than 3x expected is suspicious
            logger.debug(f"Spread {spread:.2%} rejected (3x > expected {expected:.2%})")
            return None

        return spread

    def _calculate_fill_price(
        self,
        base_price: float,
        best_ask: Optional[float],
        best_bid: Optional[float],
        spread: Optional[float],
        size_usd: float,
        side: str,
    ) -> tuple[float, float, float, Optional[float], Optional[float]]:
        """
        Calculate realistic fill price by crossing the spread.

        BUY: Execute at best_ask (or base_price + half spread if no ask)
        SELL: Execute at best_bid (or base_price - half spread if no bid)

        Uses price-aware spread model when no orderbook data available.
        Wider spreads at extreme prices reflect real market conditions.

        Args:
            base_price: Reference price (mid or last)
            best_ask: Best ask price (for buys)
            best_bid: Best bid price (for sells)
            spread: Current bid-ask spread from DB
            size_usd: Trade size in USD
            side: BUY or SELL

        Returns:
            Tuple of (fill_price, slippage, effective_spread, best_bid_used, best_ask_used)
        """
        # Validate spread from DB, use price-based model if invalid
        validated_spread = self._validate_spread(spread, base_price)

        # Get realistic spread based on price level
        price_based_spread = self._get_realistic_spread(base_price)

        # Use validated DB spread if available, otherwise price-based
        effective_spread = validated_spread if validated_spread else price_based_spread
        effective_bid = best_bid
        effective_ask = best_ask

        if side == "BUY":
            # Cross to ask side
            if best_ask and best_ask > 0:
                fill_price = best_ask
            else:
                # No orderbook data - estimate using spread
                fill_price = base_price + (effective_spread / 2)
                effective_ask = fill_price
                effective_bid = base_price - (effective_spread / 2)
        else:
            # Cross to bid side
            if best_bid and best_bid > 0:
                fill_price = best_bid
            else:
                # No orderbook data - estimate using spread
                fill_price = base_price - (effective_spread / 2)
                effective_ask = base_price + (effective_spread / 2)
                effective_bid = fill_price

        # Add size impact (small additional slippage for large orders)
        size_impact = (size_usd / 100) * self.SIZE_IMPACT  # 0.1% per $100
        if side == "BUY":
            fill_price *= (1 + size_impact)
        else:
            fill_price *= (1 - size_impact)

        # Clamp to valid range [0.001, 0.999]
        # Slightly tighter than [0.01, 0.99] to allow near-resolution trades
        fill_price = max(0.001, min(0.999, fill_price))

        # Calculate slippage from mid
        slippage = abs(fill_price - base_price) / base_price if base_price > 0 else 0

        return fill_price, slippage, effective_spread, effective_bid, effective_ask

    # =========================================================================
    # Trade Recording
    # =========================================================================

    def _record_trade(
        self,
        db: Session,
        position_id: int,
        token_id: str,
        side: str,
        shares: float,
        price: float,
        cost_usd: float,
        tick: Tick,
        slippage: float,
        leg_id: Optional[int] = None,
        effective_spread: Optional[float] = None,
        effective_bid: Optional[float] = None,
        effective_ask: Optional[float] = None,
    ) -> CSGOTrade:
        """Record trade to csgo_trades table.

        Uses effective values from slippage calculation when tick values are NULL.
        This ensures we always have spread data for P&L analysis.

        Also denormalizes match context (team names, format, map#) for audit trail.
        """
        # Use effective values when tick doesn't have data
        best_bid = tick.best_bid if tick.best_bid else effective_bid
        best_ask = tick.best_ask if tick.best_ask else effective_ask
        spread = tick.spread if tick.spread else effective_spread

        # Parse map number from group_item_title if available
        map_number = None
        if hasattr(tick, 'map_number') and tick.map_number:
            map_number = tick.map_number
        elif hasattr(tick, 'group_item_title') and tick.group_item_title:
            # Parse "Map 1 Winner" -> 1
            import re
            match = re.search(r'Map (\d+)', tick.group_item_title)
            if match:
                map_number = int(match.group(1))

        trade = CSGOTrade(
            position_id=position_id,
            leg_id=leg_id,
            token_id=token_id,
            side=side,
            shares=Decimal(str(shares)),
            price=Decimal(str(price)),
            cost_usd=Decimal(str(cost_usd)),
            best_bid=Decimal(str(best_bid)) if best_bid else None,
            best_ask=Decimal(str(best_ask)) if best_ask else None,
            spread=Decimal(str(spread)) if spread else None,
            slippage=Decimal(str(slippage)),
            trigger_tick_id=tick.message_id,
            # Match context
            team_yes=tick.team_yes,
            team_no=tick.team_no,
            format=tick.format,
            map_number=map_number,
            game_start_time=tick.game_start_time,
        )
        db.add(trade)
        return trade

    # =========================================================================
    # Alerts
    # =========================================================================

    def _send_trade_alert(
        self,
        action: Action,
        tick: Tick,
        fill_price: float,
        shares: float,
        cost_usd: float,
        slippage: float,
    ) -> None:
        """Send Telegram alert for single trade."""
        if not self._alerter or not self._alerter.enabled:
            return

        emoji = "🎮"
        token_emoji = "🟢" if action.token_type == "YES" else "🔴"

        message = (
            f"{emoji} <b>CSGO TRADE</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>{tick.team_yes} vs {tick.team_no}</b>\n"
            f"Format: {tick.format or 'N/A'}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{token_emoji} <b>{action.token_type}</b> @ {fill_price*100:.1f}¢\n"
            f"Shares: {shares:.2f}\n"
            f"Cost: ${cost_usd:.2f}\n"
            f"Slippage: {slippage*100:.2f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Reason: {action.reason[:60] if action.reason else 'N/A'}"
        )

        self._alerter.send(message)

    def _send_spread_alert(
        self,
        action: Action,
        tick: Tick,
        yes_price: float,
        yes_shares: float,
        no_price: float,
        no_shares: float,
        total_cost: float,
    ) -> None:
        """Send Telegram alert for spread trade."""
        if not self._alerter or not self._alerter.enabled:
            return

        message = (
            f"🎮 <b>CSGO SPREAD OPENED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>{tick.team_yes} vs {tick.team_no}</b>\n"
            f"Format: {tick.format or 'N/A'}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🟢 YES @ {yes_price*100:.1f}¢ x {yes_shares:.2f}\n"
            f"🔴 NO @ {no_price*100:.1f}¢ x {no_shares:.2f}\n"
            f"Total: <b>${total_cost:.2f}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Reason: {action.reason[:60] if action.reason else 'N/A'}"
        )

        self._alerter.send(message)
