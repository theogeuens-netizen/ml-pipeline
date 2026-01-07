"""
Compute trade metrics from Redis buffer.

These metrics are computed from the rolling 1h trade buffer
and added to snapshots for ML features.
"""
from datetime import datetime, timezone
from typing import Optional

from src.db.redis import RedisClient, SyncRedisClient

# Singleton redis client
_redis: Optional[RedisClient] = None


def get_redis() -> RedisClient:
    """Get or create Redis client."""
    global _redis
    if _redis is None:
        _redis = RedisClient()
    return _redis


async def compute_trade_metrics(condition_id: str) -> dict:
    """
    Compute all trade flow metrics from 1h buffer.

    Returns:
        Dictionary with trade flow features:
        - trade_count_1h
        - buy_count_1h
        - sell_count_1h
        - volume_1h
        - buy_volume_1h
        - sell_volume_1h
        - avg_trade_size_1h
        - max_trade_size_1h
        - vwap_1h
    """
    redis = get_redis()
    trades = await redis.get_trades_1h(condition_id)

    if not trades:
        return {
            "trade_count_1h": 0,
            "buy_count_1h": 0,
            "sell_count_1h": 0,
            "volume_1h": 0,
            "buy_volume_1h": 0,
            "sell_volume_1h": 0,
            "avg_trade_size_1h": None,
            "max_trade_size_1h": None,
            "vwap_1h": None,
        }

    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    total_volume = sum(t["size"] for t in trades)
    buy_volume = sum(t["size"] for t in buys)
    sell_volume = sum(t["size"] for t in sells)

    # VWAP calculation
    vwap = None
    if total_volume > 0:
        vwap = sum(t["price"] * t["size"] for t in trades) / total_volume

    return {
        "trade_count_1h": len(trades),
        "buy_count_1h": len(buys),
        "sell_count_1h": len(sells),
        "volume_1h": total_volume,
        "buy_volume_1h": buy_volume,
        "sell_volume_1h": sell_volume,
        "avg_trade_size_1h": total_volume / len(trades) if trades else None,
        "max_trade_size_1h": max(t["size"] for t in trades) if trades else None,
        "vwap_1h": vwap,
    }


async def compute_whale_metrics(condition_id: str) -> dict:
    """
    Compute whale metrics from 1h buffer.

    Returns:
        Dictionary with whale features:
        - whale_count_1h
        - whale_volume_1h
        - whale_buy_volume_1h
        - whale_sell_volume_1h
        - whale_net_flow_1h
        - whale_buy_ratio_1h
        - time_since_whale (seconds)
        - pct_volume_from_whales
    """
    redis = get_redis()
    trades = await redis.get_trades_1h(condition_id)

    # Filter to whales (tier >= 2 means >= $2,000)
    whales = [t for t in trades if t.get("whale_tier", 0) >= 2]

    if not whales:
        return {
            "whale_count_1h": 0,
            "whale_volume_1h": 0,
            "whale_buy_volume_1h": 0,
            "whale_sell_volume_1h": 0,
            "whale_net_flow_1h": 0,
            "whale_buy_ratio_1h": None,
            "time_since_whale": None,
            "pct_volume_from_whales": 0,
        }

    whale_buys = [t for t in whales if t["side"] == "BUY"]
    whale_sells = [t for t in whales if t["side"] == "SELL"]

    whale_volume = sum(t["size"] for t in whales)
    whale_buy_volume = sum(t["size"] for t in whale_buys)
    whale_sell_volume = sum(t["size"] for t in whale_sells)
    total_volume = sum(t["size"] for t in trades) if trades else 0

    # Time since last whale trade
    now = datetime.now(timezone.utc)
    last_whale_time = max(
        datetime.fromisoformat(t["timestamp"]) for t in whales
    )
    time_since = int((now - last_whale_time).total_seconds())

    return {
        "whale_count_1h": len(whales),
        "whale_volume_1h": whale_volume,
        "whale_buy_volume_1h": whale_buy_volume,
        "whale_sell_volume_1h": whale_sell_volume,
        "whale_net_flow_1h": whale_buy_volume - whale_sell_volume,
        "whale_buy_ratio_1h": whale_buy_volume / whale_volume if whale_volume > 0 else None,
        "time_since_whale": time_since,
        "pct_volume_from_whales": whale_volume / total_volume if total_volume > 0 else 0,
    }


async def compute_all_metrics(condition_id: str) -> dict:
    """
    Compute all trade and whale metrics for a market.

    Returns:
        Combined dictionary with all metrics
    """
    trade_metrics = await compute_trade_metrics(condition_id)
    whale_metrics = await compute_whale_metrics(condition_id)
    return {**trade_metrics, **whale_metrics}


