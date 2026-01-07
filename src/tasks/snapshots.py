"""
Snapshot collection tasks with production-grade reliability.

These tasks run at tier-specific intervals to collect:
- Price and spread data (from Gamma API)
- Momentum indicators (from Gamma API, free!)
- Volume data (from Gamma API)
- Orderbook depth (from CLOB API, T2+ only)
- Trade flow metrics (from Redis, when WebSocket active)
- Whale metrics (from Redis, when WebSocket active)

Production features:
- Automatic retry with exponential backoff on transient failures
- Data validation before DB writes
- Graceful degradation when one data source fails
- Structured logging with task context

Uses SYNCHRONOUS HTTP calls to avoid asyncio event loop issues in Celery.
"""
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
import structlog
import httpx

from src.config.settings import settings
from src.db.database import get_session, validate_price, validate_volume
from src.db.models import Market, Snapshot, TaskRun, OrderbookSnapshot
from src.db.redis import SyncRedisClient
from src.fetchers.gamma import GammaClient, SyncGammaClient
from src.fetchers.clob import CLOBClient, SyncCLOBClient
from src.fetchers.base import CircuitOpenError
from src.collectors.metrics import compute_all_metrics_sync

logger = structlog.get_logger()

# Concurrency limits for parallel fetches
ORDERBOOK_CONCURRENCY = 100  # Max parallel orderbook fetches (CLOB allows 200/10s)
METRICS_CONCURRENCY = 150    # Max parallel Redis metrics fetches
EXECUTOR_TIMEOUT = 60        # Max seconds for parallel operations

# Errors that should trigger task retry
RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    OperationalError,  # Database connection issues
    CircuitOpenError,  # API circuit breaker open
    SoftTimeLimitExceeded,  # Celery soft limit hit
)


@shared_task(
    name="src.tasks.snapshots.warm_gamma_cache",
    bind=True,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=30,
    max_retries=3,
)
def warm_gamma_cache(self) -> dict:
    """
    Pre-warm Gamma API cache to speed up snapshot tasks.

    Runs every 8 seconds to keep cache fresh for high-frequency tiers.
    Auto-retries on transient failures with exponential backoff.
    """
    task_id = str(uuid.uuid4())[:8]
    gamma = SyncGammaClient()
    redis_client = SyncRedisClient()
    try:
        all_markets = gamma.get_all_active_markets()
        # Cache with longer TTL (30s) - warming runs every 8s so plenty of buffer
        redis_client.set_gamma_markets_cache(all_markets, ttl=30)
        logger.info("Gamma cache warmed", market_count=len(all_markets), task_id=task_id)
        return {"cached": len(all_markets)}
    except RETRYABLE_ERRORS as e:
        logger.warning(
            "Gamma cache warming failed (will retry)",
            error=str(e),
            attempt=self.request.retries + 1,
            task_id=task_id,
        )
        raise
    except Exception as e:
        logger.error("Gamma cache warming failed", error=str(e), task_id=task_id)
        raise
    finally:
        gamma.close()
        redis_client.close()


