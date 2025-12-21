"""
Position Manager.

Tracks open and closed positions, calculates P&L,
and manages position lifecycle.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.executor.models import (
    Position,
    PositionStatus,
    ExecutorOrder,
    ExecutorTrade,
)

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages trading positions.

    Tracks positions, calculates P&L, and handles position updates.
    """

    def __init__(self, is_paper: bool = True):
        """
        Initialize position manager.

        Args:
            is_paper: Whether managing paper or live positions
        """
        self.is_paper = is_paper

    def get_open_positions(self, db: Optional[Session] = None) -> list[Position]:
        """
        Get all open positions.

        Args:
            db: Optional database session

        Returns:
            List of open Position objects
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            positions = db.query(Position).filter(
                Position.is_paper == self.is_paper,
                Position.status == PositionStatus.OPEN.value,
            ).all()
            return positions
        finally:
            if close_db:
                db.close()

    def get_position_by_market(
        self,
        market_id: int,
        db: Optional[Session] = None,
    ) -> Optional[Position]:
        """
        Get open position for a specific market.

        Args:
            market_id: Market ID
            db: Optional database session

        Returns:
            Position if exists, None otherwise
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            return db.query(Position).filter(
                Position.is_paper == self.is_paper,
                Position.market_id == market_id,
                Position.status == PositionStatus.OPEN.value,
            ).first()
        finally:
            if close_db:
                db.close()

    def get_position_by_strategy(
        self,
        strategy_name: str,
        db: Optional[Session] = None,
    ) -> list[Position]:
        """
        Get all open positions for a strategy.

        Args:
            strategy_name: Strategy name
            db: Optional database session

        Returns:
            List of Position objects
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            return db.query(Position).filter(
                Position.is_paper == self.is_paper,
                Position.strategy_name == strategy_name,
                Position.status == PositionStatus.OPEN.value,
            ).all()
        finally:
            if close_db:
                db.close()

    def get_total_exposure(self, db: Optional[Session] = None) -> float:
        """
        Get total USD exposure across all open positions.

        Args:
            db: Optional database session

        Returns:
            Total exposure in USD
        """
        positions = self.get_open_positions(db)
        return sum(float(p.cost_basis) for p in positions)

    def get_position_count(self, db: Optional[Session] = None) -> int:
        """
        Get count of open positions.

        Args:
            db: Optional database session

        Returns:
            Number of open positions
        """
        return len(self.get_open_positions(db))

    def update_prices(
        self,
        price_updates: dict[int, float],
        db: Optional[Session] = None,
    ):
        """
        Update current prices for open positions.

        Args:
            price_updates: Dict of market_id -> current_price
            db: Optional database session
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            positions = self.get_open_positions(db)

            for position in positions:
                if position.market_id in price_updates:
                    current_price = price_updates[position.market_id]
                    self._update_position_price(position, current_price)

            if close_db:
                db.commit()
        finally:
            if close_db:
                db.close()

    def _update_position_price(self, position: Position, current_price: float):
        """Update a position's current price and P&L."""
        position.current_price = current_price
        position.current_value = float(position.size_shares) * current_price
        position.unrealized_pnl = position.current_value - float(position.cost_basis)
        position.unrealized_pnl_pct = (
            position.unrealized_pnl / float(position.cost_basis)
            if position.cost_basis else 0
        )

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        reason: str = "manual",
        db: Optional[Session] = None,
    ) -> Optional[float]:
        """
        Close a position.

        Args:
            position_id: Position ID
            exit_price: Exit price
            reason: Close reason
            db: Optional database session

        Returns:
            Realized P&L, or None if position not found
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            position = db.query(Position).filter(
                Position.id == position_id,
                Position.is_paper == self.is_paper,
            ).first()

            if position is None:
                logger.warning(f"Position {position_id} not found")
                return None

            if position.status != PositionStatus.OPEN.value:
                logger.warning(f"Position {position_id} is not open")
                return None

            # Calculate P&L
            exit_value = float(position.size_shares) * exit_price
            pnl = exit_value - float(position.cost_basis)

            # Update position
            now = datetime.now(timezone.utc)
            position.exit_price = exit_price
            position.exit_time = now
            position.realized_pnl = pnl
            position.status = PositionStatus.CLOSED.value
            position.close_reason = reason

            if close_db:
                db.commit()

            logger.info(
                f"Position {position_id} closed @ ${exit_price:.4f}, P&L: ${pnl:.2f}"
            )

            return pnl

        finally:
            if close_db:
                db.close()

    def get_portfolio_stats(self, db: Optional[Session] = None) -> dict[str, Any]:
        """
        Get portfolio statistics.

        Args:
            db: Optional database session

        Returns:
            Dictionary with portfolio stats
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            open_positions = self.get_open_positions(db)
            closed_positions = db.query(Position).filter(
                Position.is_paper == self.is_paper,
                Position.status == PositionStatus.CLOSED.value,
            ).all()

            # Open position stats
            total_cost = sum(float(p.cost_basis) for p in open_positions)
            total_value = sum(float(p.current_value or p.cost_basis) for p in open_positions)
            unrealized_pnl = sum(float(p.unrealized_pnl) for p in open_positions)

            # Closed position stats
            realized_pnl = sum(float(p.realized_pnl) for p in closed_positions)
            winning = len([p for p in closed_positions if float(p.realized_pnl) > 0])
            losing = len([p for p in closed_positions if float(p.realized_pnl) < 0])

            # Per-strategy breakdown
            strategy_stats = {}
            all_positions = open_positions + closed_positions
            for position in all_positions:
                strategy = position.strategy_name
                if strategy not in strategy_stats:
                    strategy_stats[strategy] = {
                        "open": 0,
                        "closed": 0,
                        "winning": 0,
                        "losing": 0,
                        "realized_pnl": 0,
                        "unrealized_pnl": 0,
                    }
                stats = strategy_stats[strategy]
                if position.status == PositionStatus.OPEN.value:
                    stats["open"] += 1
                    stats["unrealized_pnl"] += float(position.unrealized_pnl)
                else:
                    stats["closed"] += 1
                    pnl = float(position.realized_pnl)
                    stats["realized_pnl"] += pnl
                    if pnl > 0:
                        stats["winning"] += 1
                    elif pnl < 0:
                        stats["losing"] += 1

            return {
                "open_positions": len(open_positions),
                "closed_positions": len(closed_positions),
                "total_exposure": total_cost,
                "current_value": total_value,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "total_pnl": unrealized_pnl + realized_pnl,
                "winning_trades": winning,
                "losing_trades": losing,
                "win_rate": winning / (winning + losing) if (winning + losing) > 0 else 0,
                "by_strategy": strategy_stats,
            }

        finally:
            if close_db:
                db.close()

    def mark_position_hedged(
        self,
        position_id: int,
        hedge_position_id: int,
        db: Optional[Session] = None,
    ):
        """
        Mark a position as hedged.

        Args:
            position_id: Main position ID
            hedge_position_id: Hedge position ID
            db: Optional database session
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            position = db.query(Position).filter(Position.id == position_id).first()
            hedge = db.query(Position).filter(Position.id == hedge_position_id).first()

            if position and hedge:
                position.status = PositionStatus.HEDGED.value
                position.hedge_position_id = hedge_position_id
                hedge.is_hedge = True
                logger.info(f"Position {position_id} hedged with {hedge_position_id}")

            if close_db:
                db.commit()

        finally:
            if close_db:
                db.close()

    def close_positions_on_resolution(
        self,
        market_id: int,
        outcome: str,
        db: Optional[Session] = None,
    ) -> list[dict]:
        """
        Close all positions on a resolved market and calculate P&L.

        In prediction markets:
        - If outcome=YES: YES tokens pay $1.00, NO tokens pay $0.00
        - If outcome=NO: NO tokens pay $1.00, YES tokens pay $0.00
        - If outcome=UNKNOWN: Return cost basis (no P&L)

        Args:
            market_id: Market ID that resolved
            outcome: Resolution outcome ("YES", "NO", or "UNKNOWN")
            db: Optional database session

        Returns:
            List of dicts with position_id, strategy_name, side, cost_basis, payout, pnl
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        results = []
        now = datetime.now(timezone.utc)

        try:
            # Find all open positions on this market
            positions = db.query(Position).filter(
                Position.market_id == market_id,
                Position.status == PositionStatus.OPEN.value,
            ).all()

            if not positions:
                return results

            for position in positions:
                # Determine payout based on outcome and position side
                # Position side is BUY YES or BUY NO (we only buy, not sell)
                # We need to know which token was bought
                # token_id tells us if it was YES or NO token

                # Get market to determine which token this is
                from src.db.models import Market
                market = db.query(Market).filter(Market.id == market_id).first()

                if not market:
                    logger.warning(f"Market {market_id} not found for position {position.id}")
                    continue

                # Determine if position is on YES or NO side
                is_yes_position = (position.token_id == market.yes_token_id)

                # Calculate payout per share
                if outcome == "YES":
                    payout_per_share = 1.0 if is_yes_position else 0.0
                elif outcome == "NO":
                    payout_per_share = 0.0 if is_yes_position else 1.0
                else:  # UNKNOWN - refund at entry price
                    payout_per_share = float(position.entry_price)

                # Calculate total payout and P&L
                shares = float(position.size_shares)
                cost_basis = float(position.cost_basis)
                payout = shares * payout_per_share
                pnl = payout - cost_basis

                # Update position
                position.exit_price = payout_per_share
                position.exit_time = now
                position.realized_pnl = pnl
                position.status = PositionStatus.CLOSED.value
                position.close_reason = f"market_resolved_{outcome.lower()}"

                results.append({
                    "position_id": position.id,
                    "strategy_name": position.strategy_name,
                    "side": "YES" if is_yes_position else "NO",
                    "shares": shares,
                    "cost_basis": cost_basis,
                    "payout": payout,
                    "pnl": pnl,
                })

                logger.info(
                    f"Position {position.id} resolved: {position.strategy_name} "
                    f"{'YES' if is_yes_position else 'NO'} → {outcome}, "
                    f"P&L: ${pnl:+.2f}"
                )

            if close_db:
                db.commit()

            return results

        finally:
            if close_db:
                db.close()

    def get_total_position_value(self, db: Optional[Session] = None) -> float:
        """
        Get total current value of all open positions.

        Args:
            db: Optional database session

        Returns:
            Total position value in USD
        """
        positions = self.get_open_positions(db)
        return sum(float(p.current_value or p.cost_basis) for p in positions)
