"""
CSGO Position Manager.

Handles position lifecycle: open, close, partial close, add.
Supports multi-leg spread positions with linked YES+NO.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.csgo.engine.models import (
    CSGOPosition,
    CSGOPositionStatus,
    CSGOPositionLeg,
    CSGOLegType,
    CSGOSpread,
    CSGOSpreadStatus,
    CSGOTrade,
    CSGOStrategyState,
)
from src.csgo.engine.strategy import Tick
from src.csgo.engine.state import CSGOStateManager

logger = logging.getLogger(__name__)


class CSGOPositionManager:
    """
    Manages CSGO position lifecycle.

    Features:
    - Single positions (YES or NO)
    - Spread positions (linked YES + NO)
    - Partial exits with full audit trail
    - Position averaging (add to existing)
    """

    def __init__(self, state_manager: Optional[CSGOStateManager] = None):
        """
        Initialize position manager.

        Args:
            state_manager: Optional state manager for cache invalidation
        """
        self.state = state_manager or CSGOStateManager()

    # =========================================================================
    # Single Position Operations
    # =========================================================================

    def open_position(
        self,
        strategy_name: str,
        market_id: int,
        condition_id: str,
        token_id: str,
        token_type: str,
        shares: float,
        price: float,
        tick: Tick,
        db: Optional[Session] = None,
    ) -> CSGOPosition:
        """
        Open a new single position.

        Creates position with entry leg and updates strategy balance.

        Args:
            strategy_name: Strategy name
            market_id: Market ID
            condition_id: Condition ID
            token_id: Token ID to trade
            token_type: 'YES' or 'NO'
            shares: Number of shares
            price: Entry price
            tick: Tick that triggered the entry
            db: Optional database session

        Returns:
            Created CSGOPosition
        """
        cost_usd = Decimal(str(shares * price))

        def _create(session: Session) -> CSGOPosition:
            # Create position
            position = CSGOPosition(
                strategy_name=strategy_name,
                market_id=market_id,
                condition_id=condition_id,
                token_id=token_id,
                token_type=token_type,
                side="BUY",
                initial_shares=Decimal(str(shares)),
                remaining_shares=Decimal(str(shares)),
                avg_entry_price=Decimal(str(price)),
                cost_basis=cost_usd,
                current_price=Decimal(str(price)),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
                team_yes=tick.team_yes,
                team_no=tick.team_no,
                game_start_time=tick.game_start_time,
                format=tick.format,
                status=CSGOPositionStatus.OPEN.value,
            )
            session.add(position)
            session.flush()  # Get position ID

            # Create entry leg
            leg = CSGOPositionLeg(
                position_id=position.id,
                leg_type=CSGOLegType.ENTRY.value,
                shares_delta=Decimal(str(shares)),
                price=Decimal(str(price)),
                cost_delta=cost_usd,
                trigger_price=Decimal(str(tick.yes_price)) if tick.yes_price else None,
                trigger_reason="initial_entry",
            )
            session.add(leg)

            # Update strategy balance
            self._deduct_from_balance(session, strategy_name, float(cost_usd))

            # Invalidate cache BEFORE commit to prevent race conditions
            # (other requests should read from DB, not stale cache)
            self.state.invalidate_position(strategy_name, market_id)
            self.state.invalidate_strategy_state(strategy_name)

            session.commit()

            logger.info(
                f"Opened position: {strategy_name} {token_type} on market {market_id}, "
                f"{shares:.4f} shares @ ${price:.4f} = ${cost_usd:.2f}"
            )

            return position

        if db:
            return _create(db)
        else:
            with get_session() as session:
                return _create(session)

    def close_position(
        self,
        position_id: int,
        price: float,
        reason: str = "manual_close",
        db: Optional[Session] = None,
    ) -> CSGOPosition:
        """
        Fully close a position.

        Args:
            position_id: Position ID
            price: Exit price
            reason: Close reason

        Returns:
            Updated CSGOPosition
        """
        def _close(session: Session) -> CSGOPosition:
            position = session.query(CSGOPosition).get(position_id)
            if not position:
                raise ValueError(f"Position {position_id} not found")

            if position.status not in (CSGOPositionStatus.OPEN.value, CSGOPositionStatus.PARTIAL.value):
                raise ValueError(f"Position {position_id} is not open (status: {position.status})")

            # Calculate P&L
            exit_value = float(position.remaining_shares) * price
            cost_basis_remaining = float(position.remaining_shares) * float(position.avg_entry_price)
            realized_pnl = exit_value - cost_basis_remaining

            # Create exit leg
            leg = CSGOPositionLeg(
                position_id=position_id,
                leg_type=CSGOLegType.FULL_EXIT.value,
                shares_delta=-position.remaining_shares,
                price=Decimal(str(price)),
                cost_delta=Decimal(str(exit_value)),
                realized_pnl=Decimal(str(realized_pnl)),
                trigger_price=Decimal(str(price)),
                trigger_reason=reason,
            )
            session.add(leg)

            # Update position
            position.remaining_shares = Decimal("0")
            position.realized_pnl = (position.realized_pnl or Decimal("0")) + Decimal(str(realized_pnl))
            position.status = CSGOPositionStatus.CLOSED.value
            position.close_reason = reason
            position.closed_at = datetime.now(timezone.utc)

            # Return capital to strategy balance
            self._add_to_balance(session, position.strategy_name, exit_value, realized_pnl)

            # Invalidate cache BEFORE commit to prevent race conditions
            self.state.invalidate_position(position.strategy_name, position.market_id)
            self.state.invalidate_strategy_state(position.strategy_name)

            # Check if spread should be closed (both legs closed)
            if position.spread_id:
                self._maybe_close_spread(session, position.spread_id)

            session.commit()

            logger.info(
                f"Closed position {position_id}: {position.token_type} @ ${price:.4f}, "
                f"P&L: ${realized_pnl:+.2f}"
            )

            return position

        if db:
            return _close(db)
        else:
            with get_session() as session:
                return _close(session)

    def partial_close(
        self,
        position_id: int,
        close_pct: float,
        price: float,
        reason: str = "partial_exit",
        db: Optional[Session] = None,
    ) -> Tuple[CSGOPosition, CSGOPositionLeg]:
        """
        Close a portion of a position.

        Args:
            position_id: Position ID
            close_pct: Percentage to close (0.0-1.0)
            price: Exit price
            reason: Exit reason

        Returns:
            Tuple of (updated position, leg record)
        """
        if not 0 < close_pct <= 1:
            raise ValueError(f"close_pct must be between 0 and 1, got {close_pct}")

        def _partial(session: Session) -> Tuple[CSGOPosition, CSGOPositionLeg]:
            position = session.query(CSGOPosition).get(position_id)
            if not position:
                raise ValueError(f"Position {position_id} not found")

            if position.status == CSGOPositionStatus.CLOSED.value:
                raise ValueError(f"Position {position_id} is already closed")

            # Calculate shares to close
            shares_to_close = float(position.remaining_shares) * close_pct
            exit_value = shares_to_close * price
            cost_basis_portion = shares_to_close * float(position.avg_entry_price)
            realized_pnl = exit_value - cost_basis_portion

            # Create partial exit leg
            leg = CSGOPositionLeg(
                position_id=position_id,
                leg_type=CSGOLegType.PARTIAL_EXIT.value,
                shares_delta=Decimal(str(-shares_to_close)),
                price=Decimal(str(price)),
                cost_delta=Decimal(str(exit_value)),
                realized_pnl=Decimal(str(realized_pnl)),
                trigger_price=Decimal(str(price)),
                trigger_reason=reason,
            )
            session.add(leg)

            # Update position
            new_remaining = float(position.remaining_shares) - shares_to_close
            position.remaining_shares = Decimal(str(new_remaining))
            position.realized_pnl = (position.realized_pnl or Decimal("0")) + Decimal(str(realized_pnl))

            if new_remaining <= 0.0001:  # Effectively zero
                position.status = CSGOPositionStatus.CLOSED.value
                position.remaining_shares = Decimal("0")
                position.closed_at = datetime.now(timezone.utc)
                position.close_reason = reason
            else:
                position.status = CSGOPositionStatus.PARTIAL.value

            # Return capital to strategy balance
            self._add_to_balance(session, position.strategy_name, exit_value, realized_pnl)

            # Invalidate cache BEFORE commit to prevent race conditions
            self.state.invalidate_position(position.strategy_name, position.market_id)
            self.state.invalidate_strategy_state(position.strategy_name)
            # Also invalidate spread cache if position is part of a spread
            if position.spread_id:
                self.state.invalidate_spread(position.strategy_name, position.market_id)
                # Check if spread should be closed (both legs closed)
                if position.status == CSGOPositionStatus.CLOSED.value:
                    self._maybe_close_spread(session, position.spread_id)

            session.commit()

            logger.info(
                f"Partial close {position_id}: {close_pct:.0%} of {position.token_type} @ ${price:.4f}, "
                f"P&L: ${realized_pnl:+.2f}, remaining: {new_remaining:.4f} shares"
            )

            return position, leg

        if db:
            return _partial(db)
        else:
            with get_session() as session:
                return _partial(session)

    def add_to_position(
        self,
        position_id: int,
        shares: float,
        price: float,
        db: Optional[Session] = None,
    ) -> Tuple[CSGOPosition, CSGOPositionLeg]:
        """
        Add shares to an existing position (averaging).

        Args:
            position_id: Position ID
            shares: Shares to add
            price: Entry price

        Returns:
            Tuple of (updated position, leg record)
        """
        def _add(session: Session) -> Tuple[CSGOPosition, CSGOPositionLeg]:
            position = session.query(CSGOPosition).get(position_id)
            if not position:
                raise ValueError(f"Position {position_id} not found")

            if position.status == CSGOPositionStatus.CLOSED.value:
                raise ValueError(f"Position {position_id} is closed")

            cost_usd = Decimal(str(shares * price))

            # Calculate new average entry price
            old_shares = float(position.remaining_shares)
            old_avg = float(position.avg_entry_price)
            new_shares = old_shares + shares
            new_avg = ((old_shares * old_avg) + (shares * price)) / new_shares

            # Create add leg
            leg = CSGOPositionLeg(
                position_id=position_id,
                leg_type=CSGOLegType.ADD.value,
                shares_delta=Decimal(str(shares)),
                price=Decimal(str(price)),
                cost_delta=cost_usd,
                trigger_reason="position_add",
            )
            session.add(leg)

            # Update position
            position.remaining_shares = Decimal(str(new_shares))
            position.initial_shares = position.initial_shares + Decimal(str(shares))
            position.avg_entry_price = Decimal(str(new_avg))
            position.cost_basis = position.cost_basis + cost_usd

            # Deduct from strategy balance
            self._deduct_from_balance(session, position.strategy_name, float(cost_usd))

            # Invalidate cache BEFORE commit to prevent race conditions
            self.state.invalidate_position(position.strategy_name, position.market_id)
            self.state.invalidate_strategy_state(position.strategy_name)

            session.commit()

            logger.info(
                f"Added to position {position_id}: {shares:.4f} shares @ ${price:.4f}, "
                f"new avg: ${new_avg:.4f}, total: {new_shares:.4f} shares"
            )

            return position, leg

        if db:
            return _add(db)
        else:
            with get_session() as session:
                return _add(session)

    # =========================================================================
    # Spread Operations
    # =========================================================================

    def open_spread(
        self,
        strategy_name: str,
        market_id: int,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        yes_shares: float,
        yes_price: float,
        no_shares: float,
        no_price: float,
        tick: Tick,
        spread_type: str = "scalp",
        db: Optional[Session] = None,
    ) -> CSGOSpread:
        """
        Open a spread position (both YES and NO).

        Creates linked positions atomically.

        Args:
            strategy_name: Strategy name
            market_id: Market ID
            condition_id: Condition ID
            yes_token_id: YES token ID
            no_token_id: NO token ID
            yes_shares: YES shares
            yes_price: YES entry price
            no_shares: NO shares
            no_price: NO entry price
            tick: Triggering tick
            spread_type: Type of spread ('scalp', 'hedge', 'arb')

        Returns:
            Created CSGOSpread
        """
        total_cost = Decimal(str(yes_shares * yes_price + no_shares * no_price))

        def _create(session: Session) -> CSGOSpread:
            # Create spread first
            # Use tick.yes_price (mid price) for entry tracking, not fill price
            # This is used for jump calculations after restart
            entry_mid = tick.yes_price if tick.yes_price else yes_price
            spread = CSGOSpread(
                strategy_name=strategy_name,
                market_id=market_id,
                condition_id=condition_id,
                spread_type=spread_type,
                total_cost_basis=total_cost,
                team_yes=tick.team_yes,
                team_no=tick.team_no,
                entry_yes_price=Decimal(str(entry_mid)),
                status=CSGOSpreadStatus.OPEN.value,
            )
            session.add(spread)
            session.flush()  # Get spread ID

            # Create YES position
            yes_position = CSGOPosition(
                strategy_name=strategy_name,
                market_id=market_id,
                condition_id=condition_id,
                token_id=yes_token_id,
                token_type="YES",
                side="BUY",
                initial_shares=Decimal(str(yes_shares)),
                remaining_shares=Decimal(str(yes_shares)),
                avg_entry_price=Decimal(str(yes_price)),
                cost_basis=Decimal(str(yes_shares * yes_price)),
                current_price=Decimal(str(yes_price)),
                spread_id=spread.id,
                team_yes=tick.team_yes,
                team_no=tick.team_no,
                game_start_time=tick.game_start_time,
                format=tick.format,
                status=CSGOPositionStatus.OPEN.value,
            )
            session.add(yes_position)
            session.flush()

            # Create YES entry leg
            yes_leg = CSGOPositionLeg(
                position_id=yes_position.id,
                leg_type=CSGOLegType.ENTRY.value,
                shares_delta=Decimal(str(yes_shares)),
                price=Decimal(str(yes_price)),
                cost_delta=Decimal(str(yes_shares * yes_price)),
                trigger_reason="spread_entry",
            )
            session.add(yes_leg)

            # Create NO position
            no_position = CSGOPosition(
                strategy_name=strategy_name,
                market_id=market_id,
                condition_id=condition_id,
                token_id=no_token_id,
                token_type="NO",
                side="BUY",
                initial_shares=Decimal(str(no_shares)),
                remaining_shares=Decimal(str(no_shares)),
                avg_entry_price=Decimal(str(no_price)),
                cost_basis=Decimal(str(no_shares * no_price)),
                current_price=Decimal(str(no_price)),
                spread_id=spread.id,
                team_yes=tick.team_yes,
                team_no=tick.team_no,
                game_start_time=tick.game_start_time,
                format=tick.format,
                status=CSGOPositionStatus.OPEN.value,
            )
            session.add(no_position)
            session.flush()

            # Create NO entry leg
            no_leg = CSGOPositionLeg(
                position_id=no_position.id,
                leg_type=CSGOLegType.ENTRY.value,
                shares_delta=Decimal(str(no_shares)),
                price=Decimal(str(no_price)),
                cost_delta=Decimal(str(no_shares * no_price)),
                trigger_reason="spread_entry",
            )
            session.add(no_leg)

            # Update spread with position IDs
            spread.yes_position_id = yes_position.id
            spread.no_position_id = no_position.id

            # Deduct from strategy balance
            self._deduct_from_balance(session, strategy_name, float(total_cost))

            # Invalidate caches BEFORE commit to prevent race conditions
            self.state.invalidate_position(strategy_name, market_id)
            self.state.invalidate_spread(strategy_name, market_id)
            self.state.invalidate_strategy_state(strategy_name)

            session.commit()

            logger.info(
                f"Opened spread: {strategy_name} on market {market_id}, "
                f"YES: {yes_shares:.4f}@${yes_price:.4f}, NO: {no_shares:.4f}@${no_price:.4f}, "
                f"total: ${total_cost:.2f}"
            )

            return spread

        if db:
            return _create(db)
        else:
            with get_session() as session:
                return _create(session)

    def close_spread(
        self,
        spread_id: int,
        yes_price: float,
        no_price: float,
        reason: str = "spread_close",
        db: Optional[Session] = None,
    ) -> CSGOSpread:
        """
        Close an entire spread (both legs).

        Args:
            spread_id: Spread ID
            yes_price: YES exit price
            no_price: NO exit price
            reason: Close reason

        Returns:
            Updated CSGOSpread
        """
        def _close(session: Session) -> CSGOSpread:
            spread = session.query(CSGOSpread).get(spread_id)
            if not spread:
                raise ValueError(f"Spread {spread_id} not found")

            # Close YES position (only if still open or partial)
            if spread.yes_position_id:
                yes_pos = session.query(CSGOPosition).get(spread.yes_position_id)
                if yes_pos and yes_pos.status in (CSGOPositionStatus.OPEN.value, CSGOPositionStatus.PARTIAL.value):
                    self.close_position(spread.yes_position_id, yes_price, reason, db=session)

            # Close NO position (only if still open or partial)
            if spread.no_position_id:
                no_pos = session.query(CSGOPosition).get(spread.no_position_id)
                if no_pos and no_pos.status in (CSGOPositionStatus.OPEN.value, CSGOPositionStatus.PARTIAL.value):
                    self.close_position(spread.no_position_id, no_price, reason, db=session)

            # Calculate total realized P&L
            total_pnl = Decimal("0")
            if spread.yes_position_id:
                yes_pos = session.query(CSGOPosition).get(spread.yes_position_id)
                if yes_pos:
                    total_pnl += yes_pos.realized_pnl or Decimal("0")
            if spread.no_position_id:
                no_pos = session.query(CSGOPosition).get(spread.no_position_id)
                if no_pos:
                    total_pnl += no_pos.realized_pnl or Decimal("0")

            # Update spread
            spread.status = CSGOSpreadStatus.CLOSED.value
            spread.total_realized_pnl = total_pnl
            spread.closed_at = datetime.now(timezone.utc)

            # Invalidate caches BEFORE commit to prevent race conditions
            self.state.invalidate_spread(spread.strategy_name, spread.market_id)

            session.commit()

            logger.info(
                f"Closed spread {spread_id}: {spread.strategy_name} on market {spread.market_id}, "
                f"total P&L: ${total_pnl:+.2f}"
            )

            return spread

        if db:
            return _close(db)
        else:
            with get_session() as session:
                return _close(session)

    # =========================================================================
    # Price Updates
    # =========================================================================

    def update_prices(self, tick: Tick, db: Optional[Session] = None) -> int:
        """
        Update current prices and unrealized P&L for all positions on a market.

        Args:
            tick: Current tick
            db: Optional database session

        Returns:
            Number of positions updated
        """
        def _update(session: Session) -> int:
            positions = session.query(CSGOPosition).filter(
                CSGOPosition.market_id == tick.market_id,
                CSGOPosition.status.in_([
                    CSGOPositionStatus.OPEN.value,
                    CSGOPositionStatus.PARTIAL.value,
                ]),
            ).all()

            count = 0
            for position in positions:
                # Get price for this token type
                if position.token_type == "YES":
                    current_price = tick.yes_price
                else:
                    current_price = tick.no_price

                if current_price is not None:
                    position.current_price = Decimal(str(current_price))
                    current_value = float(position.remaining_shares) * current_price
                    cost_basis = float(position.remaining_shares) * float(position.avg_entry_price)
                    position.unrealized_pnl = Decimal(str(current_value - cost_basis))
                    count += 1

            # Update spread unrealized P&L
            spreads = session.query(CSGOSpread).filter(
                CSGOSpread.market_id == tick.market_id,
                CSGOSpread.status.in_([
                    CSGOSpreadStatus.OPEN.value,
                    CSGOSpreadStatus.PARTIAL.value,
                ]),
            ).all()

            for spread in spreads:
                total_unrealized = Decimal("0")
                if spread.yes_position_id:
                    yes_pos = session.query(CSGOPosition).get(spread.yes_position_id)
                    if yes_pos:
                        total_unrealized += yes_pos.unrealized_pnl or Decimal("0")
                if spread.no_position_id:
                    no_pos = session.query(CSGOPosition).get(spread.no_position_id)
                    if no_pos:
                        total_unrealized += no_pos.unrealized_pnl or Decimal("0")
                spread.total_unrealized_pnl = total_unrealized

            session.commit()
            return count

        if db:
            return _update(db)
        else:
            with get_session() as session:
                return _update(session)

    # =========================================================================
    # Balance Management
    # =========================================================================

    def _deduct_from_balance(
        self,
        session: Session,
        strategy_name: str,
        amount: float,
    ) -> None:
        """Deduct amount from strategy balance.

        Uses SELECT FOR UPDATE to prevent race conditions with concurrent trades.
        """
        state = session.query(CSGOStrategyState).filter(
            CSGOStrategyState.strategy_name == strategy_name
        ).with_for_update().first()

        if not state:
            # Create with defaults
            state = CSGOStrategyState(
                strategy_name=strategy_name,
                allocated_usd=Decimal("400"),
                available_usd=Decimal("400"),
            )
            session.add(state)

        state.available_usd = state.available_usd - Decimal(str(amount))
        state.trade_count = (state.trade_count or 0) + 1
        state.last_trade_at = datetime.now(timezone.utc)

    def _add_to_balance(
        self,
        session: Session,
        strategy_name: str,
        amount: float,
        pnl: float,
    ) -> None:
        """Add amount back to strategy balance and update P&L.

        Uses SELECT FOR UPDATE to prevent race conditions with concurrent trades.
        """
        state = session.query(CSGOStrategyState).filter(
            CSGOStrategyState.strategy_name == strategy_name
        ).with_for_update().first()

        if state:
            state.available_usd = state.available_usd + Decimal(str(amount))
            state.total_realized_pnl = (state.total_realized_pnl or Decimal("0")) + Decimal(str(pnl))

            if pnl > 0:
                state.win_count = (state.win_count or 0) + 1
            elif pnl < 0:
                state.loss_count = (state.loss_count or 0) + 1

            # Update high water mark
            total_value = state.available_usd + (state.total_unrealized_pnl or Decimal("0"))
            if total_value > (state.high_water_mark or Decimal("0")):
                state.high_water_mark = total_value

    def _maybe_close_spread(self, session: Session, spread_id: int) -> None:
        """Check if both legs of a spread are closed and update spread status."""
        spread = session.query(CSGOSpread).get(spread_id)
        if not spread or spread.status == CSGOSpreadStatus.CLOSED.value:
            return

        # Check both positions
        yes_closed = True
        no_closed = True

        if spread.yes_position_id:
            yes_pos = session.query(CSGOPosition).get(spread.yes_position_id)
            yes_closed = yes_pos and yes_pos.status == CSGOPositionStatus.CLOSED.value

        if spread.no_position_id:
            no_pos = session.query(CSGOPosition).get(spread.no_position_id)
            no_closed = no_pos and no_pos.status == CSGOPositionStatus.CLOSED.value

        if yes_closed and no_closed:
            # Both legs closed - update spread
            total_pnl = Decimal("0")
            if spread.yes_position_id:
                yes_pos = session.query(CSGOPosition).get(spread.yes_position_id)
                if yes_pos:
                    total_pnl += yes_pos.realized_pnl or Decimal("0")
            if spread.no_position_id:
                no_pos = session.query(CSGOPosition).get(spread.no_position_id)
                if no_pos:
                    total_pnl += no_pos.realized_pnl or Decimal("0")

            spread.status = CSGOSpreadStatus.CLOSED.value
            spread.total_realized_pnl = total_pnl
            spread.closed_at = datetime.now(timezone.utc)

            self.state.invalidate_spread(spread.strategy_name, spread.market_id)

            logger.info(
                f"Auto-closed spread {spread_id}: both legs closed, total P&L: ${total_pnl:+.2f}"
            )

    def cleanup_resolved_positions(self) -> int:
        """
        Close positions on resolved markets (price at 0 or 1).

        This handles positions that got stuck due to:
        - Exit logic not triggering before resolution
        - Spread filter blocking exit at wide spreads

        Returns:
            Number of positions closed
        """
        closed_count = 0
        RESOLUTION_THRESHOLD = 0.005  # Consider resolved if price < 0.5% or > 99.5%

        with get_session() as session:
            # Find all open/partial positions
            open_positions = session.query(CSGOPosition).filter(
                CSGOPosition.status.in_([
                    CSGOPositionStatus.OPEN.value,
                    CSGOPositionStatus.PARTIAL.value
                ])
            ).all()

            for position in open_positions:
                current_price = float(position.current_price) if position.current_price else None
                if current_price is None:
                    continue

                # Check if market has resolved (price near 0 or 1)
                is_resolved = (
                    current_price <= RESOLUTION_THRESHOLD or
                    current_price >= (1 - RESOLUTION_THRESHOLD)
                )

                if not is_resolved:
                    continue

                # Determine resolution side
                if current_price >= (1 - RESOLUTION_THRESHOLD):
                    resolution_price = 1.0
                    winner = position.token_type  # This token won
                else:
                    resolution_price = 0.0
                    winner = "NO" if position.token_type == "YES" else "YES"

                try:
                    # Close at resolution price
                    logger.info(
                        f"Closing resolved position {position.id}: "
                        f"{position.token_type} @ {resolution_price:.2f} (winner: {winner})"
                    )

                    # Calculate P&L
                    exit_value = float(position.remaining_shares) * resolution_price
                    cost_basis_remaining = float(position.remaining_shares) * float(position.avg_entry_price)
                    realized_pnl = exit_value - cost_basis_remaining

                    # Update position
                    position.current_price = Decimal(str(resolution_price))
                    position.realized_pnl = (position.realized_pnl or Decimal("0")) + Decimal(str(realized_pnl))
                    position.unrealized_pnl = Decimal("0")
                    position.status = CSGOPositionStatus.CLOSED.value
                    position.updated_at = datetime.now(timezone.utc)

                    # Create exit leg for audit trail
                    leg = CSGOPositionLeg(
                        position_id=position.id,
                        leg_type=CSGOLegType.FULL_EXIT.value,
                        shares_delta=-float(position.remaining_shares),
                        price=Decimal(str(resolution_price)),
                        cost_delta=Decimal(str(exit_value)),
                        realized_pnl=Decimal(str(realized_pnl)),
                        trigger_reason=f"market_resolved:{winner}",
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(leg)

                    # Clear remaining shares
                    remaining = float(position.remaining_shares)
                    position.remaining_shares = Decimal("0")

                    # Return capital to strategy balance
                    self._add_to_balance(
                        session=session,
                        strategy_name=position.strategy_name,
                        amount=exit_value,
                        pnl=realized_pnl,
                    )

                    closed_count += 1

                except Exception as e:
                    logger.error(f"Error closing resolved position {position.id}: {e}")
                    continue

            session.commit()

        if closed_count > 0:
            logger.info(f"Cleaned up {closed_count} resolved positions")

        # Invalidate cache
        self.state.clear_cache()

        return closed_count
