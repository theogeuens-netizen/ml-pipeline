"""
CSGO Engine API Endpoints.

Provides health check, stats, and debugging endpoints.
Can be mounted to main FastAPI app or run standalone.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db.database import get_session
from src.csgo.engine.models import (
    CSGOPosition,
    CSGOPositionStatus,
    CSGOSpread,
    CSGOSpreadStatus,
    CSGOTrade,
    CSGOStrategyState,
    CSGOStrategyMarketState,
)
from src.csgo.signals import get_stream_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/csgo-engine", tags=["csgo-engine"])


# =========================================================================
# Response Models
# =========================================================================


class HealthResponse(BaseModel):
    status: str
    message: str


class StrategyStateResponse(BaseModel):
    strategy_name: str
    allocated_usd: float
    available_usd: float
    total_realized_pnl: float
    total_unrealized_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Optional[float]
    is_active: bool


class PositionResponse(BaseModel):
    id: int
    strategy_name: str
    market_id: int
    token_type: str
    remaining_shares: float
    avg_entry_price: float
    current_price: Optional[float]
    unrealized_pnl: Optional[float]
    realized_pnl: float
    team_yes: Optional[str]
    team_no: Optional[str]
    format: Optional[str]
    cost_basis: float
    entry_spread: Optional[float]  # Spread at entry (best_ask - best_bid)
    opened_at: Optional[str]
    status: str


class SpreadResponse(BaseModel):
    id: int
    strategy_name: str
    market_id: int
    spread_type: str
    total_cost_basis: float
    total_realized_pnl: float
    total_unrealized_pnl: float
    team_yes: Optional[str]
    team_no: Optional[str]
    status: str


class TradeResponse(BaseModel):
    id: int
    position_id: int
    side: str
    shares: float
    price: float
    cost_usd: float
    slippage: Optional[float]
    # Orderbook state
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread: Optional[float]
    # Match context
    team_yes: Optional[str]
    team_no: Optional[str]
    format: Optional[str]
    map_number: Optional[int]
    game_start_time: Optional[str]
    created_at: str


class StatsResponse(BaseModel):
    positions_open: int
    positions_closed: int
    spreads_open: int
    spreads_closed: int
    total_trades: int
    strategies_active: int
    total_realized_pnl: float
    total_unrealized_pnl: float
    stream_length: int


# =========================================================================
# Health Check
# =========================================================================


@router.get("/health", response_model=HealthResponse)
async def health():
    """Check if the CSGO engine is healthy."""
    try:
        # Check database connection
        from sqlalchemy import text
        with get_session() as db:
            db.execute(text("SELECT 1"))

        # Check Redis stream
        stream_stats = await get_stream_stats()

        if stream_stats.get("error"):
            return HealthResponse(
                status="degraded",
                message=f"Redis stream issue: {stream_stats.get('error')}",
            )

        return HealthResponse(
            status="healthy",
            message=f"Stream length: {stream_stats.get('length', 0)}",
        )

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="unhealthy",
            message=str(e),
        )


# =========================================================================
# Statistics
# =========================================================================


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get overall CSGO engine statistics. P&L from positions (source of truth)."""
    try:
        from sqlalchemy import func

        with get_session() as db:
            # Position counts
            positions_open = db.query(CSGOPosition).filter(
                CSGOPosition.status == CSGOPositionStatus.OPEN.value
            ).count()

            positions_closed = db.query(CSGOPosition).filter(
                CSGOPosition.status == CSGOPositionStatus.CLOSED.value
            ).count()

            # Spread counts
            spreads_open = db.query(CSGOSpread).filter(
                CSGOSpread.status == CSGOSpreadStatus.OPEN.value
            ).count()

            spreads_closed = db.query(CSGOSpread).filter(
                CSGOSpread.status == CSGOSpreadStatus.CLOSED.value
            ).count()

            # Trade count
            total_trades = db.query(CSGOTrade).count()

            # Strategy count
            strategies_active = db.query(CSGOStrategyState).filter(
                CSGOStrategyState.is_active == True
            ).count()

            # P&L from positions (source of truth)
            # Realized = sum of realized_pnl from closed positions
            realized_result = db.query(
                func.sum(CSGOPosition.realized_pnl)
            ).filter(
                CSGOPosition.status == "closed"
            ).scalar()
            total_realized_pnl = float(realized_result or 0)

            # Unrealized = sum of unrealized_pnl from open positions
            unrealized_result = db.query(
                func.sum(CSGOPosition.unrealized_pnl)
            ).filter(
                CSGOPosition.status == "open"
            ).scalar()
            total_unrealized_pnl = float(unrealized_result or 0)

        # Stream stats
        stream_stats = await get_stream_stats()

        return StatsResponse(
            positions_open=positions_open,
            positions_closed=positions_closed,
            spreads_open=spreads_open,
            spreads_closed=spreads_closed,
            total_trades=total_trades,
            strategies_active=strategies_active,
            total_realized_pnl=total_realized_pnl,
            total_unrealized_pnl=total_unrealized_pnl,
            stream_length=stream_stats.get("length", 0),
        )

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Strategies
# =========================================================================


