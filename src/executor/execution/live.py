"""
Live Trading Executor.

Executes real orders on Polymarket using the order client.
Tracks positions and orders in the database.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.executor.clients.order_client import PolymarketOrderClient, get_order_client
from src.executor.models import (
    ExecutorOrder,
    ExecutorTrade,
    Position,
    Signal,
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

# Polling interval for order status checks
ORDER_STATUS_POLL_INTERVAL = 0.5  # seconds
ORDER_STATUS_TIMEOUT = 30  # seconds

# Maximum allowed fee rate (in basis points)
# 200 bps = 2% - protects against high-fee markets like 15-min crypto
MAX_FEE_RATE_BPS = 200

# Maximum number of open positions allowed
# Safety limit to prevent runaway trading
MAX_OPEN_POSITIONS = 10


@dataclass
class LiveOrderbookState:
    """Current orderbook state from live market."""
    best_bid: Optional[float]
    best_ask: Optional[float]
    mid_price: Optional[float]
    bid_depth_10: Optional[float]
    ask_depth_10: Optional[float]
    spread: Optional[float]


class LiveExecutor:
    """
    Live trading executor.

    Executes real orders on Polymarket via py_clob_client.
    Tracks positions and orders in the database.
    """

    def __init__(self, order_client: Optional[PolymarketOrderClient] = None):
        """
        Initialize live executor.

        Args:
            order_client: Optional order client (uses singleton if not provided)
        """
        self._order_client = order_client
        self._initialized = False

    @property
    def order_client(self) -> PolymarketOrderClient:
        """Lazy initialization of order client."""
        if self._order_client is None:
            self._order_client = get_order_client()
            self._initialized = True
        return self._order_client

    def get_balance(self) -> float:
        """Get current USDC balance from Polymarket."""
        try:
            return self.order_client.get_balance()
        except Exception as e:
            logger.error(f"Failed to get live balance: {e}")
            return 0.0

    def get_orderbook_state(self, token_id: str) -> Optional[LiveOrderbookState]:
        """
        Fetch current orderbook state for a token.

        Args:
            token_id: Polymarket token ID

        Returns:
            LiveOrderbookState or None if failed
        """
        try:
            orderbook = self.order_client.get_orderbook(token_id)
            best_bid, best_ask = self.order_client.get_best_bid_ask(orderbook)

            # Calculate depth at 10 levels
            bids = orderbook.get("bids", []) if isinstance(orderbook, dict) else getattr(orderbook, 'bids', []) or []
            asks = orderbook.get("asks", []) if isinstance(orderbook, dict) else getattr(orderbook, 'asks', []) or []

            bid_depth = sum(
                float(b.get("size", 0) if isinstance(b, dict) else getattr(b, 'size', 0)) * float(b.get("price", 0) if isinstance(b, dict) else getattr(b, 'price', 0))
                for b in bids[:10]
            )
            ask_depth = sum(
                float(a.get("size", 0) if isinstance(a, dict) else getattr(a, 'size', 0)) * float(a.get("price", 0) if isinstance(a, dict) else getattr(a, 'price', 0))
                for a in asks[:10]
            )

            mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else None
            spread = best_ask - best_bid if best_bid and best_ask else None

            return LiveOrderbookState(
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=mid_price,
                bid_depth_10=bid_depth,
                ask_depth_10=ask_depth,
                spread=spread,
            )
        except Exception as e:
            logger.error(f"Failed to get orderbook for {token_id}: {e}")
            return None

    def execute_signal(
        self,
        signal: Signal,
        orderbook: Optional[LiveOrderbookState] = None,
        order_type: OrderType = OrderType.LIMIT,
        limit_offset_bps: int = 50,
        db: Optional[Session] = None,
    ) -> OrderResult:
        """
        Execute a signal with a real order.

        Args:
            signal: Trading signal to execute
            orderbook: Optional orderbook state (fetched if not provided)
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
            # Fetch orderbook if not provided
            if orderbook is None:
                orderbook = self.get_orderbook_state(signal.token_id)
                if orderbook is None:
                    return OrderResult(
                        success=False,
                        message="Could not fetch orderbook",
                    )

            # CRITICAL: Check max open positions FIRST (before any other checks)
            # This is our primary safety limit to prevent runaway trading
            try:
                current_positions = self.order_client.get_positions()
                num_positions = len(current_positions) if current_positions else 0
                if num_positions >= MAX_OPEN_POSITIONS:
                    return OrderResult(
                        success=False,
                        message=f"Max positions reached: {num_positions}/{MAX_OPEN_POSITIONS} - refusing new trade",
                    )
                logger.info(f"Position count: {num_positions}/{MAX_OPEN_POSITIONS}")
            except Exception as e:
                # If we can't check positions, BLOCK the trade (fail safe)
                logger.error(f"Could not check position count: {e} - blocking trade for safety")
                return OrderResult(
                    success=False,
                    message=f"Could not verify position count: {e}",
                )

            # CRITICAL: Check fee rate before trading
            # High-fee markets (like 15-min crypto with 10% fees) should be rejected
            try:
                fee_rate_bps = self.order_client.get_fee_rate_bps(signal.token_id)
                if fee_rate_bps > MAX_FEE_RATE_BPS:
                    return OrderResult(
                        success=False,
                        message=f"Fee rate too high: {fee_rate_bps} bps > {MAX_FEE_RATE_BPS} bps max",
                    )
                logger.info(f"Fee rate for token: {fee_rate_bps} bps (max allowed: {MAX_FEE_RATE_BPS})")
            except Exception as e:
                logger.warning(f"Could not fetch fee rate: {e}, proceeding with caution")

            # CRITICAL: Check for duplicate trades on Polymarket
            # This is a belt-and-suspenders check - position tracking should catch this,
            # but this prevents duplicates even if position tracking fails
            try:
                recent_trades = self.order_client.get_trades(asset_id=signal.token_id)
                existing_buys = [t for t in (recent_trades or []) if t.get('side') == 'BUY']
                if existing_buys:
                    return OrderResult(
                        success=False,
                        message=f"Duplicate trade blocked: already have {len(existing_buys)} BUY trade(s) on this token",
                    )
            except Exception as e:
                logger.warning(f"Could not check for duplicate trades: {e}")

            # Check balance
            balance = self.get_balance()
            size_usd = float(signal.suggested_size_usd or 25.0)

            if size_usd > balance:
                return OrderResult(
                    success=False,
                    message=f"Insufficient balance: ${balance:.2f} < ${size_usd:.2f}",
                )

            # Calculate limit price
            order_request = OrderRequest(
                token_id=signal.token_id,
                side=signal.side,
                size_usd=size_usd,
                order_type=order_type,
                limit_offset_bps=limit_offset_bps,
            )
            order_obj = create_order(order_request)
            limit_price = order_obj.calculate_price(
                orderbook.best_bid,
                orderbook.best_ask,
                orderbook.mid_price,
            )

            if limit_price is None:
                return OrderResult(
                    success=False,
                    message="Could not determine limit price",
                )

            # Create order record (pending)
            now = datetime.now(timezone.utc)
            order = ExecutorOrder(
                signal_id=signal.id,
                is_paper=False,
                token_id=signal.token_id,
                side=signal.side,
                order_type=order_type.value,
                limit_price=limit_price,
                size_usd=size_usd,
                status=OrderStatus.PENDING.value,
                submitted_at=now,
            )
            db.add(order)
            db.flush()

            # Place the order
            try:
                if order_type == OrderType.MARKET:
                    result = self.order_client.place_market_order(
                        token_id=signal.token_id,
                        side=signal.side,
                        size_usd=size_usd,
                    )
                else:
                    result = self.order_client.place_limit_order(
                        token_id=signal.token_id,
                        side=signal.side,
                        price=limit_price,
                        size_usd=size_usd,
                    )

                # Extract order ID from result
                polymarket_order_id = result.get("orderID") or result.get("id")
                if not polymarket_order_id:
                    logger.warning(f"No order ID in response: {result}")

                order.polymarket_order_id = polymarket_order_id

                # Check if order was immediately matched (common on Polymarket)
                order_status = result.get("status", "").lower()
                is_matched = order_status == "matched" or result.get("success") == True

                if is_matched and result.get("transactionsHashes"):
                    # Order immediately filled - extract fill info from response
                    logger.info(f"Order immediately matched: {result}")

                    # Extract fill details from response
                    # takingAmount = shares received, makingAmount = USD paid
                    filled_shares = float(result.get("takingAmount", 0))
                    filled_usd = float(result.get("makingAmount", 0))
                    fill_price = filled_usd / filled_shares if filled_shares > 0 else limit_price

                    now = datetime.now(timezone.utc)
                    order.status = OrderStatus.FILLED.value
                    order.executed_price = fill_price
                    order.filled_shares = filled_shares
                    order.size_shares = filled_shares
                    order.filled_at = now

                    # Create trade record
                    trade = ExecutorTrade(
                        order_id=order.id,
                        is_paper=False,
                        price=fill_price,
                        size_shares=filled_shares,
                        size_usd=filled_usd,
                        side=signal.side,
                        fee_usd=0,
                    )
                    db.add(trade)
                    db.flush()

                    # Create position - CRITICAL for tracking
                    position = self._create_position(
                        db=db,
                        signal=signal,
                        order=order,
                        fill_price=fill_price,
                        shares=filled_shares,
                        cost=filled_usd,
                    )
                    trade.position_id = position.id

                    db.commit()

                    logger.info(
                        f"Live order filled immediately: {filled_shares:.2f} shares @ ${fill_price:.4f}, "
                        f"Position ID: {position.id}"
                    )

                    return OrderResult(
                        success=True,
                        order_id=polymarket_order_id,
                        executed_price=fill_price,
                        executed_shares=filled_shares,
                        executed_usd=filled_usd,
                        position_id=position.id,
                        message="Order filled immediately",
                    )

                # Order submitted but not immediately matched - poll for fill
                order.status = OrderStatus.SUBMITTED.value
                db.commit()

                logger.info(f"Live order submitted, polling: {polymarket_order_id}")

                # Poll for fill status
                fill_result = self._wait_for_fill(
                    polymarket_order_id,
                    order,
                    signal,
                    db,
                )

                return fill_result

            except Exception as e:
                order.status = OrderStatus.FAILED.value
                order.error_message = str(e)
                db.commit()
                logger.error(f"Failed to place live order: {e}")
                return OrderResult(
                    success=False,
                    message=f"Order placement failed: {e}",
                )

        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            if should_close_db:
                db.rollback()
            return OrderResult(
                success=False,
                message=str(e),
            )
        finally:
            if should_close_db:
                db.close()

    def _wait_for_fill(
        self,
        order_id: str,
        order: ExecutorOrder,
        signal: Signal,
        db: Session,
    ) -> OrderResult:
        """
        Poll for order fill status.

        Args:
            order_id: Polymarket order ID
            order: ExecutorOrder record
            signal: Original signal
            db: Database session

        Returns:
            OrderResult with fill details
        """
        start_time = time.time()

        while time.time() - start_time < ORDER_STATUS_TIMEOUT:
            try:
                order_status = self.order_client.get_order(order_id)

                status = order_status.get("status", "").upper()
                filled_size = float(order_status.get("sizeFilled", 0))
                total_size = float(order_status.get("size", 0))

                if status == "FILLED" or (filled_size > 0 and filled_size >= total_size * 0.99):
                    # Order filled
                    fill_price = float(order_status.get("price", order.limit_price))
                    filled_shares = filled_size
                    filled_usd = filled_shares * fill_price

                    now = datetime.now(timezone.utc)
                    order.status = OrderStatus.FILLED.value
                    order.executed_price = fill_price
                    order.filled_shares = filled_shares
                    order.size_shares = filled_shares
                    order.filled_at = now

                    # Create trade record
                    trade = ExecutorTrade(
                        order_id=order.id,
                        is_paper=False,
                        price=fill_price,
                        size_shares=filled_shares,
                        size_usd=filled_usd,
                        side=signal.side,
                        fee_usd=0,  # Polymarket has 0% fees
                    )
                    db.add(trade)
                    db.flush()

                    # Create position
                    position = self._create_position(
                        db=db,
                        signal=signal,
                        order=order,
                        fill_price=fill_price,
                        shares=filled_shares,
                        cost=filled_usd,
                    )
                    trade.position_id = position.id

                    db.commit()

                    logger.info(
                        f"Live order filled: {filled_shares:.2f} shares @ ${fill_price:.4f}"
                    )

                    return OrderResult(
                        success=True,
                        order_id=order_id,
                        executed_price=fill_price,
                        executed_shares=filled_shares,
                        executed_usd=filled_usd,
                        message="Order filled",
                    )

                elif status in ("CANCELLED", "EXPIRED"):
                    order.status = OrderStatus.CANCELLED.value
                    db.commit()
                    return OrderResult(
                        success=False,
                        order_id=order_id,
                        message=f"Order {status.lower()}",
                    )

                elif status == "REJECTED":
                    order.status = OrderStatus.FAILED.value
                    order.error_message = order_status.get("reason", "Rejected")
                    db.commit()
                    return OrderResult(
                        success=False,
                        order_id=order_id,
                        message=f"Order rejected: {order_status.get('reason')}",
                    )

            except Exception as e:
                logger.warning(f"Error polling order status: {e}")

            time.sleep(ORDER_STATUS_POLL_INTERVAL)

        # Timeout - order still open
        order.status = OrderStatus.OPEN.value
        db.commit()

        return OrderResult(
            success=False,
            order_id=order_id,
            message="Order timeout - still open",
        )

    def _create_position(
        self,
        db: Session,
        signal: Signal,
        order: ExecutorOrder,
        fill_price: float,
        shares: float,
        cost: float,
    ) -> Position:
        """Create a position record for a filled order."""
        now = datetime.now(timezone.utc)

        position = Position(
            is_paper=False,
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

    def close_position(
        self,
        position_id: int,
        order_type: OrderType = OrderType.LIMIT,
        limit_offset_bps: int = 50,
    ) -> OrderResult:
        """
        Close a live position.

        Args:
            position_id: Position ID to close
            order_type: Type of order to place
            limit_offset_bps: Limit order offset in basis points

        Returns:
            OrderResult with details
        """
        with get_session() as db:
            position = db.query(Position).filter(
                Position.id == position_id,
                Position.is_paper == False,
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

            # Determine exit side (opposite of entry)
            exit_side = "SELL" if position.side == "BUY" else "BUY"

            # Get current orderbook
            orderbook = self.get_orderbook_state(position.token_id)
            if orderbook is None:
                return OrderResult(
                    success=False,
                    message="Could not fetch orderbook",
                )

            # Calculate exit price
            order_request = OrderRequest(
                token_id=position.token_id,
                side=exit_side,
                size_usd=float(position.size_shares) * (orderbook.mid_price or 0.5),
                order_type=order_type,
                limit_offset_bps=limit_offset_bps,
            )
            order_obj = create_order(order_request)
            exit_price = order_obj.calculate_price(
                orderbook.best_bid,
                orderbook.best_ask,
                orderbook.mid_price,
            )

            if exit_price is None:
                return OrderResult(
                    success=False,
                    message="Could not determine exit price",
                )

            # Place exit order
            try:
                size_usd = float(position.size_shares) * exit_price

                if order_type == OrderType.MARKET:
                    result = self.order_client.place_market_order(
                        token_id=position.token_id,
                        side=exit_side,
                        size_usd=size_usd,
                    )
                else:
                    result = self.order_client.place_limit_order(
                        token_id=position.token_id,
                        side=exit_side,
                        price=exit_price,
                        size_usd=size_usd,
                    )

                polymarket_order_id = result.get("orderID") or result.get("id")

                # Wait for fill (simplified - could poll like in execute_signal)
                time.sleep(2)
                order_status = self.order_client.get_order(polymarket_order_id)

                if order_status.get("status", "").upper() == "FILLED":
                    fill_price = float(order_status.get("price", exit_price))
                    exit_value = float(position.size_shares) * fill_price
                    pnl = exit_value - float(position.cost_basis)

                    # Update position
                    now = datetime.now(timezone.utc)
                    position.exit_price = fill_price
                    position.exit_time = now
                    position.exit_order_id = None  # Could create order record
                    position.realized_pnl = pnl
                    position.status = PositionStatus.CLOSED.value
                    position.close_reason = "manual"

                    db.commit()

                    logger.info(
                        f"Live position closed: {position_id} @ ${fill_price:.4f}, P&L: ${pnl:.2f}"
                    )

                    return OrderResult(
                        success=True,
                        order_id=polymarket_order_id,
                        executed_price=fill_price,
                        executed_shares=float(position.size_shares),
                        executed_usd=exit_value,
                        message=f"Position closed, P&L: ${pnl:.2f}",
                    )
                else:
                    return OrderResult(
                        success=False,
                        order_id=polymarket_order_id,
                        message="Exit order not filled",
                    )

            except Exception as e:
                logger.error(f"Failed to close position: {e}")
                return OrderResult(
                    success=False,
                    message=str(e),
                )

    def cancel_open_orders(self) -> int:
        """Cancel all open orders."""
        try:
            return self.order_client.cancel_all_orders()
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            return 0

    def get_open_positions(self) -> list[Position]:
        """Get all open live positions."""
        with get_session() as db:
            return db.query(Position).filter(
                Position.is_paper == False,
                Position.status == PositionStatus.OPEN.value,
            ).all()

    def get_stats(self) -> dict[str, Any]:
        """Get live trading statistics."""
        with get_session() as db:
            open_positions = db.query(Position).filter(
                Position.is_paper == False,
                Position.status == PositionStatus.OPEN.value,
            ).count()

            total_trades = db.query(ExecutorTrade).filter(
                ExecutorTrade.is_paper == False,
            ).count()

            closed_positions = db.query(Position).filter(
                Position.is_paper == False,
                Position.status == PositionStatus.CLOSED.value,
            ).all()

            realized_pnl = sum(float(p.realized_pnl or 0) for p in closed_positions)
            winning = len([p for p in closed_positions if float(p.realized_pnl or 0) > 0])
            losing = len([p for p in closed_positions if float(p.realized_pnl or 0) < 0])

            balance = self.get_balance()

            # Get open position values
            open_pos_list = db.query(Position).filter(
                Position.is_paper == False,
                Position.status == PositionStatus.OPEN.value,
            ).all()
            position_value = sum(float(p.current_value or p.cost_basis) for p in open_pos_list)

            return {
                "balance": balance,
                "position_value": position_value,
                "total_value": balance + position_value,
                "realized_pnl": realized_pnl,
                "open_positions": open_positions,
                "total_trades": total_trades,
                "closed_positions": len(closed_positions),
                "winning_trades": winning,
                "losing_trades": losing,
                "win_rate": winning / (winning + losing) if (winning + losing) > 0 else 0,
            }
