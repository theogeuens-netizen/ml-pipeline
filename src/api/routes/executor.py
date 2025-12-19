"""
Executor API endpoints.

Provides endpoints for:
- Executor status
- Positions
- Trades
- Signals
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.executor.models import (
    Signal,
    ExecutorOrder,
    ExecutorTrade,
    Position,
    PaperBalance,
    TradeDecision,
    PositionStatus,
    SignalStatus,
    OrderStatus,
)
from src.executor.config import get_config, TradingMode
from src.executor.execution import get_executor

router = APIRouter(prefix="/executor")


@router.get("/status")
async def get_executor_status():
    """
    Get executor status including mode, balance, and statistics.
    """
    config = get_config()
    executor = get_executor(config)

    stats = executor.get_stats()

    return {
        "mode": config.mode.value,
        "running": True,  # Would need to check runner status
        "balance": stats.get("balance", 0),
        "total_value": executor.get_total_value(),
        "stats": stats,
        "enabled_strategies": [
            name for name, cfg in config.strategies.items()
            if cfg.enabled
        ],
        "risk_limits": {
            "max_position_usd": config.risk.max_position_usd,
            "max_total_exposure_usd": config.risk.max_total_exposure_usd,
            "max_positions": config.risk.max_positions,
            "max_drawdown_pct": config.risk.max_drawdown_pct,
        },
    }


@router.get("/positions")
async def list_positions(
    status: Optional[str] = Query(None, description="Filter by status (open/closed)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    is_paper: Optional[bool] = Query(None, description="Filter by paper/live"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List positions with filtering and pagination.
    """
    query = select(Position)

    # Apply filters
    if status:
        status_value = PositionStatus.OPEN.value if status.lower() == "open" else PositionStatus.CLOSED.value
        query = query.where(Position.status == status_value)
    if strategy:
        query = query.where(Position.strategy_name == strategy)
    if is_paper is not None:
        query = query.where(Position.is_paper == is_paper)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination and ordering
    query = query.order_by(desc(Position.entry_time)).offset(offset).limit(limit)
    positions = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_format_position(p) for p in positions],
    }


@router.get("/positions/{position_id}")
async def get_position(
    position_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed position information.
    """
    position = db.get(Position, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")

    return _format_position(position)


@router.post("/positions/{position_id}/close")
async def close_position(
    position_id: int,
    exit_price: Optional[float] = Query(None, description="Exit price (paper mode)"),
    reason: str = Query("manual", description="Reason for closing"),
    db: Session = Depends(get_db),
):
    """
    Close a position.
    """
    position = db.get(Position, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")

    if position.status != PositionStatus.OPEN.value:
        raise HTTPException(status_code=400, detail="Position is not open")

    config = get_config()
    executor = get_executor(config)

    result = executor.close_position(
        position_id=position_id,
        exit_price=exit_price,
        reason=reason,
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)

    # Refresh position
    db.refresh(position)

    return {
        "success": True,
        "message": result.message,
        "position": _format_position(position),
    }


@router.get("/signals")
async def list_signals(
    status: Optional[str] = Query(None, description="Filter by status"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List signals with filtering and pagination.
    """
    query = select(Signal)

    # Apply filters
    if status:
        query = query.where(Signal.status == status)
    if strategy:
        query = query.where(Signal.strategy_name == strategy)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination and ordering
    query = query.order_by(desc(Signal.created_at)).offset(offset).limit(limit)
    signals = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_format_signal(s) for s in signals],
    }


@router.get("/signals/{signal_id}")
async def get_signal(
    signal_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed signal information.
    """
    signal = db.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    return _format_signal(signal)


@router.get("/decisions")
async def list_decisions(
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    executed: Optional[bool] = Query(None, description="Filter by executed status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List trade decisions (audit trail) with filtering and pagination.

    Trade decisions record every signal with full context for replay and analysis.
    """
    query = select(TradeDecision)

    # Apply filters
    if strategy:
        query = query.where(TradeDecision.strategy_name == strategy)
    if executed is not None:
        query = query.where(TradeDecision.executed == executed)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination and ordering
    query = query.order_by(desc(TradeDecision.timestamp)).offset(offset).limit(limit)
    decisions = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_format_decision(d) for d in decisions],
    }