@router.get("/strategies", response_model=List[StrategyStateResponse])
async def list_strategies():
    """List all CSGO strategy states."""
    try:
        from sqlalchemy import func

        with get_session() as db:
            strategies = db.query(CSGOStrategyState).all()

            # Calculate unrealized P&L from open positions for each strategy
            unrealized_by_strategy = {}
            realized_by_strategy = {}

            # Get sum of unrealized P&L from open positions
            open_pnl = db.query(
                CSGOPosition.strategy_name,
                func.sum(CSGOPosition.unrealized_pnl).label("unrealized"),
            ).filter(
                CSGOPosition.status == "open"
            ).group_by(CSGOPosition.strategy_name).all()

            for row in open_pnl:
                unrealized_by_strategy[row.strategy_name] = float(row.unrealized or 0)

            # Get sum of realized P&L from closed positions
            closed_pnl = db.query(
                CSGOPosition.strategy_name,
                func.sum(CSGOPosition.realized_pnl).label("realized"),
            ).filter(
                CSGOPosition.status == "closed"
            ).group_by(CSGOPosition.strategy_name).all()

            for row in closed_pnl:
                realized_by_strategy[row.strategy_name] = float(row.realized or 0)

            return [
                StrategyStateResponse(
                    strategy_name=s.strategy_name,
                    allocated_usd=float(s.allocated_usd or 0),
                    available_usd=float(s.available_usd or 0),
                    total_realized_pnl=realized_by_strategy.get(s.strategy_name, 0),
                    total_unrealized_pnl=unrealized_by_strategy.get(s.strategy_name, 0),
                    trade_count=s.trade_count or 0,
                    win_count=s.win_count or 0,
                    loss_count=s.loss_count or 0,
                    win_rate=(
                        (s.win_count / (s.win_count + s.loss_count))
                        if (s.win_count or 0) + (s.loss_count or 0) > 0
                        else None
                    ),
                    is_active=s.is_active,
                )
                for s in strategies
            ]

    except Exception as e:
        logger.error(f"Failed to list strategies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Positions
# =========================================================================


@router.get("/positions", response_model=List[PositionResponse])
async def list_positions(
    status: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 100,
):
    """List CSGO positions with optional filters."""
    try:
        with get_session() as db:
            query = db.query(CSGOPosition)

            if status:
                query = query.filter(CSGOPosition.status == status)

            if strategy:
                query = query.filter(CSGOPosition.strategy_name == strategy)

            positions = query.order_by(CSGOPosition.id.desc()).limit(limit).all()

            results = []
            for p in positions:
                # Get entry spread from first trade
                entry_spread = None
                first_trade = db.query(CSGOTrade).filter(
                    CSGOTrade.position_id == p.id
                ).order_by(CSGOTrade.id.asc()).first()
                if first_trade and first_trade.spread:
                    entry_spread = float(first_trade.spread)

                results.append(PositionResponse(
                    id=p.id,
                    strategy_name=p.strategy_name,
                    market_id=p.market_id,
                    token_type=p.token_type,
                    remaining_shares=float(p.remaining_shares),
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(p.current_price) if p.current_price else None,
                    unrealized_pnl=float(p.unrealized_pnl) if p.unrealized_pnl else None,
                    realized_pnl=float(p.realized_pnl or 0),
                    team_yes=p.team_yes,
                    team_no=p.team_no,
                    format=p.format,
                    cost_basis=float(p.cost_basis),
                    entry_spread=entry_spread,
                    opened_at=p.opened_at.isoformat() if p.opened_at else None,
                    status=p.status,
                ))

            return results

    except Exception as e:
        logger.error(f"Failed to list positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Spreads
# =========================================================================


@router.get("/spreads", response_model=List[SpreadResponse])
async def list_spreads(
    status: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 100,
):
    """List CSGO spreads with optional filters. P&L calculated from positions (source of truth)."""
    try:
        with get_session() as db:
            query = db.query(CSGOSpread)

            if status:
                query = query.filter(CSGOSpread.status == status)

            if strategy:
                query = query.filter(CSGOSpread.strategy_name == strategy)

            spreads = query.order_by(CSGOSpread.id.desc()).limit(limit).all()

            # Get all position IDs we need
            position_ids = set()
            for s in spreads:
                if s.yes_position_id:
                    position_ids.add(s.yes_position_id)
                if s.no_position_id:
                    position_ids.add(s.no_position_id)

            # Fetch positions in one query
            positions_by_id = {}
            if position_ids:
                positions = db.query(CSGOPosition).filter(
                    CSGOPosition.id.in_(position_ids)
                ).all()
                positions_by_id = {p.id: p for p in positions}

            results = []
            for s in spreads:
                # Calculate P&L from linked positions (source of truth)
                total_cost = 0.0
                total_realized = 0.0
                total_unrealized = 0.0

                yes_pos = positions_by_id.get(s.yes_position_id)
                no_pos = positions_by_id.get(s.no_position_id)

                if yes_pos:
                    total_cost += float(yes_pos.cost_basis or 0)
                    total_realized += float(yes_pos.realized_pnl or 0)
                    # Only add unrealized if position is open
                    if yes_pos.status == "open":
                        total_unrealized += float(yes_pos.unrealized_pnl or 0)

                if no_pos:
                    total_cost += float(no_pos.cost_basis or 0)
                    total_realized += float(no_pos.realized_pnl or 0)
                    if no_pos.status == "open":
                        total_unrealized += float(no_pos.unrealized_pnl or 0)

                results.append(SpreadResponse(
                    id=s.id,
                    strategy_name=s.strategy_name,
                    market_id=s.market_id,
                    spread_type=s.spread_type,
                    total_cost_basis=total_cost,
                    total_realized_pnl=total_realized,
                    total_unrealized_pnl=total_unrealized,
                    team_yes=s.team_yes,
                    team_no=s.team_no,
                    status=s.status,
                ))

            return results

    except Exception as e:
        logger.error(f"Failed to list spreads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Trades
# =========================================================================


@router.get("/trades", response_model=List[TradeResponse])
async def list_trades(
    position_id: Optional[int] = None,
    limit: int = 100,
):
    """List recent CSGO trades."""
    try:
        with get_session() as db:
            query = db.query(CSGOTrade)

            if position_id:
                query = query.filter(CSGOTrade.position_id == position_id)

            trades = query.order_by(CSGOTrade.id.desc()).limit(limit).all()

            return [
                TradeResponse(
                    id=t.id,
                    position_id=t.position_id,
                    side=t.side,
                    shares=float(t.shares),
                    price=float(t.price),
                    cost_usd=float(t.cost_usd),
                    slippage=float(t.slippage) if t.slippage else None,
                    # Orderbook state
                    best_bid=float(t.best_bid) if t.best_bid else None,
                    best_ask=float(t.best_ask) if t.best_ask else None,
                    spread=float(t.spread) if t.spread else None,
                    # Match context
                    team_yes=t.team_yes,
                    team_no=t.team_no,
                    format=t.format,
                    map_number=t.map_number,
                    game_start_time=t.game_start_time.isoformat() if t.game_start_time else None,
                    created_at=t.created_at.isoformat() if t.created_at else "",
                )
                for t in trades
            ]

    except Exception as e:
        logger.error(f"Failed to list trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Stream Stats
# =========================================================================


@router.get("/stream")
async def get_stream_info():
    """Get Redis stream statistics."""
    try:
        stats = await get_stream_stats()
        return stats
    except Exception as e:
        logger.error(f"Failed to get stream stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
