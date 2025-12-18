"""
Polymarket Executor - Trading execution system.

Provides:
- Strategy framework with 5 built-in strategies
- Paper and live trading modes
- Position and risk management
- Real-time signal generation and execution

Usage:
    from src.executor.config import get_config, TradingMode
    from src.executor.execution import get_executor, Executor
    from src.executor.strategies import get_registry, Signal
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
from src.executor.strategies import (
    Strategy,
    Signal,
    get_registry,
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
    "get_registry",
    # Engine
    "ExecutorRunner",
    "MarketScanner",
]
