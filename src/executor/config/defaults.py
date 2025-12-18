"""
Default configuration values for the executor.

Conservative defaults focused on paper trading safety.
"""

from .schema import (
    ExecutorConfig,
    TradingMode,
    OrderType,
    SizingMethod,
    RiskConfig,
    ExecutionConfig,
    SizingConfig,
    FilterConfig,
    SettingsConfig,
    StrategyConfig,
)

# Default strategy parameters
STRATEGY_DEFAULTS = {
    "longshot_yes": StrategyConfig(
        enabled=True,
        params={
            "min_probability": 0.92,
            "max_probability": 0.99,
            "max_hours_to_expiry": 72,
            "min_liquidity_usd": 5000,
        },
    ),
    "longshot_no": StrategyConfig(
        enabled=True,
        params={
            "max_probability": 0.08,
            "min_hours_to_expiry": 24,
            "min_liquidity_usd": 5000,
        },
    ),
    "mean_reversion": StrategyConfig(
        enabled=False,
        params={
            "spike_threshold": 0.15,
            "lookback_hours": 24,
            "min_volume_24h": 10000,
        },
    ),
    "term_structure": StrategyConfig(
        enabled=False,
        params={
            "min_violation": 0.03,
            "min_liquidity_usd": 5000,
        },
    ),
    "volatility_hedge": StrategyConfig(
        enabled=False,
        params={
            "entry_min_probability": 0.50,
            "entry_max_probability": 0.75,
            "min_hours_to_resolution": 1,
            "max_hours_to_resolution": 48,
            "hedge_trigger_price_increase": 0.15,
            "hedge_underdog_max_price": 0.15,
            "hedge_allocation_pct": 0.40,
            "categories": ["sports", "esports", "crypto"],
        },
    ),
}


def get_default_config() -> ExecutorConfig:
    """Get the default executor configuration."""
    return ExecutorConfig(
        mode=TradingMode.PAPER,
        settings=SettingsConfig(
            scan_interval_seconds=30,
            log_level="INFO",
        ),
        risk=RiskConfig(
            max_position_usd=100.0,
            max_total_exposure_usd=1000.0,
            max_positions=20,
            max_drawdown_pct=0.15,
        ),
        execution=ExecutionConfig(
            default_order_type=OrderType.LIMIT,
            limit_offset_bps=50,
            spread_timeout_seconds=30,
        ),
        sizing=SizingConfig(
            method=SizingMethod.FIXED,
            fixed_amount_usd=25.0,
            kelly_fraction=0.25,
        ),
        filters=FilterConfig(
            min_liquidity_usd=1000.0,
            excluded_keywords=["celebrity", "influencer"],
        ),
        strategies=STRATEGY_DEFAULTS.copy(),
    )
