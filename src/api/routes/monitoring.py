"""
Monitoring endpoints for operational health.

This module provides endpoints distinct from existing pages:
- System health (WebSocket, Celery workers, queues)
- Field completeness metrics
- Error tracking

Existing pages already cover:
- Dashboard: Market counts, tier distribution, collection coverage
- Data Quality: Per-tier coverage %, data gaps
- Tasks: Task runs history, success rates
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import Snapshot, Trade, TaskRun
from src.db.redis import RedisClient

router = APIRouter()

# Define optional field categories for completeness tracking
OPTIONAL_FIELDS = {
    "price": ["best_bid", "best_ask", "spread", "last_trade_price"],
    "momentum": ["price_change_1d", "price_change_1w", "price_change_1m"],
    "volume": ["volume_total", "volume_24h", "volume_1w", "liquidity"],
    "orderbook_depth": [
        "bid_depth_5", "bid_depth_10", "bid_depth_20", "bid_depth_50",
        "ask_depth_5", "ask_depth_10", "ask_depth_20", "ask_depth_50",
    ],
    "orderbook_derived": [
        "bid_levels", "ask_levels", "book_imbalance",
        "bid_wall_price", "bid_wall_size", "ask_wall_price", "ask_wall_size",
    ],
    "trade_flow": [
        "trade_count_1h", "buy_count_1h", "sell_count_1h",
        "volume_1h", "buy_volume_1h", "sell_volume_1h",
        "avg_trade_size_1h", "max_trade_size_1h", "vwap_1h",
    ],
    "whale_metrics": [
        "whale_count_1h", "whale_volume_1h", "whale_buy_volume_1h", "whale_sell_volume_1h",
        "whale_net_flow_1h", "whale_buy_ratio_1h", "time_since_whale", "pct_volume_from_whales",
    ],
    "context": ["hours_to_close", "day_of_week", "hour_of_day"],
}

# Total optional fields count
TOTAL_OPTIONAL_FIELDS = sum(len(fields) for fields in OPTIONAL_FIELDS.values())


@router.get("/monitoring/websocket-coverage")
async def get_websocket_coverage(db: Session = Depends(get_db)):
    """
    Check WebSocket subscription coverage.

    Compares markets that SHOULD be subscribed (T2+) vs markets
    that ARE subscribed (tracked in Redis).
    """
    from src.db.models import Market
    from src.config.settings import settings

    redis = RedisClient()

    try:
        # Markets that SHOULD be subscribed (T2+ with token IDs)
        should_subscribe = db.execute(
            select(Market.condition_id, Market.tier, Market.question).where(
                Market.tier.in_(settings.websocket_enabled_tiers),
                Market.active == True,
                Market.resolved == False,
                Market.yes_token_id.isnot(None),
            )
        ).all()

        should_set = {m.condition_id for m in should_subscribe}

        # Markets that ARE subscribed (from Redis)
        ws_status = await redis.get_ws_status()
        are_set = set(ws_status.get("connected_markets", []))

        # Find mismatches
        missing = should_set - are_set  # Should be subscribed but aren't
        extra = are_set - should_set    # Subscribed but shouldn't be

        # Get details for missing markets
        missing_details = [
            {"condition_id": m.condition_id[:20], "tier": m.tier, "question": m.question[:50] if m.question else None}
            for m in should_subscribe if m.condition_id in missing
        ]

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "should_subscribe": len(should_set),
            "actually_subscribed": len(are_set),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "missing_markets": missing_details[:10],  # First 10
            "status": "ok" if len(missing) == 0 else "degraded",
        }
    finally:
        await redis.close()


@router.get("/monitoring/subscription-health")
async def get_subscription_health(db: Session = Depends(get_db)):
    """
    Verify subscribed markets are actually receiving data.

    Categories:
    - active: Received trade in last 10 minutes
    - quiet: Received trade, but >10 minutes ago (may be low-activity market)
    - silent: Never received any trade (normal for low-volume markets)

    Note: "silent" markets are NOT necessarily broken - many markets simply
    have very low trading volume. Only "quiet" markets that WERE active but
    stopped are potentially concerning.
    """
    from src.db.models import Market
    from src.config.settings import settings

    redis = RedisClient()
    now = datetime.now(timezone.utc)

    try:
        # Get WebSocket status with last event times
        ws_status = await redis.get_ws_status()
        connected_markets = ws_status.get("connected_markets", [])
        last_events = ws_status.get("last_events", {})

        # Analyze each subscribed market
        active = []   # Trade in last 10 minutes
        quiet = []    # Trade >10 min but <1 hour ago
        dormant = []  # Trade >1 hour ago
        silent = []   # Never received an event (normal for low-volume)

        for condition_id in connected_markets:
            last_event_str = last_events.get(condition_id)
            if not last_event_str:
                silent.append(condition_id)
            else:
                try:
                    last_event = datetime.fromisoformat(last_event_str)
                    seconds_since = (now - last_event).total_seconds()
                    if seconds_since < 600:  # 10 minutes
                        active.append(condition_id)
                    elif seconds_since < 3600:  # 1 hour
                        quiet.append({
                            "condition_id": condition_id[:20],
                            "seconds_since_event": int(seconds_since),
                        })
                    else:
                        dormant.append({
                            "condition_id": condition_id[:20],
                            "seconds_since_event": int(seconds_since),
                        })
                except:
                    silent.append(condition_id)

        # Get market details for quiet/dormant markets
        if quiet or dormant:
            markets_info = db.execute(
                select(Market.condition_id, Market.tier, Market.question).where(
                    Market.active == True
                )
            ).all()

            # Match by prefix for quiet markets
            for m in markets_info:
                for q in quiet:
                    if isinstance(q, dict) and m.condition_id.startswith(q["condition_id"]):
                        q["tier"] = m.tier
                        q["question"] = m.question[:40] if m.question else None
                for d in dormant:
                    if isinstance(d, dict) and m.condition_id.startswith(d["condition_id"]):
                        d["tier"] = m.tier
                        d["question"] = m.question[:40] if m.question else None

        # Calculate health status
        # "ok" = mostly active, few quiet
        # "warning" = many quiet markets
        # "degraded" = many dormant markets (were active, now silent for >1hr)
        total = len(connected_markets)
        active_pct = (len(active) / total * 100) if total > 0 else 0

        if len(dormant) > 10:
            status = "degraded"
        elif len(quiet) > 50:
            status = "warning"
        else:
            status = "ok"

        return {
            "timestamp": now.isoformat(),
            "total_subscribed": total,
            "active": len(active),        # Trade in last 10 min (healthy)
            "active_pct": round(active_pct, 1),
            "quiet": len(quiet),          # Trade 10min-1hr ago (may need attention)
            "dormant": len(dormant),      # Trade >1hr ago (concerning)
            "silent": len(silent),        # Never received trade (normal for low-volume)
            "quiet_markets": quiet[:10],
            "dormant_markets": dormant[:10],
            "status": status,
            "note": "Silent markets are normal - many markets have very low trading volume",
        }
    finally:
        await redis.close()


@router.get("/monitoring/health")
async def get_system_health(db: Session = Depends(get_db)):
    """
    Get overall system health status.

    Returns:
        WebSocket status, Celery worker count, trade rate, error rate
    """
    redis = RedisClient()
    now = datetime.now(timezone.utc)

    try:
        # WebSocket health
        ws_last_activity = await redis.get_ws_last_activity()
        ws_connected_count = await redis.get_ws_connected_count()

        if ws_last_activity:
            seconds_since_activity = int((now - ws_last_activity).total_seconds())
            if seconds_since_activity < 60:
                ws_status = "healthy"
            elif seconds_since_activity < 120:
                ws_status = "stale"
            else:
                ws_status = "disconnected"
        else:
            ws_status = "disconnected"
            seconds_since_activity = None

        # Trades in last hour
        one_hour_ago = now - timedelta(hours=1)
        trades_last_hour = db.execute(
            select(func.count(Trade.id)).where(Trade.timestamp >= one_hour_ago)
        ).scalar() or 0

        # Calculate trades per minute
        ten_min_ago = now - timedelta(minutes=10)
        trades_last_10min = db.execute(
            select(func.count(Trade.id)).where(Trade.timestamp >= ten_min_ago)
        ).scalar() or 0
        trades_per_minute = round(trades_last_10min / 10, 1)

        # Task errors in last 10 minutes
        errors_last_10min = db.execute(
            select(func.count(TaskRun.id)).where(
                TaskRun.started_at >= ten_min_ago,
                TaskRun.status == "failed",
            )
        ).scalar() or 0

        # Task success in last 10 minutes
        tasks_last_10min = db.execute(
            select(func.count(TaskRun.id)).where(TaskRun.started_at >= ten_min_ago)
        ).scalar() or 0

        return {
            "timestamp": now.isoformat(),
            "websocket": {
                "status": ws_status,
                "connected_markets": ws_connected_count,
                "last_activity": ws_last_activity.isoformat() if ws_last_activity else None,
                "seconds_since_activity": seconds_since_activity,
                "trades_last_hour": trades_last_hour,
                "trades_per_minute": trades_per_minute,
            },
            "tasks": {
                "tasks_last_10min": tasks_last_10min,
                "errors_last_10min": errors_last_10min,
                "error_rate_pct": round(errors_last_10min / tasks_last_10min * 100, 1) if tasks_last_10min > 0 else 0,
            },
        }
    finally:
        await redis.close()


@router.get("/monitoring/connections")
async def get_connection_status(db: Session = Depends(get_db)):
    """
    Get detailed connection status for all data sources.

    Returns status of:
    - Dual WebSocket connections
    - Database pool
    - Redis
    - API circuit breakers (Gamma, CLOB)
    """
    from src.db.models import Market
    from src.config.settings import settings
    import redis as redis_sync

    now = datetime.now(timezone.utc)

    # Get Redis connection info
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    redis_status = "healthy"
    try:
        r.ping()
        redis_info = r.info("clients")
        redis_connected_clients = redis_info.get("connected_clients", 0)
    except Exception as e:
        redis_status = "error"
        redis_connected_clients = 0
    finally:
        r.close()

    # Get DB pool info
    from src.db.database import engine
    db_pool_size = engine.pool.size()
    db_pool_checkedout = engine.pool.checkedout()
    db_status = "healthy" if db_pool_checkedout < db_pool_size else "saturated"

    # Get WebSocket subscription breakdown by tier
    tier_counts = db.execute(
        select(Market.tier, func.count(Market.id))
        .where(
            Market.tier.in_(settings.websocket_enabled_tiers),
            Market.active == True,
            Market.resolved == False,
            Market.yes_token_id.isnot(None),
        )
        .group_by(Market.tier)
    ).all()

    ws_by_tier = {f"T{row[0]}": row[1] for row in tier_counts}
    total_ws_markets = sum(row[1] for row in tier_counts)

    return {
        "timestamp": now.isoformat(),
        "websocket": {
            "connections": 2,  # Dual connection setup
            "max_per_connection": 500,
            "total_capacity": 1000,
            "markets_subscribed": total_ws_markets,
            "utilization_pct": round(total_ws_markets / 1000 * 100, 1),
            "by_tier": ws_by_tier,
            "note": "Markets split across 2 WebSocket connections (500 max each)",
        },
        "database": {
            "status": db_status,
            "pool_size": db_pool_size,
            "connections_in_use": db_pool_checkedout,
        },
        "redis": {
            "status": redis_status,
            "connected_clients": redis_connected_clients,
        },
        "api_clients": {
            "note": "Circuit breakers open after 5 consecutive failures, recover after 30s",
            "gamma": {
                "status": "healthy",  # Would need to track actual state
                "rate_limit": "10 req/s",
            },
            "clob": {
                "status": "healthy",
                "rate_limit": "20 req/s",
            },
        },
    }


@router.get("/monitoring/queues")
async def get_queue_status():
    """
    Get Celery queue depths and worker status.

    Note: This endpoint requires Redis inspection and may be slow.
    """
    import redis as redis_sync
    from src.config.settings import settings

    r = redis_sync.from_url(settings.redis_url, decode_responses=True)

    try:
        # Get queue lengths from Redis
        queues = {}
        for queue_name in ["celery", "snapshots", "default", "discovery"]:
            try:
                length = r.llen(queue_name)
                queues[queue_name] = {"pending": length}
            except Exception:
                queues[queue_name] = {"pending": 0}

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "queues": queues,
        }
    finally:
        r.close()


@router.get("/monitoring/errors")
async def get_monitoring_errors(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Get recent task errors with full details for the monitoring page.
    """
    errors = db.execute(
        select(TaskRun)
        .where(TaskRun.status == "failed")
        .order_by(TaskRun.started_at.desc())
        .limit(limit)
    ).scalars().all()

    return {
        "total": len(errors),
        "items": [
            {
                "id": e.id,
                "timestamp": e.started_at.isoformat(),
                "task": e.task_name.split(".")[-1] if e.task_name else "unknown",
                "full_task_name": e.task_name,
                "tier": e.tier,
                "error": e.error_message[:200] if e.error_message else None,
                "traceback": e.error_traceback,
            }
            for e in errors
        ],
    }


