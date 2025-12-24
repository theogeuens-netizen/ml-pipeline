"""
Executor API endpoints.

Provides endpoints for:
- Executor status
- Positions
- Trades
- Signals
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from io import StringIO
import csv

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.executor.models import (
    Signal,
    ExecutorOrder,
    ExecutorTrade,
    Position,
    PaperBalance,
    StrategyBalance,
    TradeDecision,
    PositionStatus,
    SignalStatus,
    OrderStatus,
)
from src.db.models import Market
from src.executor.config import get_config, TradingMode
from src.executor.execution import get_executor

router = APIRouter(prefix="/executor")


@router.get("/status")
async def get_executor_status():
    """
    Get executor status including mode, balance, and statistics.
    """
    from strategies.loader import load_strategies

    config = get_config()
    executor = get_executor(config)

    stats = executor.get_stats()

    # Get strategies from YAML config, not hardcoded defaults
    strategies = load_strategies()
    strategy_names = [s.name for s in strategies]

    return {
        "mode": config.mode.value,
        "running": True,  # Would need to check runner status
        "balance": stats.get("balance", 0),
        "total_value": executor.get_total_value(),
        "stats": stats,
        "enabled_strategies": strategy_names,
        "risk_limits": {
            "max_position_usd": config.risk.max_position_usd,
            "max_total_exposure_usd": config.risk.max_total_exposure_usd,
            "max_positions_per_strategy": getattr(config.risk, "max_positions_per_strategy", None),
            "max_positions": config.risk.max_positions,
            "max_drawdown_pct": config.risk.max_drawdown_pct,
        },
    }


@router.get("/positions")
async def list_positions(
    status: Optional[str] = Query(None, description="Filter by status (open/closed)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    is_paper: Optional[bool] = Query(None, description="Filter by paper/live"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List positions with filtering and pagination.
    Includes token_side (YES/NO) and market_title for each position.
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

    # Batch-load markets to get token_side and market_title
    market_ids = [p.market_id for p in positions if p.market_id]
    if market_ids:
        markets_query = select(Market).where(Market.id.in_(market_ids))
        markets = {m.id: m for m in db.execute(markets_query).scalars().all()}
    else:
        markets = {}

    # Format positions with token_side and market_title
    items = []
    for p in positions:
        item = _format_position(p)
        market = markets.get(p.market_id)
        if market:
            # Determine token side
            if p.token_id == market.yes_token_id:
                item["token_side"] = "YES"
            elif p.token_id == market.no_token_id:
                item["token_side"] = "NO"
            else:
                item["token_side"] = "UNKNOWN"
            item["market_title"] = market.question
        else:
            item["token_side"] = "UNKNOWN"
            item["market_title"] = None
        items.append(item)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
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


@router.get("/portfolio/summary")
async def get_portfolio_summary(db: Session = Depends(get_db)):
    """
    Get aggregated portfolio summary for dashboard header.

    Returns:
    - cash: Available cash (not in positions) = allocation - cost_basis
    - portfolio_value: Cash + current value of all positions
    - unrealized_pnl: Sum of unrealized P&L from open positions
    - realized_pnl: Sum of realized P&L from closed positions
    - total_pnl: unrealized + realized
    - total_return_pct: Total P&L / total allocation
    - open_positions: Count of open positions
    - strategies_count: Number of active strategies
    """
    # Get strategy allocations (this is our "capital")
    strategy_balances = db.query(StrategyBalance).all()
    total_allocated = sum(float(b.allocated_usd) for b in strategy_balances)
    realized_pnl = sum(float(b.realized_pnl) for b in strategy_balances)

    # Get open positions for value calculation
    open_positions = db.query(Position).filter(
        Position.status == PositionStatus.OPEN.value,
        Position.is_paper == True,
    ).all()

    # Cost basis = capital deployed in positions
    cost_basis = sum(
        float(p.cost_basis) if p.cost_basis else 0
        for p in open_positions
    )

    # Position value = current market value of positions
    position_value = sum(
        float(p.current_value) if p.current_value else float(p.cost_basis or 0)
        for p in open_positions
    )

    unrealized_pnl = sum(
        float(p.unrealized_pnl) if p.unrealized_pnl else 0
        for p in open_positions
    )

    # Cash = allocated capital not yet deployed + realized P&L
    cash = total_allocated - cost_basis + realized_pnl

    total_pnl = realized_pnl + unrealized_pnl
    portfolio_value = cash + position_value

    # Calculate return %
    total_return_pct = (total_pnl / total_allocated * 100) if total_allocated > 0 else 0

    # High water mark tracking
    high_water = total_allocated + max(0, total_pnl)
    current_drawdown_pct = ((high_water - portfolio_value) / high_water * 100) if high_water > 0 else 0

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cash": round(cash, 2),
        "position_value": round(position_value, 2),
        "portfolio_value": round(portfolio_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 2),
        "open_positions": len(open_positions),
        "strategies_count": len(strategy_balances),
        "total_allocated": round(total_allocated, 2),
        "high_water_mark": round(high_water, 2),
        "current_drawdown_pct": round(max(0, current_drawdown_pct), 2),
    }


@router.get("/positions/export")
async def export_positions(
    status: Optional[str] = Query(None, description="Filter by status (open/closed)"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    format: str = Query("csv", description="Export format (csv)"),
    db: Session = Depends(get_db),
):
    """
    Export positions as CSV for analyst download.
    """
    query = select(Position)

    if status:
        status_value = PositionStatus.OPEN.value if status.lower() == "open" else PositionStatus.CLOSED.value
        query = query.where(Position.status == status_value)
    if strategy:
        query = query.where(Position.strategy_name == strategy)

    query = query.order_by(desc(Position.entry_time))
    positions = db.execute(query).scalars().all()

    # Get markets for titles
    market_ids = [p.market_id for p in positions if p.market_id]
    markets = {}
    if market_ids:
        markets_query = select(Market).where(Market.id.in_(market_ids))
        markets = {m.id: m for m in db.execute(markets_query).scalars().all()}

    # Create CSV
    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "ID", "Strategy", "Market", "Side", "Token Side", "Status",
        "Entry Price", "Exit Price", "Current Price",
        "Size (shares)", "Cost Basis", "Current Value",
        "Unrealized P&L", "Realized P&L", "P&L %",
        "Entry Time", "Exit Time", "Close Reason"
    ])

    # Data rows
    for p in positions:
        market = markets.get(p.market_id)
        market_title = market.question[:60] if market else ""

        # Determine token side
        token_side = "UNKNOWN"
        if market:
            if p.token_id == market.yes_token_id:
                token_side = "YES"
            elif p.token_id == market.no_token_id:
                token_side = "NO"

        pnl_pct = ""
        if p.cost_basis and float(p.cost_basis) > 0:
            if p.unrealized_pnl:
                pnl_pct = f"{float(p.unrealized_pnl) / float(p.cost_basis) * 100:.1f}%"
            elif p.realized_pnl:
                pnl_pct = f"{float(p.realized_pnl) / float(p.cost_basis) * 100:.1f}%"

        writer.writerow([
            p.id,
            p.strategy_name,
            market_title,
            p.side,
            token_side,
            p.status,
            f"{float(p.entry_price):.4f}" if p.entry_price else "",
            f"{float(p.exit_price):.4f}" if p.exit_price else "",
            f"{float(p.current_price):.4f}" if p.current_price else "",
            f"{float(p.size_shares):.2f}" if p.size_shares else "",
            f"{float(p.cost_basis):.2f}" if p.cost_basis else "",
            f"{float(p.current_value):.2f}" if p.current_value else "",
            f"{float(p.unrealized_pnl):.2f}" if p.unrealized_pnl else "",
            f"{float(p.realized_pnl):.2f}" if p.realized_pnl else "",
            pnl_pct,
            p.entry_time.isoformat() if p.entry_time else "",
            p.exit_time.isoformat() if p.exit_time else "",
            p.close_reason or "",
        ])

    output.seek(0)

    filename = f"positions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


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


# =============================================================================
# STRATEGY PERFORMANCE ENDPOINTS
# =============================================================================


@router.get("/strategies/leaderboard")
async def get_strategy_leaderboard(
    sort_by: str = Query("total_pnl", description="Sort metric"),
    limit: int = Query(25, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Get leaderboard of all strategies sorted by performance.

    Sort options: total_pnl, sharpe_ratio, win_rate, total_return_pct
    """
    from strategies.performance import PerformanceTracker

    tracker = PerformanceTracker(db)
    metrics_list = tracker.get_leaderboard(sort_by=sort_by, limit=limit)

    return {
        "sort_by": sort_by,
        "total": len(metrics_list),
        "strategies": [_format_strategy_metrics(m) for m in metrics_list],
    }


