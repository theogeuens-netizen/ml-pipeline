"""
Statistics endpoints.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import Market, Snapshot, Trade
from src.db.redis import RedisClient

router = APIRouter()


@router.get("/stats")
async def get_stats(db: Session = Depends(get_db)):
    """
    Get overall collection statistics.

    Returns:
        - Markets by tier
        - Snapshot counts (today, total)
        - Trade counts (today, total)
        - Database size
        - WebSocket status
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Market counts by tier
    tier_counts = {}
    for tier in range(5):
        count = db.execute(
            select(func.count(Market.id)).where(
                Market.tier == tier,
                Market.active == True,
                Market.resolved == False,
            )
        ).scalar()
        tier_counts[f"tier_{tier}"] = count

    total_markets = sum(tier_counts.values())

    # Total resolved markets
    resolved_count = db.execute(
        select(func.count(Market.id)).where(Market.resolved == True)
    ).scalar()

    # Snapshot counts
    total_snapshots = db.execute(select(func.count(Snapshot.id))).scalar()
    snapshots_today = db.execute(
        select(func.count(Snapshot.id)).where(Snapshot.timestamp >= today_start)
    ).scalar()

    # Trade counts
    total_trades = db.execute(select(func.count(Trade.id))).scalar()
    trades_today = db.execute(
        select(func.count(Trade.id)).where(Trade.timestamp >= today_start)
    ).scalar()

    # Database size
    try:
        result = db.execute(text(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        ))
        db_size = result.scalar()
    except Exception:
        db_size = "unknown"

    # Table sizes
    try:
        table_sizes = {}
        for table in ["markets", "snapshots", "trades", "orderbook_snapshots", "whale_events"]:
            result = db.execute(text(
                f"SELECT pg_size_pretty(pg_total_relation_size('{table}'))"
            ))
            table_sizes[table] = result.scalar()
    except Exception:
        table_sizes = {}

    # WebSocket status
    try:
        redis = RedisClient()
        ws_status = await redis.get_ws_status()
        ws_connected = ws_status["connected_count"]
    except Exception:
        ws_connected = 0

    return {
        "timestamp": now.isoformat(),
        "markets": {
            "total_tracked": total_markets,
            "resolved": resolved_count,
            **tier_counts,
        },
        "snapshots": {
            "total": total_snapshots,
            "today": snapshots_today,
        },
        "trades": {
            "total": total_trades,
            "today": trades_today,
        },
        "database": {
            "size": db_size,
            "tables": table_sizes,
        },
        "websocket": {
            "connected_markets": ws_connected,
        },
    }


@router.get("/stats/collection-rate")
async def get_collection_rate(db: Session = Depends(get_db)):
    """
    Get collection rate over the last hour by tier.
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    rates = {}
    for tier in range(5):
        count = db.execute(
            select(func.count(Snapshot.id)).where(
                Snapshot.tier == tier,
                Snapshot.timestamp >= one_hour_ago,
            )
        ).scalar()
        rates[f"tier_{tier}"] = count

    return {
        "timestamp": now.isoformat(),
        "period": "1h",
        "snapshots_per_hour": rates,
    }