@router.get("/trades")
async def list_trades(
    is_paper: Optional[bool] = Query(None, description="Filter by paper/live"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List trades with filtering and pagination.
    """
    query = select(ExecutorTrade)

    if is_paper is not None:
        query = query.where(ExecutorTrade.is_paper == is_paper)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination and ordering
    query = query.order_by(desc(ExecutorTrade.timestamp)).offset(offset).limit(limit)
    trades = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_format_trade(t) for t in trades],
    }


@router.get("/orders")
async def list_orders(
    status: Optional[str] = Query(None, description="Filter by status"),
    is_paper: Optional[bool] = Query(None, description="Filter by paper/live"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List orders with filtering and pagination.
    """
    query = select(ExecutorOrder)

    if status:
        query = query.where(ExecutorOrder.status == status)
    if is_paper is not None:
        query = query.where(ExecutorOrder.is_paper == is_paper)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination and ordering
    query = query.order_by(desc(ExecutorOrder.submitted_at)).offset(offset).limit(limit)
    orders = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_format_order(o) for o in orders],
    }


@router.get("/balance")
async def get_balance(db: Session = Depends(get_db)):
    """
    Get balance information for both paper and live modes.
    """
    config = get_config()
    executor = get_executor(config)

    # Paper balance from DB
    paper_balance = db.query(PaperBalance).first()
    paper_stats = executor.paper_executor.get_stats() if paper_balance else {}

    # Live balance (if configured)
    live_stats = {}
    if config.mode == TradingMode.LIVE:
        try:
            live_stats = executor.live_executor.get_stats()
        except Exception:
            live_stats = {"error": "Failed to fetch live balance"}

    return {
        "mode": config.mode.value,
        "paper": paper_stats,
        "live": live_stats,
    }


@router.post("/reset-paper")
async def reset_paper_trading(
    starting_balance: Optional[float] = Query(None, description="New starting balance"),
):
    """
    Reset paper trading state.
    """
    config = get_config()
    executor = get_executor(config)

    executor.reset_paper(starting_balance)

    return {
        "success": True,
        "message": f"Paper trading reset with balance: ${starting_balance or 10000}",
    }


@router.get("/wallet")
async def get_wallet_status():
    """
    Get live wallet status from Polymarket.

    Returns current USDC balance and computed positions from trade history.
    """
    from src.executor.clients.order_client import get_order_client

    try:
        client = get_order_client()

        balance = client.get_balance()
        positions = client.get_positions()
        open_orders = client.get_open_orders()

        # Calculate total position value
        position_value = sum(p['cost_basis'] for p in positions)

        return {
            "success": True,
            "wallet_address": client.get_address(),
            "usdc_balance": balance,
            "position_value": position_value,
            "total_value": balance + position_value,
            "positions": positions,
            "open_orders": len(open_orders),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


@router.post("/wallet/sync")
async def sync_wallet_positions(db: Session = Depends(get_db)):
    """
    Sync Polymarket wallet positions to local database.

    Fetches trade history from Polymarket and creates/updates Position records.
    """
    from src.executor.clients.order_client import get_order_client
    from sqlalchemy import text

    try:
        client = get_order_client()

        # Get trades and computed positions from Polymarket
        trades = client.get_trades()
        wallet_positions = client.get_positions()

        synced = 0
        skipped = 0
        errors = []

        for wp in wallet_positions:
            asset_id = wp['asset_id']
            market_condition_id = wp.get('market')

            # Check if position already exists in DB
            existing = db.execute(
                select(Position).where(
                    Position.token_id == asset_id,
                    Position.is_paper == False,
                    Position.status == PositionStatus.OPEN.value,
                )
            ).scalar_one_or_none()

            if existing:
                # Update existing position
                existing.size_shares = wp['size']
                existing.cost_basis = wp['cost_basis']
                existing.current_price = wp['avg_price']
                existing.current_value = wp['cost_basis']
                skipped += 1
                continue

            # Find market in our database
            market = None
            if market_condition_id:
                market = db.execute(
                    text("SELECT id FROM markets WHERE condition_id = :cid"),
                    {"cid": market_condition_id}
                ).scalar_one_or_none()

            # Create new position record
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)

            position = Position(
                is_paper=False,
                strategy_name="wallet_import",  # Mark as imported
                market_id=market,
                token_id=asset_id,
                side="BUY",  # All positions from trades are buys
                entry_price=wp['avg_price'],
                entry_time=now,
                size_shares=wp['size'],
                cost_basis=wp['cost_basis'],
                current_price=wp['avg_price'],
                current_value=wp['cost_basis'],
                status=PositionStatus.OPEN.value,
            )
            db.add(position)
            synced += 1

        db.commit()

        return {
            "success": True,
            "synced": synced,
            "updated": skipped,
            "errors": errors,
            "total_positions": len(wallet_positions),
            "usdc_balance": client.get_balance(),
        }
    except Exception as e:
        db.rollback()
        return {
            "success": False,
            "error": str(e),
        }