@router.get("/strategies/{strategy_name}/metrics")
async def get_strategy_metrics(
    strategy_name: str,
    db: Session = Depends(get_db),
):
    """
    Get detailed performance metrics for a specific strategy.
    """
    from strategies.performance import PerformanceTracker

    tracker = PerformanceTracker(db)
    metrics = tracker.get_strategy_metrics(strategy_name)

    if not metrics:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")

    return _format_strategy_metrics(metrics)


@router.get("/strategies/{strategy_name}/debug")
async def get_strategy_debug(
    strategy_name: str,
    db: Session = Depends(get_db),
):
    """
    Get debug information for a strategy.

    Answers "why isn't this trading?" with:
    - Strategy parameters
    - Recent decision history (executed, rejected)
    - Funnel stats from strategy's get_debug_stats()
    """
    from strategies.performance import PerformanceTracker

    tracker = PerformanceTracker(db)
    debug_info = tracker.get_debug_info(strategy_name)

    return debug_info


@router.get("/strategies/balances")
async def get_strategy_balances(
    db: Session = Depends(get_db),
):
    """
    Get per-strategy wallet balances.

    Returns current balance, allocation, and P&L for each strategy.
    Unrealized P&L is calculated from actual open positions (not stale DB values).
    """
    # Ensure every enabled strategy has a balance row so new strategies surface immediately
    from strategies.performance import PerformanceTracker

    PerformanceTracker(db).ensure_strategy_balances()

    balances = db.query(StrategyBalance).order_by(
        desc(StrategyBalance.total_pnl)
    ).all()

    # Get position values and unrealized P&L per strategy from actual positions
    position_values = {}
    unrealized_by_strategy = {}
    cost_basis_by_strategy = {}
    open_count_by_strategy = {}

    positions = db.query(Position).filter(
        Position.is_paper == True,
        Position.status == PositionStatus.OPEN.value,
    ).all()

    for p in positions:
        strat = p.strategy_name
        val = float(p.current_value) if p.current_value else float(p.cost_basis or 0)
        unrealized = float(p.unrealized_pnl) if p.unrealized_pnl else 0
        cost = float(p.cost_basis) if p.cost_basis else 0

        position_values[strat] = position_values.get(strat, 0) + val
        unrealized_by_strategy[strat] = unrealized_by_strategy.get(strat, 0) + unrealized
        cost_basis_by_strategy[strat] = cost_basis_by_strategy.get(strat, 0) + cost
        open_count_by_strategy[strat] = open_count_by_strategy.get(strat, 0) + 1

    strategies_data = []
    total_portfolio_value = 0.0

    for b in balances:
        pos_value = position_values.get(b.strategy_name, 0)
        unrealized_pnl = unrealized_by_strategy.get(b.strategy_name, 0)
        realized_pnl = float(b.realized_pnl)
        total_pnl = realized_pnl + unrealized_pnl

        # Cash = allocated - cost_basis + realized
        cost_basis = cost_basis_by_strategy.get(b.strategy_name, 0)
        cash = float(b.allocated_usd) - cost_basis + realized_pnl

        portfolio_value = cash + pos_value
        total_portfolio_value += portfolio_value

        strategies_data.append({
            "name": b.strategy_name,
            "allocated_usd": float(b.allocated_usd),
            "current_usd": round(cash, 2),  # Recalculated cash
            "position_value": round(pos_value, 2),
            "portfolio_value": round(portfolio_value, 2),
            "total_pnl": round(total_pnl, 2),  # Recalculated
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),  # From actual positions
            "trade_count": b.trade_count,
            "win_count": b.win_count,
            "loss_count": b.loss_count,
            "win_rate": b.win_count / b.trade_count if b.trade_count > 0 else 0,
            "max_drawdown_pct": float(b.max_drawdown_pct),
            "open_positions": open_count_by_strategy.get(b.strategy_name, 0),
        })

    return {
        "total": len(balances),
        "total_allocated": sum(float(b.allocated_usd) for b in balances),
        "total_current": round(total_portfolio_value, 2),
        "total_pnl": round(sum(s["total_pnl"] for s in strategies_data), 2),
        "strategies": strategies_data,
    }


