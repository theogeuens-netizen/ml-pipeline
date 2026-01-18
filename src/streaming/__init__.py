"""
Streaming Book Imbalance Executor.

Real-time orderbook monitoring with sub-second execution for CRYPTO markets.
Runs as a separate service alongside the polling executor.
"""

from .config import StreamingConfig, load_streaming_config
from .signals import StreamingSignal
from .state import OrderbookState, PriceLevel, StreamingStateManager, MarketInfo
from .strategy import StreamingBookImbalanceStrategy
from .safety import StreamingSafetyChecker, SafetyCheckResult
from .executor import StreamingExecutor, ExecutionResult
from .websocket import StreamingWebSocket
from .runner import StreamingRunner

__all__ = [
    # Config
    "StreamingConfig",
    "load_streaming_config",
    # Signals
    "StreamingSignal",
    # State
    "OrderbookState",
    "PriceLevel",
    "StreamingStateManager",
    "MarketInfo",
    # Strategy
    "StreamingBookImbalanceStrategy",
    # Safety
    "StreamingSafetyChecker",
    "SafetyCheckResult",
    # Executor
    "StreamingExecutor",
    "ExecutionResult",
    # WebSocket
    "StreamingWebSocket",
    # Runner
    "StreamingRunner",
]