@router.get("/wallet/trades")
async def get_wallet_trades():
    """
    Get recent trades from Polymarket wallet.
    """
    from src.executor.clients.order_client import get_order_client

    try:
        client = get_order_client()
        trades = client.get_trades()

        return {
            "success": True,
            "total": len(trades),
            "trades": [
                {
                    "id": t.get("id"),
                    "market": t.get("market"),
                    "asset_id": t.get("asset_id"),
                    "side": t.get("side"),
                    "size": t.get("size"),
                    "price": t.get("price"),
                    "outcome": t.get("outcome"),
                    "status": t.get("status"),
                    "transaction_hash": t.get("transaction_hash"),
                }
                for t in trades
            ],
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


# Helper functions

def _format_position(p: Position) -> dict:
    """Format position for API response."""
    return {
        "id": p.id,
        "is_paper": p.is_paper,
        "strategy_name": p.strategy_name,
        "market_id": p.market_id,
        "token_id": p.token_id,
        "side": p.side,
        "status": p.status,
        "entry_price": float(p.entry_price) if p.entry_price else None,
        "exit_price": float(p.exit_price) if p.exit_price else None,
        "current_price": float(p.current_price) if p.current_price else None,
        "size_shares": float(p.size_shares) if p.size_shares else None,
        "cost_basis": float(p.cost_basis) if p.cost_basis else None,
        "current_value": float(p.current_value) if p.current_value else None,
        "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl else None,
        "unrealized_pnl_pct": float(p.unrealized_pnl_pct) if p.unrealized_pnl_pct else None,
        "realized_pnl": float(p.realized_pnl) if p.realized_pnl else None,
        "entry_time": p.entry_time.isoformat() if p.entry_time else None,
        "exit_time": p.exit_time.isoformat() if p.exit_time else None,
        "close_reason": p.close_reason,
        "hedge_position_id": p.hedge_position_id,
    }


def _format_signal(s: Signal) -> dict:
    """Format signal for API response."""
    return {
        "id": s.id,
        "strategy_name": s.strategy_name,
        "market_id": s.market_id,
        "token_id": s.token_id,
        "side": s.side,
        "status": s.status,
        "reason": s.reason,
        "edge": float(s.edge) if s.edge else None,
        "confidence": float(s.confidence) if s.confidence else None,
        "price_at_signal": float(s.price_at_signal) if s.price_at_signal else None,
        "best_bid": float(s.best_bid) if s.best_bid else None,
        "best_ask": float(s.best_ask) if s.best_ask else None,
        "suggested_size_usd": float(s.suggested_size_usd) if s.suggested_size_usd else None,
        "status_reason": s.status_reason,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "processed_at": s.processed_at.isoformat() if s.processed_at else None,
    }


def _format_trade(t: ExecutorTrade) -> dict:
    """Format trade for API response."""
    return {
        "id": t.id,
        "order_id": t.order_id,
        "position_id": t.position_id,
        "is_paper": t.is_paper,
        "price": float(t.price) if t.price else None,
        "size_shares": float(t.size_shares) if t.size_shares else None,
        "size_usd": float(t.size_usd) if t.size_usd else None,
        "side": t.side,
        "fee_usd": float(t.fee_usd) if t.fee_usd else None,
        "timestamp": t.timestamp.isoformat() if t.timestamp else None,
    }


def _format_order(o: ExecutorOrder) -> dict:
    """Format order for API response."""
    return {
        "id": o.id,
        "signal_id": o.signal_id,
        "is_paper": o.is_paper,
        "token_id": o.token_id,
        "side": o.side,
        "order_type": o.order_type,
        "status": o.status,
        "limit_price": float(o.limit_price) if o.limit_price else None,
        "executed_price": float(o.executed_price) if o.executed_price else None,
        "size_usd": float(o.size_usd) if o.size_usd else None,
        "size_shares": float(o.size_shares) if o.size_shares else None,
        "filled_shares": float(o.filled_shares) if o.filled_shares else None,
        "polymarket_order_id": o.polymarket_order_id,
        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        "status_message": o.status_message,
    }


def _format_decision(d: TradeDecision) -> dict:
    """Format trade decision for API response."""
    return {
        "id": d.id,
        "timestamp": d.timestamp.isoformat() if d.timestamp else None,
        "strategy_name": d.strategy_name,
        "strategy_sha": d.strategy_sha,
        "market_id": d.market_id,
        "condition_id": d.condition_id,
        "market_snapshot": d.market_snapshot,
        "decision_inputs": d.decision_inputs,
        "signal_side": d.signal_side,
        "signal_reason": d.signal_reason,
        "signal_edge": float(d.signal_edge) if d.signal_edge else None,
        "signal_size_usd": float(d.signal_size_usd) if d.signal_size_usd else None,
        "executed": d.executed,
        "rejected_reason": d.rejected_reason,
        "execution_price": float(d.execution_price) if d.execution_price else None,
        "position_id": d.position_id,
    }
