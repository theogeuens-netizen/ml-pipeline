"""
Gamma API client for market discovery and metadata.

The Gamma API provides market information including:
- Market metadata (question, description, dates)
- Outcome prices
- Volume and liquidity
- Price change percentages (momentum)

Production notes:
- Pagination protected with MAX_PAGES limit to prevent infinite loops
- 404/422 errors handled gracefully for delisted markets
- Price parsing falls back safely on malformed data
"""
import json
from datetime import datetime
from typing import Any, Optional

import httpx
import structlog

from src.config.settings import settings
from src.fetchers.base import BaseClient, SyncBaseClient

logger = structlog.get_logger()

# Safety limit to prevent infinite pagination loops
MAX_PAGES = 100  # 100 pages * 100 items = 10,000 markets max


class GammaClient(BaseClient):
    """Client for Polymarket's Gamma API."""

    def __init__(self):
        """Initialize Gamma client with configured rate limits."""
        super().__init__(
            base_url=settings.gamma_api_base,
            rate_limit=settings.gamma_rate_limit,
        )

    async def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Fetch markets with pagination.

        Args:
            active: Filter for active markets
            closed: Filter for closed markets
            limit: Maximum results per page (max 100)
            offset: Pagination offset

        Returns:
            List of market dictionaries
        """
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }
        return await self.get("/markets", params)

    async def get_all_active_markets(self) -> list[dict[str, Any]]:
        """
        Fetch all active markets, handling pagination automatically.

        Safety features:
        - MAX_PAGES limit prevents infinite loops
        - Empty response breaks pagination
        - Logs progress for monitoring

        Returns:
            List of all active market dictionaries
        """
        all_markets = []
        offset = 0
        limit = 100
        page = 0

        while page < MAX_PAGES:
            markets = await self.get_markets(
                active=True,
                closed=False,
                limit=limit,
                offset=offset,
            )

            if not markets:
                break

            all_markets.extend(markets)
            offset += limit
            page += 1

            # Check if we've reached the end
            if len(markets) < limit:
                break

            logger.debug("Fetched markets page", page=page, offset=offset, count=len(markets))

        if page >= MAX_PAGES:
            logger.warning(
                "Pagination limit reached",
                max_pages=MAX_PAGES,
                total_fetched=len(all_markets),
            )

        logger.info("Fetched all active markets", total=len(all_markets), pages=page)
        return all_markets

    async def get_market(self, condition_id: str) -> Optional[dict[str, Any]]:
        """
        Fetch a single market by condition ID.

        Handles graceful degradation:
        - 404: Market doesn't exist (delisted)
        - 422: Market unprocessable (resolved/invalid state)

        Args:
            condition_id: The market's condition ID

        Returns:
            Market dictionary or None if not found/unavailable
        """
        try:
            return await self.get(f"/markets/{condition_id}")
        except httpx.HTTPStatusError as e:
            # 404/422 are expected for delisted/resolved markets - not a failure
            if e.response.status_code in (404, 422):
                logger.debug(
                    "Market not available",
                    condition_id=condition_id[:16],
                    status=e.response.status_code,
                )
                return None
            logger.warning(
                "Failed to fetch market",
                condition_id=condition_id[:16],
                status=e.response.status_code,
                error=str(e),
            )
            return None
        except Exception as e:
            logger.warning(
                "Failed to fetch market",
                condition_id=condition_id[:16],
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

    async def get_events(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Fetch events (groups of related markets).

        Args:
            active: Filter for active events
            closed: Filter for closed events
            limit: Maximum results per page
            offset: Pagination offset

        Returns:
            List of event dictionaries
        """
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }
        return await self.get("/events", params)

    @staticmethod
    def parse_outcome_prices(market: dict[str, Any]) -> tuple[float, float]:
        """
        Extract YES and NO prices from market response.

        Args:
            market: Market dictionary from API

        Returns:
            Tuple of (yes_price, no_price)
        """
        prices_str = market.get("outcomePrices", '["0.5", "0.5"]')
        try:
            prices = json.loads(prices_str)
            yes_price = float(prices[0])
            no_price = float(prices[1]) if len(prices) > 1 else 1 - yes_price
            return yes_price, no_price
        except (json.JSONDecodeError, IndexError, TypeError, ValueError) as e:
            logger.warning("Failed to parse outcome prices", error=str(e), raw=prices_str)
            return 0.5, 0.5

    @staticmethod
    def parse_token_ids(market: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """
        Extract YES and NO token IDs from market response.

        These token IDs are needed for CLOB API calls.

        Args:
            market: Market dictionary from API

        Returns:
            Tuple of (yes_token_id, no_token_id), either may be None
        """
        tokens_str = market.get("clobTokenIds", "[]")
        try:
            tokens = json.loads(tokens_str)
            yes_token = tokens[0] if tokens else None
            no_token = tokens[1] if len(tokens) > 1 else None
            return yes_token, no_token
        except (json.JSONDecodeError, IndexError, TypeError) as e:
            logger.warning("Failed to parse token IDs", error=str(e), raw=tokens_str)
            return None, None

    @staticmethod
    def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """
        Parse ISO datetime string from API response.

        Args:
            dt_str: ISO datetime string (may have Z suffix)

        Returns:
            Datetime object or None if parsing fails
        """
        if not dt_str:
            return None
        try:
            # Handle Z suffix for UTC
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def extract_market_data(market: dict[str, Any]) -> dict[str, Any]:
        """
        Extract and normalize all useful fields from a market response.

        Args:
            market: Raw market dictionary from API

        Returns:
            Normalized dictionary with typed values
        """
        yes_price, no_price = GammaClient.parse_outcome_prices(market)
        yes_token, no_token = GammaClient.parse_token_ids(market)

        # Extract event info from nested events array
        events = market.get("events", [])
        event_data = events[0] if events else {}

        return {
            # Identifiers
            "condition_id": market.get("conditionId"),
            "slug": market.get("slug", ""),
            "question": market.get("question", ""),
            "description": market.get("description"),
            # Event grouping (from nested events array)
            "event_id": event_data.get("id"),
            "event_slug": event_data.get("slug"),
            "event_title": event_data.get("title"),
            # Token IDs
            "yes_token_id": yes_token,
            "no_token_id": no_token,
            # Prices
            "yes_price": yes_price,
            "no_price": no_price,
            "best_bid": market.get("bestBid"),
            "best_ask": market.get("bestAsk"),
            "spread": market.get("spread"),
            "last_trade_price": market.get("lastTradePrice"),
            # Momentum (free from Gamma!)
            "price_change_1d": market.get("oneDayPriceChange"),
            "price_change_1w": market.get("oneWeekPriceChange"),
            "price_change_1m": market.get("oneMonthPriceChange"),
            # Volume
            "volume_total": market.get("volumeNum"),
            "volume_24h": market.get("volume24hr"),
            "volume_1w": market.get("volume1wk"),
            "liquidity": market.get("liquidityNum"),
            # Dates
            "start_date": GammaClient.parse_datetime(market.get("startDate")),
            "end_date": GammaClient.parse_datetime(market.get("endDate")),
            "created_at": GammaClient.parse_datetime(market.get("createdAt")),
            # Status
            "active": market.get("active", True),
            "closed": market.get("closed", False),
            "resolved": market.get("resolved", False),
            # Metadata
            "category": market.get("category"),
            "neg_risk": market.get("negRisk", False),
            "competitive": market.get("competitive"),
            "enable_order_book": market.get("enableOrderBook", True),
        }


class SyncGammaClient(SyncBaseClient):
    """Synchronous client for Polymarket's Gamma API.

    Use this in Celery tasks to avoid asyncio event loop issues.
    """

    def __init__(self):
        """Initialize Gamma client with configured rate limits."""
        super().__init__(
            base_url=settings.gamma_api_base,
            rate_limit=settings.gamma_rate_limit,
        )

    def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Fetch markets with pagination.

        Args:
            active: Filter for active markets
            closed: Filter for closed markets
            limit: Maximum results per page (max 100)
            offset: Pagination offset

        Returns:
            List of market dictionaries
        """
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }
        return self.get("/markets", params)

    def get_all_active_markets(self) -> list[dict[str, Any]]:
        """
        Fetch all active markets, handling pagination automatically.

        Safety features:
        - MAX_PAGES limit prevents infinite loops
        - Empty response breaks pagination

        Returns:
            List of all active market dictionaries
        """
        all_markets = []
        offset = 0
        limit = 100
        page = 0

        while page < MAX_PAGES:
            markets = self.get_markets(
                active=True,
                closed=False,
                limit=limit,
                offset=offset,
            )

            if not markets:
                break

            all_markets.extend(markets)
            offset += limit
            page += 1

            # Check if we've reached the end
            if len(markets) < limit:
                break

            logger.debug("Fetched markets page", page=page, offset=offset, count=len(markets))

        if page >= MAX_PAGES:
            logger.warning(
                "Pagination limit reached",
                max_pages=MAX_PAGES,
                total_fetched=len(all_markets),
            )

        logger.info("Fetched all active markets", total=len(all_markets), pages=page)
        return all_markets

    def get_market(self, condition_id: str) -> Optional[dict[str, Any]]:
        """
        Fetch a single market by condition ID.

        Handles 404/422 gracefully for delisted/resolved markets.

        Args:
            condition_id: The market's condition ID

        Returns:
            Market dictionary or None if not found/unavailable
        """
        try:
            return self.get(f"/markets/{condition_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 422):
                logger.debug(
                    "Market not available",
                    condition_id=condition_id[:16],
                    status=e.response.status_code,
                )
                return None
            logger.warning(
                "Failed to fetch market",
                condition_id=condition_id[:16],
                status=e.response.status_code,
            )
            return None
        except Exception as e:
            logger.warning(
                "Failed to fetch market",
                condition_id=condition_id[:16],
                error=str(e),
            )
            return None
