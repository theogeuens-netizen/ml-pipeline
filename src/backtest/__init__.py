"""
Backtesting engine for polymarket-ml strategies.

This package provides P&L simulation capabilities using historical data
from the historical_markets and historical_price_snapshots tables.
"""

from .metrics import (
    TradeRecord,
    EquityPoint,
    PerformanceMetrics,
    calculate_metrics,
    metrics_to_dict,
)
from .staking import (
    calculate_stake,
    calculate_kelly_stake,
)
from .engine import (
    BacktestConfig,
    BacktestResult,
    HistoricalBet,
    run_backtest,
    run_backtest_with_lockup,
    format_backtest_summary,
)
from .data import (
    HistoricalMarket,
    HistoricalPriceSnapshot,
    load_resolved_markets,
    load_price_snapshots,
    generate_bets_from_snapshots,
    get_historical_stats,
)
from .robustness import (
    SplitMetrics,
    SplitResult,
    CategorySplitResult,
    RobustnessResult,
    time_split_backtest,
    liquidity_split_backtest,
    category_split_backtest,
    run_all_robustness_checks,
    format_robustness_results,
)
from .bigquery import (
    BacktestMetrics,
    RobustnessResult as BQRobustnessResult,
    run_bq_backtest,
    run_bq_robustness,
    get_bq_client,
    get_bq_data_stats,
    format_bq_backtest_summary,
    format_bq_robustness_summary,
)

__all__ = [
    # Metrics
    "TradeRecord",
    "EquityPoint",
    "PerformanceMetrics",
    "calculate_metrics",
    "metrics_to_dict",
    # Staking
    "calculate_stake",
    "calculate_kelly_stake",
    # Engine
    "BacktestConfig",
    "BacktestResult",
    "HistoricalBet",
    "run_backtest",
    "run_backtest_with_lockup",
    "format_backtest_summary",
    # Data
    "HistoricalMarket",
    "HistoricalPriceSnapshot",
    "load_resolved_markets",
    "load_price_snapshots",
    "generate_bets_from_snapshots",
    "get_historical_stats",
    # Robustness
    "SplitMetrics",
    "SplitResult",
    "CategorySplitResult",
    "RobustnessResult",
    "time_split_backtest",
    "liquidity_split_backtest",
    "category_split_backtest",
    "run_all_robustness_checks",
    "format_robustness_results",
    # BigQuery (default for backtesting)
    "BacktestMetrics",
    "BQRobustnessResult",
    "run_bq_backtest",
    "run_bq_robustness",
    "get_bq_client",
    "get_bq_data_stats",
    "format_bq_backtest_summary",
    "format_bq_robustness_summary",
]
