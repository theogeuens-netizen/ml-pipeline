"""
CSGO State Manager.

Provides query interface for strategy state, positions, and spreads.
Used by strategies to check their current state without direct DB access.
"""

import logging
from decimal import Decimal
from typing import Optional, List, Any

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.csgo.engine.models import (
    CSGOPosition,
    CSGOPositionStatus,
    CSGOSpread,
    CSGOSpreadStatus,
    CSGOStrategyState,
    CSGOStrategyMarketState,
)

logger = logging.getLogger(__name__)


class StateDict(dict):
    """
    Dict subclass that allows attribute access.

    This avoids DetachedInstanceError by not holding ORM objects,
    while allowing existing code using attribute syntax to work.

    Example:
        d = StateDict({"foo": 1, "bar": 2})
        d["foo"]  # 1
        d.foo     # 1
        d.get("foo")  # 1
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'StateDict' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(f"'StateDict' object has no attribute '{name}'")


class CSGOStateManager:
    """
    Query interface for CSGO strategy state.

    Provides read-only access to positions, spreads, and strategy state.
    All methods are safe to call from within strategy on_tick handlers.
    """

    def __init__(self, db: Optional[Session] = None):
        """
        Initialize state manager.

        Args:
            db: Optional database session. If None, creates new session per query.
        """
        self._db = db
        # In-memory caches for fast lookups
        self._position_cache: dict[tuple[str, int, Optional[str]], StateDict] = {}
        self._spread_cache: dict[tuple[str, int], CSGOSpread] = {}
        self._strategy_state_cache: dict[str, CSGOStrategyState] = {}
        self._market_state_cache: dict[tuple[str, int], CSGOStrategyMarketState] = {}

    def _get_db(self) -> Session:
        """Get database session."""
        if self._db:
            return self._db
        return get_session().__enter__()

    # =========================================================================
    # Position Queries
    # =========================================================================

    def get_position(
        self,
        strategy_name: str,
        market_id: int,
        token_type: Optional[str] = None,
    ) -> Optional[StateDict]:
        """
        Get open position for strategy on market.

        Args:
            strategy_name: Strategy name
            market_id: Market ID
            token_type: Optional token type filter ('YES' or 'NO')

        Returns:
            StateDict with position fields if exists, None otherwise.
            Supports both dict key access (pos["id"]) and attribute access (pos.id).
        """
        # Include token_type in cache key to handle spread positions (YES + NO)
        cache_key = (strategy_name, market_id, token_type)
        if cache_key in self._position_cache:
            return self._position_cache[cache_key]

        with get_session() as db:
            query = db.query(CSGOPosition).filter(
                CSGOPosition.strategy_name == strategy_name,
                CSGOPosition.market_id == market_id,
                CSGOPosition.status.in_([
                    CSGOPositionStatus.OPEN.value,
                    CSGOPositionStatus.PARTIAL.value,
                ]),
            )
            if token_type:
                query = query.filter(CSGOPosition.token_type == token_type)

            position = query.first()
            if position:
                pos_dict = self._position_to_dict(position)
                self._position_cache[cache_key] = pos_dict
                return pos_dict
            return None

    def _position_to_dict(self, p: CSGOPosition) -> StateDict:
        """Convert position ORM object to StateDict."""
        return StateDict({
            "id": p.id,
            "strategy_name": p.strategy_name,
            "market_id": p.market_id,
            "condition_id": p.condition_id,
            "token_id": p.token_id,
            "token_type": p.token_type,
            "side": p.side,
            "initial_shares": p.initial_shares,
            "remaining_shares": p.remaining_shares,
            "avg_entry_price": p.avg_entry_price,
            "cost_basis": p.cost_basis,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "realized_pnl": p.realized_pnl,
            "spread_id": p.spread_id,
            "team_yes": p.team_yes,
            "team_no": p.team_no,
            "game_start_time": p.game_start_time,
            "format": p.format,
            "status": p.status,
            "close_reason": p.close_reason,
            "opened_at": p.opened_at,
            "closed_at": p.closed_at,
        })

    def get_open_positions(self, strategy_name: str) -> List[dict]:
        """
        Get all open positions for a strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            List of dicts with position fields
        """
        with get_session() as db:
            positions = db.query(CSGOPosition).filter(
                CSGOPosition.strategy_name == strategy_name,
                CSGOPosition.status == CSGOPositionStatus.OPEN.value,
            ).all()
            return [self._position_to_dict(p) for p in positions]

    def get_positions_for_market(self, market_id: int) -> List[dict]:
        """
        Get all open positions for a market (across all strategies).

        Args:
            market_id: Market ID

        Returns:
            List of dicts with position fields
        """
        with get_session() as db:
            positions = db.query(CSGOPosition).filter(
                CSGOPosition.market_id == market_id,
                CSGOPosition.status == CSGOPositionStatus.OPEN.value,
            ).all()
            return [self._position_to_dict(p) for p in positions]

    def position_count(self, strategy_name: str) -> int:
        """
        Count open positions for a strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            Number of open positions
        """
        with get_session() as db:
            return db.query(CSGOPosition).filter(
                CSGOPosition.strategy_name == strategy_name,
                CSGOPosition.status == CSGOPositionStatus.OPEN.value,
            ).count()

    # =========================================================================
    # Spread Queries
    # =========================================================================

    def get_spread(
        self,
        strategy_name: str,
        market_id: int,
    ) -> Optional[StateDict]:
        """
        Get open spread for strategy on market.

        Args:
            strategy_name: Strategy name
            market_id: Market ID

        Returns:
            StateDict with spread fields if exists, None otherwise.
            Supports both dict key access (spread["id"]) and attribute access (spread.id).
        """
        cache_key = (strategy_name, market_id)
        if cache_key in self._spread_cache:
            return self._spread_cache[cache_key]

        with get_session() as db:
            spread = db.query(CSGOSpread).filter(
                CSGOSpread.strategy_name == strategy_name,
                CSGOSpread.market_id == market_id,
                CSGOSpread.status == CSGOSpreadStatus.OPEN.value,
            ).first()

            if spread:
                spread_dict = self._spread_to_dict(spread)
                self._spread_cache[cache_key] = spread_dict
                return spread_dict
            return None

    def _spread_to_dict(self, s: CSGOSpread) -> StateDict:
        """Convert spread ORM object to StateDict."""
        return StateDict({
            "id": s.id,
            "strategy_name": s.strategy_name,
            "market_id": s.market_id,
            "condition_id": s.condition_id,
            "spread_type": s.spread_type,
            "yes_position_id": s.yes_position_id,
            "no_position_id": s.no_position_id,
            "total_cost_basis": s.total_cost_basis,
            "total_realized_pnl": s.total_realized_pnl,
            "total_unrealized_pnl": s.total_unrealized_pnl,
            "team_yes": s.team_yes,
            "team_no": s.team_no,
            "entry_yes_price": s.entry_yes_price,
            "status": s.status,
            "opened_at": s.opened_at,
            "closed_at": s.closed_at,
        })

    def get_open_spreads(self, strategy_name: str) -> List[dict]:
        """
        Get all open spreads for a strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            List of dicts with spread fields
        """
        with get_session() as db:
            spreads = db.query(CSGOSpread).filter(
                CSGOSpread.strategy_name == strategy_name,
                CSGOSpread.status == CSGOSpreadStatus.OPEN.value,
            ).all()
            return [self._spread_to_dict(s) for s in spreads]

    # =========================================================================
    # Strategy State (Capital & Performance)
    # =========================================================================

    def get_strategy_state(self, strategy_name: str) -> StateDict:
        """
        Get or create strategy state.

        Args:
            strategy_name: Strategy name

        Returns:
            StateDict with strategy state fields (not ORM object to avoid detached session errors)
        """
        if strategy_name in self._strategy_state_cache:
            return self._strategy_state_cache[strategy_name]

        with get_session() as db:
            state = db.query(CSGOStrategyState).filter(
                CSGOStrategyState.strategy_name == strategy_name
            ).first()

            if not state:
                # Create with defaults
                state = CSGOStrategyState(
                    strategy_name=strategy_name,
                    allocated_usd=Decimal("400"),
                    available_usd=Decimal("400"),
                )
                db.add(state)
                db.commit()
                db.refresh(state)

            # Convert to StateDict before session closes to avoid DetachedInstanceError
            state_dict = StateDict({
                "id": state.id,
                "strategy_name": state.strategy_name,
                "allocated_usd": state.allocated_usd,
                "available_usd": state.available_usd,
                "total_realized_pnl": state.total_realized_pnl,
                "total_unrealized_pnl": state.total_unrealized_pnl,
                "trade_count": state.trade_count,
                "win_count": state.win_count,
                "loss_count": state.loss_count,
                "is_active": state.is_active,
            })

            self._strategy_state_cache[strategy_name] = state_dict
            return state_dict

    def has_capacity(self, strategy_name: str, size_usd: float) -> bool:
        """
        Check if strategy has capital for a new position.

        Args:
            strategy_name: Strategy name
            size_usd: Required capital

        Returns:
            True if sufficient capital available
        """
        state = self.get_strategy_state(strategy_name)
        return float(state["available_usd"]) >= size_usd

    def get_available_usd(self, strategy_name: str) -> float:
        """
        Get available capital for a strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            Available capital in USD
        """
        state = self.get_strategy_state(strategy_name)
        return float(state["available_usd"])

    # =========================================================================
    # Per-Market Strategy State (for multi-stage strategies)
    # =========================================================================

    def get_market_state(
        self,
        strategy_name: str,
        market_id: int,
    ) -> StateDict:
        """
        Get or create per-market state for strategy.

        Creates new state with stage='WAITING' if not exists.

        Args:
            strategy_name: Strategy name
            market_id: Market ID

        Returns:
            StateDict with market state fields (not ORM object to avoid detached session errors)
        """
        cache_key = (strategy_name, market_id)
        if cache_key in self._market_state_cache:
            return self._market_state_cache[cache_key]

        with get_session() as db:
            state = db.query(CSGOStrategyMarketState).filter(
                CSGOStrategyMarketState.strategy_name == strategy_name,
                CSGOStrategyMarketState.market_id == market_id,
            ).first()

            if not state:
                # Create with defaults
                state = CSGOStrategyMarketState(
                    strategy_name=strategy_name,
                    market_id=market_id,
                    condition_id="",  # Will be set on first use
                    stage="WAITING",
                )
                db.add(state)
                db.commit()
                db.refresh(state)

            # Convert to StateDict before session closes
            state_dict = StateDict({
                "id": state.id,
                "strategy_name": state.strategy_name,
                "market_id": state.market_id,
                "condition_id": state.condition_id,
                "stage": state.stage,
                "entry_price": state.entry_price,
                "switch_price": state.switch_price,
                "exit_price": state.exit_price,
                "high_water_mark": state.high_water_mark,
                "low_water_mark": state.low_water_mark,
                "switches_count": state.switches_count,
                "reentries_count": state.reentries_count,
                "custom_state": state.custom_state,
                "team_yes": state.team_yes,
                "team_no": state.team_no,
                "current_side": state.current_side,
                "is_active": state.is_active,
            })

            self._market_state_cache[cache_key] = state_dict
            return state_dict

    def save_market_state(self, state_dict: dict) -> None:
        """
        Persist market state changes.

        Args:
            state_dict: Dict with state fields to save
        """
        with get_session() as db:
            # Find existing or create new
            state = db.query(CSGOStrategyMarketState).filter(
                CSGOStrategyMarketState.strategy_name == state_dict["strategy_name"],
                CSGOStrategyMarketState.market_id == state_dict["market_id"],
            ).first()

            if state:
                # Update existing
                for key, value in state_dict.items():
                    if key != "id" and hasattr(state, key):
                        setattr(state, key, value)
            else:
                # Create new
                state = CSGOStrategyMarketState(**{k: v for k, v in state_dict.items() if k != "id"})
                db.add(state)

            db.commit()

        # Update cache
        cache_key = (state_dict["strategy_name"], state_dict["market_id"])
        self._market_state_cache[cache_key] = state_dict

    def get_active_market_states(
        self,
        strategy_name: str,
    ) -> List[StateDict]:
        """
        Get all active (non-resolved) market states for strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            List of StateDicts with market state fields
        """
        with get_session() as db:
            states = db.query(CSGOStrategyMarketState).filter(
                CSGOStrategyMarketState.strategy_name == strategy_name,
                CSGOStrategyMarketState.is_active == True,
            ).all()

            # Convert to StateDicts before session closes
            return [
                StateDict({
                    "id": s.id,
                    "strategy_name": s.strategy_name,
                    "market_id": s.market_id,
                    "condition_id": s.condition_id,
                    "stage": s.stage,
                    "entry_price": s.entry_price,
                    "switch_price": s.switch_price,
                    "exit_price": s.exit_price,
                    "high_water_mark": s.high_water_mark,
                    "low_water_mark": s.low_water_mark,
                    "switches_count": s.switches_count,
                    "reentries_count": s.reentries_count,
                    "custom_state": s.custom_state,
                    "team_yes": s.team_yes,
                    "team_no": s.team_no,
                    "current_side": s.current_side,
                    "is_active": s.is_active,
                })
                for s in states
            ]

    def deactivate_market_state(
        self,
        strategy_name: str,
        market_id: int,
    ) -> None:
        """
        Mark market state as inactive (match resolved).

        Args:
            strategy_name: Strategy name
            market_id: Market ID
        """
        with get_session() as db:
            db.query(CSGOStrategyMarketState).filter(
                CSGOStrategyMarketState.strategy_name == strategy_name,
                CSGOStrategyMarketState.market_id == market_id,
            ).update({"is_active": False})
            db.commit()

        # Clear cache
        cache_key = (strategy_name, market_id)
        self._market_state_cache.pop(cache_key, None)

    # =========================================================================
    # Cache Management
    # =========================================================================

    def clear_cache(self) -> None:
        """Clear all in-memory caches."""
        self._position_cache.clear()
        self._spread_cache.clear()
        self._strategy_state_cache.clear()
        self._market_state_cache.clear()

    def invalidate_position(self, strategy_name: str, market_id: int, token_type: Optional[str] = None) -> None:
        """Invalidate cached position(s).

        If token_type is None, invalidates all cached positions for this market
        (None, YES, and NO keys). Otherwise invalidates specific token_type only.
        """
        if token_type:
            # Invalidate specific token type
            cache_key = (strategy_name, market_id, token_type)
            self._position_cache.pop(cache_key, None)
        else:
            # Invalidate all token types for this market
            for tt in (None, "YES", "NO"):
                cache_key = (strategy_name, market_id, tt)
                self._position_cache.pop(cache_key, None)

    def invalidate_spread(self, strategy_name: str, market_id: int) -> None:
        """Invalidate cached spread."""
        cache_key = (strategy_name, market_id)
        self._spread_cache.pop(cache_key, None)

    def invalidate_strategy_state(self, strategy_name: str) -> None:
        """Invalidate cached strategy state."""
        self._strategy_state_cache.pop(strategy_name, None)
