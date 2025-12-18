"""
Redis client for trade buffers and metrics caching.

Redis is used for:
- Trade buffers: Rolling 1h window of trades per market
- Metrics cache: Pre-computed trade metrics
- Tier sets: Markets in each tier for quick lookup
- WebSocket health: Connection status tracking

Production features:
- Connection retry with exponential backoff
- Graceful degradation on JSON parse errors
- Health check methods for monitoring
"""
import json
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Optional, TypeVar, Callable

import redis.asyncio as redis_async
import redis as redis_sync
from redis.exceptions import ConnectionError, TimeoutError, RedisError
import structlog

from src.config.settings import settings

logger = structlog.get_logger()

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5  # seconds

T = TypeVar('T')


def redis_retry_sync(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator for synchronous Redis operations with retry."""
    @wraps(func)
    def wrapper(self, *args, **kwargs) -> T:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(self, *args, **kwargs)
            except (ConnectionError, TimeoutError) as e:
                last_error = e
                if attempt >= MAX_RETRIES - 1:
                    logger.error(
                        "Redis operation failed after retries",
                        operation=func.__name__,
                        error=str(e),
                    )
                    raise

                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    "Redis connection error, retrying",
                    operation=func.__name__,
                    attempt=attempt + 1,
                    delay=delay,
                )
                time.sleep(delay)

                # Reset connection on retry
                if hasattr(self, '_client') and self._client:
                    try:
                        self._client.close()
                    except Exception:
                        pass
                    self._client = None

        raise last_error or RedisError("Redis operation failed")
    return wrapper


class RedisClient:
    """Async Redis client for trade buffers and caching."""

    def __init__(self, url: Optional[str] = None):
        """
        Initialize Redis client.

        Args:
            url: Redis URL (defaults to settings.redis_url)
        """
        self.url = url or settings.redis_url
        self._client: Optional[redis_async.Redis] = None

    @property
    def client(self) -> redis_async.Redis:
        """Lazy-initialize Redis client."""
        if self._client is None:
            self._client = redis_async.from_url(self.url, decode_responses=True)
        return self._client

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # === Trade Buffer Operations ===

    async def push_trade(self, condition_id: str, trade_data: dict) -> None:
        """
        Push trade to buffer (FIFO, max size limited).

        Args:
            condition_id: Market condition ID
            trade_data: Trade dictionary with timestamp, price, size, side, whale_tier
        """
        key = f"trades:{condition_id}"
        await self.client.lpush(key, json.dumps(trade_data))
        await self.client.ltrim(key, 0, settings.redis_trade_buffer_max - 1)
        await self.client.expire(key, settings.redis_trade_buffer_ttl)

    async def get_trades_1h(self, condition_id: str) -> list[dict]:
        """
        Get trades from the last hour.

        Args:
            condition_id: Market condition ID

        Returns:
            List of trade dictionaries within the last hour
        """
        key = f"trades:{condition_id}"
        trades_raw = await self.client.lrange(key, 0, -1)

        one_hour_ago = datetime.now(timezone.utc).timestamp() - 3600
        trades = []

        for raw in trades_raw:
            trade = json.loads(raw)
            ts = datetime.fromisoformat(trade["timestamp"]).timestamp()
            if ts >= one_hour_ago:
                trades.append(trade)

        return trades

    async def get_trade_count(self, condition_id: str) -> int:
        """Get total trades in buffer."""
        key = f"trades:{condition_id}"
        return await self.client.llen(key)

    # === Metrics Cache Operations ===

    async def set_metrics(self, condition_id: str, metrics: dict) -> None:
        """
        Cache computed metrics for a market.

        Args:
            condition_id: Market condition ID
            metrics: Dictionary of computed metrics
        """
        key = f"metrics:{condition_id}"
        await self.client.hset(key, mapping={k: json.dumps(v) for k, v in metrics.items()})
        await self.client.expire(key, 300)  # 5 min TTL

    async def get_metrics(self, condition_id: str) -> Optional[dict]:
        """
        Get cached metrics for a market.

        Args:
            condition_id: Market condition ID

        Returns:
            Metrics dictionary or None if not cached
        """
        key = f"metrics:{condition_id}"
        raw = await self.client.hgetall(key)
        if not raw:
            return None
        return {k: json.loads(v) for k, v in raw.items()}

    # === Tier Management ===

    async def set_market_tier(self, condition_id: str, tier: int) -> None:
        """
        Track market tier membership.

        Args:
            condition_id: Market condition ID
            tier: Tier number (0-4)
        """
        # Remove from all tiers
        for t in range(5):
            await self.client.srem(f"tier:{t}", condition_id)
        # Add to current tier
        await self.client.sadd(f"tier:{tier}", condition_id)

    async def get_markets_in_tier(self, tier: int) -> set[str]:
        """
        Get all markets in a tier.

        Args:
            tier: Tier number (0-4)

        Returns:
            Set of condition IDs in tier
        """
        return await self.client.smembers(f"tier:{tier}")

    async def get_tier_counts(self) -> dict[int, int]:
        """Get market counts for each tier."""
        return {
            t: await self.client.scard(f"tier:{t}")
            for t in range(5)
        }

    # === WebSocket Health Tracking ===

    async def set_ws_connected(self, condition_id: str, connected: bool) -> None:
        """
        Track WebSocket connection status for a market.

        Args:
            condition_id: Market condition ID
            connected: Whether currently subscribed
        """
        if connected:
            await self.client.sadd("ws:connected", condition_id)
        else:
            await self.client.srem("ws:connected", condition_id)

    async def set_ws_last_event(self, condition_id: str) -> None:
        """Update last event timestamp for a market."""
        await self.client.hset(
            "ws:last_event",
            condition_id,
            datetime.now(timezone.utc).isoformat()
        )

    async def get_ws_status(self) -> dict:
        """
        Get WebSocket health status.

        Returns:
            Dictionary with connected_count, connected_markets, last_events
        """
        connected = await self.client.smembers("ws:connected")
        last_events = await self.client.hgetall("ws:last_event")
        return {
            "connected_count": len(connected),
            "connected_markets": list(connected),
            "last_events": last_events,
        }

    async def get_ws_connected_count(self) -> int:
        """Get count of connected markets."""
        return await self.client.scard("ws:connected")

    async def set_ws_last_activity(self) -> None:
        """Update global WebSocket activity timestamp (for health checks)."""
        await self.client.set(
            "ws:last_activity",
            datetime.now(timezone.utc).isoformat(),
            ex=300  # 5 min TTL
        )

    async def get_ws_last_activity(self) -> Optional[datetime]:
        """
        Get last global WebSocket activity timestamp.

        Returns:
            Datetime of last activity or None if no recent activity
        """
        raw = await self.client.get("ws:last_activity")
        return datetime.fromisoformat(raw) if raw else None

    # === Orderbook Cache ===

    async def set_orderbook(self, condition_id: str, orderbook: dict) -> None:
        """
        Cache latest orderbook.

        Args:
            condition_id: Market condition ID
            orderbook: Orderbook data from WebSocket
        """
        key = f"orderbook:{condition_id}"
        await self.client.set(key, json.dumps(orderbook), ex=60)

    async def get_orderbook(self, condition_id: str) -> Optional[dict]:
        """
        Get cached orderbook.

        Args:
            condition_id: Market condition ID

        Returns:
            Orderbook dictionary or None if not cached
        """
        key = f"orderbook:{condition_id}"
        raw = await self.client.get(key)
        return json.loads(raw) if raw else None

    # === Price Cache ===

    async def set_price(self, condition_id: str, price: float) -> None:
        """Cache latest price."""
        await self.client.hset("prices", condition_id, str(price))

    async def get_price(self, condition_id: str) -> Optional[float]:
        """Get cached price."""
        raw = await self.client.hget("prices", condition_id)
        return float(raw) if raw else None

    async def get_all_prices(self) -> dict[str, float]:
        """Get all cached prices."""
        raw = await self.client.hgetall("prices")
        return {k: float(v) for k, v in raw.items()}

    # === Gamma API Cache ===

    async def set_gamma_markets_cache(self, markets: list[dict], ttl: int = 10) -> None:
        """
        Cache Gamma API markets response.

        Args:
            markets: List of market dictionaries from Gamma API
            ttl: Time to live in seconds (default 10s)
        """
        await self.client.set("gamma:markets", json.dumps(markets), ex=ttl)

    async def get_gamma_markets_cache(self) -> Optional[list[dict]]:
        """
        Get cached Gamma API markets response.

        Returns:
            List of market dictionaries or None if not cached/expired
        """
        raw = await self.client.get("gamma:markets")
        return json.loads(raw) if raw else None

    # === Stats ===

    async def get_stats(self) -> dict:
        """Get overall Redis stats."""
        info = await self.client.info("memory")
        keys_count = await self.client.dbsize()

        return {
            "used_memory": info.get("used_memory_human", "unknown"),
            "peak_memory": info.get("used_memory_peak_human", "unknown"),
            "total_keys": keys_count,
        }


# Singleton instance for shared use
_redis_client: Optional[RedisClient] = None


def get_redis() -> RedisClient:
    """Get shared Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client


class SyncRedisClient:
    """
    Synchronous Redis client for use in Celery tasks.

    Features:
    - Connection retry with exponential backoff
    - Graceful JSON parse error handling
    - Use this instead of RedisClient in Celery tasks to avoid asyncio event loop issues.
    """

    def __init__(self, url: Optional[str] = None):
        """
        Initialize Redis client.

        Args:
            url: Redis URL (defaults to settings.redis_url)
        """
        self.url = url or settings.redis_url
        self._client: Optional[redis_sync.Redis] = None

    @property
    def client(self) -> redis_sync.Redis:
        """Lazy-initialize Redis client."""
        if self._client is None:
            self._client = redis_sync.from_url(
                self.url,
                decode_responses=True,
                socket_timeout=10.0,
                socket_connect_timeout=5.0,
                retry_on_timeout=True,
            )
        return self._client

    def close(self) -> None:
        """Close Redis connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def ping(self) -> bool:
        """Health check - verify Redis connection."""
        try:
            return self.client.ping()
        except Exception:
            return False

    # === Trade Buffer Operations ===

    @redis_retry_sync
    def get_trades_1h(self, condition_id: str) -> list[dict]:
        """
        Get trades from the last hour.

        Args:
            condition_id: Market condition ID

        Returns:
            List of trade dictionaries within the last hour.
            Returns empty list on parse errors (graceful degradation).
        """
        key = f"trades:{condition_id}"
        trades_raw = self.client.lrange(key, 0, -1)

        one_hour_ago = datetime.now(timezone.utc).timestamp() - 3600
        trades = []

        for raw in trades_raw:
            try:
                trade = json.loads(raw)
                ts = datetime.fromisoformat(trade["timestamp"]).timestamp()
                if ts >= one_hour_ago:
                    trades.append(trade)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                # Log but don't fail - skip corrupt entries
                logger.debug("Skipping corrupt trade entry", error=str(e))
                continue

        return trades

    # === Metrics Cache Operations ===

    @redis_retry_sync
    def get_metrics(self, condition_id: str) -> Optional[dict]:
        """
        Get cached metrics for a market.

        Args:
            condition_id: Market condition ID

        Returns:
            Metrics dictionary or None if not cached/corrupt
        """
        key = f"metrics:{condition_id}"
        raw = self.client.hgetall(key)
        if not raw:
            return None
        try:
            return {k: json.loads(v) for k, v in raw.items()}
        except json.JSONDecodeError as e:
            logger.warning("Corrupt metrics cache", condition_id=condition_id[:16], error=str(e))
            return None

    # === Orderbook Cache ===

    @redis_retry_sync
    def get_orderbook(self, condition_id: str) -> Optional[dict]:
        """
        Get cached orderbook.

        Args:
            condition_id: Market condition ID

        Returns:
            Orderbook dictionary or None if not cached/corrupt
        """
        key = f"orderbook:{condition_id}"
        raw = self.client.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Corrupt orderbook cache", condition_id=condition_id[:16], error=str(e))
            return None

    # === Gamma API Cache ===

    @redis_retry_sync
    def set_gamma_markets_cache(self, markets: list[dict], ttl: int = 10) -> None:
        """
        Cache Gamma API markets response.

        Args:
            markets: List of market dictionaries from Gamma API
            ttl: Time to live in seconds (default 10s)
        """
        self.client.set("gamma:markets", json.dumps(markets), ex=ttl)

    @redis_retry_sync
    def get_gamma_markets_cache(self) -> Optional[list[dict]]:
        """
        Get cached Gamma API markets response.

        Returns:
            List of market dictionaries or None if not cached/expired/corrupt
        """
        raw = self.client.get("gamma:markets")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Corrupt Gamma cache", error=str(e))
            return None

    # === WebSocket Health Tracking ===

    @redis_retry_sync
    def set_ws_last_activity(self) -> None:
        """Update global WebSocket activity timestamp (for health checks)."""
        self.client.set(
            "ws:last_activity",
            datetime.now(timezone.utc).isoformat(),
            ex=300  # 5 min TTL
        )

    @redis_retry_sync
    def get_ws_last_activity(self) -> Optional[datetime]:
        """
        Get last global WebSocket activity timestamp.

        Returns:
            Datetime of last activity or None if no recent activity
        """
        raw = self.client.get("ws:last_activity")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @redis_retry_sync
    def get_ws_connected_count(self) -> int:
        """Get count of connected markets."""
        return self.client.scard("ws:connected") or 0


# Singleton instance for sync client
_sync_redis_client: Optional[SyncRedisClient] = None


def get_sync_redis() -> SyncRedisClient:
    """Get shared sync Redis client instance."""
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = SyncRedisClient()
    return _sync_redis_client
