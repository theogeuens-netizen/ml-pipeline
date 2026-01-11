"""
Unified CSGO Trading Engine.

Combines WebSocket event handling and strategy execution in a single process.
No Redis streams - direct function calls for simplicity and reliability.

Architecture:
- WebSocket receives events (trades, book changes, price changes)
- Events trigger direct callbacks (no pub/sub)
- Prices come from csgo_matches table (CLOB API source, updated every 5s)
- Strategies see reliable prices, not garbage WebSocket data
"""

import asyncio
import json
import logging
import signal as sig
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Callable, List

import websockets
from sqlalchemy import and_, or_

from src.config.settings import settings
from src.db.database import get_session
from src.db.models import CSGOMatch, Market, CSGOPriceTick

from src.csgo.engine.strategy import Tick, Action, ActionType
from src.csgo.engine.executor import CSGOExecutor
from src.csgo.engine.positions import CSGOPositionManager
from src.csgo.engine.state import CSGOStateManager

logger = logging.getLogger(__name__)

# Configuration
SUBSCRIPTION_REFRESH_SECONDS = 60
HEALTH_CHECK_SECONDS = 30
TICK_BUFFER_SIZE = 50
TICK_FLUSH_SECONDS = 5
PERIODIC_TICK_SECONDS = 5  # Generate ticks every 5s even without WebSocket events
PRICE_STALENESS_THRESHOLD_SECONDS = 15  # Reject trades if prices older than this
CLEANUP_INTERVAL_TICKS = 12  # Run cleanup every 60s (12 * 5s)