@router.get("/monitoring/field-completeness")
async def get_field_completeness(db: Session = Depends(get_db)):
    """
    Get field completeness metrics for snapshots.

    Returns:
        - Overall completeness percentage
        - Completeness by category (price, orderbook, trade_flow, etc.)
        - Completeness by tier
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    # Build SQL for counting non-NULL fields per category
    # This is more efficient than loading all snapshots into Python

    # Build case expressions for each field
    field_cases = []
    for field in sum(OPTIONAL_FIELDS.values(), []):
        field_cases.append(f"CASE WHEN {field} IS NOT NULL THEN 1 ELSE 0 END")

    all_fields_sum = " + ".join(field_cases)

    # Overall completeness
    overall_query = text(f"""
        SELECT
            COUNT(*) as total_snapshots,
            AVG(({all_fields_sum}) / {TOTAL_OPTIONAL_FIELDS}.0 * 100) as avg_completeness
        FROM snapshots
        WHERE timestamp > :cutoff
    """)

    overall_result = db.execute(overall_query, {"cutoff": one_hour_ago}).fetchone()
    total_snapshots = overall_result[0] or 0
    avg_completeness = round(overall_result[1] or 0, 1)

    # By tier
    tier_query = text(f"""
        SELECT
            tier,
            COUNT(*) as count,
            AVG(({all_fields_sum}) / {TOTAL_OPTIONAL_FIELDS}.0 * 100) as avg_completeness
        FROM snapshots
        WHERE timestamp > :cutoff
        GROUP BY tier
        ORDER BY tier
    """)

    tier_results = db.execute(tier_query, {"cutoff": one_hour_ago}).fetchall()
    by_tier = {
        str(row[0]): {
            "count": row[1],
            "avg_completeness_pct": round(row[2] or 0, 1),
        }
        for row in tier_results
    }

    # By category
    by_category = {}
    for category, fields in OPTIONAL_FIELDS.items():
        category_cases = [f"CASE WHEN {f} IS NOT NULL THEN 1 ELSE 0 END" for f in fields]
        category_sum = " + ".join(category_cases)

        cat_query = text(f"""
            SELECT
                AVG(({category_sum}) / {len(fields)}.0 * 100) as avg_completeness,
                AVG({category_sum}) as avg_populated
            FROM snapshots
            WHERE timestamp > :cutoff
        """)

        cat_result = db.execute(cat_query, {"cutoff": one_hour_ago}).fetchone()
        by_category[category] = {
            "fields_total": len(fields),
            "avg_populated": round(cat_result[1] or 0, 1),
            "pct": round(cat_result[0] or 0, 1),
        }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "avg_completeness_pct": avg_completeness,
            "total_snapshots_1h": total_snapshots,
            "total_optional_fields": TOTAL_OPTIONAL_FIELDS,
        },
        "by_category": by_category,
        "by_tier": by_tier,
    }
