"""
Health check endpoints.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.redis import RedisClient

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/detailed")
async def detailed_health_check(db: Session = Depends(get_db)):
    """
    Detailed health check including database, Redis, and services.
    """
    checks = {}

    # Database check
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"status": "healthy"}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}

    # Redis check
    try:
        redis = RedisClient()
        await redis.client.ping()
        stats = await redis.get_stats()
        checks["redis"] = {
            "status": "healthy",
            "used_memory": stats["used_memory"],
            "total_keys": stats["total_keys"],
        }
    except Exception as e:
        checks["redis"] = {"status": "unhealthy", "error": str(e)}

    # WebSocket status
    try:
        redis = RedisClient()
        ws_status = await redis.get_ws_status()
        checks["websocket"] = {
            "status": "healthy" if ws_status["connected_count"] > 0 else "idle",
            "connected_markets": ws_status["connected_count"],
        }
    except Exception as e:
        checks["websocket"] = {"status": "unknown", "error": str(e)}

    # Overall status
    all_healthy = all(
        c.get("status") in ("healthy", "idle")
        for c in checks.values()
    )

    return {
        "status": "healthy" if all_healthy else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
