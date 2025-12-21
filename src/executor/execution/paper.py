"""
Paper Trading Executor.

Simulates order execution using real market data without placing real orders.
Tracks virtual balance and positions in the database.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.executor.models import (
    ExecutorOrder,
    ExecutorTrade,
    Position,
    Signal,
    PaperBalance,
    OrderStatus,
    PositionStatus,
)
from .order_types import (
    OrderRequest,
    OrderResult,
    OrderType,
    create_order,
    calculate_shares_from_usd,
    calculate_usd_from_shares,
)

logger = logging.getLogger(__name__)

# Default paper trading balance
DEFAULT_STARTING_BALANCE = 10000.0

# Simulated slippage based on order size relative to orderbook depth
SLIPPAGE_FACTOR = 0.001  # 0.1% base slippage

# Simulated maker/taker fees (Polymarket is ~0%)
MAKER_FEE = 0.0
TAKER_FEE = 0.0


@dataclass
class OrderbookState:
    """Current orderbook state for simulation."""
    best_bid: Optional[float]
    best_ask: Optional[float]
    mid_price: Optional[float]
    bid_depth_10: Optional[float]  # Depth at 10 levels
    ask_depth_10: Optional[float]
    spread: Optional[float]


class PaperExecutor:
    """
    Paper trading executor.

    Simulates order execution using real market data.
    Tracks virtual balance and positions in the database.
    """

    def __init__(self, starting_balance: float = DEFAULT_STARTING_BALANCE):
        """
        Initialize paper executor.

        Args:
            starting_balance: Initial paper trading balance in USD
        """
        self.starting_balance = starting_balance
        self._ensure_paper_balance()

    def _ensure_paper_balance(self):
        """Ensure paper balance record exists in database."""
        with get_session() as db:
            balance = db.query(PaperBalance).first()
            if balance is None:
                balance = PaperBalance(
                    balance_usd=self.starting_balance,
                    starting_balance_usd=self.starting_balance,
                    high_water_mark=self.starting_balance,
                    low_water_mark=self.starting_balance,
                )
                db.add(balance)
                db.commit()
                logger.info(f"Initialized paper balance: ${self.starting_balance}")

    def get_balance(self) -> float:
        """Get current paper trading balance."""
        with get_session() as db:
            balance = db.query(PaperBalance).first()
            if balance:
                return float(balance.balance_usd)
            return self.starting_balance

    def get_total_value(self) -> float:
        """Get total value (balance + open positions)."""
        balance = self.get_balance()

        with get_session() as db:
            positions = db.query(Position).filter(
                Position.is_paper == True,
                Position.status == PositionStatus.OPEN.value,
            ).all()

            position_value = sum(
                float(p.current_value or p.cost_basis) for p in positions
            )

        return balance + position_value

    def _calculate_slippage(
        self,
        size_usd: float,
        orderbook: OrderbookState,
        is_buy: bool,
    ) -> float:
        """
        Calculate simulated slippage based on order size and liquidity.

        Args:
            size_usd: Order size in USD
            orderbook: Current orderbook state
            is_buy: True for buy orders

        Returns:
            Slippage as decimal (e.g., 0.001 = 0.1%)
        """
        # Base slippage
        slippage = SLIPPAGE_FACTOR

        # Adjust for order size relative to available depth
        depth = orderbook.ask_depth_10 if is_buy else orderbook.bid_depth_10
        if depth and depth > 0:
            size_ratio = size_usd / depth
            # Larger orders relative to depth have more slippage
            slippage += size_ratio * 0.005  # 0.5% per 100% of depth

        # Cap slippage at 2%
        return min(slippage, 0.02)

    def _simulate_fill_price(
        self,
        order: OrderRequest,
        orderbook: OrderbookState,
    ) -> Optional[float]:
        """
        Simulate the fill price for an order.

        Args:
            order: Order request
            orderbook: Current orderbook state

        Returns:
            Simulated fill price, or None if order cannot be filled
        """
        order_obj = create_order(order)
        is_buy = order.side.upper() == "BUY"

        # Get the base price from order type logic
        base_price = order_obj.calculate_price(
            orderbook.best_bid,
            orderbook.best_ask,
            orderbook.mid_price,
        )

        if base_price is None:
            return None

        # For market orders, add slippage
        if order.order_type == OrderType.MARKET:
            slippage = self._calculate_slippage(order.size_usd, orderbook, is_buy)
            if is_buy:
                return min(base_price * (1 + slippage), 0.999)
            else:
                return max(base_price * (1 - slippage), 0.001)

        # For limit orders, assume fill at limit price (optimistic)
        # In reality, limit orders may not fill
        return base_price

    def execute_signal(
        self,
        signal: Signal,
        orderbook: OrderbookState,
        order_type: OrderType = OrderType.LIMIT,
        limit_offset_bps: int = 50,
        db: Optional[Session] = None,
    ) -> OrderResult:
        """
        Execute a signal in paper mode.

        Args:
            signal: Trading signal to execute
            orderbook: Current orderbook state
            order_type: Type of order to place
            limit_offset_bps: Limit order offset in basis points
            db: Optional database session

        Returns:
            OrderResult with execution details
        """
        should_close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            # Check balance
            balance = self.get_balance()
            size_usd = float(signal.suggested_size_usd or 25.0)

            if size_usd > balance:
                return OrderResult(
                    success=False,
                    message=f"Insufficient balance: ${balance:.2f} < ${size_usd:.2f}",
                )

            # Create order request
            order_request = OrderRequest(
                token_id=signal.token_id,
                side=signal.side,
                size_usd=size_usd,
                order_type=order_type,
                limit_offset_bps=limit_offset_bps,
            )

            # Simulate fill price
            fill_price = self._simulate_fill_price(order_request, orderbook)
            if fill_price is None:
                return OrderResult(
                    success=False,
                    message="Could not determine fill price (no liquidity)",
                )

            # Calculate shares
            shares = calculate_shares_from_usd(size_usd, fill_price)
            actual_usd = calculate_usd_from_shares(shares, fill_price)

            # Create order record
            now = datetime.now(timezone.utc)
            order = ExecutorOrder(
                signal_id=signal.id,
                is_paper=True,
                token_id=signal.token_id,
                side=signal.side,
                order_type=order_type.value,
                limit_price=fill_price,
                executed_price=fill_price,
                size_usd=actual_usd,
                size_shares=shares,
                filled_shares=shares,
                status=OrderStatus.FILLED.value,
                submitted_at=now,
                filled_at=now,
            )
            db.add(order)
            db.flush()

            # Create trade record
            trade = ExecutorTrade(
                order_id=order.id,
                is_paper=True,
                price=fill_price,
                size_shares=shares,
                size_usd=actual_usd,
                side=signal.side,
                fee_usd=actual_usd * TAKER_FEE if order_type == OrderType.MARKET else actual_usd * MAKER_FEE,
            )
            db.add(trade)
            db.flush()

            # Create or update position
            position = self._update_position(
                db=db,
                signal=signal,
                order=order,
                trade=trade,
                fill_price=fill_price,
                shares=shares,
                cost=actual_usd,
            )
            trade.position_id = position.id

            # Update balance
            self._update_balance(db, -actual_usd)

            # Update signal status
            signal.status = "executed"
            signal.processed_at = now

            db.commit()

            logger.info(
                f"Paper order executed: {signal.side} {shares:.2f} shares @ ${fill_price:.4f} = ${actual_usd:.2f}"
            )

            return OrderResult(
                success=True,
                order_id=str(order.id),
                executed_price=fill_price,
                executed_shares=shares,
                executed_usd=actual_usd,
                message="Paper order filled",
            )

        except Exception as e:
            logger.error(f"Paper execution failed: {e}")
            if should_close_db:
                db.rollback()
            return OrderResult(
                success=False,
                message=str(e),
            )
        finally:
            if should_close_db:
                db.close()

    def _update_position(
        self,
        db: Session,
        signal: Signal,
        order: ExecutorOrder,
        trade: ExecutorTrade,
        fill_price: float,
        shares: float,
        cost: float,
    ) -> Position:
        """
        Create or update position for the trade.

        For paper trading, we create a new position for each signal.
        """
        now = datetime.now(timezone.utc)

        position = Position(
            is_paper=True,
            strategy_name=signal.strategy_name,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            entry_order_id=order.id,
            entry_price=fill_price,
            entry_time=now,
            size_shares=shares,
            cost_basis=cost,
            current_price=fill_price,
            current_value=cost,
            status=PositionStatus.OPEN.value,
        )
        db.add(position)
        db.flush()

        return position

    def _update_balance(self, db: Session, change: float):
        """
        Update paper balance.

        High water mark is based on total portfolio value (cash + positions),
        not just cash balance, to be consistent with drawdown calculation.

        Args:
            db: Database session
            change: Amount to add (negative for deductions)
        """
        balance = db.query(PaperBalance).first()
        if balance:
            new_balance = float(balance.balance_usd) + change
            balance.balance_usd = new_balance

            # Calculate total portfolio value (cash + open positions)
            positions = db.query(Position).filter(
                Position.is_paper == True,
                Position.status == PositionStatus.OPEN.value,
            ).all()
            position_value = sum(
                float(p.current_value or p.cost_basis) for p in positions
            )
            total_value = new_balance + position_value

            # Total P&L based on portfolio value vs starting balance
            balance.total_pnl = total_value - float(balance.starting_balance_usd)

            # High/low water marks track total portfolio value, not just cash
            if total_value > float(balance.high_water_mark):
                balance.high_water_mark = total_value
            if total_value < float(balance.low_water_mark):
                balance.low_water_mark = total_value

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        reason: str = "manual",
    ) -> OrderResult:
        """
        Close a paper position.

        Args:
            position_id: Position ID to close
            exit_price: Price to close at
            reason: Reason for closing

        Returns:
            OrderResult with details
        """
        with get_session() as db:
            position = db.query(Position).filter(
                Position.id == position_id,
                Position.is_paper == True,
            ).first()

            if position is None:
                return OrderResult(
                    success=False,
                    message=f"Position {position_id} not found",
                )

            if position.status != PositionStatus.OPEN.value:
                return OrderResult(
                    success=False,
                    message=f"Position {position_id} is not open",
                )

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

            # Return funds to balance
            self._update_balance(db, exit_value)

            db.commit()

            logger.info(
                f"Paper position closed: {position_id} @ ${exit_price:.4f}, P&L: ${pnl:.2f}"
            )

            return OrderResult(
                success=True,
                order_id=str(position_id),
                executed_price=exit_price,
                executed_shares=float(position.size_shares),
                executed_usd=exit_value,
                message=f"Position closed, P&L: ${pnl:.2f}",
            )

    def update_position_prices(self, price_updates: dict[int, float]):
        """
        Update current prices for open positions.

        Args:
            price_updates: Dict of market_id -> current_price
        """
        with get_session() as db:
            positions = db.query(Position).filter(
                Position.is_paper == True,
                Position.status == PositionStatus.OPEN.value,
            ).all()

            for position in positions:
                if position.market_id in price_updates:
                    current_price = price_updates[position.market_id]
                    position.current_price = current_price
                    position.current_value = float(position.size_shares) * current_price
                    position.unrealized_pnl = position.current_value - float(position.cost_basis)
                    position.unrealized_pnl_pct = (
                        position.unrealized_pnl / float(position.cost_basis)
                        if position.cost_basis else 0
                    )

            db.commit()

    def get_open_positions(self) -> list[Position]:
        """Get all open paper positions."""
        with get_session() as db:
            return db.query(Position).filter(
                Position.is_paper == True,
                Position.status == PositionStatus.OPEN.value,
            ).all()

    def get_stats(self) -> dict[str, Any]:
        """Get paper trading statistics."""
        with get_session() as db:
            balance_record = db.query(PaperBalance).first()
            if not balance_record:
                return {
                    "balance": self.starting_balance,
                    "starting_balance": self.starting_balance,
                    "total_pnl": 0,
                    "total_pnl_pct": 0,
                    "open_positions": 0,
                    "total_trades": 0,
                }

            open_positions = db.query(Position).filter(
                Position.is_paper == True,
                Position.status == PositionStatus.OPEN.value,
            ).count()

            total_trades = db.query(ExecutorTrade).filter(
                ExecutorTrade.is_paper == True,
            ).count()

            closed_positions = db.query(Position).filter(
                Position.is_paper == True,
                Position.status == PositionStatus.CLOSED.value,
            ).all()

            realized_pnl = sum(float(p.realized_pnl) for p in closed_positions)
            winning = len([p for p in closed_positions if float(p.realized_pnl) > 0])
            losing = len([p for p in closed_positions if float(p.realized_pnl) < 0])

            balance = float(balance_record.balance_usd)
            starting = float(balance_record.starting_balance_usd)

            return {
                "balance": balance,
                "starting_balance": starting,
                "total_pnl": float(balance_record.total_pnl),
                "total_pnl_pct": (balance - starting) / starting if starting else 0,
                "realized_pnl": realized_pnl,
                "high_water_mark": float(balance_record.high_water_mark),
                "low_water_mark": float(balance_record.low_water_mark),
                "max_drawdown": (float(balance_record.high_water_mark) - float(balance_record.low_water_mark)) / float(balance_record.high_water_mark) if balance_record.high_water_mark else 0,
                "open_positions": open_positions,
                "total_trades": total_trades,
                "closed_positions": len(closed_positions),
                "winning_trades": winning,
                "losing_trades": losing,
                "win_rate": winning / (winning + losing) if (winning + losing) > 0 else 0,
            }

    def reset(self, starting_balance: Optional[float] = None):
        """
        Reset paper trading state.

        Args:
            starting_balance: New starting balance (uses default if None)
        """
        balance = starting_balance or self.starting_balance

        with get_session() as db:
            # Delete all paper trades
            db.query(ExecutorTrade).filter(ExecutorTrade.is_paper == True).delete()

            # Delete all paper positions
            db.query(Position).filter(Position.is_paper == True).delete()

            # Delete all paper orders
            db.query(ExecutorOrder).filter(ExecutorOrder.is_paper == True).delete()

            # Reset balance
            balance_record = db.query(PaperBalance).first()
            if balance_record:
                balance_record.balance_usd = balance
                balance_record.starting_balance_usd = balance
                balance_record.high_water_mark = balance
                balance_record.low_water_mark = balance
                balance_record.total_pnl = 0
            else:
                db.add(PaperBalance(
                    balance_usd=balance,
                    starting_balance_usd=balance,
                    high_water_mark=balance,
                    low_water_mark=balance,
                ))

            db.commit()

        logger.info(f"Paper trading reset with balance: ${balance}")
