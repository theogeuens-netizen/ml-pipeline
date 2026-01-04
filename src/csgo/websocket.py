"""
Dedicated CS:GO WebSocket Collector.

Subscribes to CS:GO match markets and publishes signals to Redis streams.
Runs as a separate container/process from the main WebSocket collector.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import websockets
from sqlalchemy import and_, or_

from src.config.settings import settings
from src.db.database import get_session
from src.db.models import CSGOMatch, Market, CSGOPriceTick

logger = logging.getLogger(__name__)

# CS:GO WebSocket constants
CSGO_WS_CONNECTION_ID = 99  # Dedicated ID for CS:GO collector
SUBSCRIPTION_REFRESH_SECONDS = 60
HEALTH_CHECK_INTERVAL_SECONDS = 30
MAX_SUBSCRIPTIONS = 500

# Tick persistence settings
TICK_BUFFER_SIZE = 50  # Flush after 50 ticks
TICK_FLUSH_INTERVAL_SECONDS = 5  # Flush every 5 seconds


class CSGOWebSocketCollector:
    """
    Dedicated WebSocket collector for CS:GO markets.

    Subscribes to upcoming CS:GO matches and publishes real-time data
    to Redis streams for strategy consumption.
    """

    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.reconnect_delay = 5.0
        self.max_reconnect_delay = 60.0

        # Token tracking
        self.subscribed_tokens: set[str] = set()
        self.token_to_match: dict[str, dict] = {}  # token_id -> {match_id, condition_id, token_type}

        # Price tracking for velocity calculation
        self.price_history: dict[str, list[tuple[datetime, float]]] = {}  # condition_id -> [(timestamp, price)]
        self.last_activity: datetime = datetime.now(timezone.utc)

        # Spread cache - stores latest bid/ask from book events
        # Key: asset_id (token), Value: {best_bid, best_ask, spread, timestamp}
        self.spread_cache: dict[str, dict] = {}

        # Tick buffer for batch DB persistence
        self.tick_buffer: list[dict] = []
        self.last_tick_flush: datetime = datetime.now(timezone.utc)

    async def start(self) -> None:
        """Start the CS:GO WebSocket collector with automatic reconnection."""
        self.running = True
        logger.info("CS:GO WebSocket collector starting")

        while self.running:
            try:
                await self._connect_and_run()
            except websockets.ConnectionClosed as e:
                logger.warning(f"CS:GO WebSocket connection closed: code={e.code}, reason={e.reason}")
            except Exception as e:
                logger.error(f"CS:GO WebSocket error: {e}")

            if self.running:
                logger.info(f"CS:GO WebSocket reconnecting in {self.reconnect_delay}s")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 1.5, self.max_reconnect_delay)

    async def _connect_and_run(self) -> None:
        """Connect to WebSocket and process messages."""
        logger.info(f"CS:GO WebSocket connecting to {settings.websocket_url}")

        async with websockets.connect(
            settings.websocket_url,
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            self.ws = ws
            self.reconnect_delay = 5.0  # Reset on successful connect
            logger.info("CS:GO WebSocket connected")

            # Initial subscription
            await self._update_subscriptions()

            # Start background tasks
            subscription_task = asyncio.create_task(self._subscription_loop())
            health_task = asyncio.create_task(self._health_check_loop())
            tick_flush_task = asyncio.create_task(self._tick_flush_loop())

            try:
                async for message in ws:
                    await self._handle_message(message)
            finally:
                subscription_task.cancel()
                health_task.cancel()
                tick_flush_task.cancel()
                # Flush remaining ticks before disconnect
                await self._flush_ticks()
                try:
                    await subscription_task
                except asyncio.CancelledError:
                    pass
                try:
                    await health_task
                except asyncio.CancelledError:
                    pass
                try:
                    await tick_flush_task
                except asyncio.CancelledError:
                    pass

    async def _subscription_loop(self) -> None:
        """Periodically refresh subscriptions."""
        while self.running:
            await asyncio.sleep(SUBSCRIPTION_REFRESH_SECONDS)
            try:
                await self._update_subscriptions()
            except Exception as e:
                logger.error(f"CS:GO subscription update failed: {e}")

    async def _health_check_loop(self) -> None:
        """Monitor connection health."""
        while self.running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
            now = datetime.now(timezone.utc)
            if (now - self.last_activity).total_seconds() > 120:
                logger.warning("CS:GO WebSocket stale, forcing reconnect")
                if self.ws:
                    await self.ws.close()
                return

    async def _tick_flush_loop(self) -> None:
        """Periodically flush tick buffer to database."""
        while self.running:
            await asyncio.sleep(TICK_FLUSH_INTERVAL_SECONDS)
            await self._flush_ticks()

    async def _flush_ticks(self) -> None:
        """
        Flush buffered ticks to database.

        Uses batch insert for efficiency.
        """
        if not self.tick_buffer:
            return

        ticks_to_insert = self.tick_buffer.copy()
        self.tick_buffer = []
        self.last_tick_flush = datetime.now(timezone.utc)

        try:
            with get_session() as db:
                # Batch insert using bulk_insert_mappings
                db.bulk_insert_mappings(CSGOPriceTick, ticks_to_insert)
                db.commit()
                logger.debug(f"Flushed {len(ticks_to_insert)} price ticks to database")
        except Exception as e:
            logger.error(f"Failed to flush price ticks: {e}")
            # Don't re-add to buffer - accept data loss rather than memory growth

    def _buffer_tick(self, match_info: dict, event_data: dict, signal: dict) -> None:
        """
        Buffer a tick for batch database insertion.

        Args:
            match_info: Match metadata from token lookup
            event_data: Raw WebSocket event data
            signal: Processed signal data
        """
        event_type = event_data.get("event_type", "unknown")

        # Map event types
        db_event_type = {
            "last_trade_price": "trade",
            "book": "book",
            "price_change": "price_change",
        }.get(event_type, event_type)

        tick = {
            "market_id": match_info["market_id"],
            "timestamp": datetime.now(timezone.utc),
            "token_type": match_info["token_type"],
            "event_type": db_event_type,
            "price": signal.get("price"),
            "best_bid": signal.get("best_bid"),
            "best_ask": signal.get("best_ask"),
            "spread": signal.get("spread"),
            "trade_size": signal.get("size"),
            "trade_side": signal.get("side"),
            "price_velocity_1m": signal.get("price_velocity_1m"),
        }

        self.tick_buffer.append(tick)

        # Flush if buffer is full
        if len(self.tick_buffer) >= TICK_BUFFER_SIZE:
            asyncio.create_task(self._flush_ticks())

    async def _update_subscriptions(self) -> None:
        """
        Update subscriptions to match upcoming CS:GO matches.

        Subscribes to matches starting within 6 hours of game start time.
        """
        from src.csgo.discovery import get_matches_for_subscription

        with get_session() as db:
            matches = get_matches_for_subscription(db, hours_ahead=6.0)

            # Build token lookup
            new_tokens: set[str] = set()
            new_token_to_match: dict[str, dict] = {}

            for match in matches:
                # Get market for token IDs
                market = db.query(Market).filter(Market.condition_id == match.condition_id).first()
                if not market or not market.yes_token_id or not market.no_token_id:
                    continue

                # Add both YES and NO tokens
                for token_id, token_type in [
                    (market.yes_token_id, "YES"),
                    (market.no_token_id, "NO"),
                ]:
                    new_tokens.add(token_id)
                    new_token_to_match[token_id] = {
                        "match_id": match.id,
                        "condition_id": match.condition_id,
                        "token_type": token_type,
                        "market_id": market.id,
                        "gamma_id": match.gamma_id,
                        "team_yes": match.team_yes,
                        "team_no": match.team_no,
                        "game_start_time": match.game_start_time.isoformat() if match.game_start_time else None,
                    }

        # Calculate diff
        tokens_to_add = new_tokens - self.subscribed_tokens
        tokens_to_remove = self.subscribed_tokens - new_tokens

        # Unsubscribe from removed
        if tokens_to_remove:
            await self._unsubscribe_tokens(list(tokens_to_remove))

        # Subscribe to new
        if tokens_to_add:
            await self._subscribe_tokens(list(tokens_to_add))

        # Update state
        self.subscribed_tokens = new_tokens
        self.token_to_match = new_token_to_match

        # Mark subscribed matches in DB
        # CRITICAL: Only unsubscribe matches that are truly finished (resolved/closed)
        # NEVER mass-unsubscribe based on filtering logic - that caused the in-play bug
        with get_session() as db:
            match_ids = {m["match_id"] for m in new_token_to_match.values()}

            # Mark newly subscribed matches
            if match_ids:
                db.query(CSGOMatch).filter(CSGOMatch.id.in_(match_ids)).update(
                    {"subscribed": True}, synchronize_session=False
                )

            # Only unsubscribe matches that are DEFINITIVELY finished:
            # - resolved=True (game result is known)
            # - closed=True (market is no longer accepting orders)
            # This prevents the bug where filtering logic changes caused mass unsubscription
            finished_count = db.query(CSGOMatch).filter(
                and_(
                    CSGOMatch.subscribed == True,
                    or_(
                        CSGOMatch.resolved == True,
                        CSGOMatch.closed == True,
                    ),
                )
            ).update({"subscribed": False}, synchronize_session=False)

            if finished_count > 0:
                logger.info(f"Unsubscribed {finished_count} finished matches (resolved/closed)")

            db.commit()

        logger.info(f"CS:GO WebSocket subscribed to {len(new_tokens)} tokens ({len(matches)} matches)")

    async def _subscribe_tokens(self, token_ids: list[str]) -> None:
        """Subscribe to token IDs."""
        if not self.ws or not token_ids:
            return

        # Polymarket expects assets_ids
        msg = {"type": "market", "assets_ids": token_ids}
        logger.info(f"CS:GO subscribing with message: {json.dumps(msg)[:500]}...")
        await self.ws.send(json.dumps(msg))
        logger.info(f"CS:GO subscribed to {len(token_ids)} tokens")

    async def _unsubscribe_tokens(self, token_ids: list[str]) -> None:
        """Unsubscribe from token IDs."""
        if not self.ws or not token_ids:
            return

        # Note: Polymarket may not support explicit unsubscribe
        # We rely on connection recreation for cleanup
        logger.debug(f"CS:GO would unsubscribe from {len(token_ids)} tokens")

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket message."""
        self.last_activity = datetime.now(timezone.utc)

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"CS:GO failed to parse message: {message[:100]}")
            return

        # Handle batch messages (lists of events)
        if isinstance(data, list):
            for item in data:
                await self._process_event(item)
        else:
            await self._process_event(data)

    async def _process_event(self, data: dict) -> None:
        """Process a single WebSocket event.

        Handles two message formats:
        1. Old format: {"event_type": "...", "asset_id": "...", ...}
        2. New format: {"market": "0x...", "price_changes": [{"asset_id": "...", "price": "..."}]}
        """
        if not isinstance(data, dict):
            return

        # Handle new format: {"market": "...", "price_changes": [...]}
        if "price_changes" in data:
            market_id = data.get("market")  # This is the condition_id
            for change in data.get("price_changes", []):
                asset_id = change.get("asset_id")
                if not asset_id or asset_id not in self.token_to_match:
                    continue

                match_info = self.token_to_match[asset_id]
                condition_id = match_info["condition_id"]

                # Extract price change data
                price = float(change.get("price", 0))
                self._update_price_history(condition_id, price)

                # Build event data for publishing
                event_data = {
                    "event_type": "price_change",
                    "asset_id": asset_id,
                    "price": price,
                    "size": float(change.get("size", 0)),
                    "side": change.get("side"),
                    "best_bid": float(change.get("best_bid", 0)) if change.get("best_bid") else None,
                    "best_ask": float(change.get("best_ask", 0)) if change.get("best_ask") else None,
                }
                await self._publish_signal(match_info, event_data)
            return

        # Handle old format: {"event_type": "...", "asset_id": "..."}
        event_type = data.get("event_type")
        asset_id = data.get("asset_id")

        if not asset_id or asset_id not in self.token_to_match:
            return

        match_info = self.token_to_match[asset_id]
        condition_id = match_info["condition_id"]

        if event_type == "last_trade_price":
            price = float(data.get("price", 0))
            self._update_price_history(condition_id, price)
            await self._publish_signal(match_info, data)

        elif event_type == "book":
            await self._publish_signal(match_info, data)

        elif event_type == "price_change":
            price = float(data.get("price", 0))
            self._update_price_history(condition_id, price)
            await self._publish_signal(match_info, data)

    def _update_price_history(self, condition_id: str, price: float) -> None:
        """Update price history for velocity calculation."""
        now = datetime.now(timezone.utc)

        if condition_id not in self.price_history:
            self.price_history[condition_id] = []

        history = self.price_history[condition_id]
        history.append((now, price))

        # Keep only last 5 minutes of data
        cutoff = now - timedelta(minutes=5)
        self.price_history[condition_id] = [(t, p) for t, p in history if t > cutoff]

    def _calculate_price_velocity(self, condition_id: str) -> Optional[float]:
        """
        Calculate price velocity (price change per minute) over last minute.

        Returns:
            Price velocity (positive = price increasing, negative = decreasing)
            None if insufficient data
        """
        history = self.price_history.get(condition_id, [])
        if len(history) < 2:
            return None

        now = datetime.now(timezone.utc)
        one_min_ago = now - timedelta(minutes=1)

        # Find oldest price within last minute
        recent_prices = [(t, p) for t, p in history if t > one_min_ago]
        if len(recent_prices) < 2:
            return None

        oldest_time, oldest_price = recent_prices[0]
        newest_time, newest_price = recent_prices[-1]

        time_diff = (newest_time - oldest_time).total_seconds() / 60.0
        if time_diff < 0.1:  # Less than 6 seconds
            return None

        return (newest_price - oldest_price) / time_diff

    async def _publish_signal(self, match_info: dict, event_data: dict) -> None:
        """
        Publish signal to Redis stream for strategy consumption.

        Stream key: csgo:signals
        """
        from src.csgo.signals import publish_csgo_signal

        condition_id = match_info["condition_id"]
        token_type = match_info["token_type"]

        # Build signal data
        signal = {
            "market_id": match_info["market_id"],
            "match_id": match_info["match_id"],
            "condition_id": condition_id,
            "gamma_id": match_info.get("gamma_id"),
            "team_yes": match_info.get("team_yes"),
            "team_no": match_info.get("team_no"),
            "game_start_time": match_info.get("game_start_time"),
            "token_type": token_type,
            "event_type": event_data.get("event_type"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add event-specific data
        if event_data.get("event_type") == "last_trade_price":
            signal["price"] = float(event_data.get("price", 0))
            signal["size"] = float(event_data.get("size", 0))
            signal["side"] = event_data.get("side")

        elif event_data.get("event_type") == "book":
            bids = event_data.get("bids", [])
            asks = event_data.get("asks", [])
            if bids:
                signal["best_bid"] = float(bids[0].get("price", 0))
            if asks:
                signal["best_ask"] = float(asks[0].get("price", 0))
            if "best_bid" in signal and "best_ask" in signal:
                signal["spread"] = signal["best_ask"] - signal["best_bid"]
                signal["mid_price"] = (signal["best_ask"] + signal["best_bid"]) / 2

                # Cache the spread data for use in price_change events
                cache_key = f"{condition_id}:{token_type}"
                self.spread_cache[cache_key] = {
                    "best_bid": signal["best_bid"],
                    "best_ask": signal["best_ask"],
                    "spread": signal["spread"],
                    "timestamp": datetime.now(timezone.utc),
                }

        elif event_data.get("event_type") == "price_change":
            signal["price"] = float(event_data.get("price", 0))

            # Enrich with cached spread data from recent book events
            cache_key = f"{condition_id}:{token_type}"
            cached = self.spread_cache.get(cache_key)
            if cached:
                # Only use cache if it's recent (within 60 seconds)
                cache_age = (datetime.now(timezone.utc) - cached["timestamp"]).total_seconds()
                if cache_age < 60:
                    signal["best_bid"] = cached["best_bid"]
                    signal["best_ask"] = cached["best_ask"]
                    signal["spread"] = cached["spread"]

        # Add price velocity
        velocity = self._calculate_price_velocity(condition_id)
        if velocity is not None:
            signal["price_velocity_1m"] = velocity

        # Buffer tick for DB persistence (charts)
        self._buffer_tick(match_info, event_data, signal)

        # Publish to Redis for real-time strategy consumption
        await publish_csgo_signal(signal)

    def stop(self) -> None:
        """Stop the collector."""
        self.running = False


async def run_csgo_collector() -> None:
    """Entry point for running the CS:GO WebSocket collector."""
    import signal as sig

    collector = CSGOWebSocketCollector()

    def handle_shutdown(signum, frame):
        logger.info("CS:GO WebSocket collector shutting down")
        collector.stop()

    sig.signal(sig.SIGTERM, handle_shutdown)
    sig.signal(sig.SIGINT, handle_shutdown)

    await collector.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_csgo_collector())