@router.get("/strategies")
async def list_strategies(db: Session = Depends(get_db)):
    """
    List all loaded strategies with their configuration.
    """
    from strategies.loader import load_strategies

    strategies = load_strategies()

    return {
        "total": len(strategies),
        "strategies": [
            {
                "name": s.name,
                "type": type(s).__name__,
                "version": s.version,
                "sha": s.get_sha(),
                "params": {
                    k: getattr(s, k)
                    for k in dir(s)
                    if not k.startswith("_")
                    and k not in ("name", "version", "logger", "scan", "filter",
                                  "get_sha", "get_params", "should_exit",
                                  "on_signal_executed", "on_position_closed",
                                  "get_debug_stats")
                    and not callable(getattr(s, k, None))
                },
            }
            for s in strategies
        ],
    }


@router.get("/strategies/equity-curve")
async def get_equity_curve(
    days: int = Query(30, ge=1, le=365, description="Number of days of history"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    db: Session = Depends(get_db),
):
    """
    Get daily equity curve data for charting.

    Returns cumulative P&L by day for each strategy and total portfolio.
    Includes current unrealized P&L so chart shows data even without closed positions.
    """
    from datetime import timedelta
    from sqlalchemy import cast, Date

    # Calculate date range
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    # Get strategy allocations for starting values
    strategy_balances = db.query(StrategyBalance).all()
    balances = {b.strategy_name: float(b.allocated_usd) for b in strategy_balances}
    total_allocated = sum(balances.values()) if balances else 1200

    # Query closed positions grouped by exit date and strategy
    query = select(
        cast(Position.exit_time, Date).label("date"),
        Position.strategy_name,
        func.sum(Position.realized_pnl).label("daily_pnl"),
        func.count(Position.id).label("trade_count"),
    ).where(
        Position.status == PositionStatus.CLOSED.value,
        Position.exit_time >= start_date,
        Position.realized_pnl.isnot(None),
    )

    if strategy:
        query = query.where(Position.strategy_name == strategy)

    query = query.group_by(
        cast(Position.exit_time, Date),
        Position.strategy_name,
    ).order_by("date")

    results = db.execute(query).all()

    # Get current unrealized P&L per strategy for today's value
    open_positions = db.query(Position).filter(
        Position.status == PositionStatus.OPEN.value,
        Position.is_paper == True,
    ).all()

    unrealized_by_strategy = {}
    for p in open_positions:
        strat = p.strategy_name
        if strat not in unrealized_by_strategy:
            unrealized_by_strategy[strat] = 0
        unrealized_by_strategy[strat] += float(p.unrealized_pnl) if p.unrealized_pnl else 0

    # Build time series per strategy
    strategies_data = {}
    for row in results:
        strat = row.strategy_name
        if strat not in strategies_data:
            strategies_data[strat] = []
        strategies_data[strat].append({
            "date": row.date.isoformat(),
            "daily_pnl": float(row.daily_pnl) if row.daily_pnl else 0,
            "trade_count": row.trade_count,
        })

    # Calculate cumulative values and add today's unrealized P&L
    for strat, daily_data in strategies_data.items():
        allocated = balances.get(strat, 400)
        cumulative = allocated
        for day in daily_data:
            cumulative += day["daily_pnl"]
            day["value"] = round(cumulative, 2)
            day["cumulative_pnl"] = round(cumulative - allocated, 2)

    # Add today's data point with unrealized P&L for each strategy
    today_str = end_date.isoformat()
    for strat in balances:
        if strat not in strategies_data:
            strategies_data[strat] = []

        # Calculate current value: allocation + realized + unrealized
        realized = sum(float(b.realized_pnl) for b in strategy_balances if b.strategy_name == strat)
        unrealized = unrealized_by_strategy.get(strat, 0)
        current_value = balances[strat] + realized + unrealized

        # Only add today if not already in data
        if not strategies_data[strat] or strategies_data[strat][-1]["date"] != today_str:
            strategies_data[strat].append({
                "date": today_str,
                "daily_pnl": 0,  # Today's closed trades already counted
                "trade_count": 0,
                "value": round(current_value, 2),
                "cumulative_pnl": round(realized + unrealized, 2),
            })

    # Also add start date as baseline for each strategy
    start_str = start_date.isoformat()
    for strat in balances:
        if strategies_data[strat] and strategies_data[strat][0]["date"] != start_str:
            strategies_data[strat].insert(0, {
                "date": start_str,
                "daily_pnl": 0,
                "trade_count": 0,
                "value": round(balances[strat], 2),
                "cumulative_pnl": 0,
            })

    # Calculate total portfolio
    total_data = {}
    for strat, daily_data in strategies_data.items():
        for day in daily_data:
            date = day["date"]
            if date not in total_data:
                total_data[date] = {"date": date, "daily_pnl": 0, "trade_count": 0, "value": 0}
            total_data[date]["daily_pnl"] += day.get("daily_pnl", 0)
            total_data[date]["trade_count"] += day.get("trade_count", 0)
            total_data[date]["value"] += day.get("value", 0)

    total_list = sorted(total_data.values(), key=lambda x: x["date"])
    for day in total_list:
        day["value"] = round(day["value"], 2)
        day["cumulative_pnl"] = round(day["value"] - total_allocated, 2)

    # Calculate realized and unrealized lines for the chart
    total_realized = sum(float(b.realized_pnl) for b in strategy_balances)
    total_unrealized = sum(unrealized_by_strategy.values())

    # Build a simple 2-point line: start (allocation) to current (with P&L)
    chart_data = [
        {
            "date": start_date.isoformat(),
            "realized": round(total_allocated, 2),
            "unrealized": round(total_allocated, 2),
            "total": round(total_allocated, 2),
            "baseline": round(total_allocated, 2),
        },
        {
            "date": end_date.isoformat(),
            "realized": round(total_allocated + total_realized, 2),
            "unrealized": round(total_allocated + total_realized + total_unrealized, 2),
            "total": round(total_allocated + total_realized + total_unrealized, 2),
            "baseline": round(total_allocated, 2),
        },
    ]

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": days,
        "strategies": strategies_data,
        "total": total_list,
        "chart_data": chart_data,  # Simple 2-line chart data
        "summary": {
            "total_allocated": round(total_allocated, 2),
            "total_realized": round(total_realized, 2),
            "total_unrealized": round(total_unrealized, 2),
            "portfolio_value": round(total_allocated + total_realized + total_unrealized, 2),
        },
        "allocations": balances,
    }


