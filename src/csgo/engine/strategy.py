"""
CSGO Strategy Interface.

Provides the base class and data structures for event-driven CSGO strategies.
Strategies receive real-time ticks and emit actions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.csgo.engine.state import CSGOStateManager


class ActionType(Enum):
    """What the strategy wants to do."""
    OPEN_LONG = "open_long"           # Buy a single token
    OPEN_SPREAD = "open_spread"       # Buy both YES and NO
    CLOSE = "close"                   # Close entire position
    PARTIAL_CLOSE = "partial_close"   # Close part of position
    ADD = "add"                       # Add to existing position
    REBALANCE = "rebalance"           # Adjust spread ratio


@dataclass(frozen=True)
class Tick:
    """
    Real-time tick from Redis stream.

    Immutable snapshot of market state at a point in time.
    All price-related fields are for the YES token unless specified.
    """
    # Identity
    market_id: int
    condition_id: str
    message_id: str  # Redis message ID for deduplication

    # Teams
    team_yes: str
    team_no: str

    # Match info
    game_start_time: Optional[datetime]
    format: Optional[str]  # BO1, BO3, BO5
    market_type: Optional[str]  # moneyline, child_moneyline

    # Event metadata
    timestamp: datetime
    event_type: str  # trade, book, price_change
    token_type: str  # YES, NO - which token this event is for

    # Prices (for the token specified in token_type)
    price: Optional[float]  # Last trade price
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread: Optional[float]
    mid_price: Optional[float]

    # Trade details (if event_type == 'trade')
    trade_size: Optional[float] = None
    trade_side: Optional[str] = None  # BUY, SELL

    # Derived metrics
    price_velocity_1m: Optional[float] = None

    # Token IDs for trading
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None

    # Actual order book prices (from match cache, not derived)
    # IMPORTANT: These are the REAL prices from separate order books
    # YES + NO may NOT sum to 100% due to bid-ask spreads and market inefficiencies
    actual_yes_mid: Optional[float] = None
    actual_no_mid: Optional[float] = None

    @property
    def yes_price(self) -> Optional[float]:
        """
        Get YES token price.

        Priority:
        1. actual_yes_mid (real order book price from match cache)
        2. mid_price/price if this tick is for YES token
        3. Derived from NO price (1 - no_price) as last resort
        """
        # Prefer actual price from order book
        if self.actual_yes_mid is not None:
            return self.actual_yes_mid

        # Fall back to tick's mid_price if this is a YES token tick
        if self.token_type == "YES":
            return self.mid_price or self.price

        # Last resort: derive from NO price (less accurate)
        if self.token_type == "NO":
            raw = self.mid_price or self.price
            return 1 - raw if raw is not None else None

        return None

    @property
    def no_price(self) -> Optional[float]:
        """
        Get NO token price.

        Priority:
        1. actual_no_mid (real order book price from match cache)
        2. mid_price/price if this tick is for NO token
        3. Derived from YES price (1 - yes_price) as last resort
        """
        # Prefer actual price from order book
        if self.actual_no_mid is not None:
            return self.actual_no_mid

        # Fall back to tick's mid_price if this is a NO token tick
        if self.token_type == "NO":
            return self.mid_price or self.price

        # Last resort: derive from YES price (less accurate)
        if self.token_type == "YES":
            raw = self.mid_price or self.price
            return 1 - raw if raw is not None else None

        return None

    @property
    def is_in_play(self) -> bool:
        """Check if the game has started (in-play)."""
        if not self.game_start_time:
            return False
        return datetime.now(timezone.utc) >= self.game_start_time

    @property
    def minutes_since_start(self) -> Optional[float]:
        """Minutes since game start, or None if not started."""
        if not self.game_start_time:
            return None
        delta = datetime.now(timezone.utc) - self.game_start_time
        return delta.total_seconds() / 60

    def __repr__(self) -> str:
        return (
            f"Tick({self.team_yes} vs {self.team_no}, "
            f"{self.token_type}={self.price:.3f if self.price else 'N/A'}, "
            f"type={self.event_type})"
        )


@dataclass
class Action:
    """
    Strategy output - what to execute.

    Strategies return an Action to trigger trade execution.
    """
    action_type: ActionType

    # Target market
    market_id: int
    condition_id: str

    # For OPEN_LONG
    token_type: Optional[str] = None  # YES or NO
    size_usd: Optional[float] = None

    # For PARTIAL_CLOSE
    close_pct: Optional[float] = None  # 0.0-1.0

    # For OPEN_SPREAD
    yes_size_usd: Optional[float] = None
    no_size_usd: Optional[float] = None

    # For ADD
    add_size_usd: Optional[float] = None

    # Strategy identity (required for proper capital tracking)
    strategy_name: Optional[str] = None

    # Context
    reason: str = ""
    trigger_price: Optional[float] = None

    # Execution hints
    limit_price: Optional[float] = None  # None = market order
    urgency: str = "normal"  # normal, high (affects slippage tolerance)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"Action({self.action_type.value}, market={self.market_id}, reason={self.reason[:50]})"


class CSGOStrategy(ABC):
    """
    Base class for CSGO trading strategies.

    Strategies are stateful - they track their own positions and
    make decisions based on tick stream + current position state.

    Lifecycle:
    1. Strategy receives tick via on_tick() or on_position_update()
    2. Strategy returns Action or None
    3. Executor executes the action
    4. Position state updates automatically

    Example:
        class MyStrategy(CSGOStrategy):
            name = "my_strategy"

            def on_tick(self, tick: Tick) -> Optional[Action]:
                if tick.yes_price and 0.45 <= tick.yes_price <= 0.55:
                    return Action(
                        action_type=ActionType.OPEN_SPREAD,
                        market_id=tick.market_id,
                        condition_id=tick.condition_id,
                        yes_size_usd=20.0,
                        no_size_usd=20.0,
                        reason="Entry at 50/50",
                    )
                return None
    """

    # Identity
    name: str = "base"
    version: str = "1.0.0"

    # Filters (which ticks to receive)
    formats: List[str] = ["BO3", "BO5"]  # Skip BO1 by default
    market_types: List[str] = ["moneyline"]  # Match winner only

    # Capital limits
    max_position_usd: float = 100.0
    max_positions: int = 5
    min_spread: float = 0.0  # Minimum spread to trade (0 = no filter)
    max_spread: float = 0.10  # Maximum spread to trade

    # Extreme price protection - don't trade near-resolved markets
    min_entry_price: float = 0.05  # Don't buy tokens below 5%
    max_entry_price: float = 0.95  # Don't buy tokens above 95%

    def __init__(self, state_manager: "CSGOStateManager"):
        """
        Initialize with reference to state manager.

        Args:
            state_manager: CSGOStateManager for position/state queries
        """
        self.state = state_manager

    @abstractmethod
    def on_tick(self, tick: Tick) -> Optional[Action]:
        """
        Process a tick and optionally return an action.

        Called for every tick that passes filters when NO position exists.
        Use for entry logic.

        Should be FAST (<10ms) - avoid database queries here.

        Args:
            tick: Market tick data

        Returns:
            Action to execute, or None
        """
        pass

    def on_position_update(self, position, tick: Tick) -> Optional[Action]:
        """
        Called when a position owned by this strategy receives a tick.

        Use for exit logic, stop losses, rebalancing, etc.

        Args:
            position: CSGOPosition or CSGOSpread object
            tick: Current tick for that market

        Returns:
            Action to execute, or None
        """
        return None

    def filter_tick(self, tick: Tick) -> bool:
        """
        Pre-filter ticks before on_tick.

        Override for custom filtering logic.
        Default filters by format and market_type.
        """
        # Format filter (BO1, BO3, BO5)
        if tick.format and tick.format not in self.formats:
            return False

        # Market type filter (moneyline = match winner)
        if tick.market_type and tick.market_type not in self.market_types:
            return False

        # Spread filter
        if tick.spread is not None:
            if tick.spread < self.min_spread or tick.spread > self.max_spread:
                return False

        # CRITICAL: Extreme price filter - don't trade near-resolved markets
        # These have terrible liquidity (50%+ spreads) and are usually decided
        yes_price = tick.yes_price
        if yes_price is not None:
            if yes_price < self.min_entry_price or yes_price > self.max_entry_price:
                return False

        return True

    def get_state(self) -> dict:
        """Return strategy state for debugging."""
        return {
            "name": self.name,
            "version": self.version,
            "formats": self.formats,
            "market_types": self.market_types,
            "max_position_usd": self.max_position_usd,
            "max_positions": self.max_positions,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, version={self.version})>"