@shared_task(
    name="src.tasks.snapshots.snapshot_tier",
    bind=True,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=30,
    max_retries=2,  # Lower retries for frequent tasks
)
def snapshot_tier(self, tier: int) -> dict:
    """
    Collect snapshots for all markets in a specific tier.

    Args:
        tier: Tier number (0-4)

    Returns:
        Dictionary with tier, markets, and snapshots counts
    """
    task_run_id = _start_task_run("snapshot_tier", tier=tier)

    try:
        # Get markets in this tier
        with get_session() as session:
            markets = session.execute(
                select(Market).where(
                    Market.tier == tier,
                    Market.active == True,
                    Market.resolved == False,
                )
            ).scalars().all()

            if not markets:
                logger.debug("No markets in tier", tier=tier)
                _complete_task_run(task_run_id, "success", 0, 0)
                return {"tier": tier, "markets": 0, "snapshots": 0}

            # Build lookup maps
            market_ids = {m.condition_id: m.id for m in markets}
            yes_tokens = {m.condition_id: m.yes_token_id for m in markets}
            market_end_dates = {m.condition_id: m.end_date for m in markets}

        # Initialize sync clients
        clob = SyncCLOBClient()
        redis_client = SyncRedisClient()

        try:
            # Try cached Gamma data first (shared across all tier tasks)
            # IMPORTANT: Tasks should ONLY use cached data to avoid rate limiting
            # The warm_gamma_cache task keeps the cache populated
            all_markets = redis_client.get_gamma_markets_cache()
            if all_markets is None:
                # Cache miss - wait briefly and retry once (cache warming might be in progress)
                time.sleep(2)
                all_markets = redis_client.get_gamma_markets_cache()

            if all_markets is None:
                # Still no cache - skip this cycle (cache warming will populate soon)
                logger.warning("Gamma cache empty - skipping snapshot cycle", tier=tier)
                _complete_task_run(task_run_id, "skipped", len(market_ids), 0)
                return {"tier": tier, "markets": len(market_ids), "snapshots": 0, "skipped": True}

            logger.debug("Gamma cache hit", count=len(all_markets))

            # Filter to our tier's markets
            tier_markets = [
                m for m in all_markets
                if m.get("conditionId") in market_ids
            ]

            # Log if markets are missing from gamma cache (indicates stale DB data)
            cache_condition_ids = {m.get("conditionId") for m in all_markets}
            missing_from_cache = set(market_ids.keys()) - cache_condition_ids
            if missing_from_cache:
                logger.warning(
                    "Markets in DB but not in gamma cache",
                    tier=tier,
                    missing_count=len(missing_from_cache),
                    sample_missing=list(missing_from_cache)[:3],
                )

            now = datetime.now(timezone.utc)

            # === PARALLEL ORDERBOOK FETCHING (with Redis cache priority) ===
            orderbook_features: dict[str, dict] = {}
            orderbook_raw: dict[str, dict] = {}  # Store raw orderbooks for OrderbookSnapshot
            if tier in settings.orderbook_enabled_tiers:
                cache_hits = 0
                api_calls = 0

                def fetch_orderbook(args):
                    """Fetch orderbook from Redis cache first, fall back to CLOB API."""
                    condition_id, token_id = args
                    nonlocal cache_hits, api_calls

                    # Try Redis cache first (from WebSocket updates)
                    cached = redis_client.get_orderbook(condition_id)
                    if cached and cached.get("bids") and cached.get("asks"):
                        return condition_id, cached, CLOBClient.extract_orderbook_features(cached), "cache"

                    # Fall back to CLOB API
                    try:
                        orderbook = clob.get_orderbook(token_id)
                        return condition_id, orderbook, CLOBClient.extract_orderbook_features(orderbook), "api"
                    except Exception as e:
                        logger.debug("Orderbook fetch failed", market=condition_id[:16], error=str(e))
                        return condition_id, None, {}, "error"

                # Build list of orderbook fetch arguments
                orderbook_args = [
                    (m.get("conditionId"), yes_tokens.get(m.get("conditionId")))
                    for m in tier_markets
                    if yes_tokens.get(m.get("conditionId"))
                ]

                # Execute all orderbook fetches in parallel using ThreadPoolExecutor
                if orderbook_args:
                    with ThreadPoolExecutor(max_workers=min(ORDERBOOK_CONCURRENCY, len(orderbook_args))) as executor:
                        futures = {executor.submit(fetch_orderbook, args): args for args in orderbook_args}
                        for future in as_completed(futures):
                            try:
                                cid, raw, features, source = future.result()
                                if features:
                                    orderbook_features[cid] = features
                                if raw:
                                    orderbook_raw[cid] = raw
                                if source == "cache":
                                    cache_hits += 1
                                elif source == "api":
                                    api_calls += 1
                            except Exception as e:
                                logger.debug("Orderbook future failed", error=str(e))

                logger.debug("Orderbooks fetched", tier=tier, total=len(orderbook_features),
                           cache_hits=cache_hits, api_calls=api_calls)

            # === PARALLEL METRICS FETCHING ===
            trade_metrics: dict[str, dict] = {}
            if tier in settings.websocket_enabled_tiers:
                def fetch_metrics(condition_id: str):
                    try:
                        return condition_id, compute_all_metrics_sync(condition_id)
                    except Exception as e:
                        logger.debug("Metrics fetch failed", market=condition_id[:16], error=str(e))
                        return condition_id, {}

                # Execute all metrics fetches in parallel
                condition_ids = [m.get("conditionId") for m in tier_markets]
                if condition_ids:
                    with ThreadPoolExecutor(max_workers=min(METRICS_CONCURRENCY, len(condition_ids))) as executor:
                        futures = {executor.submit(fetch_metrics, cid): cid for cid in condition_ids}
                        for future in as_completed(futures):
                            try:
                                cid, metrics = future.result()
                                if metrics:
                                    trade_metrics[cid] = metrics
                            except Exception as e:
                                logger.debug("Metrics future failed", error=str(e))

                logger.debug("Metrics fetched", tier=tier, count=len(trade_metrics))

            # === BUILD SNAPSHOTS ===
            snapshots = []
            for market_data in tier_markets:
                condition_id = market_data.get("conditionId")
                market_id = market_ids[condition_id]
                yes_price, _ = GammaClient.parse_outcome_prices(market_data)

                # Calculate hours to close
                end_date = market_end_dates.get(condition_id)
                hours_to_close = None
                if end_date:
                    hours_to_close = (end_date - now).total_seconds() / 3600

                # Create base snapshot with Gamma data
                snapshot = Snapshot(
                    market_id=market_id,
                    timestamp=now,
                    tier=tier,
                    # === PRICE FIELDS ===
                    price=yes_price,
                    best_bid=_safe_float(market_data.get("bestBid")),
                    best_ask=_safe_float(market_data.get("bestAsk")),
                    spread=_safe_float(market_data.get("spread")),
                    last_trade_price=_safe_float(market_data.get("lastTradePrice")),
                    # === MOMENTUM (FREE from Gamma!) ===
                    price_change_1d=_safe_float(market_data.get("oneDayPriceChange")),
                    price_change_1w=_safe_float(market_data.get("oneWeekPriceChange")),
                    price_change_1m=_safe_float(market_data.get("oneMonthPriceChange")),
                    # === VOLUME ===
                    volume_total=_safe_float(market_data.get("volumeNum")),
                    volume_24h=_safe_float(market_data.get("volume24hr")),
                    volume_1w=_safe_float(market_data.get("volume1wk")),
                    liquidity=_safe_float(market_data.get("liquidityNum")),
                    # === CONTEXT ===
                    hours_to_close=hours_to_close,
                    day_of_week=now.weekday(),
                    hour_of_day=now.hour,
                )

                # Apply orderbook features (fetched in parallel above)
                features = orderbook_features.get(condition_id, {})
                if features:
                    snapshot.bid_depth_5 = features.get("bid_depth_5")
                    snapshot.bid_depth_10 = features.get("bid_depth_10")
                    snapshot.bid_depth_20 = features.get("bid_depth_20")
                    snapshot.bid_depth_50 = features.get("bid_depth_50")
                    snapshot.ask_depth_5 = features.get("ask_depth_5")
                    snapshot.ask_depth_10 = features.get("ask_depth_10")
                    snapshot.ask_depth_20 = features.get("ask_depth_20")
                    snapshot.ask_depth_50 = features.get("ask_depth_50")
                    snapshot.bid_levels = features.get("bid_levels")
                    snapshot.ask_levels = features.get("ask_levels")
                    snapshot.book_imbalance = features.get("book_imbalance")
                    snapshot.bid_wall_price = features.get("bid_wall_price")
                    snapshot.bid_wall_size = features.get("bid_wall_size")
                    snapshot.ask_wall_price = features.get("ask_wall_price")
                    snapshot.ask_wall_size = features.get("ask_wall_size")

                    # Use CLOB prices only if BOTH sides exist and spread is reasonable
                    clob_bid = features.get("best_bid")
                    clob_ask = features.get("best_ask")
                    clob_spread = features.get("spread")
                    if clob_bid and clob_ask and clob_spread and clob_spread < 0.5:
                        snapshot.best_bid = clob_bid
                        snapshot.best_ask = clob_ask
                        snapshot.spread = clob_spread

                # Apply trade metrics (fetched in parallel above)
                metrics = trade_metrics.get(condition_id, {})
                if metrics:
                    snapshot.trade_count_1h = metrics.get("trade_count_1h")
                    snapshot.buy_count_1h = metrics.get("buy_count_1h")
                    snapshot.sell_count_1h = metrics.get("sell_count_1h")
                    snapshot.volume_1h = metrics.get("volume_1h")
                    snapshot.buy_volume_1h = metrics.get("buy_volume_1h")
                    snapshot.sell_volume_1h = metrics.get("sell_volume_1h")
                    snapshot.avg_trade_size_1h = metrics.get("avg_trade_size_1h")
                    snapshot.max_trade_size_1h = metrics.get("max_trade_size_1h")
                    snapshot.vwap_1h = metrics.get("vwap_1h")
                    snapshot.whale_count_1h = metrics.get("whale_count_1h")
                    snapshot.whale_volume_1h = metrics.get("whale_volume_1h")
                    snapshot.whale_buy_volume_1h = metrics.get("whale_buy_volume_1h")
                    snapshot.whale_sell_volume_1h = metrics.get("whale_sell_volume_1h")
                    snapshot.whale_net_flow_1h = metrics.get("whale_net_flow_1h")
                    snapshot.whale_buy_ratio_1h = metrics.get("whale_buy_ratio_1h")
                    snapshot.time_since_whale = metrics.get("time_since_whale")
                    snapshot.pct_volume_from_whales = metrics.get("pct_volume_from_whales")

                snapshots.append(snapshot)

            # Build OrderbookSnapshot objects from raw orderbook data
            orderbook_snapshots = []
            for condition_id, raw in orderbook_raw.items():
                market_id = market_ids.get(condition_id)
                if not market_id:
                    continue
                bids = raw.get("bids", [])
                asks = raw.get("asks", [])
                # Find largest bid/ask
                largest_bid = max(bids, key=lambda x: float(x.get("size", 0)), default=None) if bids else None
                largest_ask = max(asks, key=lambda x: float(x.get("size", 0)), default=None) if asks else None
                orderbook_snapshots.append(OrderbookSnapshot(
                    market_id=market_id,
                    timestamp=now,
                    bids=bids,
                    asks=asks,
                    total_bid_depth=sum(float(b.get("size", 0)) for b in bids),
                    total_ask_depth=sum(float(a.get("size", 0)) for a in asks),
                    num_bid_levels=len(bids),
                    num_ask_levels=len(asks),
                    largest_bid_price=float(largest_bid.get("price", 0)) if largest_bid else None,
                    largest_bid_size=float(largest_bid.get("size", 0)) if largest_bid else None,
                    largest_ask_price=float(largest_ask.get("price", 0)) if largest_ask else None,
                    largest_ask_size=float(largest_ask.get("size", 0)) if largest_ask else None,
                ))

            # Bulk insert snapshots and orderbook snapshots
            if snapshots or orderbook_snapshots:
                with get_session() as session:
                    if snapshots:
                        session.add_all(snapshots)
                    if orderbook_snapshots:
                        session.add_all(orderbook_snapshots)

                    # Update market last_snapshot_at and snapshot_count
                    for snapshot in snapshots:
                        session.execute(
                            update(Market)
                            .where(Market.id == snapshot.market_id)
                            .values(
                                last_snapshot_at=now,
                                snapshot_count=Market.snapshot_count + 1,
                            )
                        )

                    session.commit()

            _complete_task_run(task_run_id, "success", len(market_ids), len(snapshots))
            logger.info(
                "Snapshots collected",
                tier=tier,
                markets=len(market_ids),
                snapshots=len(snapshots),
            )
            return {
                "tier": tier,
                "markets": len(market_ids),
                "snapshots": len(snapshots),
            }

        finally:
            clob.close()
            redis_client.close()

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Snapshot collection failed", tier=tier, error=str(e))
        raise


