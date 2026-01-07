"""
CSGO Tick Router.

Consumes Redis stream and routes ticks to registered strategies.
Main orchestration component of the CSGO trading engine.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Type

from sqlalchemy import and_

from src.db.database import get_session
from src.db.models import CSGOMatch, Market
from src.csgo.signals import consume_csgo_signals
from src.csgo.engine.strategy import CSGOStrategy, Tick, Action
from src.csgo.engine.state import CSGOStateManager
from src.csgo.engine.positions import CSGOPositionManager
from src.csgo.engine.executor import CSGOExecutor, ExecutionResult

logger = logging.getLogger(__name__)


class CSGOTickRouter:
    """
    Main router for CSGO tick processing.

    Responsibilities:
    - Consume ticks from Redis stream
    - Build Tick objects with enriched metadata
    - Filter ticks by format (BO3+) and market type
    - Update position prices
    - Dispatch ticks to registered strategies
    - Execute returned actions

    The router is the central orchestrator of the CSGO trading engine.
    """

    # Global filters applied to all ticks
    ALLOWED_FORMATS = {"BO3", "BO5"}  # Skip BO1
    ALLOWED_MARKET_TYPES = {"moneyline", "child_moneyline"}  # Match + map winners

    def __init__(
        self,
        strategies: Optional[List[CSGOStrategy]] = None,
        state_manager: Optional[CSGOStateManager] = None,
        position_manager: Optional[CSGOPositionManager] = None,
        executor: Optional[CSGOExecutor] = None,
        consumer_group: str = "csgo-engine",
        consumer_name: Optional[str] = None,
    ):
        """
        Initialize tick router.

        Args:
            strategies: List of strategies to register
            state_manager: State manager for position queries
            position_manager: Position manager for lifecycle
            executor: Executor for trade execution
            consumer_group: Redis consumer group name
            consumer_name: Redis consumer name (defaults to engine-{pid})
        """
        self.state = state_manager or CSGOStateManager()
        self.positions = position_manager or CSGOPositionManager(self.state)
        self.executor = executor or CSGOExecutor(self.state, self.positions)

        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"engine-{os.getpid()}"

        # Strategy registry
        self._strategies: List[CSGOStrategy] = []
        if strategies:
            for strategy in strategies:
                self.register_strategy(strategy)

        # Match metadata cache (market_id -> CSGOMatch)
        self._match_cache: Dict[int, CSGOMatch] = {}

        # Token lookup cache (condition_id -> Market)
        self._token_cache: Dict[str, Market] = {}

        # Seen message IDs (for deduplication)
        self._seen_messages: Set[str] = set()
        self._max_seen = 10000

        # Stats
        self._ticks_processed = 0
        self._ticks_filtered = 0
        self._actions_executed = 0
        self._errors = 0

        # Running flag
        self._running = False

    def register_strategy(self, strategy: CSGOStrategy) -> None:
        """
        Register a strategy with the router.

        Args:
            strategy: Strategy instance to register
        """
        # Inject state manager if not already set
        if not hasattr(strategy, 'state') or strategy.state is None:
            strategy.state = self.state

        self._strategies.append(strategy)

        # Initialize strategy state in database (creates record if not exists)
        self.state.get_strategy_state(strategy.name)

        logger.info(f"Registered strategy: {strategy.name} v{strategy.version}")

    def register_strategy_class(self, strategy_class: Type[CSGOStrategy]) -> None:
        """
        Register a strategy class (will be instantiated).

        Args:
            strategy_class: Strategy class to instantiate and register
        """
        strategy = strategy_class(self.state)
        self.register_strategy(strategy)

    async def run(self) -> None:
        """
        Main async loop - consume ticks and dispatch to strategies.

        This is the entry point for the router. It:
        1. Loads match metadata cache
        2. Consumes ticks from Redis stream
        3. Builds and enriches Tick objects
        4. Applies global and strategy filters
        5. Dispatches to strategies
        6. Executes returned actions
        """
        logger.info(
            f"Starting CSGO Tick Router with {len(self._strategies)} strategies, "
            f"consumer={self.consumer_name}"
        )

        self._running = True

        # Load caches
        self._load_match_cache()
        self._load_token_cache()

        # Periodic cache refresh task
        refresh_task = asyncio.create_task(self._refresh_caches_loop())

        try:
            async for signal in consume_csgo_signals(
                consumer_group=self.consumer_group,
                consumer_name=self.consumer_name,
                block_ms=500,
                count=20,
            ):
                if not self._running:
                    break

                try:
                    await self._process_signal(signal)
                except Exception as e:
                    logger.exception(f"Error processing signal: {e}")
                    self._errors += 1

        except asyncio.CancelledError:
            logger.info("Router cancelled")
        finally:
            self._running = False
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

        logger.info(
            f"Router stopped. Stats: ticks={self._ticks_processed}, "
            f"filtered={self._ticks_filtered}, actions={self._actions_executed}, "
            f"errors={self._errors}"
        )

    def stop(self) -> None:
        """Stop the router."""
        self._running = False

    async def _process_signal(self, signal: dict) -> None:
        """
        Process a single signal from Redis stream.

        Args:
            signal: Raw signal dict from Redis
        """
        message_id = signal.get("_message_id", "")

        # Deduplication
        if message_id in self._seen_messages:
            return
        self._seen_messages.add(message_id)
        if len(self._seen_messages) > self._max_seen:
            # Clear oldest half
            self._seen_messages = set(list(self._seen_messages)[self._max_seen // 2:])

        # Build tick
        tick = self._build_tick(signal)
        if not tick:
            return

        # Enrich with match metadata
        tick = self._enrich_tick(tick)

        # Global filters
        if not self._global_filter(tick):
            self._ticks_filtered += 1
            return

        self._ticks_processed += 1

        # Update position prices for this market
        self.positions.update_prices(tick)

        # Dispatch to strategies
        for strategy in self._strategies:
            await self._dispatch_to_strategy(strategy, tick)

    def _build_tick(self, signal: dict) -> Optional[Tick]:
        """
        Build a Tick object from raw signal.

        Args:
            signal: Raw signal dict

        Returns:
            Tick object or None if invalid
        """
        try:
            # Parse market_id
            market_id = signal.get("market_id")
            if not market_id:
                return None
            market_id = int(market_id)

            # Parse timestamp
            timestamp_str = signal.get("timestamp")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)

            # Parse game_start_time
            game_start_str = signal.get("game_start_time")
            game_start_time = None
            if game_start_str:
                try:
                    game_start_time = datetime.fromisoformat(game_start_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            # Parse numeric fields
            def parse_float(key: str) -> Optional[float]:
                val = signal.get(key)
                if val is None or val == "":
                    return None
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            return Tick(
                market_id=market_id,
                condition_id=str(signal.get("condition_id", "")),
                message_id=signal.get("_message_id", ""),
                team_yes=str(signal.get("team_yes", "")),
                team_no=str(signal.get("team_no", "")),
                game_start_time=game_start_time,
                format=signal.get("format"),
                market_type=signal.get("market_type"),
                timestamp=timestamp,
                event_type=str(signal.get("event_type", "unknown")),
                token_type=str(signal.get("token_type", "YES")),
                price=parse_float("price"),
                best_bid=parse_float("best_bid"),
                best_ask=parse_float("best_ask"),
                spread=parse_float("spread"),
                mid_price=parse_float("mid_price"),
                trade_size=parse_float("size"),
                trade_side=signal.get("side"),
                price_velocity_1m=parse_float("price_velocity_1m"),
            )

        except Exception as e:
            logger.warning(f"Failed to build tick from signal: {e}")
            return None

    def _enrich_tick(self, tick: Tick) -> Tick:
        """
        Enrich tick with match metadata and token IDs.

        Args:
            tick: Tick to enrich

        Returns:
            Enriched tick (new object if modified)
        """
        # Get match metadata (stored as dict)
        match = self._match_cache.get(tick.market_id)
        if match:
            # Create new tick with enriched data if needed
            updates = {}

            if not tick.format and match.get("format"):
                updates["format"] = match["format"]
            if not tick.market_type and match.get("market_type"):
                updates["market_type"] = match["market_type"]
            if not tick.game_start_time and match.get("game_start_time"):
                updates["game_start_time"] = match["game_start_time"]
            if not tick.team_yes and match.get("team_yes"):
                updates["team_yes"] = match["team_yes"]
            if not tick.team_no and match.get("team_no"):
                updates["team_no"] = match["team_no"]
            # Use match spread if tick spread is missing or garbage (>50%)
            if match.get("spread") and (tick.spread is None or tick.spread > 0.50):
                updates["spread"] = match["spread"]
            # CRITICAL: Populate BOTH actual prices from match cache
            # YES and NO have separate order books - prices don't sum to 100%
            if match.get("yes_price"):
                updates["actual_yes_mid"] = match["yes_price"]
                # Also set mid_price for backwards compatibility
                if tick.token_type == "YES":
                    updates["mid_price"] = match["yes_price"]
            if match.get("no_price"):
                updates["actual_no_mid"] = match["no_price"]
                if tick.token_type == "NO":
                    updates["mid_price"] = match["no_price"]

            if updates:
                # Tick is frozen, need to create new one with updates
                tick_dict = {
                    "market_id": tick.market_id,
                    "condition_id": tick.condition_id,
                    "message_id": tick.message_id,
                    "team_yes": updates.get("team_yes", tick.team_yes),
                    "team_no": updates.get("team_no", tick.team_no),
                    "game_start_time": updates.get("game_start_time", tick.game_start_time),
                    "format": updates.get("format", tick.format),
                    "market_type": updates.get("market_type", tick.market_type),
                    "timestamp": tick.timestamp,
                    "event_type": tick.event_type,
                    "token_type": tick.token_type,
                    "price": tick.price,
                    "best_bid": tick.best_bid,
                    "best_ask": tick.best_ask,
                    "spread": updates.get("spread", tick.spread),
                    "mid_price": updates.get("mid_price", tick.mid_price),
                    "trade_size": tick.trade_size,
                    "trade_side": tick.trade_side,
                    "price_velocity_1m": tick.price_velocity_1m,
                    "yes_token_id": tick.yes_token_id,
                    "no_token_id": tick.no_token_id,
                    # Actual order book prices (separate for YES and NO)
                    "actual_yes_mid": updates.get("actual_yes_mid", tick.actual_yes_mid),
                    "actual_no_mid": updates.get("actual_no_mid", tick.actual_no_mid),
                }
                tick = Tick(**tick_dict)

        # Get token IDs (stored as dict)
        token_data = self._token_cache.get(tick.condition_id)
        if token_data and (not tick.yes_token_id or not tick.no_token_id):
            tick_dict = {
                "market_id": tick.market_id,
                "condition_id": tick.condition_id,
                "message_id": tick.message_id,
                "team_yes": tick.team_yes,
                "team_no": tick.team_no,
                "game_start_time": tick.game_start_time,
                "format": tick.format,
                "market_type": tick.market_type,
                "timestamp": tick.timestamp,
                "event_type": tick.event_type,
                "token_type": tick.token_type,
                "price": tick.price,
                "best_bid": tick.best_bid,
                "best_ask": tick.best_ask,
                "spread": tick.spread,
                "mid_price": tick.mid_price,
                "trade_size": tick.trade_size,
                "trade_side": tick.trade_side,
                "price_velocity_1m": tick.price_velocity_1m,
                "yes_token_id": token_data.get("yes_token_id"),
                "no_token_id": token_data.get("no_token_id"),
                "actual_yes_mid": tick.actual_yes_mid,
                "actual_no_mid": tick.actual_no_mid,
            }
            tick = Tick(**tick_dict)

        return tick

    def _global_filter(self, tick: Tick) -> bool:
        """
        Apply global filters to a tick.

        Args:
            tick: Tick to filter

        Returns:
            True if tick passes filters, False to filter out
        """
        # Check if market is resolved/closed from cache
        match = self._match_cache.get(tick.market_id)
        if match:
            if match.get("resolved"):
                logger.debug(f"Filtering tick for resolved market {tick.market_id}")
                return False
            if match.get("closed"):
                logger.debug(f"Filtering tick for closed market {tick.market_id}")
                return False

        # Format filter (BO3+ only by default)
        if tick.format and tick.format not in self.ALLOWED_FORMATS:
            return False

        # Market type filter (match winner only)
        if tick.market_type and tick.market_type not in self.ALLOWED_MARKET_TYPES:
            return False

        return True

    async def _dispatch_to_strategy(self, strategy: CSGOStrategy, tick: Tick) -> None:
        """
        Dispatch tick to a strategy and execute any returned action.

        Args:
            strategy: Strategy to dispatch to
            tick: Tick to process
        """
        try:
            # Check strategy-level filters
            if not strategy.filter_tick(tick):
                return

            # Check if strategy has position on this market
            position = self.state.get_position(strategy.name, tick.market_id)
            spread = self.state.get_spread(strategy.name, tick.market_id)

            action: Optional[Action] = None

            if position or spread:
                # Position update path - check for exit/management signals
                action = strategy.on_position_update(position or spread, tick)
            else:
                # Check position limits
                current_count = self.state.position_count(strategy.name)
                if current_count >= strategy.max_positions:
                    return  # At capacity

                # New opportunity path
                action = strategy.on_tick(tick)

            if action:
                # Validate action
                if not self._validate_action(action, strategy):
                    return

                # Execute
                result = self.executor.execute(action, tick)
                self._actions_executed += 1

                if result.success:
                    price_str = f"{result.fill_price:.4f}" if result.fill_price is not None else "N/A"
                    shares_str = f"{result.shares_filled:.2f}" if result.shares_filled is not None else "N/A"
                    logger.info(
                        f"[{strategy.name}] Action executed: {action.action_type.value}, "
                        f"price={price_str}, shares={shares_str}"
                    )
                else:
                    logger.warning(
                        f"[{strategy.name}] Action failed: {action.action_type.value}, "
                        f"error={result.error}"
                    )
                    self._errors += 1

        except Exception as e:
            logger.exception(f"Error dispatching to {strategy.name}: {e}")
            self._errors += 1

    def _validate_action(self, action: Action, strategy: CSGOStrategy) -> bool:
        """
        Validate an action before execution.

        Args:
            action: Action to validate
            strategy: Strategy that generated the action

        Returns:
            True if valid, False otherwise
        """
        # Check size limits
        if action.size_usd and action.size_usd > strategy.max_position_usd:
            logger.warning(
                f"[{strategy.name}] Action size ${action.size_usd} exceeds limit ${strategy.max_position_usd}"
            )
            return False

        if action.yes_size_usd and action.yes_size_usd > strategy.max_position_usd:
            logger.warning(f"[{strategy.name}] YES size exceeds limit")
            return False

        if action.no_size_usd and action.no_size_usd > strategy.max_position_usd:
            logger.warning(f"[{strategy.name}] NO size exceeds limit")
            return False

        return True

    # =========================================================================
    # Cache Management
    # =========================================================================

    def _load_match_cache(self) -> None:
        """Load match metadata from database (excludes resolved markets)."""
        with get_session() as db:
            matches = db.query(CSGOMatch).filter(
                and_(
                    CSGOMatch.subscribed == True,
                    CSGOMatch.resolved == False,  # Skip resolved markets
                )
            ).all()

            # Store as dicts to avoid DetachedInstanceError
            # Include yes_price/no_price from CLOB snapshots (reliable, not raw tick data)
            self._match_cache = {
                m.market_id: {
                    "format": m.format,
                    "market_type": m.market_type,
                    "game_start_time": m.game_start_time,
                    "team_yes": m.team_yes,
                    "team_no": m.team_no,
                    "resolved": m.resolved,
                    "closed": m.closed,
                    "spread": float(m.spread) if m.spread else None,
                    "yes_price": float(m.yes_price) if m.yes_price else None,
                    "no_price": float(m.no_price) if m.no_price else None,
                }
                for m in matches
            }

        logger.info(f"Loaded {len(self._match_cache)} matches into cache")

    def _load_token_cache(self) -> None:
        """Load token IDs from markets table."""
        with get_session() as db:
            # Get markets that have CSGO matches
            market_ids = list(self._match_cache.keys())
            if market_ids:
                markets = db.query(Market).filter(
                    Market.id.in_(market_ids)
                ).all()

                # Store as dicts to avoid DetachedInstanceError
                for m in markets:
                    if m.condition_id:
                        self._token_cache[m.condition_id] = {
                            "yes_token_id": m.yes_token_id,
                            "no_token_id": m.no_token_id,
                        }

        logger.info(f"Loaded {len(self._token_cache)} market tokens into cache")

    async def _refresh_caches_loop(self) -> None:
        """Periodically refresh caches."""
        while self._running:
            await asyncio.sleep(30)  # 30 seconds - sync with CLOB poll rate
            try:
                self._load_match_cache()
                self._load_token_cache()
                logger.debug("Refreshed match and token caches")
            except Exception as e:
                logger.error(f"Failed to refresh caches: {e}")

    # =========================================================================
    # Stats
    # =========================================================================

    def get_stats(self) -> dict:
        """Get router statistics."""
        return {
            "running": self._running,
            "strategies": len(self._strategies),
            "strategy_names": [s.name for s in self._strategies],
            "ticks_processed": self._ticks_processed,
            "ticks_filtered": self._ticks_filtered,
            "actions_executed": self._actions_executed,
            "errors": self._errors,
            "match_cache_size": len(self._match_cache),
            "token_cache_size": len(self._token_cache),
            "consumer_group": self.consumer_group,
            "consumer_name": self.consumer_name,
        }
