"""
WebSocket collector for real-time trade data.

This service connects to Polymarket's WebSocket API to:
- Stream live trade events (last_trade_price)
- Track orderbook updates (book)
- Monitor price changes (price_change)

Trade data is:
1. Stored in PostgreSQL (trades table)
2. Pushed to Redis buffer for metrics computation
3. Whale trades trigger whale_events records
"""
import asyncio
import json
import signal
from datetime import datetime, timezone
from typing import Optional

import websockets
from sqlalchemy import select
import structlog

from src.config.settings import settings
from src.db.database import get_session
from src.db.models import Market, Trade, WhaleEvent
from src.db.redis import RedisClient

logger = structlog.get_logger()


# Health check constants
STALE_THRESHOLD_SECONDS = 120  # Force reconnect if no activity for 2 minutes
HEALTH_CHECK_INTERVAL_SECONDS = 30  # Check health every 30 seconds
MAX_SUBSCRIPTIONS = 500  # Polymarket limits to 500 instruments per connection


class WebSocketCollector:
    """Manages WebSocket connections for trade data collection."""

    def __init__(self, managed: bool = False):
        """Initialize the WebSocket collector.

        Args:
            managed: If True, subscription updates are managed externally
                     (by MultiConnectionCollector). Internal updates are disabled.
        """
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.redis = RedisClient()
        self.subscribed_markets: dict[str, dict] = {}  # condition_id -> {yes_token_id, market_id}
        self.running = False
        self.reconnect_delay = settings.websocket_reconnect_delay
        self._subscription_update_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self.last_activity: datetime = datetime.now(timezone.utc)
        self.managed = managed  # If True, skip internal subscription updates
        self.connection_id: int = 0  # Set by MultiConnectionCollector

    async def start(self) -> None:
        """Start the WebSocket collector with automatic reconnection."""
        self.running = True
        print("WebSocket collector starting...", flush=True)
        logger.info("WebSocket collector starting")

        while self.running:
            try:
                await self._connect_and_run()
            except websockets.ConnectionClosed as e:
                logger.warning("WebSocket connection closed", code=e.code, reason=e.reason)
            except Exception as e:
                logger.error("WebSocket error", error=str(e))

            if self.running:
                logger.info("Reconnecting in seconds", delay=self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(
                    self.reconnect_delay * 1.5,
                    settings.websocket_max_reconnect_delay
                )

    async def _connect_and_run(self) -> None:
        """Connect to WebSocket and process messages."""
        print(f"Connecting to WebSocket: {settings.websocket_url}", flush=True)
        logger.info("Connecting to WebSocket", url=settings.websocket_url)

        async with websockets.connect(
            settings.websocket_url,
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            self.ws = ws
            self.reconnect_delay = settings.websocket_reconnect_delay
            print("WebSocket connected!", flush=True)
            logger.info("WebSocket connected", connection=self.connection_id)

            # Initial subscription update (skip if managed externally)
            if not self.managed:
                print("Updating subscriptions...", flush=True)
                await self._update_subscriptions()
            else:
                # For managed mode, just subscribe to pre-assigned markets
                batch = [
                    (cid, info["yes_token_id"])
                    for cid, info in self.subscribed_markets.items()
                ]
                if batch:
                    await self._subscribe_batch(batch)
            print(f"Subscribed to {len(self.subscribed_markets)} markets", flush=True)

            # Start periodic subscription updates (only if not managed)
            if not self.managed:
                self._subscription_update_task = asyncio.create_task(self._subscription_update_loop())

            # Start health check task
            self._health_check_task = asyncio.create_task(self._health_check_loop())

            try:
                # Process messages
                async for message in ws:
                    await self._handle_message(message)
            finally:
                if self._subscription_update_task:
                    self._subscription_update_task.cancel()
                    try:
                        await self._subscription_update_task
                    except asyncio.CancelledError:
                        pass
                if self._health_check_task:
                    self._health_check_task.cancel()
                    try:
                        await self._health_check_task
                    except asyncio.CancelledError:
                        pass

    async def _subscription_update_loop(self) -> None:
        """Periodically update subscriptions."""
        while self.running:
            await asyncio.sleep(60)  # Update every minute
            try:
                await self._update_subscriptions()
            except Exception as e:
                logger.error("Subscription update failed", error=str(e))

    async def _health_check_loop(self) -> None:
        """Periodically check connection health and force reconnect if stale."""
        while self.running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
            try:
                seconds_since_activity = (datetime.now(timezone.utc) - self.last_activity).total_seconds()

                if seconds_since_activity > STALE_THRESHOLD_SECONDS:
                    logger.warning(
                        "WebSocket stale, forcing reconnect",
                        seconds_since_activity=int(seconds_since_activity),
                        threshold=STALE_THRESHOLD_SECONDS,
                    )
                    if self.ws:
                        await self.ws.close()
                elif seconds_since_activity > STALE_THRESHOLD_SECONDS / 2:
                    logger.warning(
                        "WebSocket activity low",
                        seconds_since_activity=int(seconds_since_activity),
                    )
            except Exception as e:
                logger.error("Health check failed", error=str(e))

    async def _update_subscriptions(self) -> None:
        """Subscribe to markets in T2+ tiers, prioritizing by tier (T4 first).

        Polymarket limits WebSocket connections to 500 instruments max.
        We prioritize higher tiers (T4 > T3 > T2) since they need real-time data most.
        """
        with get_session() as session:
            markets = session.execute(
                select(Market).where(
                    Market.tier.in_(settings.websocket_enabled_tiers),
                    Market.active == True,
                    Market.resolved == False,
                    Market.yes_token_id.isnot(None),
                ).order_by(Market.tier.desc())  # T4 first, then T3, then T2
            ).scalars().all()

            # Limit to MAX_SUBSCRIPTIONS (500) to comply with Polymarket limits
            if len(markets) > MAX_SUBSCRIPTIONS:
                logger.warning(
                    "Limiting WebSocket subscriptions",
                    total_markets=len(markets),
                    max_allowed=MAX_SUBSCRIPTIONS,
                    dropped=len(markets) - MAX_SUBSCRIPTIONS,
                )
                markets = markets[:MAX_SUBSCRIPTIONS]

            new_subscriptions = {
                m.condition_id: {
                    "yes_token_id": m.yes_token_id,
                    "market_id": m.id,
                }
                for m in markets
            }

        # Unsubscribe from removed markets
        removed = set(self.subscribed_markets.keys()) - set(new_subscriptions.keys())
        for cid in removed:
            await self._unsubscribe(cid)

        # Subscribe to new markets (batch for efficiency)
        added = set(new_subscriptions.keys()) - set(self.subscribed_markets.keys())
        if added:
            batch = [(cid, new_subscriptions[cid]["yes_token_id"]) for cid in added]
            await self._subscribe_batch(batch)

        self.subscribed_markets = new_subscriptions
        logger.info(
            "Subscriptions updated",
            total=len(self.subscribed_markets),
            added=len(added),
            removed=len(removed),
        )

    async def _subscribe_batch(self, markets: list[tuple[str, str]]) -> None:
        """Subscribe to multiple markets at once (more efficient)."""
        if self.ws and markets:
            token_ids = [token_id for _, token_id in markets]
            message = {
                "type": "market",
                "assets_ids": token_ids,
            }
            await self.ws.send(json.dumps(message))
            for condition_id, _ in markets:
                await self.redis.set_ws_connected(condition_id, True)
            logger.info("Batch subscribed to markets", count=len(markets))

    async def _subscribe(self, condition_id: str, token_id: str) -> None:
        """Subscribe to a market's trade feed."""
        if self.ws:
            message = {
                "type": "market",
                "assets_ids": [token_id],
            }
            await self.ws.send(json.dumps(message))
            await self.redis.set_ws_connected(condition_id, True)
            logger.debug("Subscribed to market", condition_id=condition_id[:20])

    async def _unsubscribe(self, condition_id: str) -> None:
        """Unsubscribe from a market."""
        await self.redis.set_ws_connected(condition_id, False)
        logger.debug("Unsubscribed from market", condition_id=condition_id[:20])

    async def _handle_message(self, message: str | bytes) -> None:
        """Process incoming WebSocket message."""
        # Track activity on ANY message (for health detection)
        self.last_activity = datetime.now(timezone.utc)
        await self.redis.set_ws_last_activity()

        try:
            # Handle binary messages (msgpack encoded)
            if isinstance(message, bytes):
                try:
                    import msgpack
                    data = msgpack.unpackb(message, raw=False)
                except ImportError:
                    logger.warning("Received binary message but msgpack not installed")
                    return
                except Exception as e:
                    logger.warning("Failed to decode msgpack message", error=str(e), raw=message[:50])
                    return
            else:
                # Skip ping/pong text messages
                if message in ("PING", "PONG"):
                    return
                data = json.loads(message)

            # Handle arrays of events
            if isinstance(data, list):
                for event in data:
                    await self._process_event(event)
            else:
                await self._process_event(data)

        except json.JSONDecodeError:
            logger.warning("Invalid JSON message", preview=str(message)[:100] if message else "empty")
        except Exception as e:
            logger.warning("Failed to handle message", error=str(e))

    async def _process_event(self, data: dict) -> None:
        """Process a single event."""
        if not isinstance(data, dict):
            logger.debug("Skipping non-dict event", data_type=type(data).__name__)
            return

        event_type = data.get("event_type")

        if event_type == "last_trade_price":
            await self._handle_trade(data)
        elif event_type == "book":
            await self._handle_book(data)
        elif event_type == "price_change":
            await self._handle_price_change(data)
        elif event_type == "tick_size_change":
            pass  # Ignore tick size changes
        elif event_type is None:
            # Log first few keys to debug unknown message format
            logger.info("Received event without event_type", keys=list(data.keys())[:5])
        else:
            logger.debug("Unknown event type", event_type=event_type)

    async def _handle_trade(self, data: dict) -> None:
        """
        Process trade event with validation.

        Validates:
        - Asset ID exists and is subscribed
        - Price is in range [0, 1]
        - Size is positive
        - Side is valid (BUY/SELL)
        """
        # Get market info from asset ID
        asset_id = data.get("asset_id")
        if not asset_id:
            logger.debug("Trade event missing asset_id")
            return

        # Find market by token ID
        market_info = None
        condition_id = None
        for cid, info in self.subscribed_markets.items():
            if info["yes_token_id"] == asset_id:
                market_info = info
                condition_id = cid
                break

        if not market_info:
            # Unknown asset ID - could be unsubscribed market
            return

        market_id = market_info["market_id"]

        # Parse and validate trade data
        timestamp = datetime.now(timezone.utc)

        try:
            price = float(data.get("price", 0))
            size = float(data.get("size", 0))
        except (TypeError, ValueError) as e:
            logger.warning(
                "Invalid trade data types",
                market_id=market_id,
                raw_price=data.get("price"),
                raw_size=data.get("size"),
                error=str(e),
            )
            return

        side = data.get("side", "buy").upper()

        # Validate price range [0, 1]
        if price < 0 or price > 1:
            logger.warning(
                "Trade price out of range",
                market_id=market_id,
                price=price,
                connection=self.connection_id,
            )
            return

        # Validate size is positive
        if size <= 0:
            logger.debug("Ignoring zero/negative size trade", market_id=market_id, size=size)
            return

        # Validate side
        if side not in ("BUY", "SELL"):
            logger.warning("Invalid trade side", market_id=market_id, side=side)
            side = "BUY"  # Default fallback

        # Classify whale tier
        whale_tier = self._classify_whale(size)

        # Insert trade to database
        try:
            with get_session() as session:
                trade = Trade(
                    market_id=market_id,
                    timestamp=timestamp,
                    price=price,
                    size=size,
                    side=side,
                    whale_tier=whale_tier,
                )
                session.add(trade)
                session.flush()  # Get trade ID

                # Create whale event if whale trade
                if whale_tier >= 2:
                    whale_event = WhaleEvent(
                        market_id=market_id,
                        trade_id=trade.id,
                        timestamp=timestamp,
                        price=price,
                        size=size,
                        side=side,
                        whale_tier=whale_tier,
                    )
                    session.add(whale_event)
                    logger.info(
                        "Whale trade detected",
                        market_id=market_id,
                        size=size,
                        whale_tier=whale_tier,
                        connection=self.connection_id,
                    )

                session.commit()

        except Exception as e:
            # Log but continue - don't let DB errors stop trade processing
            logger.error(
                "Failed to insert trade",
                error=str(e),
                market_id=market_id,
                connection=self.connection_id,
            )
            # Still push to Redis even if DB fails
            # This maintains the real-time buffer for metrics

        # Push to Redis buffer (graceful degradation - continue even if Redis fails)
        try:
            trade_data = {
                "timestamp": timestamp.isoformat(),
                "price": price,
                "size": size,
                "side": side,
                "whale_tier": whale_tier,
            }
            await self.redis.push_trade(condition_id, trade_data)
            await self.redis.set_ws_last_event(condition_id)
            await self.redis.set_price(condition_id, price)
        except Exception as e:
            logger.warning("Redis trade push failed", error=str(e), market_id=market_id)

        logger.debug(
            "Trade recorded",
            market=condition_id[:20] if condition_id else "unknown",
            price=price,
            size=size,
            side=side,
            connection=self.connection_id,
        )

    async def _handle_book(self, data: dict) -> None:
        """Process orderbook update."""
        asset_id = data.get("asset_id")
        if not asset_id:
            return

        # Find condition_id by token ID
        condition_id = None
        for cid, info in self.subscribed_markets.items():
            if info["yes_token_id"] == asset_id:
                condition_id = cid
                break

        if condition_id:
            # Cache orderbook in Redis
            orderbook = {
                "bids": data.get("buys", []),
                "asks": data.get("sells", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.redis.set_orderbook(condition_id, orderbook)

    async def _handle_price_change(self, data: dict) -> None:
        """Process price change event."""
        asset_id = data.get("asset_id")
        price = data.get("price")

        if not asset_id or price is None:
            return

        # Find condition_id and cache price
        for cid, info in self.subscribed_markets.items():
            if info["yes_token_id"] == asset_id:
                await self.redis.set_price(cid, float(price))
                break

    def _classify_whale(self, size: float) -> int:
        """
        Classify trade size into whale tiers.

        Returns:
            0: Normal trade
            1: Large trade (> $500)
            2: Whale (> $2,000)
            3: Mega whale (> $10,000)
        """
        if size >= settings.whale_tier_3_threshold:
            return 3
        elif size >= settings.whale_tier_2_threshold:
            return 2
        elif size >= settings.whale_tier_1_threshold:
            return 1
        return 0

    def stop(self) -> None:
        """Stop the collector."""
        self.running = False
        logger.info("WebSocket collector stopping")


class MultiConnectionCollector:
    """
    Manages multiple WebSocket connections to handle more than 500 markets.

    Polymarket limits each connection to 500 instruments, so we split markets
    across multiple connections, prioritizing by tier (T4 > T3 > T2).
    """

    def __init__(self, num_connections: int = 2):
        self.num_connections = num_connections
        self.collectors: list[WebSocketCollector] = []
        self.running = False

    async def start(self) -> None:
        """Start all WebSocket collectors in parallel."""
        self.running = True
        logger.info(
            "Starting multi-connection collector",
            num_connections=self.num_connections,
            max_markets=self.num_connections * MAX_SUBSCRIPTIONS,
        )

        # Create collectors (managed mode - subscriptions handled by MultiConnectionCollector)
        for i in range(self.num_connections):
            collector = WebSocketCollector(managed=True)
            collector.connection_id = i  # Tag for logging
            collector.running = True  # Enable the collector's run loop
            self.collectors.append(collector)

        # Override subscription logic to split markets across connections
        await self._assign_markets_to_connections()

        # Start all collectors in parallel
        tasks = [
            asyncio.create_task(self._run_collector(collector, i))
            for i, collector in enumerate(self.collectors)
        ]

        # Also run periodic market reassignment
        tasks.append(asyncio.create_task(self._reassignment_loop()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def _run_collector(self, collector: WebSocketCollector, conn_id: int) -> None:
        """Run a single collector with its assigned markets.

        Uses staggered reconnection delays to prevent all connections
        from reconnecting simultaneously (which would cause data gaps).
        """
        # Stagger initial connection by connection ID to avoid thundering herd
        if conn_id > 0:
            stagger_delay = conn_id * 2  # 0s, 2s, 4s, 6s for connections 0-3
            logger.info("Staggering initial connection", connection=conn_id, delay=stagger_delay)
            await asyncio.sleep(stagger_delay)

        while self.running:
            try:
                await collector._connect_and_run()
            except websockets.ConnectionClosed as e:
                logger.warning(
                    "WebSocket connection closed",
                    connection=conn_id,
                    code=e.code,
                    reason=e.reason,
                )
            except Exception as e:
                logger.error("WebSocket error", connection=conn_id, error=str(e))

            if self.running:
                # Stagger reconnection: base delay + connection-specific offset
                # This ensures connections don't all reconnect at the same time
                base_delay = collector.reconnect_delay
                stagger_offset = conn_id * 3  # 0s, 3s, 6s, 9s offset
                total_delay = base_delay + stagger_offset

                logger.info(
                    "Reconnecting with stagger",
                    connection=conn_id,
                    base_delay=base_delay,
                    stagger_offset=stagger_offset,
                    total_delay=total_delay,
                )
                await asyncio.sleep(total_delay)
                collector.reconnect_delay = min(
                    base_delay * 1.5,
                    settings.websocket_max_reconnect_delay
                )

    async def _assign_markets_to_connections(self) -> None:
        """Split markets across connections, prioritizing by tier."""
        # Extract data within session to avoid detached instance errors
        with get_session() as session:
            markets = session.execute(
                select(Market).where(
                    Market.tier.in_(settings.websocket_enabled_tiers),
                    Market.active == True,
                    Market.resolved == False,
                    Market.yes_token_id.isnot(None),
                ).order_by(Market.tier.desc())  # T4 first
            ).scalars().all()

            # Extract data while session is active
            market_data = [
                {
                    "condition_id": m.condition_id,
                    "yes_token_id": m.yes_token_id,
                    "market_id": m.id,
                }
                for m in markets
            ]

        total_capacity = self.num_connections * MAX_SUBSCRIPTIONS
        if len(market_data) > total_capacity:
            logger.warning(
                "Markets exceed total capacity",
                total_markets=len(market_data),
                total_capacity=total_capacity,
                dropped=len(market_data) - total_capacity,
            )
            market_data = market_data[:total_capacity]

        # Distribute markets evenly across connections (round-robin for better balance)
        # This ensures each connection gets a mix of tiers rather than one getting all T4
        markets_per_connection: list[list[dict]] = [[] for _ in range(self.num_connections)]
        for idx, market in enumerate(market_data):
            conn_idx = idx % self.num_connections
            if len(markets_per_connection[conn_idx]) < MAX_SUBSCRIPTIONS:
                markets_per_connection[conn_idx].append(market)

        for i, collector in enumerate(self.collectors):
            assigned = markets_per_connection[i]

            collector.subscribed_markets = {
                m["condition_id"]: {
                    "yes_token_id": m["yes_token_id"],
                    "market_id": m["market_id"],
                }
                for m in assigned
            }

            logger.info(
                "Assigned markets to connection",
                connection=i,
                count=len(assigned),
            )

    async def _reassignment_loop(self) -> None:
        """Periodically reassign markets to handle tier changes."""
        while self.running:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                await self._assign_markets_to_connections()
                # Trigger subscription updates on all collectors
                for collector in self.collectors:
                    if collector.ws:
                        # Re-subscribe with new assignments
                        batch = [
                            (cid, info["yes_token_id"])
                            for cid, info in collector.subscribed_markets.items()
                        ]
                        if batch:
                            await collector._subscribe_batch(batch)
            except Exception as e:
                logger.error("Market reassignment failed", error=str(e))

    def stop(self) -> None:
        """Stop all collectors."""
        self.running = False
        for collector in self.collectors:
            collector.stop()


async def run_collector() -> None:
    """Entry point for WebSocket collector service."""
    # Use multi-connection collector to handle >500 markets
    # 4 connections = 2000 market capacity (configurable via settings)
    collector = MultiConnectionCollector(num_connections=settings.websocket_num_connections)

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        logger.info("Shutdown signal received")
        collector.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_handler)

    try:
        await collector.start()
    finally:
        # Close Redis connections
        for c in collector.collectors:
            await c.redis.close()


if __name__ == "__main__":
    asyncio.run(run_collector())