@shared_task(
    name="src.tasks.snapshots.snapshot_tier_batch",
    bind=True,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=15,  # Shorter max for high-frequency batches
    max_retries=2,
)
def snapshot_tier_batch(self, tier: int, batch: int, total_batches: int = 2) -> dict:
    """
    Collect snapshots for a batch of markets in a specific tier.

    Splits the tier's markets into batches for parallel processing by multiple workers.

    Args:
        tier: Tier number (0-4)
        batch: Batch number (0 to total_batches-1)
        total_batches: Total number of batches to split into

    Returns:
        Dictionary with tier, batch, markets, and snapshots counts
    """
    task_run_id = _start_task_run(f"snapshot_tier_batch_{batch}", tier=tier)

    try:
        # Get markets in this tier
        with get_session() as session:
            all_tier_markets = session.execute(
                select(Market).where(
                    Market.tier == tier,
                    Market.active == True,
                    Market.resolved == False,
                ).order_by(Market.id)  # Consistent ordering for batching
            ).scalars().all()

            if not all_tier_markets:
                logger.debug("No markets in tier", tier=tier)
                _complete_task_run(task_run_id, "success", 0, 0)
                return {"tier": tier, "batch": batch, "markets": 0, "snapshots": 0}

            # Split into batches - this batch gets every Nth market
            markets = [m for i, m in enumerate(all_tier_markets) if i % total_batches == batch]

            if not markets:
                _complete_task_run(task_run_id, "success", 0, 0)
                return {"tier": tier, "batch": batch, "markets": 0, "snapshots": 0}

            # Build lookup maps
            market_ids = {m.condition_id: m.id for m in markets}
            yes_tokens = {m.condition_id: m.yes_token_id for m in markets}
            market_end_dates = {m.condition_id: m.end_date for m in markets}

        # Initialize sync clients
        clob = SyncCLOBClient()
        redis_client = SyncRedisClient()

        try:
            # Use cached Gamma data ONLY - avoid rate limiting
            all_gamma_markets = redis_client.get_gamma_markets_cache()
            if all_gamma_markets is None:
                time.sleep(2)
                all_gamma_markets = redis_client.get_gamma_markets_cache()

            if all_gamma_markets is None:
                logger.warning("Gamma cache empty - skipping batch", tier=tier, batch=batch)
                _complete_task_run(task_run_id, "skipped", len(market_ids), 0)
                return {"tier": tier, "batch": batch, "markets": len(market_ids), "snapshots": 0, "skipped": True}

            # Filter to our batch's markets
            tier_markets = [
                m for m in all_gamma_markets
                if m.get("conditionId") in market_ids
            ]

            # Log if markets are missing from gamma cache
            cache_condition_ids = {m.get("conditionId") for m in all_gamma_markets}
            missing_from_cache = set(market_ids.keys()) - cache_condition_ids
            if missing_from_cache:
                logger.warning(
                    "Markets in DB but not in gamma cache (batch)",
                    tier=tier,
                    batch=batch,
                    missing_count=len(missing_from_cache),
                )

            now = datetime.now(timezone.utc)

            # === PARALLEL ORDERBOOK FETCHING ===
            orderbook_features: dict[str, dict] = {}
            orderbook_raw: dict[str, dict] = {}  # Store raw orderbooks for OrderbookSnapshot
            if tier in settings.orderbook_enabled_tiers:
                def fetch_orderbook(args):
                    condition_id, token_id = args
                    cached = redis_client.get_orderbook(condition_id)
                    if cached and cached.get("bids") and cached.get("asks"):
                        return condition_id, cached, CLOBClient.extract_orderbook_features(cached), "cache"
                    try:
                        orderbook = clob.get_orderbook(token_id)
                        return condition_id, orderbook, CLOBClient.extract_orderbook_features(orderbook), "api"
                    except Exception:
                        return condition_id, None, {}, "error"

                orderbook_args = [
                    (m.get("conditionId"), yes_tokens.get(m.get("conditionId")))
                    for m in tier_markets
                    if yes_tokens.get(m.get("conditionId"))
                ]

                if orderbook_args:
                    with ThreadPoolExecutor(max_workers=min(ORDERBOOK_CONCURRENCY, len(orderbook_args))) as executor:
                        futures = {executor.submit(fetch_orderbook, args): args for args in orderbook_args}
                        for future in as_completed(futures):
                            try:
                                cid, raw, features, _ = future.result()
                                if features:
                                    orderbook_features[cid] = features
                                if raw:
                                    orderbook_raw[cid] = raw
                            except Exception:
                                pass

            # === PARALLEL METRICS FETCHING ===
            trade_metrics: dict[str, dict] = {}
            if tier in settings.websocket_enabled_tiers:
                def fetch_metrics(condition_id: str):
                    try:
                        return condition_id, compute_all_metrics_sync(condition_id)
                    except Exception:
                        return condition_id, {}

                condition_ids = [m.get("conditionId") for m in tier_markets]
                if condition_ids:
                    with ThreadPoolExecutor(max_workers=min(METRICS_CONCURRENCY, len(condition_ids))) as executor:
                        futures = {executor.submit(fetch_metrics, cid): cid for cid in condition_ids}
                        for future in as_completed(futures):
                            try:
                                cid, metrics = future.result()
                                if metrics:
                                    trade_metrics[cid] = metrics
                            except Exception:
                                pass

            # === BUILD SNAPSHOTS ===
            snapshots = []
            for market_data in tier_markets:
                condition_id = market_data.get("conditionId")
                market_id = market_ids[condition_id]
                yes_price, _ = GammaClient.parse_outcome_prices(market_data)

                end_date = market_end_dates.get(condition_id)
                hours_to_close = (end_date - now).total_seconds() / 3600 if end_date else None

                snapshot = Snapshot(
                    market_id=market_id,
                    timestamp=now,
                    tier=tier,
                    price=yes_price,
                    best_bid=_safe_float(market_data.get("bestBid")),
                    best_ask=_safe_float(market_data.get("bestAsk")),
                    spread=_safe_float(market_data.get("spread")),
                    last_trade_price=_safe_float(market_data.get("lastTradePrice")),
                    price_change_1d=_safe_float(market_data.get("oneDayPriceChange")),
                    price_change_1w=_safe_float(market_data.get("oneWeekPriceChange")),
                    price_change_1m=_safe_float(market_data.get("oneMonthPriceChange")),
                    volume_total=_safe_float(market_data.get("volumeNum")),
                    volume_24h=_safe_float(market_data.get("volume24hr")),
                    volume_1w=_safe_float(market_data.get("volume1wk")),
                    liquidity=_safe_float(market_data.get("liquidityNum")),
                    hours_to_close=hours_to_close,
                    day_of_week=now.weekday(),
                    hour_of_day=now.hour,
                )

                # Apply orderbook features
                features = orderbook_features.get(condition_id, {})
                if features:
                    snapshot.bid_depth_5 = features.get("bid_depth_5")
                    snapshot.bid_depth_10 = features.get("bid_depth_10")
                    snapshot.bid_depth_20 = features.get("bid_depth_20")
                    snapshot.bid_depth_50 = features.get("bid_depth_50")
                    snapshot.ask_depth_5 = features.get("ask_depth_5")
                    snapshot.ask_depth_10 = features.get("ask_depth_10")
                    snapshot.ask_depth_20 = features.get("ask_depth_20")
                    snapshot.ask_depth_50 = features.get("ask_depth_50")
                    snapshot.bid_levels = features.get("bid_levels")
                    snapshot.ask_levels = features.get("ask_levels")
                    snapshot.book_imbalance = features.get("book_imbalance")
                    snapshot.bid_wall_price = features.get("bid_wall_price")
                    snapshot.bid_wall_size = features.get("bid_wall_size")
                    snapshot.ask_wall_price = features.get("ask_wall_price")
                    snapshot.ask_wall_size = features.get("ask_wall_size")

                    clob_bid = features.get("best_bid")
                    clob_ask = features.get("best_ask")
                    clob_spread = features.get("spread")
                    if clob_bid and clob_ask and clob_spread and clob_spread < 0.5:
                        snapshot.best_bid = clob_bid
                        snapshot.best_ask = clob_ask
                        snapshot.spread = clob_spread

                # Apply trade metrics
                metrics = trade_metrics.get(condition_id, {})
                if metrics:
                    snapshot.trade_count_1h = metrics.get("trade_count_1h")
                    snapshot.buy_count_1h = metrics.get("buy_count_1h")
                    snapshot.sell_count_1h = metrics.get("sell_count_1h")
                    snapshot.volume_1h = metrics.get("volume_1h")
                    snapshot.buy_volume_1h = metrics.get("buy_volume_1h")
                    snapshot.sell_volume_1h = metrics.get("sell_volume_1h")
                    snapshot.avg_trade_size_1h = metrics.get("avg_trade_size_1h")
                    snapshot.max_trade_size_1h = metrics.get("max_trade_size_1h")
                    snapshot.vwap_1h = metrics.get("vwap_1h")
                    snapshot.whale_count_1h = metrics.get("whale_count_1h")
                    snapshot.whale_volume_1h = metrics.get("whale_volume_1h")
                    snapshot.whale_buy_volume_1h = metrics.get("whale_buy_volume_1h")
                    snapshot.whale_sell_volume_1h = metrics.get("whale_sell_volume_1h")
                    snapshot.whale_net_flow_1h = metrics.get("whale_net_flow_1h")
                    snapshot.whale_buy_ratio_1h = metrics.get("whale_buy_ratio_1h")
                    snapshot.time_since_whale = metrics.get("time_since_whale")
                    snapshot.pct_volume_from_whales = metrics.get("pct_volume_from_whales")

                snapshots.append(snapshot)

            # Build OrderbookSnapshot objects from raw orderbook data
            orderbook_snapshots = []
            for condition_id, raw in orderbook_raw.items():
                market_id = market_ids.get(condition_id)
                if not market_id:
                    continue
                bids = raw.get("bids", [])
                asks = raw.get("asks", [])
                largest_bid = max(bids, key=lambda x: float(x.get("size", 0)), default=None) if bids else None
                largest_ask = max(asks, key=lambda x: float(x.get("size", 0)), default=None) if asks else None
                orderbook_snapshots.append(OrderbookSnapshot(
                    market_id=market_id,
                    timestamp=now,
                    bids=bids,
                    asks=asks,
                    total_bid_depth=sum(float(b.get("size", 0)) for b in bids),
                    total_ask_depth=sum(float(a.get("size", 0)) for a in asks),
                    num_bid_levels=len(bids),
                    num_ask_levels=len(asks),
                    largest_bid_price=float(largest_bid.get("price", 0)) if largest_bid else None,
                    largest_bid_size=float(largest_bid.get("size", 0)) if largest_bid else None,
                    largest_ask_price=float(largest_ask.get("price", 0)) if largest_ask else None,
                    largest_ask_size=float(largest_ask.get("size", 0)) if largest_ask else None,
                ))

            # Bulk insert snapshots and orderbook snapshots
            if snapshots or orderbook_snapshots:
                with get_session() as session:
                    if snapshots:
                        session.add_all(snapshots)
                    if orderbook_snapshots:
                        session.add_all(orderbook_snapshots)
                    for snapshot in snapshots:
                        session.execute(
                            update(Market)
                            .where(Market.id == snapshot.market_id)
                            .values(
                                last_snapshot_at=now,
                                snapshot_count=Market.snapshot_count + 1,
                            )
                        )
                    session.commit()

            _complete_task_run(task_run_id, "success", len(market_ids), len(snapshots))
            logger.info(
                "Batch snapshots collected",
                tier=tier,
                batch=batch,
                markets=len(market_ids),
                snapshots=len(snapshots),
                orderbook_snapshots=len(orderbook_snapshots),
            )
            return {
                "tier": tier,
                "batch": batch,
                "markets": len(market_ids),
                "snapshots": len(snapshots),
                "orderbook_snapshots": len(orderbook_snapshots),
            }

        finally:
            clob.close()
            redis_client.close()

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Batch snapshot collection failed", tier=tier, batch=batch, error=str(e))
        raise


