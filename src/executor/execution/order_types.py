"""
Order type implementations.

Supports:
- Market orders: Cross spread immediately
- Limit orders: Post at offset from mid
- Spread orders: Post to capture spread, fall back to market after timeout
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


class OrderType(str, Enum):
    """Order execution type."""
    MARKET = "market"
    LIMIT = "limit"
    SPREAD = "spread"


@dataclass
class OrderResult:
    """Result of order execution attempt."""
    success: bool
    order_id: Optional[str] = None
    executed_price: Optional[float] = None
    executed_shares: Optional[float] = None
    executed_usd: Optional[float] = None
    message: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass
class OrderRequest:
    """Request to execute an order."""
    token_id: str
    side: str  # BUY or SELL
    size_usd: float
    order_type: OrderType = OrderType.LIMIT
    limit_price: Optional[float] = None  # For limit orders
    limit_offset_bps: int = 50  # Basis points from mid for limit orders
    spread_timeout_seconds: int = 30  # For spread orders
    created_at: datetime = field(default_factory=datetime.utcnow)


class BaseOrder(ABC):
    """Base class for order types."""

    def __init__(self, request: OrderRequest):
        self.request = request

    @abstractmethod
    def calculate_price(
        self,
        best_bid: Optional[float],
        best_ask: Optional[float],
        mid_price: Optional[float],
    ) -> Optional[float]:
        """Calculate the order price based on current market data."""
        pass

    @abstractmethod
    def should_cross_spread(self, elapsed_seconds: float) -> bool:
        """Determine if the order should cross the spread (become market order)."""
        pass


class MarketOrder(BaseOrder):
    """
    Market order - crosses the spread immediately.

    Pays taker fee but guarantees execution.
    """

    def calculate_price(
        self,
        best_bid: Optional[float],
        best_ask: Optional[float],
        mid_price: Optional[float],
    ) -> Optional[float]:
        """For market orders, use the best available price on the other side."""
        if self.request.side.upper() == "BUY":
            return best_ask
        else:
            return best_bid

    def should_cross_spread(self, elapsed_seconds: float) -> bool:
        """Market orders always cross."""
        return True


class LimitOrder(BaseOrder):
    """
    Limit order - posts at offset from mid.

    May earn maker rebate, but may not fill.
    """

    def calculate_price(
        self,
        best_bid: Optional[float],
        best_ask: Optional[float],
        mid_price: Optional[float],
    ) -> Optional[float]:
        """
        Calculate limit price at offset from mid.

        For BUY: mid - offset (try to buy cheaper)
        For SELL: mid + offset (try to sell higher)
        """
        if mid_price is None:
            return None

        offset = self.request.limit_offset_bps / 10000  # Convert bps to decimal

        if self.request.side.upper() == "BUY":
            # Place below mid to try to get filled as maker
            price = mid_price - offset
            # But don't go above the best ask (would cross)
            if best_ask is not None:
                price = min(price, best_ask - 0.001)
            return max(0.001, price)
        else:
            # Place above mid to try to get filled as maker
            price = mid_price + offset
            # But don't go below the best bid (would cross)
            if best_bid is not None:
                price = max(price, best_bid + 0.001)
            return min(0.999, price)

    def should_cross_spread(self, elapsed_seconds: float) -> bool:
        """Limit orders don't automatically cross."""
        return False


class SpreadOrder(BaseOrder):
    """
    Spread capture order - posts to capture spread, falls back to market after timeout.

    Good for low-edge strategies where spread matters.
    """

    def calculate_price(
        self,
        best_bid: Optional[float],
        best_ask: Optional[float],
        mid_price: Optional[float],
    ) -> Optional[float]:
        """
        Calculate price to capture spread.

        Posts at or slightly better than the current best on our side.
        """
        if self.request.side.upper() == "BUY":
            if best_bid is not None:
                # Post at best bid or slightly better
                return min(best_bid + 0.001, (best_bid + best_ask) / 2 if best_ask else best_bid + 0.01)
            elif mid_price is not None:
                return mid_price - 0.01
            return None
        else:
            if best_ask is not None:
                # Post at best ask or slightly better
                return max(best_ask - 0.001, (best_bid + best_ask) / 2 if best_bid else best_ask - 0.01)
            elif mid_price is not None:
                return mid_price + 0.01
            return None

    def should_cross_spread(self, elapsed_seconds: float) -> bool:
        """Cross spread after timeout."""
        return elapsed_seconds >= self.request.spread_timeout_seconds


def create_order(request: OrderRequest) -> BaseOrder:
    """
    Factory function to create the appropriate order type.

    Args:
        request: OrderRequest with type and parameters

    Returns:
        Appropriate BaseOrder subclass instance
    """
    order_classes = {
        OrderType.MARKET: MarketOrder,
        OrderType.LIMIT: LimitOrder,
        OrderType.SPREAD: SpreadOrder,
    }

    order_class = order_classes.get(request.order_type)
    if order_class is None:
        raise ValueError(f"Unknown order type: {request.order_type}")

    return order_class(request)


def calculate_shares_from_usd(size_usd: float, price: float) -> float:
    """
    Calculate number of shares from USD amount and price.

    Args:
        size_usd: Amount in USD
        price: Price per share (0-1)

    Returns:
        Number of shares (rounded down to 2 decimals)
    """
    if price <= 0:
        raise ValueError(f"Invalid price: {price}")

    shares = size_usd / price
    # Round down to avoid rounding errors
    return round(shares, 2)


def calculate_usd_from_shares(shares: float, price: float) -> float:
    """
    Calculate USD value from shares and price.

    Args:
        shares: Number of shares
        price: Price per share (0-1)

    Returns:
        Value in USD
    """
    return round(shares * price, 2)
