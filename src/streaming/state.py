"""
Streaming state management.

Maintains in-memory orderbook state and position tracking for fast evaluation.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class PriceLevel:
    """Single price level in orderbook."""

    price: float
    size: float


@dataclass
class MarketInfo:
    """Minimal market info for streaming executor."""

    id: int
    condition_id: str
    yes_token_id: str
    no_token_id: str
    question: str
    hours_to_close: float
    category_l1: str


@dataclass
class OrderbookState:
    """
    In-memory orderbook state for a single token.

    Updated on each WebSocket "book" event.
    """

    token_id: str
    bids: list[PriceLevel] = field(default_factory=list)
    asks: list[PriceLevel] = field(default_factory=list)
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def imbalance(self) -> float:
        """
        Calculate book imbalance.

        Formula: (bid_depth - ask_depth) / (bid_depth + ask_depth)
        Uses top 5 levels for calculation.

        Returns:
            Imbalance between -1 and 1
            Positive = bid-heavy (price likely to rise)
            Negative = ask-heavy (price likely to fall)
        """
        bid_depth = sum(level.size for level in self.bids[:5])
        ask_depth = sum(level.size for level in self.asks[:5])
        total = bid_depth + ask_depth

        if total == 0:
            return 0.0

        return (bid_depth - ask_depth) / total

    @property
    def best_bid(self) -> Optional[float]:
        """Best bid price (highest buy order)."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Best ask price (lowest sell order)."""
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid price between best bid and ask."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        """Spread between best ask and best bid."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def age_seconds(self) -> float:
        """Seconds since last update."""
        now = datetime.now(timezone.utc)
        last = self.last_update
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds()

    @property
    def bid_depth_5(self) -> float:
        """Total bid depth at top 5 levels."""
        return sum(level.size for level in self.bids[:5])

    @property
    def ask_depth_5(self) -> float:
        """Total ask depth at top 5 levels."""
        return sum(level.size for level in self.asks[:5])