@shared_task(name="src.tasks.snapshots.snapshot_market")
def snapshot_market(market_id: int) -> dict:
    """
    Collect a single snapshot for a specific market.

    Useful for manual collection or backfilling.

    Args:
        market_id: Database market ID

    Returns:
        Dictionary with market_id and success status
    """
    with get_session() as session:
        market = session.get(Market, market_id)
        if not market:
            logger.warning("Market not found", market_id=market_id)
            return {"market_id": market_id, "success": False, "error": "Not found"}

        condition_id = market.condition_id
        yes_token_id = market.yes_token_id
        tier = market.tier
        end_date = market.end_date

    gamma = SyncGammaClient()
    clob = SyncCLOBClient()

    try:
        # Fetch market data
        market_data = gamma.get_market(condition_id)
        if not market_data:
            return {"market_id": market_id, "success": False, "error": "API fetch failed"}

        now = datetime.now(timezone.utc)
        yes_price, _ = GammaClient.parse_outcome_prices(market_data)
        hours_to_close = (end_date - now).total_seconds() / 3600 if end_date else None

        # Create snapshot
        snapshot = Snapshot(
            market_id=market_id,
            timestamp=now,
            tier=tier,
            price=yes_price,
            best_bid=_safe_float(market_data.get("bestBid")),
            best_ask=_safe_float(market_data.get("bestAsk")),
            spread=_safe_float(market_data.get("spread")),
            last_trade_price=_safe_float(market_data.get("lastTradePrice")),
            price_change_1d=_safe_float(market_data.get("oneDayPriceChange")),
            price_change_1w=_safe_float(market_data.get("oneWeekPriceChange")),
            price_change_1m=_safe_float(market_data.get("oneMonthPriceChange")),
            volume_total=_safe_float(market_data.get("volumeNum")),
            volume_24h=_safe_float(market_data.get("volume24hr")),
            volume_1w=_safe_float(market_data.get("volume1wk")),
            liquidity=_safe_float(market_data.get("liquidityNum")),
            hours_to_close=hours_to_close,
            day_of_week=now.weekday(),
            hour_of_day=now.hour,
        )

        # Fetch orderbook if token available
        orderbook_snapshot = None
        if yes_token_id:
            try:
                orderbook = clob.get_orderbook(yes_token_id)
                features = CLOBClient.extract_orderbook_features(orderbook)

                snapshot.bid_depth_5 = features["bid_depth_5"]
                snapshot.bid_depth_10 = features["bid_depth_10"]
                snapshot.bid_depth_20 = features["bid_depth_20"]
                snapshot.bid_depth_50 = features["bid_depth_50"]
                snapshot.ask_depth_5 = features["ask_depth_5"]
                snapshot.ask_depth_10 = features["ask_depth_10"]
                snapshot.ask_depth_20 = features["ask_depth_20"]
                snapshot.ask_depth_50 = features["ask_depth_50"]
                snapshot.bid_levels = features["bid_levels"]
                snapshot.ask_levels = features["ask_levels"]
                snapshot.book_imbalance = features["book_imbalance"]
                snapshot.bid_wall_price = features["bid_wall_price"]
                snapshot.bid_wall_size = features["bid_wall_size"]
                snapshot.ask_wall_price = features["ask_wall_price"]
                snapshot.ask_wall_size = features["ask_wall_size"]

                # Create OrderbookSnapshot from raw data
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])
                largest_bid = max(bids, key=lambda x: float(x.get("size", 0)), default=None) if bids else None
                largest_ask = max(asks, key=lambda x: float(x.get("size", 0)), default=None) if asks else None
                orderbook_snapshot = OrderbookSnapshot(
                    market_id=market_id,
                    timestamp=now,
                    bids=bids,
                    asks=asks,
                    total_bid_depth=sum(float(b.get("size", 0)) for b in bids),
                    total_ask_depth=sum(float(a.get("size", 0)) for a in asks),
                    num_bid_levels=len(bids),
                    num_ask_levels=len(asks),
                    largest_bid_price=float(largest_bid.get("price", 0)) if largest_bid else None,
                    largest_bid_size=float(largest_bid.get("size", 0)) if largest_bid else None,
                    largest_ask_price=float(largest_ask.get("price", 0)) if largest_ask else None,
                    largest_ask_size=float(largest_ask.get("size", 0)) if largest_ask else None,
                )

            except Exception as e:
                logger.warning("Orderbook fetch failed", error=str(e))

        # Save snapshot and orderbook snapshot
        with get_session() as session:
            session.add(snapshot)
            if orderbook_snapshot:
                session.add(orderbook_snapshot)
            session.execute(
                update(Market)
                .where(Market.id == market_id)
                .values(
                    last_snapshot_at=now,
                    snapshot_count=Market.snapshot_count + 1,
                )
            )
            session.commit()

        return {"market_id": market_id, "success": True}

    finally:
        gamma.close()
        clob.close()


