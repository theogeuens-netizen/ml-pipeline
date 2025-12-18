"""
Pydantic models for executor configuration.

All configuration can be set via config.yaml or the React UI.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class TradingMode(str, Enum):
    """Trading mode."""
    PAPER = "paper"
    LIVE = "live"


class OrderType(str, Enum):
    """Order execution type."""
    MARKET = "market"  # Cross spread immediately
    LIMIT = "limit"    # Post at offset from mid
    SPREAD = "spread"  # Post to capture spread, fall back to market


class SizingMethod(str, Enum):
    """Position sizing method."""
    FIXED = "fixed"
    KELLY = "kelly"
    VOLATILITY_SCALED = "volatility_scaled"


class RiskConfig(BaseModel):
    """Risk management configuration."""
    max_position_usd: float = Field(default=100.0, ge=1.0, description="Max USD per position")
    max_total_exposure_usd: float = Field(default=1000.0, ge=1.0, description="Max total USD exposure")
    max_positions: int = Field(default=20, ge=1, description="Max number of open positions")
    max_drawdown_pct: float = Field(default=0.15, ge=0.01, le=1.0, description="Max drawdown before stopping")


class ExecutionConfig(BaseModel):
    """Order execution configuration."""
    default_order_type: OrderType = Field(default=OrderType.LIMIT, description="Default order type")
    limit_offset_bps: int = Field(default=50, ge=0, le=500, description="Limit order offset in basis points")
    spread_timeout_seconds: int = Field(default=30, ge=5, le=300, description="Spread mode timeout before crossing")


class SizingConfig(BaseModel):
    """Position sizing configuration."""
    method: SizingMethod = Field(default=SizingMethod.FIXED, description="Sizing method")
    fixed_amount_usd: float = Field(default=25.0, ge=1.0, description="Fixed size in USD")
    kelly_fraction: float = Field(default=0.25, ge=0.01, le=1.0, description="Kelly fraction (0.25 = quarter-Kelly)")
    max_size_usd: Optional[float] = Field(default=None, ge=1.0, description="Max size cap in USD")


class StrategyExecutionConfig(BaseModel):
    """Per-strategy execution override."""
    order_type: Optional[OrderType] = Field(default=None, description="Override default order type")
    limit_offset_bps: Optional[int] = Field(default=None, ge=0, le=500, description="Override limit offset")


class StrategySizingConfig(BaseModel):
    """Per-strategy sizing override."""
    method: Optional[SizingMethod] = Field(default=None, description="Override sizing method")
    fixed_amount_usd: Optional[float] = Field(default=None, ge=1.0, description="Override fixed amount")
    max_size_usd: Optional[float] = Field(default=None, ge=1.0, description="Override max size")


class StrategyConfig(BaseModel):
    """Configuration for a single strategy."""
    enabled: bool = Field(default=False, description="Whether strategy is active")
    params: dict = Field(default_factory=dict, description="Strategy-specific parameters")
    execution: Optional[StrategyExecutionConfig] = Field(default=None, description="Execution overrides")
    sizing: Optional[StrategySizingConfig] = Field(default=None, description="Sizing overrides")


class FilterConfig(BaseModel):
    """Global market filtering configuration."""
    min_liquidity_usd: float = Field(default=1000.0, ge=0, description="Minimum market liquidity")
    excluded_keywords: list[str] = Field(default_factory=list, description="Keywords to exclude from market questions")


class SettingsConfig(BaseModel):
    """General settings."""
    scan_interval_seconds: int = Field(default=30, ge=5, le=300, description="How often to scan for opportunities")
    log_level: str = Field(default="INFO", description="Logging level")


class ExecutorConfig(BaseModel):
    """
    Root configuration for the executor.

    Loaded from config.yaml and can be modified via UI.
    """
    mode: TradingMode = Field(default=TradingMode.PAPER, description="Trading mode (paper/live)")
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    sizing: SizingConfig = Field(default_factory=SizingConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    strategies: dict[str, StrategyConfig] = Field(default_factory=dict, description="Per-strategy configuration")

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v):
        if isinstance(v, str):
            return TradingMode(v.lower())
        return v

    def get_strategy_config(self, strategy_name: str) -> StrategyConfig:
        """Get config for a strategy, with defaults if not configured."""
        return self.strategies.get(strategy_name, StrategyConfig())

    def get_effective_execution(self, strategy_name: str) -> ExecutionConfig:
        """Get effective execution config, applying strategy overrides."""
        strategy = self.get_strategy_config(strategy_name)
        if strategy.execution is None:
            return self.execution

        # Apply overrides
        return ExecutionConfig(
            default_order_type=strategy.execution.order_type or self.execution.default_order_type,
            limit_offset_bps=strategy.execution.limit_offset_bps if strategy.execution.limit_offset_bps is not None else self.execution.limit_offset_bps,
            spread_timeout_seconds=self.execution.spread_timeout_seconds,
        )

    def get_effective_sizing(self, strategy_name: str) -> SizingConfig:
        """Get effective sizing config, applying strategy overrides."""
        strategy = self.get_strategy_config(strategy_name)
        if strategy.sizing is None:
            return self.sizing

        # Apply overrides
        return SizingConfig(
            method=strategy.sizing.method or self.sizing.method,
            fixed_amount_usd=strategy.sizing.fixed_amount_usd if strategy.sizing.fixed_amount_usd is not None else self.sizing.fixed_amount_usd,
            kelly_fraction=self.sizing.kelly_fraction,
            max_size_usd=strategy.sizing.max_size_usd if strategy.sizing.max_size_usd is not None else self.sizing.max_size_usd,
        )