async def compute_and_cache_metrics(condition_id: str) -> dict:
    """
    Compute all metrics and cache them in Redis.

    Returns:
        Combined metrics dictionary
    """
    metrics = await compute_all_metrics(condition_id)
    redis = get_redis()
    await redis.set_metrics(condition_id, metrics)
    return metrics


async def get_cached_metrics(condition_id: str) -> Optional[dict]:
    """
    Get cached metrics from Redis.

    Returns:
        Metrics dictionary or None if not cached
    """
    redis = get_redis()
    return await redis.get_metrics(condition_id)


# ===== SYNCHRONOUS VERSIONS FOR CELERY TASKS =====
# Use these in Celery tasks to avoid asyncio event loop issues.

# Singleton sync redis client
_sync_redis: Optional[SyncRedisClient] = None


def get_sync_redis() -> SyncRedisClient:
    """Get or create sync Redis client."""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = SyncRedisClient()
    return _sync_redis


def compute_trade_metrics_sync(condition_id: str) -> dict:
    """
    Compute all trade flow metrics from 1h buffer (synchronous).

    Returns:
        Dictionary with trade flow features
    """
    redis = get_sync_redis()
    trades = redis.get_trades_1h(condition_id)

    if not trades:
        return {
            "trade_count_1h": 0,
            "buy_count_1h": 0,
            "sell_count_1h": 0,
            "volume_1h": 0,
            "buy_volume_1h": 0,
            "sell_volume_1h": 0,
            "avg_trade_size_1h": None,
            "max_trade_size_1h": None,
            "vwap_1h": None,
        }

    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    total_volume = sum(t["size"] for t in trades)
    buy_volume = sum(t["size"] for t in buys)
    sell_volume = sum(t["size"] for t in sells)

    # VWAP calculation
    vwap = None
    if total_volume > 0:
        vwap = sum(t["price"] * t["size"] for t in trades) / total_volume

    return {
        "trade_count_1h": len(trades),
        "buy_count_1h": len(buys),
        "sell_count_1h": len(sells),
        "volume_1h": total_volume,
        "buy_volume_1h": buy_volume,
        "sell_volume_1h": sell_volume,
        "avg_trade_size_1h": total_volume / len(trades) if trades else None,
        "max_trade_size_1h": max(t["size"] for t in trades) if trades else None,
        "vwap_1h": vwap,
    }


def compute_whale_metrics_sync(condition_id: str) -> dict:
    """
    Compute whale metrics from 1h buffer (synchronous).

    Returns:
        Dictionary with whale features
    """
    redis = get_sync_redis()
    trades = redis.get_trades_1h(condition_id)

    # Filter to whales (tier >= 2 means >= $2,000)
    whales = [t for t in trades if t.get("whale_tier", 0) >= 2]

    if not whales:
        return {
            "whale_count_1h": 0,
            "whale_volume_1h": 0,
            "whale_buy_volume_1h": 0,
            "whale_sell_volume_1h": 0,
            "whale_net_flow_1h": 0,
            "whale_buy_ratio_1h": None,
            "time_since_whale": None,
            "pct_volume_from_whales": 0,
        }

    whale_buys = [t for t in whales if t["side"] == "BUY"]
    whale_sells = [t for t in whales if t["side"] == "SELL"]

    whale_volume = sum(t["size"] for t in whales)
    whale_buy_volume = sum(t["size"] for t in whale_buys)
    whale_sell_volume = sum(t["size"] for t in whale_sells)
    total_volume = sum(t["size"] for t in trades) if trades else 0

    # Time since last whale trade
    now = datetime.now(timezone.utc)
    last_whale_time = max(
        datetime.fromisoformat(t["timestamp"]) for t in whales
    )
    time_since = int((now - last_whale_time).total_seconds())

    return {
        "whale_count_1h": len(whales),
        "whale_volume_1h": whale_volume,
        "whale_buy_volume_1h": whale_buy_volume,
        "whale_sell_volume_1h": whale_sell_volume,
        "whale_net_flow_1h": whale_buy_volume - whale_sell_volume,
        "whale_buy_ratio_1h": whale_buy_volume / whale_volume if whale_volume > 0 else None,
        "time_since_whale": time_since,
        "pct_volume_from_whales": whale_volume / total_volume if total_volume > 0 else 0,
    }


def compute_all_metrics_sync(condition_id: str) -> dict:
    """
    Compute all trade and whale metrics for a market (synchronous).

    Returns:
        Combined dictionary with all metrics
    """
    trade_metrics = compute_trade_metrics_sync(condition_id)
    whale_metrics = compute_whale_metrics_sync(condition_id)
    return {**trade_metrics, **whale_metrics}