# === Helper Functions ===


def _safe_float(value, field_name: str = "field") -> Optional[float]:
    """
    Safely convert value to float, returning None on failure.

    Logs warning for suspicious but valid values.
    """
    if value is None:
        return None
    try:
        result = float(value)
        # Log suspicious values but don't reject them
        if result < 0:
            logger.debug(f"Negative value for {field_name}", value=result)
        return result
    except (ValueError, TypeError) as e:
        logger.debug(f"Invalid value for {field_name}", value=value, error=str(e))
        return None


def _safe_price(value) -> Optional[float]:
    """
    Safely convert price value, validating range [0, 1].

    Returns None for invalid prices.
    """
    if value is None:
        return None
    try:
        price = float(value)
        if price < 0 or price > 1:
            logger.debug("Price out of range [0,1]", value=price)
            return None
        return price
    except (ValueError, TypeError):
        return None


def validate_snapshot_data(snapshot: Snapshot) -> bool:
    """
    Validate snapshot data before insert.

    Returns True if data is valid, False otherwise.
    Logs warnings for invalid data.
    """
    # Price must be in [0, 1]
    if snapshot.price is not None:
        if snapshot.price < 0 or snapshot.price > 1:
            logger.warning(
                "Invalid snapshot price",
                market_id=snapshot.market_id,
                price=snapshot.price,
            )
            return False

    # Volume must be non-negative
    if snapshot.volume_total is not None and snapshot.volume_total < 0:
        logger.warning(
            "Negative volume",
            market_id=snapshot.market_id,
            volume=snapshot.volume_total,
        )
        return False

    return True


def _start_task_run(task_name: str, tier: Optional[int] = None) -> int:
    """Create a task run record and return its ID."""
    with get_session() as session:
        run = TaskRun(
            task_name=task_name,
            task_id="",  # Could use celery task ID
            tier=tier,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        session.add(run)
        session.commit()
        return run.id


def _complete_task_run(run_id: int, status: str, markets: int, rows: int) -> None:
    """Mark a task run as complete."""
    with get_session() as session:
        run = session.get(TaskRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
            run.status = status
            run.markets_processed = markets
            run.rows_inserted = rows
            session.commit()


def _fail_task_run(run_id: int, error: Exception) -> None:
    """Mark a task run as failed."""
    with get_session() as session:
        run = session.get(TaskRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
            run.status = "failed"
            run.error_message = str(error)
            run.error_traceback = traceback.format_exc()
            session.commit()
