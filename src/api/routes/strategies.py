"""
Strategy management API endpoints.

Provides endpoints for:
- Listing strategies
- Enabling/disabling strategies
- Configuring strategy parameters
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.executor.config import get_config, reload_config, StrategyConfig
from src.executor.strategies import get_registry
from src.executor.models import StrategyState, Signal

router = APIRouter(prefix="/strategies")


class StrategyConfigUpdate(BaseModel):
    """Request body for strategy configuration update."""
    enabled: Optional[bool] = None
    params: Optional[dict] = None


class StrategyEnableRequest(BaseModel):
    """Request body for enable/disable."""
    enabled: bool


@router.get("")
async def list_strategies():
    """
    List all available strategies with their status.
    """
    config = get_config()
    registry = get_registry()

    strategies = []

    # Get all registered strategies
    for strategy_name in registry.list_strategies():
        # Get strategy metadata
        strategy_info = registry.get_strategy_info(strategy_name)
        if strategy_info is None:
            continue

        # Get config for this strategy
        strategy_config = config.strategies.get(strategy_name)

        strategies.append({
            "name": strategy_name,
            "description": strategy_info.get("description", ""),
            "version": strategy_info.get("version", "1.0.0"),
            "enabled": strategy_config.enabled if strategy_config else False,
            "params": strategy_config.params if strategy_config else {},
        })

    return {
        "total": len(strategies),
        "items": strategies,
    }


@router.get("/{strategy_name}")
async def get_strategy(
    strategy_name: str,
    db: Session = Depends(get_db),
):
    """
    Get detailed information about a strategy.
    """
    registry = get_registry()
    config = get_config()

    # Check if strategy exists
    strategy_info = registry.get_strategy_info(strategy_name)
    if strategy_info is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    # Get config
    strategy_config = config.strategies.get(strategy_name)

    # Get strategy state from DB
    state = db.execute(
        select(StrategyState).where(StrategyState.strategy_name == strategy_name)
    ).scalar_one_or_none()

    # Get signal counts
    signal_counts = _get_signal_counts(db, strategy_name)

    return {
        "name": strategy_name,
        "description": strategy_info.get("description", ""),
        "version": strategy_info.get("version", "1.0.0"),
        "enabled": strategy_config.enabled if strategy_config else False,
        "params": strategy_config.params if strategy_config else {},
        "state": state.state_json if state else None,
        "statistics": {
            "signals_generated": signal_counts.get("total", 0),
            "signals_executed": signal_counts.get("executed", 0),
            "signals_rejected": signal_counts.get("rejected", 0),
        },
        "sizing": {
            "method": config.sizing.method.value,
            "fixed_amount_usd": config.sizing.fixed_amount_usd,
        } if strategy_config else None,
        "execution": {
            "order_type": config.execution.default_order_type.value,
            "limit_offset_bps": config.execution.limit_offset_bps,
        } if strategy_config else None,
    }


@router.post("/{strategy_name}/enable")
async def enable_strategy(
    strategy_name: str,
    request: StrategyEnableRequest,
):
    """
    Enable or disable a strategy.
    """
    config = get_config()
    registry = get_registry()

    # Check if strategy exists
    if registry.get_strategy_class(strategy_name) is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    # Update config
    if strategy_name not in config.strategies:
        config.strategies[strategy_name] = StrategyConfig(
            enabled=request.enabled,
            params={},
        )
    else:
        config.strategies[strategy_name].enabled = request.enabled

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "strategy": strategy_name,
        "enabled": request.enabled,
    }


@router.post("/{strategy_name}/config")
async def update_strategy_config(
    strategy_name: str,
    update: StrategyConfigUpdate,
):
    """
    Update strategy configuration.
    """
    config = get_config()
    registry = get_registry()

    # Check if strategy exists
    if registry.get_strategy_class(strategy_name) is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    # Get or create strategy config
    if strategy_name not in config.strategies:
        config.strategies[strategy_name] = StrategyConfig(
            enabled=False,
            params={},
        )

    strategy_config = config.strategies[strategy_name]

    # Update fields
    if update.enabled is not None:
        strategy_config.enabled = update.enabled
    if update.params is not None:
        strategy_config.params = update.params

    # Reconfigure the strategy instance if it exists
    strategy = registry.get_or_create_strategy(strategy_name, strategy_config.params)
    if strategy:
        strategy.configure(strategy_config.params)

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "strategy": strategy_name,
        "config": {
            "enabled": strategy_config.enabled,
            "params": strategy_config.params,
        },
    }


@router.get("/{strategy_name}/signals")
async def get_strategy_signals(
    strategy_name: str,
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get signals generated by a specific strategy.
    """
    registry = get_registry()

    # Check if strategy exists
    if registry.get_strategy_class(strategy_name) is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    query = select(Signal).where(Signal.strategy_name == strategy_name)

    if status:
        query = query.where(Signal.status == status)

    # Get total count
    from sqlalchemy import func
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Apply pagination
    from sqlalchemy import desc
    query = query.order_by(desc(Signal.created_at)).offset(offset).limit(limit)
    signals = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": s.id,
                "market_id": s.market_id,
                "token_id": s.token_id,
                "side": s.side,
                "status": s.status,
                "reason": s.reason,
                "edge": float(s.edge) if s.edge else None,
                "confidence": float(s.confidence) if s.confidence else None,
                "price_at_signal": float(s.price_at_signal) if s.price_at_signal else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in signals
        ],
    }


@router.get("/{strategy_name}/stats")
async def get_strategy_stats(
    strategy_name: str,
    db: Session = Depends(get_db),
):
    """
    Get detailed statistics for a strategy.
    """
    registry = get_registry()

    # Check if strategy exists
    if registry.get_strategy_class(strategy_name) is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    signal_counts = _get_signal_counts(db, strategy_name)

    # Get position stats
    from src.executor.models import Position, PositionStatus
    from sqlalchemy import func

    positions = db.execute(
        select(Position).where(Position.strategy_name == strategy_name)
    ).scalars().all()

    total_pnl = sum(float(p.realized_pnl or 0) for p in positions if p.status == PositionStatus.CLOSED.value)
    winning = len([p for p in positions if p.status == PositionStatus.CLOSED.value and float(p.realized_pnl or 0) > 0])
    losing = len([p for p in positions if p.status == PositionStatus.CLOSED.value and float(p.realized_pnl or 0) < 0])
    open_positions = len([p for p in positions if p.status == PositionStatus.OPEN.value])

    return {
        "strategy": strategy_name,
        "signals": {
            "total": signal_counts.get("total", 0),
            "pending": signal_counts.get("pending", 0),
            "approved": signal_counts.get("approved", 0),
            "executed": signal_counts.get("executed", 0),
            "rejected": signal_counts.get("rejected", 0),
        },
        "positions": {
            "total": len(positions),
            "open": open_positions,
            "closed": len(positions) - open_positions,
            "winning": winning,
            "losing": losing,
            "win_rate": winning / (winning + losing) if (winning + losing) > 0 else 0,
        },
        "pnl": {
            "total_realized": total_pnl,
            "average_per_trade": total_pnl / (winning + losing) if (winning + losing) > 0 else 0,
        },
    }


def _get_signal_counts(db: Session, strategy_name: str) -> dict:
    """Get signal counts by status for a strategy."""
    from sqlalchemy import func

    results = db.execute(
        select(Signal.status, func.count(Signal.id))
        .where(Signal.strategy_name == strategy_name)
        .group_by(Signal.status)
    ).all()

    counts = {status: count for status, count in results}
    counts["total"] = sum(counts.values())

    return counts
