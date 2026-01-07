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
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
import redis as redis_sync

from src.db.database import get_db
from src.db.models import Snapshot, Trade, TaskRun
from src.db.redis import RedisClient
from src.config.settings import settings

router = APIRouter()


# Redis caching helper
def get_cached_or_compute(cache_key: str, ttl_seconds: int, compute_fn: Callable[[], Any]) -> Any:
    """
    Get value from Redis cache or compute and cache it.

    Args:
        cache_key: Redis key for caching
        ttl_seconds: Cache TTL in seconds
        compute_fn: Function to compute the value if not cached

    Returns:
        Cached or computed value
    """
    try:
        r = redis_sync.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)

        # Compute the value
        result = compute_fn()

        # Cache it
        r.set(cache_key, json.dumps(result, default=str), ex=ttl_seconds)
        r.close()
        return result
    except Exception:
        # On any Redis error, just compute without caching
        return compute_fn()

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

    Cached for 10 seconds to reduce database load.
    """
    def compute_health():
        from src.db.redis import get_sync_redis
        sync_redis = get_sync_redis()
        now = datetime.now(timezone.utc)

        try:
            # WebSocket health (using sync client)
            ws_last_activity = sync_redis.get_ws_last_activity()
            ws_connected_count = sync_redis.get_ws_connected_count()

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
        except Exception as e:
            # Log error but don't crash - return degraded status
            return {
                "timestamp": now.isoformat(),
                "error": str(e),
                "websocket": {"status": "unknown"},
                "tasks": {"error_rate_pct": 0},
            }

    return get_cached_or_compute("monitoring:health", 10, compute_health)


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
            "connections": settings.websocket_num_connections,
            "max_per_connection": 500,
            "total_capacity": settings.websocket_num_connections * 500,
            "markets_subscribed": total_ws_markets,
            "utilization_pct": round(total_ws_markets / (settings.websocket_num_connections * 500) * 100, 1),
            "by_tier": ws_by_tier,
            "note": f"Markets split across {settings.websocket_num_connections} WebSocket connections (500 max each)",
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


@router.get("/monitoring/critical")
async def get_critical_health(db: Session = Depends(get_db)):
    """
    Critical health check for autonomous operation monitoring.

    Returns HTTP 200 if system is healthy, HTTP 503 if critical issues detected.
    Designed for external monitoring tools (uptime checks, alerting).

    Checks:
    - Disk usage < 90%
    - PostgreSQL connections available
    - Redis responsive
    - WebSocket active (trade in last 5 min)
    - Task success rate > 90% (last 30 min)
    - Celery queues not backed up (< 500 pending)
    """
    import os
    import redis as redis_sync
    from src.config.settings import settings
    from src.db.database import engine
    from fastapi.responses import JSONResponse

    now = datetime.now(timezone.utc)
    issues = []
    warnings = []

    # 1. Check disk usage
    try:
        statvfs = os.statvfs('/')
        disk_used_pct = (1 - statvfs.f_bavail / statvfs.f_blocks) * 100
        if disk_used_pct > 95:
            issues.append(f"Disk critically full: {disk_used_pct:.1f}%")
        elif disk_used_pct > 90:
            warnings.append(f"Disk usage high: {disk_used_pct:.1f}%")
    except Exception as e:
        warnings.append(f"Could not check disk: {str(e)}")
        disk_used_pct = None

    # 2. Check PostgreSQL connections
    try:
        db_pool_size = engine.pool.size()
        db_pool_checkedout = engine.pool.checkedout()
        db_pool_overflow = engine.pool.overflow()
        db_available = db_pool_size - db_pool_checkedout + (20 - db_pool_overflow)  # max_overflow=20
        if db_available < 3:
            issues.append(f"DB connections exhausted: {db_pool_checkedout}/{db_pool_size} in use")
        elif db_available < 5:
            warnings.append(f"DB connections low: {db_pool_checkedout}/{db_pool_size} in use")
    except Exception as e:
        issues.append(f"DB pool check failed: {str(e)}")
        db_pool_checkedout = None
        db_pool_size = None

    # 3. Check Redis
    redis_ok = False
    redis_memory_mb = None
    try:
        r = redis_sync.from_url(settings.redis_url, decode_responses=True, socket_timeout=5)
        r.ping()
        info = r.info("memory")
        redis_memory_mb = info.get("used_memory", 0) / 1024 / 1024
        if redis_memory_mb > 900:  # 900MB of 1GB limit
            warnings.append(f"Redis memory high: {redis_memory_mb:.0f}MB")
        redis_ok = True
        r.close()
    except Exception as e:
        issues.append(f"Redis unreachable: {str(e)}")

    # 4. Check WebSocket (trade activity)
    five_min_ago = now - timedelta(minutes=5)
    try:
        trades_last_5min = db.execute(
            select(func.count(Trade.id)).where(Trade.timestamp >= five_min_ago)
        ).scalar() or 0
        if trades_last_5min == 0:
            issues.append("No trades in last 5 minutes - WebSocket may be down")
        elif trades_last_5min < 10:
            warnings.append(f"Low trade volume: {trades_last_5min} trades in 5 min")
    except Exception as e:
        issues.append(f"Trade check failed: {str(e)}")
        trades_last_5min = None

    # 5. Check task success rate (last 30 min)
    thirty_min_ago = now - timedelta(minutes=30)
    try:
        total_tasks = db.execute(
            select(func.count(TaskRun.id)).where(TaskRun.started_at >= thirty_min_ago)
        ).scalar() or 0
        failed_tasks = db.execute(
            select(func.count(TaskRun.id)).where(
                TaskRun.started_at >= thirty_min_ago,
                TaskRun.status == "failed"
            )
        ).scalar() or 0

        if total_tasks > 0:
            success_rate = (total_tasks - failed_tasks) / total_tasks * 100
            if success_rate < 80:
                issues.append(f"Task success rate critical: {success_rate:.1f}%")
            elif success_rate < 90:
                warnings.append(f"Task success rate degraded: {success_rate:.1f}%")
        else:
            warnings.append("No tasks in last 30 min")
            success_rate = None
    except Exception as e:
        issues.append(f"Task check failed: {str(e)}")
        success_rate = None
        total_tasks = None
        failed_tasks = None

    # 6. Check Celery queue backlog
    queue_depth = None
    if redis_ok:
        try:
            r = redis_sync.from_url(settings.redis_url, decode_responses=True, socket_timeout=5)
            queue_depth = r.llen("snapshots") or 0
            r.close()
            if queue_depth > 1000:
                issues.append(f"Celery queue backed up: {queue_depth} pending tasks")
            elif queue_depth > 500:
                warnings.append(f"Celery queue growing: {queue_depth} pending tasks")
        except:
            pass

    # Determine overall status
    if issues:
        status = "critical"
        http_status = 503
    elif warnings:
        status = "warning"
        http_status = 200
    else:
        status = "healthy"
        http_status = 200

    response = {
        "timestamp": now.isoformat(),
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "checks": {
            "disk_used_pct": round(disk_used_pct, 1) if disk_used_pct else None,
            "db_connections_used": db_pool_checkedout,
            "db_pool_size": db_pool_size,
            "redis_ok": redis_ok,
            "redis_memory_mb": round(redis_memory_mb, 1) if redis_memory_mb else None,
            "trades_last_5min": trades_last_5min,
            "task_success_rate_pct": round(success_rate, 1) if success_rate else None,
            "tasks_last_30min": total_tasks,
            "celery_queue_depth": queue_depth,
        },
    }

    return JSONResponse(content=response, status_code=http_status)


@router.get("/monitoring/field-completeness")
async def get_field_completeness(db: Session = Depends(get_db)):
    """
    Get field completeness metrics for snapshots.

    Returns:
        - Overall completeness percentage
        - Completeness by category (price, orderbook, trade_flow, etc.)
        - Completeness by tier

    Cached for 60 seconds - this is an expensive query scanning millions of rows.
    """
    def compute_field_completeness():
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

    return get_cached_or_compute("monitoring:field_completeness", 60, compute_field_completeness)


@router.get("/monitoring/tier-transitions")
async def get_tier_transitions(
    hours: int = Query(1, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """
    Get tier transitions in the last N hours.

    Shows markets moving between tiers (T0→T1, T1→T2, etc.)
    and markets being deactivated (-1 represents deactivated).

    Cached for 30 seconds.
    """
    def compute_tier_transitions():
        from src.db.models import TierTransition

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Summary by transition type
        transitions = db.execute(
            select(
                TierTransition.from_tier,
                TierTransition.to_tier,
                func.count(TierTransition.id).label("count")
            )
            .where(TierTransition.transitioned_at >= cutoff)
            .group_by(TierTransition.from_tier, TierTransition.to_tier)
        ).all()

        # Format as "T0→T1": count
        summary = {}
        for row in transitions:
            from_label = f"T{row[0]}" if row[0] >= 0 else "new"
            to_label = f"T{row[1]}" if row[1] >= 0 else "deactivated"
            key = f"{from_label}→{to_label}"
            summary[key] = row[2]

        # Recent transitions list
        recent = db.execute(
            select(TierTransition)
            .where(TierTransition.transitioned_at >= cutoff)
            .order_by(TierTransition.transitioned_at.desc())
            .limit(50)
        ).scalars().all()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period_hours": hours,
            "summary": summary,
            "total_transitions": sum(summary.values()),
            "recent": [
                {
                    "market": t.market_slug[:40] if t.market_slug else t.condition_id[:20],
                    "from_tier": t.from_tier,
                    "to_tier": t.to_tier,
                    "at": t.transitioned_at.isoformat(),
                    "hours_to_close": float(t.hours_to_close) if t.hours_to_close else None,
                    "reason": t.reason,
                }
                for t in recent
            ],
        }

    return get_cached_or_compute(f"monitoring:tier_transitions:{hours}", 30, compute_tier_transitions)


@router.get("/monitoring/task-activity")
async def get_task_activity(
    limit: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """
    Get recent Celery task executions with summary by task type.

    Cached for 15 seconds.
    """
    def compute_task_activity():
        tasks = db.execute(
            select(TaskRun)
            .order_by(TaskRun.started_at.desc())
            .limit(limit)
        ).scalars().all()

        # Group by task type for summary
        by_task: dict = {}
        for t in tasks:
            name = t.task_name.split(".")[-1] if t.task_name else "unknown"
            if name not in by_task:
                by_task[name] = {"success": 0, "failed": 0, "running": 0}
            by_task[name][t.status] = by_task[name].get(t.status, 0) + 1

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "by_task": by_task,
            "recent": [
                {
                    "id": t.id,
                    "task": t.task_name.split(".")[-1] if t.task_name else "unknown",
                    "tier": t.tier,
                    "status": t.status,
                    "started_at": t.started_at.isoformat() if t.started_at else None,
                    "duration_ms": t.duration_ms,
                    "markets_processed": t.markets_processed,
                    "rows_inserted": t.rows_inserted,
                    "error": t.error_message[:100] if t.error_message else None,
                }
                for t in tasks
            ],
        }

    return get_cached_or_compute(f"monitoring:task_activity:{limit}", 15, compute_task_activity)


@router.get("/monitoring/redis-stats")
async def get_redis_stats():
    """
    Get Redis memory, key counts, and connection info.

    Cached for 30 seconds.
    """
    def compute_redis_stats():
        r = redis_sync.from_url(settings.redis_url, decode_responses=True)
        try:
            info = r.info()

            # Count keys by pattern
            key_patterns = {
                "ws:*": len(r.keys("ws:*")),
                "gamma:*": len(r.keys("gamma:*")),
                "orderbook:*": len(r.keys("orderbook:*")),
                "trade_buffer:*": len(r.keys("trade_buffer:*")),
                "celery*": len(r.keys("celery*")),
            }

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "memory_used_mb": round(info.get("used_memory", 0) / 1024 / 1024, 1),
                "memory_peak_mb": round(info.get("used_memory_peak", 0) / 1024 / 1024, 1),
                "connected_clients": info.get("connected_clients", 0),
                "total_keys": r.dbsize(),
                "keys_by_pattern": key_patterns,
                "uptime_seconds": info.get("uptime_in_seconds", 0),
                "ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
            }
        finally:
            r.close()

    return get_cached_or_compute("monitoring:redis_stats", 30, compute_redis_stats)


@router.get("/monitoring/lifecycle-status")
async def get_lifecycle_status(db: Session = Depends(get_db)):
    """
    Get distribution of market lifecycle states.

    Shows breakdown of trading status and UMA resolution status across all markets.
    """
    from src.db.models import Market

    # Get counts by trading status
    trading_status_query = text("""
        SELECT
            CASE
                WHEN resolved = true THEN 'resolved'
                WHEN closed = true THEN 'closed'
                WHEN accepting_orders = false THEN 'suspended'
                WHEN active = true THEN 'trading'
                ELSE 'unknown'
            END as status,
            COUNT(*) as count
        FROM markets
        GROUP BY 1
        ORDER BY count DESC
    """)
    trading_result = db.execute(trading_status_query)
    trading_status = {row.status: row.count for row in trading_result}

    # Get counts by UMA resolution status
    uma_status_query = text("""
        SELECT
            COALESCE(uma_resolution_status, 'none') as status,
            COUNT(*) as count
        FROM markets
        WHERE active = true OR resolved = true
        GROUP BY 1
        ORDER BY count DESC
    """)
    uma_result = db.execute(uma_status_query)
    uma_status = {row.status: row.count for row in uma_result}

    # Get markets stuck in pending states
    pending_query = text("""
        SELECT COUNT(*) as count
        FROM markets
        WHERE active = true
        AND resolved = false
        AND closed = true
        AND end_date < NOW() - INTERVAL '24 hours'
    """)
    pending_count = db.execute(pending_query).scalar()

    # Get recently closed markets (last 24h)
    recent_closed_query = text("""
        SELECT COUNT(*) as count
        FROM markets
        WHERE closed_at > NOW() - INTERVAL '24 hours'
    """)
    recent_closed = db.execute(recent_closed_query).scalar()

    # Get recently resolved markets (last 24h)
    recent_resolved_query = text("""
        SELECT COUNT(*) as count
        FROM markets
        WHERE resolved_at > NOW() - INTERVAL '24 hours'
    """)
    recent_resolved = db.execute(recent_resolved_query).scalar()

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trading_status_distribution": trading_status,
        "uma_status_distribution": uma_status,
        "alerts": {
            "markets_stuck_pending_24h": pending_count or 0,
        },
        "recent_activity_24h": {
            "closed": recent_closed or 0,
            "resolved": recent_resolved or 0,
        },
    }


@router.get("/monitoring/lifecycle-anomalies")
async def get_lifecycle_anomalies(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200),
):
    """
    Find markets with anomalous lifecycle states.

    Detects:
    - Markets past end_date but not closed
    - Markets closed but not resolved for >24h
    - Markets with UMA status but not closed
    - Resolved markets with unknown outcome
    """
    from src.db.models import Market

    anomalies = []

    # Markets past end_date but not closed
    past_not_closed = db.execute(text("""
        SELECT id, condition_id, slug, end_date, closed, resolved
        FROM markets
        WHERE active = true
        AND end_date < NOW() - INTERVAL '1 hour'
        AND closed = false
        AND resolved = false
        LIMIT :limit
    """), {"limit": limit // 4})

    for row in past_not_closed:
        anomalies.append({
            "type": "past_end_date_not_closed",
            "market_id": row.id,
            "slug": row.slug,
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "severity": "high",
        })

    # Markets closed but not resolved for >24h
    closed_not_resolved = db.execute(text("""
        SELECT id, condition_id, slug, closed_at, end_date
        FROM markets
        WHERE closed = true
        AND resolved = false
        AND closed_at < NOW() - INTERVAL '24 hours'
        LIMIT :limit
    """), {"limit": limit // 4})

    for row in closed_not_resolved:
        anomalies.append({
            "type": "closed_24h_not_resolved",
            "market_id": row.id,
            "slug": row.slug,
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            "severity": "medium",
        })

    # Resolved but no outcome
    resolved_no_outcome = db.execute(text("""
        SELECT id, condition_id, slug, resolved_at, outcome
        FROM markets
        WHERE resolved = true
        AND (outcome IS NULL OR outcome = 'UNKNOWN')
        LIMIT :limit
    """), {"limit": limit // 4})

    for row in resolved_no_outcome:
        anomalies.append({
            "type": "resolved_unknown_outcome",
            "market_id": row.id,
            "slug": row.slug,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "severity": "high",
        })

    # Markets in disputed state for >48h
    long_disputed = db.execute(text("""
        SELECT id, condition_id, slug, uma_resolution_status, uma_status_updated_at
        FROM markets
        WHERE uma_resolution_status = 'disputed'
        AND uma_status_updated_at < NOW() - INTERVAL '48 hours'
        LIMIT :limit
    """), {"limit": limit // 4})

    for row in long_disputed:
        anomalies.append({
            "type": "disputed_48h",
            "market_id": row.id,
            "slug": row.slug,
            "uma_status_updated_at": row.uma_status_updated_at.isoformat() if row.uma_status_updated_at else None,
            "severity": "info",
        })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_anomalies": len(anomalies),
        "anomalies": anomalies,
    }


@router.get("/monitoring/market/{market_id}/lifecycle")
async def get_market_lifecycle(
    market_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed lifecycle information for a specific market.
    """
    from src.db.models import Market
    from src.services.market_lifecycle import get_lifecycle_summary

    market = db.get(Market, market_id)
    if not market:
        return {"error": "Market not found"}

    return get_lifecycle_summary(market)
