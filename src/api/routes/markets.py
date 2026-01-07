"""
Market data endpoints.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import Market, Snapshot

router = APIRouter()


@router.get("/markets")
async def list_markets(
    tier: Optional[int] = Query(None, ge=0, le=4, description="Filter by tier"),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    resolved: Optional[bool] = Query(None, description="Filter by resolved status"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search in question"),
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
):
    """
    List markets with filtering and pagination.
    """
    query = select(Market)

    # Apply filters
    if tier is not None:
        query = query.where(Market.tier == tier)
    if active is not None:
        query = query.where(Market.active == active)
    if resolved is not None:
        query = query.where(Market.resolved == resolved)
    if category is not None:
        query = query.where(Market.category == category)
    if search:
        query = query.where(Market.question.ilike(f"%{search}%"))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination and ordering
    query = query.order_by(desc(Market.updated_at)).offset(offset).limit(limit)

    markets = db.execute(query).scalars().all()

    # Format response
    items = []
    for m in markets:
        items.append({
            "id": m.id,
            "condition_id": m.condition_id,
            "slug": m.slug,
            "question": m.question,
            "tier": m.tier,
            "active": m.active,
            "resolved": m.resolved,
            "outcome": m.outcome,
            "initial_price": float(m.initial_price) if m.initial_price else None,
            "snapshot_count": m.snapshot_count,
            "last_snapshot_at": m.last_snapshot_at.isoformat() if m.last_snapshot_at else None,
            "end_date": m.end_date.isoformat() if m.end_date else None,
            "category": m.category,
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/markets/{market_id}")
async def get_market(
    market_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed market information including recent snapshots.
    """
    market = db.get(Market, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    # Get recent snapshots
    snapshots_query = (
        select(Snapshot)
        .where(Snapshot.market_id == market_id)
        .order_by(desc(Snapshot.timestamp))
        .limit(100)
    )
    snapshots = db.execute(snapshots_query).scalars().all()

    # Calculate hours to close
    hours_to_close = None
    if market.end_date:
        delta = market.end_date - datetime.now(timezone.utc)
        hours_to_close = delta.total_seconds() / 3600

    return {
        "id": market.id,
        "condition_id": market.condition_id,
        "slug": market.slug,
        "question": market.question,
        "description": market.description,
        "tier": market.tier,
        "active": market.active,
        "resolved": market.resolved,
        "outcome": market.outcome,
        "resolved_at": market.resolved_at.isoformat() if market.resolved_at else None,
        "initial_price": float(market.initial_price) if market.initial_price else None,
        "initial_volume": float(market.initial_volume) if market.initial_volume else None,
        "initial_liquidity": float(market.initial_liquidity) if market.initial_liquidity else None,
        "snapshot_count": market.snapshot_count,
        "last_snapshot_at": market.last_snapshot_at.isoformat() if market.last_snapshot_at else None,
        "tracking_started_at": market.tracking_started_at.isoformat() if market.tracking_started_at else None,
        "end_date": market.end_date.isoformat() if market.end_date else None,
        "hours_to_close": hours_to_close,
        "category": market.category,
        "event_id": market.event_id,
        "event_title": market.event_title,
        "yes_token_id": market.yes_token_id,
        "recent_snapshots": [
            {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "price": float(s.price) if s.price else None,
                "spread": float(s.spread) if s.spread else None,
                "volume_24h": float(s.volume_24h) if s.volume_24h else None,
                "book_imbalance": float(s.book_imbalance) if s.book_imbalance else None,
                "trade_count_1h": s.trade_count_1h,
                "whale_count_1h": s.whale_count_1h,
            }
            for s in snapshots
        ],
    }


@router.get("/markets/{market_id}/snapshots")
async def get_market_snapshots(
    market_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get snapshots for a specific market.
    """
    market = db.get(Market, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    # Get total count
    total = db.execute(
        select(func.count(Snapshot.id)).where(Snapshot.market_id == market_id)
    ).scalar()

    # Get snapshots
    query = (
        select(Snapshot)
        .where(Snapshot.market_id == market_id)
        .order_by(desc(Snapshot.timestamp))
        .offset(offset)
        .limit(limit)
    )
    snapshots = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "tier": s.tier,
                # Price
                "price": float(s.price) if s.price else None,
                "best_bid": float(s.best_bid) if s.best_bid else None,
                "best_ask": float(s.best_ask) if s.best_ask else None,
                "spread": float(s.spread) if s.spread else None,
                # Momentum
                "price_change_1d": float(s.price_change_1d) if s.price_change_1d else None,
                "price_change_1w": float(s.price_change_1w) if s.price_change_1w else None,
                # Volume
                "volume_24h": float(s.volume_24h) if s.volume_24h else None,
                "liquidity": float(s.liquidity) if s.liquidity else None,
                # Orderbook
                "book_imbalance": float(s.book_imbalance) if s.book_imbalance else None,
                "bid_depth_10": float(s.bid_depth_10) if s.bid_depth_10 else None,
                "ask_depth_10": float(s.ask_depth_10) if s.ask_depth_10 else None,
                # Trade flow
                "trade_count_1h": s.trade_count_1h,
                "volume_1h": float(s.volume_1h) if s.volume_1h else None,
                "vwap_1h": float(s.vwap_1h) if s.vwap_1h else None,
                # Whales
                "whale_count_1h": s.whale_count_1h,
                "whale_net_flow_1h": float(s.whale_net_flow_1h) if s.whale_net_flow_1h else None,
                # Context
                "hours_to_close": float(s.hours_to_close) if s.hours_to_close else None,
            }
            for s in snapshots
        ],
    }
