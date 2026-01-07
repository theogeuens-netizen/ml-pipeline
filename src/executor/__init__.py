"""
Polymarket Executor - Trading execution system.

Provides:
- Strategy framework with config-driven strategies
- Paper and live trading modes
- Position and risk management
- Real-time signal generation and execution

Usage:
    from src.executor.config import get_config, TradingMode
    from src.executor.execution import get_executor, Executor
    from strategies.base import Strategy, Signal
    from src.executor.engine import ExecutorRunner
"""

__version__ = "1.0.0"

# Convenience imports
from src.executor.config import (
    get_config,
    reload_config,
    ExecutorConfig,
    TradingMode,
)
from src.executor.execution import (
    Executor,
    get_executor,
    PaperExecutor,
    LiveExecutor,
)
from strategies.base import (
    Strategy,
    Signal,
    MarketData,
)
from src.executor.engine import (
    ExecutorRunner,
    MarketScanner,
)

__all__ = [
    # Config
    "get_config",
    "reload_config",
    "ExecutorConfig",
    "TradingMode",
    # Execution
    "Executor",
    "get_executor",
    "PaperExecutor",
    "LiveExecutor",
    # Strategies
    "Strategy",
    "Signal",
    "MarketData",
    # Engine
    "ExecutorRunner",
    "MarketScanner",
]
