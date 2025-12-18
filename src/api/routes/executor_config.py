"""
Executor configuration API endpoints.

Provides endpoints for:
- Getting and updating executor configuration
- Switching trading mode
- Risk and sizing settings
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.executor.config import (
    get_config,
    reload_config,
    TradingMode,
    SizingMethod,
    ExecutorConfig,
    RiskConfig,
    SizingConfig,
    ExecutionConfig,
    FilterConfig,
    SettingsConfig,
)
from src.executor.execution import get_executor
from src.executor.execution.order_types import OrderType

router = APIRouter(prefix="/executor/config")


class ModeUpdateRequest(BaseModel):
    """Request body for mode update."""
    mode: str = Field(..., description="Trading mode: 'paper' or 'live'")


class RiskConfigUpdate(BaseModel):
    """Request body for risk config update."""
    max_position_usd: Optional[float] = None
    max_total_exposure_usd: Optional[float] = None
    max_positions: Optional[int] = None
    max_drawdown_pct: Optional[float] = None


class SizingConfigUpdate(BaseModel):
    """Request body for sizing config update."""
    method: Optional[str] = None
    fixed_amount_usd: Optional[float] = None
    kelly_fraction: Optional[float] = None
    max_size_usd: Optional[float] = None


class ExecutionConfigUpdate(BaseModel):
    """Request body for execution config update."""
    default_order_type: Optional[str] = None
    limit_offset_bps: Optional[int] = None
    spread_timeout_seconds: Optional[int] = None


class FilterConfigUpdate(BaseModel):
    """Request body for filter config update."""
    min_liquidity_usd: Optional[float] = None
    excluded_keywords: Optional[list[str]] = None


@router.get("")
async def get_executor_config():
    """
    Get current executor configuration.
    """
    config = get_config()

    return {
        "mode": config.mode.value,
        "settings": {
            "scan_interval_seconds": config.settings.scan_interval_seconds,
            "log_level": config.settings.log_level,
        },
        "risk": {
            "max_position_usd": config.risk.max_position_usd,
            "max_total_exposure_usd": config.risk.max_total_exposure_usd,
            "max_positions": config.risk.max_positions,
            "max_drawdown_pct": config.risk.max_drawdown_pct,
        },
        "sizing": {
            "method": config.sizing.method.value,
            "fixed_amount_usd": config.sizing.fixed_amount_usd,
            "kelly_fraction": config.sizing.kelly_fraction,
            "max_size_usd": config.sizing.max_size_usd,
        },
        "execution": {
            "default_order_type": config.execution.default_order_type.value,
            "limit_offset_bps": config.execution.limit_offset_bps,
            "spread_timeout_seconds": config.execution.spread_timeout_seconds,
        },
        "filters": {
            "min_liquidity_usd": config.filters.min_liquidity_usd,
            "excluded_keywords": config.filters.excluded_keywords,
        },
        "strategies": {
            name: {
                "enabled": cfg.enabled,
                "params": cfg.params,
            }
            for name, cfg in config.strategies.items()
        },
    }


@router.get("/mode")
async def get_trading_mode():
    """
    Get current trading mode.
    """
    config = get_config()
    return {
        "mode": config.mode.value,
        "available_modes": [m.value for m in TradingMode],
    }


@router.post("/mode")
async def set_trading_mode(request: ModeUpdateRequest):
    """
    Set trading mode (paper or live).

    CAUTION: Switching to live mode will execute real trades!
    """
    try:
        new_mode = TradingMode(request.mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{request.mode}'. Must be 'paper' or 'live'."
        )

    config = get_config()
    old_mode = config.mode

    if new_mode == old_mode:
        return {
            "success": True,
            "message": f"Already in {new_mode.value} mode",
            "mode": new_mode.value,
        }

    # Update config
    config.mode = new_mode

    # Notify executor
    executor = get_executor(config)
    executor.reload_config(config)

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "message": f"Switched from {old_mode.value} to {new_mode.value} mode",
        "mode": new_mode.value,
        "warning": "Live mode will execute REAL trades!" if new_mode == TradingMode.LIVE else None,
    }


@router.post("/risk")
async def update_risk_config(update: RiskConfigUpdate):
    """
    Update risk configuration.
    """
    config = get_config()

    if update.max_position_usd is not None:
        config.risk.max_position_usd = update.max_position_usd
    if update.max_total_exposure_usd is not None:
        config.risk.max_total_exposure_usd = update.max_total_exposure_usd
    if update.max_positions is not None:
        config.risk.max_positions = update.max_positions
    if update.max_drawdown_pct is not None:
        config.risk.max_drawdown_pct = update.max_drawdown_pct

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "risk": {
            "max_position_usd": config.risk.max_position_usd,
            "max_total_exposure_usd": config.risk.max_total_exposure_usd,
            "max_positions": config.risk.max_positions,
            "max_drawdown_pct": config.risk.max_drawdown_pct,
        },
    }


@router.post("/sizing")
async def update_sizing_config(update: SizingConfigUpdate):
    """
    Update sizing configuration.
    """
    config = get_config()

    if update.method is not None:
        try:
            config.sizing.method = SizingMethod(update.method)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sizing method '{update.method}'"
            )
    if update.fixed_amount_usd is not None:
        config.sizing.fixed_amount_usd = update.fixed_amount_usd
    if update.kelly_fraction is not None:
        config.sizing.kelly_fraction = update.kelly_fraction
    if update.max_size_usd is not None:
        config.sizing.max_size_usd = update.max_size_usd

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "sizing": {
            "method": config.sizing.method.value,
            "fixed_amount_usd": config.sizing.fixed_amount_usd,
            "kelly_fraction": config.sizing.kelly_fraction,
            "max_size_usd": config.sizing.max_size_usd,
        },
    }


@router.post("/execution")
async def update_execution_config(update: ExecutionConfigUpdate):
    """
    Update execution configuration.
    """
    config = get_config()

    if update.default_order_type is not None:
        try:
            config.execution.default_order_type = OrderType(update.default_order_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid order type '{update.default_order_type}'"
            )
    if update.limit_offset_bps is not None:
        config.execution.limit_offset_bps = update.limit_offset_bps
    if update.spread_timeout_seconds is not None:
        config.execution.spread_timeout_seconds = update.spread_timeout_seconds

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "execution": {
            "default_order_type": config.execution.default_order_type.value,
            "limit_offset_bps": config.execution.limit_offset_bps,
            "spread_timeout_seconds": config.execution.spread_timeout_seconds,
        },
    }


@router.post("/filters")
async def update_filter_config(update: FilterConfigUpdate):
    """
    Update market filter configuration.
    """
    config = get_config()

    if update.min_liquidity_usd is not None:
        config.filters.min_liquidity_usd = update.min_liquidity_usd
    if update.excluded_keywords is not None:
        config.filters.excluded_keywords = update.excluded_keywords

    # Note: In production, would persist to config.yaml

    return {
        "success": True,
        "filters": {
            "min_liquidity_usd": config.filters.min_liquidity_usd,
            "excluded_keywords": config.filters.excluded_keywords,
        },
    }


@router.post("/reload")
async def reload_executor_config():
    """
    Reload configuration from config.yaml file.
    """
    try:
        config = reload_config()

        # Update executor
        executor = get_executor(config)
        executor.reload_config(config)

        return {
            "success": True,
            "message": "Configuration reloaded from file",
            "mode": config.mode.value,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload config: {str(e)}"
        )
