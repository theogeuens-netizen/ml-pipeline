"""
CLOB API client for orderbook data.

The CLOB (Central Limit Order Book) API provides:
- Full orderbook depth (bids and asks)
- Midpoint prices
- Spreads
- Price history
"""
from typing import Any, Optional

import structlog

from src.config.settings import settings
from src.fetchers.base import BaseClient, SyncBaseClient

logger = structlog.get_logger()


class CLOBClient(BaseClient):
    """Client for Polymarket's CLOB API."""

    def __init__(self):
        """Initialize CLOB client with configured rate limits."""
        super().__init__(
            base_url=settings.clob_api_base,
            rate_limit=settings.clob_rate_limit,
        )

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """
        Fetch full orderbook for a token.

        Args:
            token_id: The token ID (from market's clobTokenIds)

        Returns:
            Orderbook dictionary with 'bids' and 'asks' arrays
        """
        return await self.get("/book", params={"token_id": token_id})

    async def get_midpoint(self, token_id: str) -> float:
        """
        Get current midpoint price.

        Args:
            token_id: The token ID

        Returns:
            Midpoint price as float
        """
        result = await self.get("/midpoint", params={"token_id": token_id})
        return float(result.get("mid", 0))

    async def get_spread(self, token_id: str) -> float:
        """
        Get current bid-ask spread.

        Args:
            token_id: The token ID

        Returns:
            Spread as float
        """
        result = await self.get("/spread", params={"token_id": token_id})
        return float(result.get("spread", 0))

    async def get_price(self, token_id: str) -> dict[str, float]:
        """
        Get current price info (bid, ask, mid).

        Args:
            token_id: The token ID

        Returns:
            Dictionary with bid, ask, and mid prices
        """
        result = await self.get("/price", params={"token_id": token_id})
        return {
            "bid": float(result.get("bid", 0)),
            "ask": float(result.get("ask", 0)),
            "mid": float(result.get("mid", 0)),
        }

    @staticmethod
    def calculate_depth(
        orderbook: dict[str, Any],
        side: str,
        levels: int,
    ) -> float:
        """
        Calculate total depth (size) up to N price levels.

        Args:
            orderbook: Orderbook dictionary from API
            side: 'bid' or 'ask'
            levels: Number of price levels to include

        Returns:
            Total size across specified levels
        """
        orders = orderbook.get("bids" if side == "bid" else "asks", [])
        total = 0.0
        for order in orders[:levels]:
            size = float(order.get("size", 0))
            total += size
        return total

    @staticmethod
    def calculate_depth_at_price(
        orderbook: dict[str, Any],
        side: str,
        price_distance: float,
    ) -> float:
        """
        Calculate total depth within a price distance from best price.

        Args:
            orderbook: Orderbook dictionary from API
            side: 'bid' or 'ask'
            price_distance: Maximum distance from best price (e.g., 0.05 for 5%)

        Returns:
            Total size within price distance
        """
        orders = orderbook.get("bids" if side == "bid" else "asks", [])
        if not orders:
            return 0.0

        best_price = float(orders[0].get("price", 0))
        total = 0.0

        for order in orders:
            price = float(order.get("price", 0))
            size = float(order.get("size", 0))

            if side == "bid":
                if best_price - price <= price_distance:
                    total += size
                else:
                    break
            else:  # ask
                if price - best_price <= price_distance:
                    total += size
                else:
                    break

        return total

    @staticmethod
    def find_wall(
        orderbook: dict[str, Any],
        side: str,
    ) -> tuple[float, float]:
        """
        Find the largest order (wall) on a side.

        Args:
            orderbook: Orderbook dictionary from API
            side: 'bid' or 'ask'

        Returns:
            Tuple of (price, size) for the largest order
        """
        orders = orderbook.get("bids" if side == "bid" else "asks", [])
        if not orders:
            return 0.0, 0.0

        max_order = max(orders, key=lambda x: float(x.get("size", 0)))
        return float(max_order.get("price", 0)), float(max_order.get("size", 0))

    @staticmethod
    def calculate_imbalance(orderbook: dict[str, Any]) -> float:
        """
        Calculate orderbook imbalance.

        Formula: (bid_depth - ask_depth) / (bid_depth + ask_depth)

        Positive values indicate buy pressure (more bids).
        Negative values indicate sell pressure (more asks).

        Args:
            orderbook: Orderbook dictionary from API

        Returns:
            Imbalance value between -1 and 1
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        bid_depth = sum(float(o.get("size", 0)) for o in bids)
        ask_depth = sum(float(o.get("size", 0)) for o in asks)

        total = bid_depth + ask_depth
        if total == 0:
            return 0.0

        return (bid_depth - ask_depth) / total

    @staticmethod
    def calculate_weighted_midpoint(orderbook: dict[str, Any]) -> Optional[float]:
        """
        Calculate volume-weighted midpoint price.

        Weights the midpoint by the sizes at best bid/ask.

        Args:
            orderbook: Orderbook dictionary from API

        Returns:
            Weighted midpoint or None if orderbook is empty
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            return None

        best_bid_price = float(bids[0].get("price", 0))
        best_bid_size = float(bids[0].get("size", 0))
        best_ask_price = float(asks[0].get("price", 0))
        best_ask_size = float(asks[0].get("size", 0))

        total_size = best_bid_size + best_ask_size
        if total_size == 0:
            return (best_bid_price + best_ask_price) / 2

        return (
            best_bid_price * best_ask_size + best_ask_price * best_bid_size
        ) / total_size

    @staticmethod
    def extract_orderbook_features(orderbook: dict[str, Any]) -> dict[str, Any]:
        """
        Extract all orderbook-derived features for a snapshot.

        Args:
            orderbook: Raw orderbook dictionary from API

        Returns:
            Dictionary with all computed features
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        # Depth at various levels
        bid_depth_5 = CLOBClient.calculate_depth(orderbook, "bid", 5)
        bid_depth_10 = CLOBClient.calculate_depth(orderbook, "bid", 10)
        bid_depth_20 = CLOBClient.calculate_depth(orderbook, "bid", 20)
        bid_depth_50 = CLOBClient.calculate_depth(orderbook, "bid", 50)

        ask_depth_5 = CLOBClient.calculate_depth(orderbook, "ask", 5)
        ask_depth_10 = CLOBClient.calculate_depth(orderbook, "ask", 10)
        ask_depth_20 = CLOBClient.calculate_depth(orderbook, "ask", 20)
        ask_depth_50 = CLOBClient.calculate_depth(orderbook, "ask", 50)

        # Level counts
        bid_levels = len(bids)
        ask_levels = len(asks)

        # Imbalance
        book_imbalance = CLOBClient.calculate_imbalance(orderbook)

        # Walls
        bid_wall_price, bid_wall_size = CLOBClient.find_wall(orderbook, "bid")
        ask_wall_price, ask_wall_size = CLOBClient.find_wall(orderbook, "ask")

        # Best prices
        best_bid = float(bids[0].get("price", 0)) if bids else None
        best_ask = float(asks[0].get("price", 0)) if asks else None
        spread = (best_ask - best_bid) if (best_bid and best_ask) else None

        return {
            # Depth features
            "bid_depth_5": bid_depth_5,
            "bid_depth_10": bid_depth_10,
            "bid_depth_20": bid_depth_20,
            "bid_depth_50": bid_depth_50,
            "ask_depth_5": ask_depth_5,
            "ask_depth_10": ask_depth_10,
            "ask_depth_20": ask_depth_20,
            "ask_depth_50": ask_depth_50,
            # Level counts
            "bid_levels": bid_levels,
            "ask_levels": ask_levels,
            # Derived
            "book_imbalance": book_imbalance,
            "bid_wall_price": bid_wall_price,
            "bid_wall_size": bid_wall_size,
            "ask_wall_price": ask_wall_price,
            "ask_wall_size": ask_wall_size,
            # Prices
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
        }


class SyncCLOBClient(SyncBaseClient):
    """Synchronous client for Polymarket's CLOB API.

    Use this in Celery tasks to avoid asyncio event loop issues.
    """

    def __init__(self):
        """Initialize CLOB client with configured rate limits."""
        super().__init__(
            base_url=settings.clob_api_base,
            rate_limit=settings.clob_rate_limit,
        )

    def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """
        Fetch full orderbook for a token.

        Args:
            token_id: The token ID (from market's clobTokenIds)

        Returns:
            Orderbook dictionary with 'bids' and 'asks' arrays
        """
        return self.get("/book", params={"token_id": token_id})

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """
        Get current midpoint price.

        Args:
            token_id: The token ID

        Returns:
            Midpoint price as float, or None if no liquidity (empty order book)
        """
        result = self.get("/midpoint", params={"token_id": token_id})
        mid = result.get("mid")
        if mid is None or mid == "":
            return None  # No liquidity on this token
        return float(mid)

    def get_spread(self, token_id: str) -> float:
        """
        Get current bid-ask spread.

        Args:
            token_id: The token ID

        Returns:
            Spread as float
        """
        result = self.get("/spread", params={"token_id": token_id})
        return float(result.get("spread", 0))

    def get_price(self, token_id: str) -> dict[str, float]:
        """
        Get current price info (bid, ask, mid).

        Args:
            token_id: The token ID

        Returns:
            Dictionary with bid, ask, and mid prices
        """
        result = self.get("/price", params={"token_id": token_id})
        return {
            "bid": float(result.get("bid", 0)),
            "ask": float(result.get("ask", 0)),
            "mid": float(result.get("mid", 0)),
        }