class StreamingStateManager:
    """
    Manages all in-memory state for streaming executor.

    Provides fast lookups for:
    - Orderbook state by token
    - Market info by ID
    - Position tracking
    - Cooldown tracking
    """

    def __init__(self):
        # Token ID -> OrderbookState
        self.orderbooks: dict[str, OrderbookState] = {}

        # Market ID -> MarketInfo (from DB)
        self.market_info: dict[int, MarketInfo] = {}

        # Token ID -> Market ID (reverse lookup)
        self.token_to_market: dict[str, int] = {}

        # Strategy:Market -> last entry timestamp (cooldowns)
        self.cooldowns: dict[str, datetime] = {}

        # Strategy -> set of market IDs with open positions (fast lookup)
        self.open_positions: dict[str, set[int]] = defaultdict(set)

        # Stats for monitoring
        self.stats = {
            "book_updates": 0,
            "signals_generated": 0,
            "signals_executed": 0,
        }

    def update_orderbook(self, token_id: str, bids: list, asks: list):
        """
        Update orderbook from WebSocket event.

        Args:
            token_id: Token ID
            bids: List of bid dicts with 'price' and 'size'
            asks: List of ask dicts with 'price' and 'size'
        """
        try:
            parsed_bids = [
                PriceLevel(float(b.get("price", 0)), float(b.get("size", 0)))
                for b in bids
                if float(b.get("size", 0)) > 0
            ]
            parsed_asks = [
                PriceLevel(float(a.get("price", 0)), float(a.get("size", 0)))
                for a in asks
                if float(a.get("size", 0)) > 0
            ]

            # Sort bids descending (highest first), asks ascending (lowest first)
            parsed_bids.sort(key=lambda x: x.price, reverse=True)
            parsed_asks.sort(key=lambda x: x.price)

            self.orderbooks[token_id] = OrderbookState(
                token_id=token_id,
                bids=parsed_bids,
                asks=parsed_asks,
                last_update=datetime.now(timezone.utc),
            )

            self.stats["book_updates"] += 1

        except Exception as e:
            logger.warning(f"Failed to update orderbook for {token_id}: {e}")

    def get_orderbook(self, token_id: str) -> Optional[OrderbookState]:
        """Get orderbook state for a token."""
        return self.orderbooks.get(token_id)

    def get_imbalance(self, token_id: str) -> Optional[float]:
        """Get current imbalance for token."""
        book = self.orderbooks.get(token_id)
        return book.imbalance if book else None

    def get_market_for_token(self, token_id: str) -> Optional[MarketInfo]:
        """Get market info for a token."""
        market_id = self.token_to_market.get(token_id)
        if market_id is None:
            return None
        return self.market_info.get(market_id)

    def is_in_cooldown(
        self, strategy_name: str, market_id: int, cooldown_minutes: float
    ) -> bool:
        """
        Check if market is in cooldown for this strategy.

        Args:
            strategy_name: Strategy name
            market_id: Market ID
            cooldown_minutes: Cooldown duration in minutes

        Returns:
            True if still in cooldown
        """
        key = f"{strategy_name}:{market_id}"
        last_entry = self.cooldowns.get(key)

        if last_entry is None:
            return False

        if last_entry.tzinfo is None:
            last_entry = last_entry.replace(tzinfo=timezone.utc)

        elapsed = (datetime.now(timezone.utc) - last_entry).total_seconds() / 60
        return elapsed < cooldown_minutes

    def set_cooldown(self, strategy_name: str, market_id: int):
        """
        Record entry time for cooldown.

        Args:
            strategy_name: Strategy name
            market_id: Market ID
        """
        key = f"{strategy_name}:{market_id}"
        self.cooldowns[key] = datetime.now(timezone.utc)

    def has_open_position(self, strategy_name: str, market_id: int) -> bool:
        """Check if strategy has open position on market."""
        return market_id in self.open_positions.get(strategy_name, set())

    def add_position(self, strategy_name: str, market_id: int):
        """Record that a position was opened."""
        self.open_positions[strategy_name].add(market_id)

    def remove_position(self, strategy_name: str, market_id: int):
        """Record that a position was closed."""
        self.open_positions[strategy_name].discard(market_id)

    def get_position_count(self, strategy_name: str) -> int:
        """Get number of open positions for strategy."""
        return len(self.open_positions.get(strategy_name, set()))

    def sync_positions_from_db(self, db: Session, strategy_name: str, is_paper: bool):
        """
        Sync open positions from database.

        Called on startup and periodically to ensure consistency.

        Args:
            db: Database session
            strategy_name: Strategy name to sync
            is_paper: Whether this is paper or live trading
        """
        from src.executor.models import Position, PositionStatus

        try:
            positions = (
                db.query(Position)
                .filter(
                    Position.strategy_name == strategy_name,
                    Position.is_paper == is_paper,
                    Position.status == PositionStatus.OPEN.value,
                )
                .all()
            )

            self.open_positions[strategy_name] = {p.market_id for p in positions}

            logger.debug(
                f"Synced {len(self.open_positions[strategy_name])} positions for {strategy_name}"
            )

        except Exception as e:
            logger.error(f"Failed to sync positions: {e}")

    def set_markets(self, markets: list[MarketInfo]):
        """
        Update market info and token mappings.

        Args:
            markets: List of MarketInfo from selector
        """
        self.market_info = {m.id: m for m in markets}
        self.token_to_market = {}

        for m in markets:
            if m.yes_token_id:
                self.token_to_market[m.yes_token_id] = m.id
            if m.no_token_id:
                self.token_to_market[m.no_token_id] = m.id

    def get_subscribed_tokens(self) -> list[str]:
        """Get list of all token IDs to subscribe to."""
        tokens = []
        for m in self.market_info.values():
            if m.yes_token_id:
                tokens.append(m.yes_token_id)
            if m.no_token_id:
                tokens.append(m.no_token_id)
        return tokens

    def get_stats(self) -> dict:
        """Get statistics for monitoring."""
        return {
            "markets": len(self.market_info),
            "tokens_subscribed": len(self.token_to_market),
            "orderbooks_cached": len(self.orderbooks),
            "book_updates": self.stats["book_updates"],
            "signals_generated": self.stats["signals_generated"],
            "signals_executed": self.stats["signals_executed"],
        }

    def increment_stat(self, name: str, amount: int = 1):
        """Increment a stat counter."""
        if name in self.stats:
            self.stats[name] += amount