class UnifiedCSGOEngine:
    """
    Unified CSGO trading engine.

    Single process handling:
    - WebSocket connection to Polymarket
    - Event processing and tick generation
    - Strategy routing and execution
    - Position management

    Key principle: Prices ALWAYS come from csgo_matches table (CLOB API),
    never from WebSocket events (which have garbage spread/price data).
    """

    def __init__(self):
        # WebSocket state
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.reconnect_delay = 5.0

        # Token tracking
        self.subscribed_tokens: set[str] = set()
        self.token_to_match: dict[str, dict] = {}  # token_id -> match info

        # Event tracking for velocity calculation
        self.price_history: dict[str, list[tuple[datetime, float]]] = {}
        self.last_activity: datetime = datetime.now(timezone.utc)

        # Tick buffer for DB persistence (charts)
        self.tick_buffer: list[dict] = []
        self.last_tick_flush: datetime = datetime.now(timezone.utc)

        # Strategy components
        self.state = CSGOStateManager()
        self.positions = CSGOPositionManager(self.state)
        self.executor = CSGOExecutor(self.state, self.positions)
        self._strategies: List = []

        # Stats
        self._ticks_processed = 0
        self._actions_executed = 0
        self._errors = 0

        # Deduplication
        self._seen_messages: set[str] = set()
        self._max_seen = 10000

        # Cleanup counter (runs every CLEANUP_INTERVAL_TICKS iterations)
        self._cleanup_counter = 0

    def register_strategy(self, strategy) -> None:
        """Register a strategy for tick processing."""
        strategy.state = self.state
        self._strategies.append(strategy)
        logger.info(f"Registered strategy: {strategy.name} v{strategy.version}")

    async def start(self) -> None:
        """Start the unified engine."""
        self.running = True
        logger.info("=" * 60)
        logger.info("Unified CSGO Engine Starting")
        logger.info("=" * 60)
        logger.info(f"Strategies: {[s.name for s in self._strategies]}")

        while self.running:
            try:
                await self._connect_and_run()
            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket closed: code={e.code}, reason={e.reason}")
            except Exception as e:
                logger.error(f"Engine error: {e}")
                self._errors += 1

            if self.running:
                logger.info(f"Reconnecting in {self.reconnect_delay}s")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 1.5, 60.0)

    async def _connect_and_run(self) -> None:
        """Connect to WebSocket and process events."""
        logger.info(f"Connecting to {settings.websocket_url}")

        async with websockets.connect(
            settings.websocket_url,
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            self.ws = ws
            self.reconnect_delay = 5.0
            self.last_activity = datetime.now(timezone.utc)  # Reset on connect
            logger.info("WebSocket connected")

            # Initial subscription
            await self._update_subscriptions()

            # Start background tasks
            tasks = [
                asyncio.create_task(self._subscription_loop()),
                asyncio.create_task(self._health_check_loop()),
                asyncio.create_task(self._tick_flush_loop()),
                asyncio.create_task(self._periodic_tick_loop()),  # Evaluate strategies even without WS events
            ]

            try:
                async for message in ws:
                    await self._handle_message(message)
            finally:
                for task in tasks:
                    task.cancel()
                await self._flush_ticks()
                for task in tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def _subscription_loop(self) -> None:
        """Refresh subscriptions periodically."""
        while self.running:
            await asyncio.sleep(SUBSCRIPTION_REFRESH_SECONDS)
            try:
                await self._update_subscriptions()
            except Exception as e:
                logger.error(f"Subscription update failed: {e}")

    async def _health_check_loop(self) -> None:
        """Monitor connection health."""
        while self.running:
            await asyncio.sleep(HEALTH_CHECK_SECONDS)
            if (datetime.now(timezone.utc) - self.last_activity).total_seconds() > 120:
                logger.warning("Connection stale, forcing reconnect")
                if self.ws:
                    await self.ws.close()
                return

    async def _tick_flush_loop(self) -> None:
        """Flush tick buffer to database periodically."""
        while self.running:
            await asyncio.sleep(TICK_FLUSH_SECONDS)
            await self._flush_ticks()

    async def _periodic_tick_loop(self) -> None:
        """
        Generate synthetic ticks periodically for strategy evaluation.

        This ensures time-based entries (3 min, 5 min windows) are not missed
        even when no WebSocket trades are happening on the market.

        Also runs periodic cleanup of resolved positions.
        """
        while self.running:
            await asyncio.sleep(PERIODIC_TICK_SECONDS)

            try:
                # Run cleanup periodically (every 60s)
                self._cleanup_counter += 1
                if self._cleanup_counter >= CLEANUP_INTERVAL_TICKS:
                    self._cleanup_counter = 0
                    try:
                        closed_count = self.positions.cleanup_resolved_positions()
                        if closed_count > 0:
                            logger.info(f"Cleanup: closed {closed_count} resolved positions")
                    except Exception as e:
                        logger.error(f"Position cleanup error: {e}")

                # Generate a tick for each subscribed match
                for token_id, match_info in self.token_to_match.items():
                    # Only process YES tokens to avoid duplicate ticks
                    if match_info.get("token_type") != "YES":
                        continue

                    await self._generate_periodic_tick(match_info)

            except Exception as e:
                logger.error(f"Periodic tick error: {e}")

    async def _generate_periodic_tick(self, match_info: dict) -> None:
        """Generate a synthetic tick for strategy evaluation."""
        market_id = match_info["market_id"]

        # Get prices from DB
        prices = self._get_prices_from_db(market_id)
        if not prices:
            return

        # Build synthetic tick
        tick = Tick(
            market_id=market_id,
            condition_id=match_info["condition_id"],
            message_id=f"periodic:{market_id}:{datetime.now(timezone.utc).timestamp()}",
            team_yes=match_info.get("team_yes", ""),
            team_no=match_info.get("team_no", ""),
            game_start_time=match_info.get("game_start_time"),
            format=match_info.get("format"),
            market_type=match_info.get("market_type"),
            timestamp=datetime.now(timezone.utc),
            event_type="periodic",  # Mark as periodic tick
            token_type="YES",
            price=prices["yes_price"],
            best_bid=prices.get("best_bid"),   # Actual bid from CLOB
            best_ask=prices.get("best_ask"),   # Actual ask from CLOB
            spread=prices["spread"],           # Calculated: ask - bid
            mid_price=prices["yes_price"],
            trade_size=None,
            trade_side=None,
            yes_token_id=prices.get("yes_token_id"),
            no_token_id=prices.get("no_token_id"),
            # Actual order book prices (both YES and NO from DB)
            actual_yes_mid=prices["yes_price"],
            actual_no_mid=prices["no_price"],
        )

        # Skip global filter for periodic ticks - we want to evaluate all matches
        # (the strategies will do their own filtering)

        self._ticks_processed += 1

        # Store CLOB prices for charting (5-second candles from reliable source)
        tick_data = {
            "market_id": market_id,
            "timestamp": datetime.now(timezone.utc),
            "token_type": "YES",
            "event_type": "clob_poll",
            "price": prices["yes_price"],
            "best_bid": prices.get("best_bid"),   # Actual bid from CLOB
            "best_ask": prices.get("best_ask"),   # Actual ask from CLOB
            "spread": prices["spread"],
            "trade_size": None,
            "trade_side": None,
        }
        self.tick_buffer.append(tick_data)

        # Update position prices
        self.positions.update_prices(tick)

        # Dispatch to strategies
        for strategy in self._strategies:
            await self._dispatch_to_strategy(strategy, tick)

    async def _update_subscriptions(self) -> None:
        """Update WebSocket subscriptions for active matches."""
        from src.csgo.discovery import get_matches_for_subscription

        with get_session() as db:
            matches = get_matches_for_subscription(db, hours_ahead=6.0)

            new_tokens: set[str] = set()
            new_token_to_match: dict[str, dict] = {}

            for match in matches:
                market = db.query(Market).filter(
                    Market.condition_id == match.condition_id
                ).first()

                if not market or not market.yes_token_id or not market.no_token_id:
                    continue

                for token_id, token_type in [
                    (market.yes_token_id, "YES"),
                    (market.no_token_id, "NO"),
                ]:
                    new_tokens.add(token_id)
                    new_token_to_match[token_id] = {
                        "match_id": match.id,
                        "market_id": market.id,
                        "condition_id": match.condition_id,
                        "token_type": token_type,
                        "team_yes": match.team_yes,
                        "team_no": match.team_no,
                        "game_start_time": match.game_start_time,
                        "format": match.format,
                        "market_type": match.market_type,
                    }

        # Unsubscribe removed tokens
        tokens_to_remove = self.subscribed_tokens - new_tokens
        if tokens_to_remove and self.ws:
            msg = {"type": "market", "assets_ids": list(tokens_to_remove), "action": "unsubscribe"}
            await self.ws.send(json.dumps(msg))

        # Subscribe new tokens
        tokens_to_add = new_tokens - self.subscribed_tokens
        if tokens_to_add and self.ws:
            msg = {"type": "market", "assets_ids": list(tokens_to_add)}
            await self.ws.send(json.dumps(msg))
            logger.info(f"Subscribed to {len(tokens_to_add)} new tokens")

        self.subscribed_tokens = new_tokens
        self.token_to_match = new_token_to_match

        # Update subscription status in DB
        with get_session() as db:
            match_ids = {m["match_id"] for m in new_token_to_match.values()}
            if match_ids:
                db.query(CSGOMatch).filter(CSGOMatch.id.in_(match_ids)).update(
                    {"subscribed": True}, synchronize_session=False
                )

            # Unsubscribe finished matches
            db.query(CSGOMatch).filter(
                and_(
                    CSGOMatch.subscribed == True,
                    or_(CSGOMatch.resolved == True, CSGOMatch.closed == True),
                )
            ).update({"subscribed": False}, synchronize_session=False)
            db.commit()

        logger.info(f"Subscribed to {len(new_tokens)} tokens ({len(matches)} matches)")

    async def _handle_message(self, raw_message: str) -> None:
        """Handle incoming WebSocket message."""
        self.last_activity = datetime.now(timezone.utc)

        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        if not isinstance(data, dict):
            return

        # Handle price_changes format
        if "price_changes" in data:
            for change in data.get("price_changes", []):
                asset_id = change.get("asset_id")
                if asset_id and asset_id in self.token_to_match:
                    await self._process_event(
                        self.token_to_match[asset_id],
                        {
                            "event_type": "price_change",
                            "asset_id": asset_id,
                            "size": float(change.get("size", 0)),
                            "side": change.get("side"),
                        }
                    )
            return

        # Handle individual events
        asset_id = data.get("asset_id")
        if not asset_id or asset_id not in self.token_to_match:
            return

        await self._process_event(self.token_to_match[asset_id], data)

    async def _process_event(self, match_info: dict, event_data: dict) -> None:
        """
        Process a WebSocket event.

        Key change from old architecture:
        - We DON'T use prices from event_data
        - We ALWAYS query csgo_matches for current prices
        - WebSocket just tells us "something happened"
        """
        market_id = match_info["market_id"]
        event_type = event_data.get("event_type", "unknown")

        # Get RELIABLE prices from database (CLOB API source)
        prices = self._get_prices_from_db(market_id)
        if not prices:
            return

        # Build tick with reliable prices
        tick = Tick(
            market_id=market_id,
            condition_id=match_info["condition_id"],
            message_id=f"{market_id}:{datetime.now(timezone.utc).timestamp()}",
            team_yes=match_info.get("team_yes", ""),
            team_no=match_info.get("team_no", ""),
            game_start_time=match_info.get("game_start_time"),
            format=match_info.get("format"),
            market_type=match_info.get("market_type"),
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            token_type=match_info["token_type"],
            # PRICES FROM DB, NOT WEBSOCKET
            price=prices["yes_price"] if match_info["token_type"] == "YES" else prices["no_price"],
            best_bid=None,  # Not needed - using mid price
            best_ask=None,
            spread=prices["spread"],
            mid_price=prices["yes_price"] if match_info["token_type"] == "YES" else prices["no_price"],
            # Trade details from WebSocket (this is fine - it's event data, not prices)
            trade_size=event_data.get("size"),
            trade_side=event_data.get("side"),
            yes_token_id=prices.get("yes_token_id"),
            no_token_id=prices.get("no_token_id"),
            # Actual order book prices (both YES and NO from DB)
            actual_yes_mid=prices["yes_price"],
            actual_no_mid=prices["no_price"],
        )

        # Buffer for charts
        self._buffer_tick(match_info, event_data, tick)

        # Deduplicate (don't process same market twice in quick succession)
        dedup_key = f"{market_id}:{tick.timestamp.second}"
        if dedup_key in self._seen_messages:
            return
        self._seen_messages.add(dedup_key)
        if len(self._seen_messages) > self._max_seen:
            self._seen_messages = set(list(self._seen_messages)[self._max_seen // 2:])

        # Global filter
        if not self._passes_global_filter(tick):
            return

        self._ticks_processed += 1

        # Update position prices
        self.positions.update_prices(tick)

        # Dispatch to strategies
        for strategy in self._strategies:
            await self._dispatch_to_strategy(strategy, tick)

    def _get_prices_from_db(self, market_id: int, check_staleness: bool = True) -> Optional[dict]:
        """
        Get current prices from csgo_matches table.

        This is the SINGLE SOURCE OF TRUTH for prices.
        Updated every 5 seconds by Celery task from CLOB API.

        Args:
            market_id: Market ID to look up
            check_staleness: If True, reject prices older than threshold

        Returns:
            Dict with prices or None if prices are missing/stale
        """
        with get_session() as db:
            match = db.query(CSGOMatch).filter(
                CSGOMatch.market_id == market_id
            ).first()

            if not match or not match.yes_price:
                return None

            # Check for stale prices
            if check_staleness and match.last_status_check:
                age_seconds = (datetime.now(timezone.utc) - match.last_status_check).total_seconds()
                if age_seconds > PRICE_STALENESS_THRESHOLD_SECONDS:
                    logger.warning(
                        f"Stale prices for market {market_id}: {age_seconds:.1f}s old "
                        f"(threshold: {PRICE_STALENESS_THRESHOLD_SECONDS}s)"
                    )
                    return None

            # Get token IDs
            market = db.query(Market).filter(Market.id == market_id).first()

            # Calculate spread from actual bid/ask - the DB spread field is garbage
            best_bid = float(match.best_bid) if match.best_bid else None
            best_ask = float(match.best_ask) if match.best_ask else None

            # Compute real spread from order book prices
            if best_bid is not None and best_ask is not None and best_bid > 0:
                computed_spread = best_ask - best_bid
                # Sanity check: spread should be positive and reasonable
                if computed_spread < 0 or computed_spread > 0.50:
                    # Something is wrong with the data - use conservative fallback
                    computed_spread = 0.10  # Assume 10% spread when data is bad
            else:
                # No bid/ask data - use conservative default
                computed_spread = 0.10

            return {
                "yes_price": float(match.yes_price),
                "no_price": float(match.no_price) if match.no_price else 1 - float(match.yes_price),
                "spread": computed_spread,  # Computed from bid/ask, not the garbage DB field
                "best_bid": best_bid,
                "best_ask": best_ask,
                "yes_token_id": market.yes_token_id if market else None,
                "no_token_id": market.no_token_id if market else None,
            }

    def _passes_global_filter(self, tick: Tick) -> bool:
        """Apply global filters."""
        # Must have market type
        if not tick.market_type:
            return False

        # Only allow moneyline and child_moneyline
        if tick.market_type not in {"moneyline", "child_moneyline"}:
            return False

        # Format required for moneyline, optional for child_moneyline (map winners)
        if tick.market_type == "moneyline" and not tick.format:
            return False

        return True

    async def _dispatch_to_strategy(self, strategy, tick: Tick) -> None:
        """Dispatch tick to a strategy and handle any action."""
        try:
            # Check if strategy has position on this market (needed for filter decision)
            position = self.state.get_position(strategy.name, tick.market_id)
            spread = self.state.get_spread(strategy.name, tick.market_id)
            has_position = bool(position or spread)

            # Check strategy filters (spread filter only applies to entries, not exits)
            if not self._strategy_accepts_tick(strategy, tick, has_position=has_position):
                return

            if has_position:
                action = strategy.on_position_update(position or spread, tick)
            else:
                # Check position limits before allowing new entry
                current_count = self.state.position_count(strategy.name)
                if current_count >= getattr(strategy, 'max_positions', 5):
                    return  # At capacity, skip new entries
                action = strategy.on_tick(tick)

            if action:
                # Validate action before execution (includes exit spread validation)
                if not self._validate_action(action, strategy, tick):
                    return
                await self._execute_action(strategy, action, tick)

        except Exception as e:
            logger.error(f"[{strategy.name}] Error: {e}")
            self._errors += 1

    def _strategy_accepts_tick(self, strategy, tick: Tick, has_position: bool = False) -> bool:
        """Check if strategy accepts this tick based on filters.

        Args:
            strategy: Strategy to check
            tick: Tick to validate
            has_position: If True, skip spread filter (allow position management on wide spreads)
        """
        # Format filter
        if hasattr(strategy, 'formats') and strategy.formats:
            if tick.format not in strategy.formats:
                return False

        # Market type filter
        if hasattr(strategy, 'market_types') and strategy.market_types:
            if tick.market_type not in strategy.market_types:
                return False

        # Call strategy's filter_tick method for ENTRY only (not position management)
        # Position exits have separate spread validation in executor
        if not has_position and hasattr(strategy, 'filter_tick'):
            if not strategy.filter_tick(tick):
                return False

        return True

    def _validate_action(self, action: Action, strategy, tick: Tick) -> bool:
        """
        Validate an action before execution (hard-reject on limit violations).

        Args:
            action: Action to validate
            strategy: Strategy that generated the action
            tick: Current tick (for spread validation)

        Returns:
            True if valid, False to reject
        """
        max_position_usd = getattr(strategy, 'max_position_usd', 100.0)

        # Check single position size limit
        if action.size_usd and action.size_usd > max_position_usd:
            logger.warning(
                f"[{strategy.name}] REJECTED: size ${action.size_usd:.2f} exceeds "
                f"limit ${max_position_usd:.2f}"
            )
            return False

        # Check spread YES leg size
        if action.yes_size_usd and action.yes_size_usd > max_position_usd:
            logger.warning(
                f"[{strategy.name}] REJECTED: YES size ${action.yes_size_usd:.2f} exceeds "
                f"limit ${max_position_usd:.2f}"
            )
            return False

        # Check spread NO leg size
        if action.no_size_usd and action.no_size_usd > max_position_usd:
            logger.warning(
                f"[{strategy.name}] REJECTED: NO size ${action.no_size_usd:.2f} exceeds "
                f"limit ${max_position_usd:.2f}"
            )
            return False

        # Exit spread validation - don't execute exits when spread is too wide
        # This prevents selling at terrible prices when orderbook is one-sided
        if action.action_type in (ActionType.CLOSE, ActionType.PARTIAL_CLOSE):
            max_exit_spread = getattr(strategy, 'max_exit_spread', 0.15)  # Default 15% max exit spread
            if tick.spread is not None and tick.spread > max_exit_spread:
                logger.warning(
                    f"[{strategy.name}] EXIT BLOCKED: spread {tick.spread:.1%} > "
                    f"max_exit_spread {max_exit_spread:.0%} - waiting for better liquidity"
                )
                return False

        return True

    async def _execute_action(self, strategy, action: Action, tick: Tick) -> None:
        """Execute a strategy action."""
        try:
            result = self.executor.execute(action, tick)
            if result and result.success:
                self._actions_executed += 1
                logger.info(f"[{strategy.name}] Action executed: {action.action_type.value}")
            elif result and not result.success:
                logger.warning(
                    f"[{strategy.name}] Action FAILED: {action.action_type.value}, "
                    f"error={result.error}"
                )
        except Exception as e:
            logger.warning(f"[{strategy.name}] Action exception: {action.action_type.value}, error={e}")

    def _buffer_tick(self, match_info: dict, event_data: dict, tick: Tick) -> None:
        """Buffer tick for database persistence."""
        event_type = event_data.get("event_type", "unknown")
        db_event_type = {
            "last_trade_price": "trade",
            "book": "book",
            "price_change": "price_change",
        }.get(event_type, event_type)

        tick_data = {
            "market_id": match_info["market_id"],
            "timestamp": datetime.now(timezone.utc),
            "token_type": match_info["token_type"],
            "event_type": db_event_type,
            "price": tick.price,
            "best_bid": tick.best_bid,
            "best_ask": tick.best_ask,
            "spread": tick.spread,
            "trade_size": event_data.get("size"),
            "trade_side": event_data.get("side"),
        }

        self.tick_buffer.append(tick_data)

        if len(self.tick_buffer) >= TICK_BUFFER_SIZE:
            asyncio.create_task(self._flush_ticks())

    async def _flush_ticks(self) -> None:
        """Flush buffered ticks to database.

        On failure, ticks are restored to buffer for retry.
        Buffer is only cleared on successful DB commit.
        """
        if not self.tick_buffer:
            return

        ticks_to_insert = self.tick_buffer.copy()

        try:
            with get_session() as db:
                db.bulk_insert_mappings(CSGOPriceTick, ticks_to_insert)
                db.commit()
            # Only clear buffer on success
            self.tick_buffer = []
        except Exception as e:
            logger.error(f"Failed to flush {len(ticks_to_insert)} ticks: {e}")
            # Keep ticks in buffer for retry
            # Trim buffer if it grows too large (prevent memory leak)
            if len(self.tick_buffer) > TICK_BUFFER_SIZE * 10:
                logger.warning(f"Tick buffer overflow, dropping oldest {TICK_BUFFER_SIZE * 5} ticks")
                self.tick_buffer = self.tick_buffer[TICK_BUFFER_SIZE * 5:]

    def stop(self) -> None:
        """Stop the engine."""
        self.running = False
        logger.info(f"Engine stopped. Stats: ticks={self._ticks_processed}, actions={self._actions_executed}, errors={self._errors}")


async def run_unified_engine() -> None:
    """Entry point for the unified CSGO engine."""
    from src.csgo.strategies.scalp import CSGOScalpStrategy
    from src.csgo.strategies.favorite_hedge import CSGOFavoriteHedgeStrategy
    from src.csgo.strategies.swing_rebalance import CSGOSwingRebalanceStrategy
    from src.csgo.strategies.map_longshot import CSGOMapLongshotStrategy
    from src.csgo.strategies.bo3_longshot import CSGOB03LongshotStrategy
    from src.csgo.strategies.comeback_buy import CSGOComebackBuyStrategy

    engine = UnifiedCSGOEngine()

    # Register strategies
    engine.register_strategy(CSGOScalpStrategy())
    engine.register_strategy(CSGOFavoriteHedgeStrategy())
    engine.register_strategy(CSGOSwingRebalanceStrategy())
    engine.register_strategy(CSGOMapLongshotStrategy())
    engine.register_strategy(CSGOB03LongshotStrategy())
    engine.register_strategy(CSGOComebackBuyStrategy())

    # Handle shutdown
    def handle_shutdown(signum, frame):
        logger.info("Shutdown signal received")
        engine.stop()

    sig.signal(sig.SIGTERM, handle_shutdown)
    sig.signal(sig.SIGINT, handle_shutdown)

    await engine.start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(run_unified_engine())
