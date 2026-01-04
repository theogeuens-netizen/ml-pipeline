"""
CSGO Real-Time Trading Engine.

An isolated, event-driven trading system for CS:GO markets.
Completely separate from the main Polymarket executor.

Components:
- Models: CSGOPosition, CSGOSpread, CSGOTrade, etc.
- Strategy: CSGOStrategy base class, Tick, Action
- State: CSGOStateManager for position queries
- Positions: CSGOPositionManager for lifecycle
- Executor: CSGOExecutor for paper trading
- Router: CSGOTickRouter for stream consumption
"""

from src.csgo.engine.models import (
    CSGOPosition,
    CSGOPositionLeg,
    CSGOPositionStatus,
    CSGOLegType,
    CSGOSpread,
    CSGOSpreadStatus,
    CSGOTrade,
    CSGOStrategyState,
    CSGOStrategyMarketState,
)
from src.csgo.engine.strategy import (
    CSGOStrategy,
    Tick,
    Action,
    ActionType,
)
from src.csgo.engine.state import CSGOStateManager
from src.csgo.engine.positions import CSGOPositionManager
from src.csgo.engine.executor import CSGOExecutor, ExecutionResult
from src.csgo.engine.router import CSGOTickRouter

__all__ = [
    # Models
    "CSGOPosition",
    "CSGOPositionLeg",
    "CSGOPositionStatus",
    "CSGOLegType",
    "CSGOSpread",
    "CSGOSpreadStatus",
    "CSGOTrade",
    "CSGOStrategyState",
    "CSGOStrategyMarketState",
    # Strategy interface
    "CSGOStrategy",
    "Tick",
    "Action",
    "ActionType",
    # Components
    "CSGOStateManager",
    "CSGOPositionManager",
    "CSGOExecutor",
    "ExecutionResult",
    "CSGOTickRouter",
]
