"""
Live Trading Executor.

Executes real orders on Polymarket using the order client.
Tracks positions and orders in the database.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
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

# Retry settings for order placement (handles flaky proxy connections)
ORDER_PLACEMENT_MAX_RETRIES = 3
ORDER_PLACEMENT_RETRY_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff in seconds
# Maximum price movement allowed between retries before aborting
MAX_PRICE_MOVE_BETWEEN_RETRIES = 0.03  # 3% - abort if price moved more than this

# Maximum allowed fee rate (in basis points)
# 200 bps = 2% - protects against high-fee markets like 15-min crypto
MAX_FEE_RATE_BPS = 200

# Minimum order size in USD (Polymarket minimum is $1)
# Order size = max(MIN_ORDER_SIZE_USD, strategy_suggested_size)
# Set to $1.05 to account for rounding (ROUND_DOWN) during USD→shares→USD conversion
MIN_ORDER_SIZE_USD = 1.05

# Maximum allowed price deviation between signal and live orderbook
# If the live price differs from signal price by more than this %, reject the trade
# This prevents trading on stale snapshot data (critical for fast-moving markets like esports)
MAX_PRICE_DEVIATION = 0.05  # 5% max deviation (strict to catch stale signals)

# Maximum signal age in seconds - reject signals older than this
# Prevents executing on stale signals that sat in queue too long
# Note: Strict price deviation check (5%) provides primary staleness protection
MAX_SIGNAL_AGE_SECONDS = 120  # 2 minutes max (price check is primary safeguard)

# Maximum spread allowed for live trading
# High spread = illiquid market or stale data = dangerous
MAX_SPREAD_FOR_LIVE = 0.03  # 3% max spread (ensures liquid markets only)


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

    def _is_retryable_error(self, error: Exception) -> bool:
        """
        Check if an error is retryable (network/proxy issue vs business logic rejection).

        Only retries on network-related errors to avoid slippage from re-submitting
        orders that were actually rejected for valid reasons (price, insufficient funds, etc.)

        Args:
            error: The exception that occurred

        Returns:
            True if error is network-related and safe to retry
        """
        error_str = str(error).lower()

        # Network/proxy errors that are safe to retry
        retryable_patterns = [
            "request exception",  # PolyApiException network error
            "connection",
            "timeout",
            "timed out",
            "proxy",
            "socks",
            "socket",
            "reset by peer",
            "broken pipe",
            "network",
            "unreachable",
            "refused",
            "status_code=none",  # Common proxy failure pattern
        ]

        for pattern in retryable_patterns:
            if pattern in error_str:
                return True

        # Check exception type for common network errors
        error_type = type(error).__name__.lower()
        network_error_types = [
            "connectionerror",
            "timeout",
            "sockserror",
            "proxyerror",
            "requestexception",
        ]

        for error_type_pattern in network_error_types:
            if error_type_pattern in error_type:
                return True

        return False

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
            # CRITICAL: Check signal age - reject stale signals
            # This prevents executing signals that sat in queue too long
            from datetime import datetime, timezone
            if hasattr(signal, 'created_at') and signal.created_at:
                signal_age = (datetime.now(timezone.utc) - signal.created_at.replace(tzinfo=timezone.utc)).total_seconds()
                if signal_age > MAX_SIGNAL_AGE_SECONDS:
                    return OrderResult(
                        success=False,
                        message=f"Signal too old: {signal_age:.1f}s > max {MAX_SIGNAL_AGE_SECONDS}s",
                    )
                logger.info(f"Signal age: {signal_age:.1f}s (max {MAX_SIGNAL_AGE_SECONDS}s)")

            # Fetch orderbook if not provided
            if orderbook is None:
                orderbook = self.get_orderbook_state(signal.token_id)
                if orderbook is None:
                    return OrderResult(
                        success=False,
                        message="Could not fetch orderbook",
                    )

            # CRITICAL: Check spread - reject high-spread (illiquid) markets
            # High spread indicates illiquidity or stale data
            if orderbook.spread is not None and orderbook.spread > MAX_SPREAD_FOR_LIVE:
                return OrderResult(
                    success=False,
                    message=f"Spread too high: {orderbook.spread:.1%} > max {MAX_SPREAD_FOR_LIVE:.0%}",
                )

            # CRITICAL: Check price deviation to prevent trading on stale data
            # This catches cases where snapshot price is outdated vs live orderbook
            # (e.g., esports markets that move fast during gameplay)
            if hasattr(signal, 'price_at_signal') and signal.price_at_signal:
                signal_price = float(signal.price_at_signal)  # Convert Decimal to float
                # Calculate live YES price from orderbook
                live_yes_price = None
                if orderbook.best_bid is not None and orderbook.best_ask is not None:
                    live_yes_price = (orderbook.best_bid + orderbook.best_ask) / 2
                elif orderbook.best_bid is not None:
                    live_yes_price = orderbook.best_bid
                elif orderbook.best_ask is not None:
                    live_yes_price = orderbook.best_ask

                if live_yes_price is not None and signal_price > 0:
                    # Calculate deviation
                    deviation = abs(live_yes_price - signal_price) / signal_price
                    if deviation > MAX_PRICE_DEVIATION:
                        return OrderResult(
                            success=False,
                            message=f"Price moved too much: signal={signal_price:.2%}, live={live_yes_price:.2%}, deviation={deviation:.1%} > max {MAX_PRICE_DEVIATION:.0%}",
                        )
                    logger.info(f"Price check passed: signal={signal_price:.2%}, live={live_yes_price:.2%}, deviation={deviation:.1%}")

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

            # CRITICAL: Multi-layer duplicate prevention
            # Layer 1: Check our DB for recent orders on this token (catches untracked fills)
            try:
                from sqlalchemy import and_
                ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
                recent_orders = db.query(ExecutorOrder).filter(
                    and_(
                        ExecutorOrder.token_id == signal.token_id,
                        ExecutorOrder.is_paper == False,
                        ExecutorOrder.submitted_at >= ten_mins_ago,
                        ExecutorOrder.status.in_([
                            OrderStatus.PENDING.value,
                            OrderStatus.SUBMITTED.value,
                            OrderStatus.FILLED.value,
                            # Include cancelled - they might have actually filled!
                        ]),
                    )
                ).all()
                if recent_orders:
                    return OrderResult(
                        success=False,
                        message=f"Duplicate blocked: {len(recent_orders)} recent order(s) on this token in last 10 min",
                    )
            except Exception as e:
                logger.error(f"Could not check recent orders: {e} - blocking trade for safety")
                return OrderResult(
                    success=False,
                    message=f"Could not verify recent orders: {e}",
                )

            # Layer 2: Check our DB for open positions on this token
            try:
                existing_position = db.query(Position).filter(
                    and_(
                        Position.token_id == signal.token_id,
                        Position.is_paper == False,
                        Position.status == PositionStatus.OPEN.value,
                    )
                ).first()
                if existing_position:
                    return OrderResult(
                        success=False,
                        message=f"Duplicate blocked: already have open position {existing_position.id} on this token",
                    )
            except Exception as e:
                logger.error(f"Could not check positions: {e} - blocking trade for safety")
                return OrderResult(
                    success=False,
                    message=f"Could not verify positions: {e}",
                )

            # Layer 3: Check Polymarket API for existing trades (belt and suspenders)
            try:
                recent_trades = self.order_client.get_trades(asset_id=signal.token_id)
                existing_buys = [t for t in (recent_trades or []) if t.get('side') == 'BUY']
                if existing_buys:
                    return OrderResult(
                        success=False,
                        message=f"Duplicate blocked: Polymarket shows {len(existing_buys)} BUY trade(s) on this token",
                    )
            except Exception as e:
                logger.warning(f"Could not check Polymarket trades: {e}")

            # Check balance and apply minimum order size
            balance = self.get_balance()
            size_usd = float(signal.suggested_size_usd or 25.0)

            # Apply minimum order size: max($1, strategy_suggested_size)
            # Polymarket requires minimum $1 orders
            size_usd = max(MIN_ORDER_SIZE_USD, size_usd)

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

            # Place the order with retry logic for network errors
            # Store initial price for slippage protection on retries
            initial_mid_price = orderbook.mid_price
            current_limit_price = limit_price
            last_error: Optional[Exception] = None

            for attempt in range(ORDER_PLACEMENT_MAX_RETRIES):
                try:
                    if attempt > 0:
                        # Wait before retry with exponential backoff
                        delay = ORDER_PLACEMENT_RETRY_DELAYS[min(attempt - 1, len(ORDER_PLACEMENT_RETRY_DELAYS) - 1)]
                        logger.info(f"Order retry {attempt + 1}/{ORDER_PLACEMENT_MAX_RETRIES}, waiting {delay}s...")
                        time.sleep(delay)

                        # CRITICAL: Check if previous attempt actually succeeded (response lost due to network)
                        # This prevents duplicate orders when order succeeds but response times out
                        try:
                            wallet_check = self._check_wallet_for_fill(signal.token_id)
                            if wallet_check:
                                logger.warning(
                                    f"Retry {attempt + 1}: Found fill in wallet from previous attempt! "
                                    f"Size: {wallet_check['size']:.2f} @ ${wallet_check['avg_price']:.4f}"
                                )
                                # Create position from wallet data
                                return self._create_position_from_wallet(db, signal, order, wallet_check)

                            # Also check Polymarket API for trades (belt and suspenders)
                            api_trades = self.order_client.get_trades(asset_id=signal.token_id)
                            existing_buys = [t for t in (api_trades or []) if t.get('side') == 'BUY']
                            if existing_buys:
                                logger.warning(
                                    f"Retry {attempt + 1}: Found BUY trade on Polymarket from previous attempt! "
                                    f"Aborting retry to prevent duplicate."
                                )
                                order.status = OrderStatus.FAILED.value
                                order.error_message = "Previous attempt may have succeeded (found trade) - aborting retry"
                                db.commit()
                                return OrderResult(
                                    success=False,
                                    message="Retry aborted: previous attempt may have succeeded (found existing trade)",
                                )
                        except Exception as check_error:
                            # If we can't verify, abort retry to be safe
                            logger.error(f"Retry {attempt + 1}: Could not verify previous attempt status: {check_error}")
                            order.status = OrderStatus.FAILED.value
                            order.error_message = f"Could not verify previous attempt: {check_error}"
                            db.commit()
                            return OrderResult(
                                success=False,
                                message=f"Retry aborted: could not verify previous attempt status",
                            )

                        # Re-fetch orderbook to get fresh prices (prevents slippage)
                        fresh_orderbook = self.get_orderbook_state(signal.token_id)
                        if fresh_orderbook is None:
                            logger.warning(f"Retry {attempt + 1}: Could not fetch fresh orderbook")
                            continue

                        # Check if price moved too much since initial signal
                        if fresh_orderbook.mid_price and initial_mid_price:
                            price_move = abs(fresh_orderbook.mid_price - initial_mid_price) / initial_mid_price
                            if price_move > MAX_PRICE_MOVE_BETWEEN_RETRIES:
                                order.status = OrderStatus.FAILED.value
                                order.error_message = f"Price moved {price_move:.1%} during retry (max {MAX_PRICE_MOVE_BETWEEN_RETRIES:.0%})"
                                db.commit()
                                logger.warning(f"Aborting retry: price moved {price_move:.1%} > {MAX_PRICE_MOVE_BETWEEN_RETRIES:.0%}")
                                return OrderResult(
                                    success=False,
                                    message=f"Retry aborted: price moved {price_move:.1%} (slippage protection)",
                                )

                        # Check spread on fresh orderbook
                        if fresh_orderbook.spread is not None and fresh_orderbook.spread > MAX_SPREAD_FOR_LIVE:
                            logger.warning(f"Retry {attempt + 1}: Spread too high {fresh_orderbook.spread:.1%}")
                            continue

                        # Recalculate limit price from fresh orderbook
                        order_request = OrderRequest(
                            token_id=signal.token_id,
                            side=signal.side,
                            size_usd=size_usd,
                            order_type=order_type,
                            limit_offset_bps=limit_offset_bps,
                        )
                        order_obj = create_order(order_request)
                        current_limit_price = order_obj.calculate_price(
                            fresh_orderbook.best_bid,
                            fresh_orderbook.best_ask,
                            fresh_orderbook.mid_price,
                        )

                        if current_limit_price is None:
                            logger.warning(f"Retry {attempt + 1}: Could not determine fresh limit price")
                            continue

                        # Update order with fresh price
                        order.limit_price = current_limit_price
                        logger.info(f"Retry {attempt + 1}: Fresh price ${current_limit_price:.4f} (was ${limit_price:.4f})")

                    # Attempt to place the order
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
                            price=current_limit_price,
                            size_usd=size_usd,
                        )

                    # Order submitted successfully - process result
                    polymarket_order_id = result.get("orderID") or result.get("id")
                    if not polymarket_order_id:
                        logger.warning(f"No order ID in response: {result}")

                    order.polymarket_order_id = polymarket_order_id

                    # Check if order was immediately matched (common on Polymarket)
                    order_status_str = result.get("status", "").lower()
                    is_matched = order_status_str == "matched" or result.get("success") == True

                    if is_matched and result.get("transactionsHashes"):
                        # Order immediately filled - extract fill info from response
                        logger.info(f"Order immediately matched: {result}")

                        # Extract fill details from response
                        # takingAmount = shares received, makingAmount = USD paid
                        filled_shares = float(result.get("takingAmount", 0))
                        filled_usd = float(result.get("makingAmount", 0))
                        fill_price = filled_usd / filled_shares if filled_shares > 0 else current_limit_price

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

                        retry_msg = f" (after {attempt} retries)" if attempt > 0 else ""
                        logger.info(
                            f"Live order filled immediately{retry_msg}: {filled_shares:.2f} shares @ ${fill_price:.4f}, "
                            f"Position ID: {position.id}"
                        )

                        return OrderResult(
                            success=True,
                            order_id=polymarket_order_id,
                            executed_price=fill_price,
                            executed_shares=filled_shares,
                            executed_usd=filled_usd,
                            position_id=position.id,
                            message=f"Order filled immediately{retry_msg}",
                        )

                    # Order submitted but not immediately matched - poll for fill
                    order.status = OrderStatus.SUBMITTED.value
                    db.commit()

                    retry_msg = f" (after {attempt} retries)" if attempt > 0 else ""
                    logger.info(f"Live order submitted{retry_msg}, polling: {polymarket_order_id}")

                    # Poll for fill status
                    fill_result = self._wait_for_fill(
                        polymarket_order_id,
                        order,
                        signal,
                        db,
                    )

                    return fill_result

                except Exception as e:
                    last_error = e
                    # Check if this error is retryable (network issue)
                    if self._is_retryable_error(e) and attempt < ORDER_PLACEMENT_MAX_RETRIES - 1:
                        logger.warning(
                            f"Order placement failed (attempt {attempt + 1}/{ORDER_PLACEMENT_MAX_RETRIES}), "
                            f"will retry: {e}"
                        )
                        continue
                    else:
                        # Non-retryable error or final attempt - fail permanently
                        order.status = OrderStatus.FAILED.value
                        order.error_message = str(e)
                        db.commit()
                        retry_info = f" after {attempt + 1} attempts" if attempt > 0 else ""
                        logger.error(f"Failed to place live order{retry_info}: {e}")
                        return OrderResult(
                            success=False,
                            message=f"Order placement failed{retry_info}: {e}",
                        )

            # Should not reach here, but handle just in case
            order.status = OrderStatus.FAILED.value
            order.error_message = f"Max retries exceeded: {last_error}"
            db.commit()
            return OrderResult(
                success=False,
                message=f"Order placement failed after {ORDER_PLACEMENT_MAX_RETRIES} retries: {last_error}",
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
        none_count = 0
        WALLET_CHECK_AFTER_NONE = 5  # Check wallet after this many None responses

        while time.time() - start_time < ORDER_STATUS_TIMEOUT:
            try:
                order_status = self.order_client.get_order(order_id)

                # Handle None response from API
                if order_status is None:
                    none_count += 1
                    logger.warning(f"Got None response for order {order_id} ({none_count}x)")

                    # After several None responses, check wallet as fallback
                    if none_count >= WALLET_CHECK_AFTER_NONE:
                        logger.info(f"Checking wallet for fill after {none_count} None responses")
                        wallet_data = self._check_wallet_for_fill(signal.token_id)
                        if wallet_data:
                            logger.info(f"Found fill in wallet! Creating position from wallet data")
                            return self._create_position_from_wallet(db, signal, order, wallet_data)
                        none_count = 0  # Reset counter, keep polling

                    time.sleep(ORDER_STATUS_POLL_INTERVAL)
                    continue

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

        # Timeout - CHECK WALLET FIRST (source of truth), then try to cancel
        logger.warning(f"Order {order_id} timed out after {ORDER_STATUS_TIMEOUT}s - checking wallet for fill")

        # CRITICAL: Check wallet FIRST - this is the source of truth
        wallet_data = self._check_wallet_for_fill(signal.token_id)
        if wallet_data:
            logger.info(f"Order {order_id} confirmed FILLED via wallet check!")
            return self._create_position_from_wallet(db, signal, order, wallet_data)

        # Wallet doesn't show fill - try to cancel the order
        try:
            cancel_result = self.order_client.cancel_order(order_id)
            logger.info(f"Cancel result for {order_id}: {cancel_result}")

            # Check if cancel failed because order was already matched
            not_canceled = cancel_result.get("not_canceled", {})
            if order_id in not_canceled and "matched" in str(not_canceled.get(order_id, "")).lower():
                # Order was filled! Check wallet again for accurate fill data
                logger.warning(f"Order {order_id} was matched (cancel rejected) - checking wallet")
                wallet_data = self._check_wallet_for_fill(signal.token_id)
                if wallet_data:
                    return self._create_position_from_wallet(db, signal, order, wallet_data)

                # Wallet still doesn't show it - use order data as last resort
                logger.warning(f"Wallet doesn't show fill, using order data as fallback")
                fill_price = float(order.limit_price)
                filled_shares = float(order.size_usd / fill_price) if order.size_usd else 0
                filled_usd = float(order.size_usd) if order.size_usd else 0

                now = datetime.now(timezone.utc)
                order.status = OrderStatus.FILLED.value
                order.executed_price = fill_price
                order.filled_shares = filled_shares
                order.filled_at = now

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

                logger.info(f"Position created from order data: {filled_shares:.2f} @ ${fill_price:.4f}")
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    executed_price=fill_price,
                    executed_shares=filled_shares,
                    executed_usd=filled_usd,
                    message="Order filled (detected from cancel response)",
                )

            # Cancel succeeded - order didn't fill
            order.status = OrderStatus.CANCELLED.value
            order.status_message = f"Cancelled after {ORDER_STATUS_TIMEOUT}s timeout"
        except Exception as cancel_error:
            # If cancel fails, log ERROR and mark as failed (not open)
            # This is a critical safety issue - we may have an untracked order!
            logger.error(f"CRITICAL: Failed to cancel orphan order {order_id}: {cancel_error}")
            order.status = OrderStatus.FAILED.value
            order.status_message = f"Timeout + cancel failed: {cancel_error} - MANUAL CHECK REQUIRED"

        db.commit()

        return OrderResult(
            success=False,
            order_id=order_id,
            message=f"Order cancelled after {ORDER_STATUS_TIMEOUT}s timeout",
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

    def _check_wallet_for_fill(self, token_id: str) -> Optional[dict]:
        """
        Check wallet positions to detect if an order filled.

        This is the ultimate source of truth - if we hold the token, the order filled.

        Args:
            token_id: Token ID to check

        Returns:
            Dict with fill info if found, None otherwise
        """
        try:
            wallet_positions = self.order_client.get_positions()
            if not wallet_positions:
                return None

            for wp in wallet_positions:
                if wp.get('asset_id') == token_id:
                    size = float(wp.get('size', 0))
                    if size > 0:
                        return {
                            'filled': True,
                            'size': size,
                            'avg_price': float(wp.get('avg_price', 0)),
                            'cost_basis': float(wp.get('cost_basis', 0)),
                        }
            return None
        except Exception as e:
            logger.warning(f"Error checking wallet for fill: {e}")
            return None

    def _create_position_from_wallet(
        self,
        db: Session,
        signal: Signal,
        order: ExecutorOrder,
        wallet_data: dict,
    ) -> OrderResult:
        """
        Create position from wallet data when normal tracking failed.

        Args:
            db: Database session
            signal: Original signal
            order: ExecutorOrder record
            wallet_data: Dict from _check_wallet_for_fill

        Returns:
            OrderResult with fill details
        """
        fill_price = wallet_data['avg_price']
        filled_shares = wallet_data['size']
        filled_usd = wallet_data['cost_basis']

        now = datetime.now(timezone.utc)
        order.status = OrderStatus.FILLED.value
        order.executed_price = fill_price
        order.filled_shares = filled_shares
        order.size_shares = filled_shares
        order.filled_at = now

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

        logger.info(f"Position created from wallet data: {filled_shares:.2f} @ ${fill_price:.4f}")
        return OrderResult(
            success=True,
            order_id=order.polymarket_order_id,
            executed_price=fill_price,
            executed_shares=filled_shares,
            executed_usd=filled_usd,
            position_id=position.id,
            message="Order filled (detected from wallet)",
        )

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

    def reconcile_wallet_positions(self) -> dict[str, Any]:
        """
        Reconcile wallet positions with database positions.

        This should be called on startup to detect any untracked fills.
        Fetches positions from Polymarket wallet and compares to DB.

        Returns:
            Dict with reconciliation results
        """
        from src.db.models import Market

        results = {
            "wallet_positions": 0,
            "db_positions": 0,
            "untracked": [],
            "synced": 0,
            "errors": [],
        }

        try:
            # Get wallet positions from Polymarket
            wallet_positions = self.order_client.get_positions() or []
            results["wallet_positions"] = len([wp for wp in wallet_positions if float(wp.get('size', 0)) > 0])

            with get_session() as db:
                # Get open positions from DB
                db_positions = db.query(Position).filter(
                    Position.is_paper == False,
                    Position.status == PositionStatus.OPEN.value,
                ).all()
                results["db_positions"] = len(db_positions)

                # Build set of tracked token IDs
                tracked_tokens = {p.token_id for p in db_positions}

                # Find untracked wallet positions
                for wp in wallet_positions:
                    token_id = wp.get('asset_id')
                    size = float(wp.get('size', 0))

                    if size > 0 and token_id and token_id not in tracked_tokens:
                        # Untracked position found!
                        avg_price = float(wp.get('avg_price', 0))
                        cost_basis = float(wp.get('cost_basis', 0))

                        # Find market info
                        market = db.query(Market).filter(
                            (Market.yes_token_id == token_id) | (Market.no_token_id == token_id)
                        ).first()

                        # Skip resolved markets
                        if market and market.resolved:
                            continue

                        results["untracked"].append({
                            "token_id": token_id,
                            "market_id": market.id if market else None,
                            "market_title": market.question if market else "Unknown",
                            "size": size,
                            "avg_price": avg_price,
                            "cost_basis": cost_basis,
                        })

                        # Create position for untracked fill
                        try:
                            position = Position(
                                is_paper=False,
                                strategy_name="wallet_reconcile",
                                market_id=market.id if market else None,
                                token_id=token_id,
                                side="BUY",
                                entry_price=avg_price,
                                entry_time=datetime.now(timezone.utc),
                                size_shares=size,
                                cost_basis=cost_basis,
                                current_price=avg_price,
                                current_value=cost_basis,
                                status=PositionStatus.OPEN.value,
                            )
                            db.add(position)
                            results["synced"] += 1
                            logger.warning(
                                f"Created position for untracked wallet fill: "
                                f"{market.question if market else token_id[:20]} - "
                                f"{size:.2f} shares @ ${avg_price:.4f}"
                            )
                        except Exception as e:
                            results["errors"].append(f"Failed to create position: {e}")

                db.commit()

            if results["untracked"]:
                logger.warning(
                    f"RECONCILIATION: Found {len(results['untracked'])} untracked positions, "
                    f"synced {results['synced']}"
                )
            else:
                logger.info(
                    f"Reconciliation complete: {results['db_positions']} DB positions, "
                    f"{results['wallet_positions']} wallet positions, all tracked"
                )

            return results

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            results["errors"].append(str(e))
            return results
