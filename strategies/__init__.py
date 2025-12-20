"""
Strategy loading for config-driven strategies.

Strategies are defined in strategies.yaml and instantiated from
type classes in strategies/types/.

Usage:
    from strategies import load_strategies, get_strategy_by_name

    strategies = load_strategies()
    for s in strategies:
        signals = list(s.scan(markets))

    # Or load a specific strategy
    strat = get_strategy_by_name("esports_no_1h")
"""

from strategies.base import Strategy, Signal, Side, MarketData
from strategies.loader import (
    load_strategies,
    get_strategy_by_name,
    get_strategy_config,
    list_strategy_names,
    validate_config,
)

__all__ = [
    # Base classes
    "Strategy",
    "Signal",
    "Side",
    "MarketData",
    # Loader functions
    "load_strategies",
    "get_strategy_by_name",
    "get_strategy_config",
    "list_strategy_names",
    "validate_config",
]

# Performance tracking (requires sqlalchemy)
try:
    from strategies.performance import PerformanceTracker, StrategyMetrics
    __all__.extend(["PerformanceTracker", "StrategyMetrics"])
except ImportError:
    pass  # sqlalchemy not available