@router.get("/strategies/funnel-stats")
async def get_funnel_stats(
    hours: int = Query(24, ge=1, le=720, description="Hours of history"),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    db: Session = Depends(get_db),
):
    """
    Get decision funnel statistics for visualization.

    Shows conversion from signals -> executed -> profitable.
    Also groups rejected decisions by reason.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Base query for decisions in time range
    base_query = select(TradeDecision).where(TradeDecision.timestamp >= cutoff)
    if strategy:
        base_query = base_query.where(TradeDecision.strategy_name == strategy)

    decisions = db.execute(base_query).scalars().all()

    # Count totals
    total_decisions = len(decisions)
    executed = [d for d in decisions if d.executed]
    rejected = [d for d in decisions if not d.executed]

    # Count profitable (need to join with positions)
    profitable_count = 0
    for d in executed:
        if d.position_id:
            pos = db.get(Position, d.position_id)
            if pos and pos.realized_pnl and float(pos.realized_pnl) > 0:
                profitable_count += 1

    # Group rejections by reason
    rejection_reasons = {}
    for d in rejected:
        reason = d.rejected_reason or "unknown"
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    # Sort rejection reasons by count
    sorted_reasons = dict(sorted(
        rejection_reasons.items(),
        key=lambda x: x[1],
        reverse=True
    ))

    # Per-strategy breakdown
    by_strategy = {}
    for d in decisions:
        strat = d.strategy_name
        if strat not in by_strategy:
            by_strategy[strat] = {"total": 0, "executed": 0, "rejected": 0}
        by_strategy[strat]["total"] += 1
        if d.executed:
            by_strategy[strat]["executed"] += 1
        else:
            by_strategy[strat]["rejected"] += 1

    return {
        "period_hours": hours,
        "total_decisions": total_decisions,
        "executed": len(executed),
        "rejected": len(rejected),
        "profitable": profitable_count,
        "execution_rate": len(executed) / total_decisions if total_decisions > 0 else 0,
        "win_rate": profitable_count / len(executed) if executed else 0,
        "rejection_reasons": sorted_reasons,
        "by_strategy": by_strategy,
    }


@router.get("/analytics/capital")
async def get_capital_analytics(db: Session = Depends(get_db)):
    """
    Capital utilization analytics for hedge fund dashboard.

    Shows deployed vs available capital per strategy.
    """
    from sqlalchemy import text

    # Get strategy balances and position values
    result = db.execute(text("""
        SELECT
            sb.strategy_name,
            sb.allocated_usd,
            sb.current_usd as cash,
            COALESCE(SUM(p.cost_basis), 0) as deployed,
            COALESCE(SUM(p.current_value), 0) as position_value,
            COUNT(p.id) as position_count
        FROM strategy_balances sb
        LEFT JOIN positions p ON p.strategy_name = sb.strategy_name AND p.status = 'open'
        GROUP BY sb.strategy_name, sb.allocated_usd, sb.current_usd
        ORDER BY sb.strategy_name
    """)).fetchall()

    strategies = []
    totals = {"allocated": 0, "cash": 0, "deployed": 0, "position_value": 0}

    for row in result:
        allocated = float(row[1])
        cash = float(row[2])
        deployed = float(row[3])
        position_value = float(row[4])
        position_count = row[5]

        utilization = (deployed / allocated * 100) if allocated > 0 else 0

        strategies.append({
            "strategy_name": row[0],
            "allocated_usd": allocated,
            "cash_usd": cash,
            "deployed_usd": deployed,
            "position_value_usd": position_value,
            "position_count": position_count,
            "utilization_pct": round(utilization, 1),
            "available_usd": cash,  # Cash is what's available
            "is_blocked": cash <= 0,
        })

        totals["allocated"] += allocated
        totals["cash"] += cash
        totals["deployed"] += deployed
        totals["position_value"] += position_value

    totals["utilization_pct"] = round(
        (totals["deployed"] / totals["allocated"] * 100) if totals["allocated"] > 0 else 0, 1
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "totals": totals,
        "strategies": strategies,
    }


@router.get("/analytics/positions")
async def get_position_analytics(db: Session = Depends(get_db)):
    """
    Position aging and health analytics.

    Shows position age distribution and flags stuck positions.
    """
    from sqlalchemy import text

    # Get positions with age and expected hold time
    result = db.execute(text("""
        SELECT
            p.strategy_name,
            p.market_id,
            m.question,
            m.end_date,
            p.entry_time,
            p.cost_basis,
            p.current_value,
            p.unrealized_pnl,
            EXTRACT(EPOCH FROM (NOW() - p.entry_time))/3600 as age_hours,
            CASE
                WHEN m.end_date < NOW() THEN 'pending_resolution'
                ELSE 'active'
            END as market_status
        FROM positions p
        JOIN markets m ON p.market_id = m.id
        WHERE p.status = 'open'
        ORDER BY p.entry_time ASC
    """)).fetchall()

    # Age buckets
    buckets = {"0-1h": 0, "1-4h": 0, "4-12h": 0, "12-24h": 0, "24h+": 0}
    pending_resolution = []
    positions = []

    # Strategy expected hold times (from strategy config)
    expected_hold = {
        "uncertain_zone_1h": 4,  # max_hours from config
        "uncertain_zone_24h": 24,
        "uncertain_zone_5d": 120,
    }

    for row in result:
        age = float(row[8])
        strategy = row[0]
        max_hold = expected_hold.get(strategy, 24)
        is_overdue = age > max_hold

        pos = {
            "strategy_name": strategy,
            "market_id": row[1],
            "market_question": row[2][:60] + "..." if row[2] and len(row[2]) > 60 else row[2],
            "end_date": row[3].isoformat() if row[3] else None,
            "entry_time": row[4].isoformat() if row[4] else None,
            "cost_basis": float(row[5]) if row[5] else 0,
            "current_value": float(row[6]) if row[6] else 0,
            "unrealized_pnl": float(row[7]) if row[7] else 0,
            "age_hours": round(age, 1),
            "expected_hold_hours": max_hold,
            "is_overdue": is_overdue,
            "market_status": row[9],
        }
        positions.append(pos)

        # Bucket by age
        if age < 1:
            buckets["0-1h"] += 1
        elif age < 4:
            buckets["1-4h"] += 1
        elif age < 12:
            buckets["4-12h"] += 1
        elif age < 24:
            buckets["12-24h"] += 1
        else:
            buckets["24h+"] += 1

        # Track pending resolution
        if row[9] == "pending_resolution":
            pending_resolution.append(pos)

    # Per-strategy summary
    by_strategy = {}
    for pos in positions:
        s = pos["strategy_name"]
        if s not in by_strategy:
            by_strategy[s] = {
                "count": 0, "avg_age": 0, "overdue": 0,
                "pending_resolution": 0, "total_age": 0
            }
        by_strategy[s]["count"] += 1
        by_strategy[s]["total_age"] += pos["age_hours"]
        if pos["is_overdue"]:
            by_strategy[s]["overdue"] += 1
        if pos["market_status"] == "pending_resolution":
            by_strategy[s]["pending_resolution"] += 1

    for s in by_strategy:
        if by_strategy[s]["count"] > 0:
            by_strategy[s]["avg_age"] = round(
                by_strategy[s]["total_age"] / by_strategy[s]["count"], 1
            )
        del by_strategy[s]["total_age"]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_positions": len(positions),
        "age_buckets": buckets,
        "pending_resolution_count": len(pending_resolution),
        "by_strategy": by_strategy,
        "pending_resolution": pending_resolution[:10],  # Top 10 oldest
        "oldest_positions": positions[:5],  # 5 oldest
    }


@router.get("/analytics/signals")
async def get_signal_analytics(
    hours: int = Query(6, ge=1, le=48),
    db: Session = Depends(get_db),
):
    """
    Signal flow analytics - generated, executed, rejected with reasons.
    """
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Overall stats
    stats = db.execute(text("""
        SELECT
            strategy_name,
            COUNT(*) as total,
            SUM(CASE WHEN executed THEN 1 ELSE 0 END) as executed,
            COUNT(DISTINCT market_id) as unique_markets,
            MAX(timestamp) as last_signal
        FROM trade_decisions
        WHERE timestamp > :cutoff
        GROUP BY strategy_name
    """), {"cutoff": cutoff}).fetchall()

    # Rejection reasons (categorized)
    rejections = db.execute(text("""
        SELECT
            CASE
                WHEN rejected_reason LIKE '%Insufficient balance%' THEN 'No Capital'
                WHEN rejected_reason LIKE '%already has position%' THEN 'Already Holding'
                WHEN rejected_reason LIKE '%max positions%' THEN 'Position Limit'
                WHEN rejected_reason LIKE '%exposure%' THEN 'Exposure Limit'
                ELSE 'Other'
            END as reason_category,
            COUNT(*) as count
        FROM trade_decisions
        WHERE timestamp > :cutoff AND NOT executed
        GROUP BY reason_category
        ORDER BY count DESC
    """), {"cutoff": cutoff}).fetchall()

    # Missed opportunities (unique new markets rejected due to capital)
    missed = db.execute(text("""
        SELECT
            d.strategy_name,
            COUNT(DISTINCT d.market_id) as missed_markets
        FROM trade_decisions d
        WHERE d.timestamp > :cutoff
        AND NOT d.executed
        AND d.rejected_reason LIKE '%Insufficient balance%'
        AND d.market_id NOT IN (
            SELECT market_id FROM positions WHERE status = 'open'
        )
        GROUP BY d.strategy_name
    """), {"cutoff": cutoff}).fetchall()

    # Signals per hour trend
    hourly = db.execute(text("""
        SELECT
            DATE_TRUNC('hour', timestamp) as hour,
            COUNT(*) as signals,
            SUM(CASE WHEN executed THEN 1 ELSE 0 END) as executed
        FROM trade_decisions
        WHERE timestamp > :cutoff
        GROUP BY DATE_TRUNC('hour', timestamp)
        ORDER BY hour
    """), {"cutoff": cutoff}).fetchall()

    strategies = []
    for row in stats:
        total = row[1]
        executed = row[2]
        strategies.append({
            "strategy_name": row[0],
            "total_signals": total,
            "executed": executed,
            "rejected": total - executed,
            "execution_rate_pct": round(executed / total * 100, 2) if total > 0 else 0,
            "unique_markets": row[3],
            "last_signal": row[4].isoformat() if row[4] else None,
        })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "period_hours": hours,
        "strategies": strategies,
        "rejection_breakdown": [
            {"reason": r[0], "count": r[1]} for r in rejections
        ],
        "missed_opportunities": [
            {"strategy_name": m[0], "missed_markets": m[1]} for m in missed
        ],
        "hourly_trend": [
            {
                "hour": h[0].isoformat() if h[0] else None,
                "signals": h[1],
                "executed": h[2],
            } for h in hourly
        ],
    }


@router.get("/analytics/pipeline")
async def get_market_pipeline(db: Session = Depends(get_db)):
    """
    Market opportunity pipeline - shows incoming trading opportunities.

    For each strategy time window, shows:
    - Markets currently in window (tradeable now)
    - Markets approaching window (pipeline)
    - Whether we have positions
    """
    from sqlalchemy import text

    # Strategy windows configuration
    windows = {
        "1h": {"min_hours": 1, "max_hours": 4, "approach_min": 4, "approach_max": 24},
        "24h": {"min_hours": 24, "max_hours": 48, "approach_min": 48, "approach_max": 72},
        "5d": {"min_hours": 120, "max_hours": 168, "approach_min": 168, "approach_max": 240},
    }

    # Get markets with filters matching strategy criteria
    # Price in uncertain zone (0.45-0.55), volume >= 2000, liquidity >= 1000
    result = db.execute(text("""
        WITH latest_snapshots AS (
            SELECT DISTINCT ON (market_id)
                market_id, price, liquidity, volume_24h
            FROM snapshots
            ORDER BY market_id, timestamp DESC
        ),
        eligible AS (
            SELECT
                m.id,
                LEFT(m.question, 50) as question,
                s.price,
                s.liquidity,
                s.volume_24h,
                EXTRACT(EPOCH FROM (m.end_date - NOW()))/3600 as hours_to_close,
                EXISTS (
                    SELECT 1 FROM positions p
                    WHERE p.market_id = m.id AND p.status = 'open'
                ) as has_position
            FROM markets m
            JOIN latest_snapshots s ON s.market_id = m.id
            WHERE m.active = true
            AND m.resolved = false
            AND m.closed = false
            AND m.accepting_orders = true
            AND m.no_token_id IS NOT NULL
            AND s.price BETWEEN 0.45 AND 0.55
            AND s.volume_24h >= 2000
            AND s.liquidity >= 1000
        )
        SELECT
            id, question, price, liquidity, hours_to_close, has_position
        FROM eligible
        WHERE hours_to_close > 0
        ORDER BY hours_to_close
    """)).fetchall()

    pipeline = {}
    for window_name, cfg in windows.items():
        in_window = []
        approaching = []

        for row in result:
            hours = float(row[4])
            market = {
                "id": row[0],
                "question": row[1],
                "price": float(row[2]),
                "hours_to_close": round(hours, 1),
                "has_position": row[5],
            }

            if cfg["min_hours"] <= hours <= cfg["max_hours"]:
                in_window.append(market)
            elif cfg["approach_min"] < hours <= cfg["approach_max"]:
                approaching.append(market)

        new_in_window = len([m for m in in_window if not m["has_position"]])
        new_approaching = len([m for m in approaching if not m["has_position"]])

        pipeline[window_name] = {
            "window": f"{cfg['min_hours']}-{cfg['max_hours']}h",
            "in_window": len(in_window),
            "in_window_new": new_in_window,
            "in_window_holding": len(in_window) - new_in_window,
            "approaching": len(approaching),
            "approaching_new": new_approaching,
            "approaching_time": f"{cfg['approach_min']}-{cfg['approach_max']}h",
            "markets_in_window": in_window[:5],  # Top 5 closest to expiry
            "markets_approaching": approaching[:5],  # Top 5 coming soon
        }

    # Summary stats
    total_in_window = sum(p["in_window"] for p in pipeline.values())
    total_new = sum(p["in_window_new"] for p in pipeline.values())
    total_approaching = sum(p["approaching_new"] for p in pipeline.values())

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_in_window": total_in_window,
            "new_opportunities_now": total_new,
            "new_opportunities_approaching": total_approaching,
            "status": "opportunities" if total_new > 0 else ("pipeline" if total_approaching > 0 else "saturated"),
        },
        "by_window": pipeline,
    }


def _format_strategy_metrics(m) -> dict:
    """Format StrategyMetrics for API response."""
    return {
        "strategy_name": m.strategy_name,
        "allocated_usd": m.allocated_usd,
        "current_usd": m.current_usd,
        "total_pnl": m.total_pnl,
        "realized_pnl": m.realized_pnl,
        "unrealized_pnl": m.unrealized_pnl,
        "total_return_pct": m.total_return_pct,
        "trade_count": m.trade_count,
        "win_count": m.win_count,
        "loss_count": m.loss_count,
        "win_rate": m.win_rate,
        "sharpe_ratio": m.sharpe_ratio,
        "sortino_ratio": m.sortino_ratio,
        "max_drawdown_usd": m.max_drawdown_usd,
        "max_drawdown_pct": m.max_drawdown_pct,
        "current_drawdown_pct": m.current_drawdown_pct,
        "avg_win_usd": m.avg_win_usd,
        "avg_loss_usd": m.avg_loss_usd,
        "profit_factor": m.profit_factor,
        "expectancy_usd": m.expectancy_usd,
        "avg_hold_hours": m.avg_hold_hours,
        "open_positions": m.open_positions,
        "first_trade": m.first_trade.isoformat() if m.first_trade else None,
        "last_trade": m.last_trade.isoformat() if m.last_trade else None,
    }
