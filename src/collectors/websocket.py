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
MIN_TRADES_PER_MINUTE = 30  # Minimum expected trade rate (trigger reconnect if below)
RATE_CHECK_WINDOW_SECONDS = 300  # Window for rate calculation (5 minutes)


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
        self.subscribed_markets: dict[str, dict] = {}  # condition_id -> {yes_token_id, no_token_id, market_id}
        self.token_to_market: dict[str, dict] = {}  # token_id -> {condition_id, market_id, token_type}
        self.running = False
        self.reconnect_delay = settings.websocket_reconnect_delay
        self._subscription_update_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self.last_activity: datetime = datetime.now(timezone.utc)
        self.managed = managed  # If True, skip internal subscription updates
        self.connection_id: int = 0  # Set by MultiConnectionCollector
        # Trade rate tracking for health monitoring
        self.trade_timestamps: list[datetime] = []  # Rolling window of trade times
        self.last_rate_check: datetime = datetime.now(timezone.utc)

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
                # For managed mode, build token lookup and subscribe to pre-assigned markets
                self._build_token_lookup()
                token_ids = list(self.token_to_market.keys())
                if token_ids:
                    await self._subscribe_tokens(token_ids)
            print(f"Subscribed to {len(self.subscribed_markets)} markets ({len(self.token_to_market)} tokens)", flush=True)

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

    def _record_trade(self) -> None:
        """Record a trade timestamp for rate tracking."""
        now = datetime.now(timezone.utc)
        self.trade_timestamps.append(now)
        # Keep only trades within the rate check window
        cutoff = now.timestamp() - RATE_CHECK_WINDOW_SECONDS
        self.trade_timestamps = [t for t in self.trade_timestamps if t.timestamp() > cutoff]

    def get_trade_rate(self) -> float:
        """Get current trade rate (trades per minute) over the last window."""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - RATE_CHECK_WINDOW_SECONDS
        recent_trades = [t for t in self.trade_timestamps if t.timestamp() > cutoff]
        if not recent_trades:
            return 0.0
        # Calculate rate per minute
        window_minutes = RATE_CHECK_WINDOW_SECONDS / 60
        return len(recent_trades) / window_minutes

    async def _health_check_loop(self) -> None:
        """Periodically check connection health and force reconnect if stale."""
        while self.running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
            try:
                now = datetime.now(timezone.utc)
                seconds_since_activity = (now - self.last_activity).total_seconds()

                # Check 1: No activity at all (stale connection)
                if seconds_since_activity > STALE_THRESHOLD_SECONDS:
                    logger.warning(
                        "WebSocket stale, forcing reconnect",
                        seconds_since_activity=int(seconds_since_activity),
                        threshold=STALE_THRESHOLD_SECONDS,
                        connection=self.connection_id,
                    )
                    if self.ws:
                        await self.ws.close()
                    continue

                # Check 2: Low trade rate (degraded connection)
                # Only check after initial warmup period (5 minutes)
                seconds_since_start = (now - self.last_rate_check).total_seconds()
                if seconds_since_start > RATE_CHECK_WINDOW_SECONDS:
                    trade_rate = self.get_trade_rate()
                    if trade_rate < MIN_TRADES_PER_MINUTE and len(self.subscribed_markets) > 100:
                        logger.warning(
                            "WebSocket trade rate too low, forcing reconnect",
                            trade_rate=round(trade_rate, 1),
                            min_expected=MIN_TRADES_PER_MINUTE,
                            subscribed_markets=len(self.subscribed_markets),
                            connection=self.connection_id,
                        )
                        # Reset rate tracking
                        self.trade_timestamps = []
                        self.last_rate_check = now
                        if self.ws:
                            await self.ws.close()
                        continue

                # Warning: activity is low but not critical
                if seconds_since_activity > STALE_THRESHOLD_SECONDS / 2:
                    logger.warning(
                        "WebSocket activity low",
                        seconds_since_activity=int(seconds_since_activity),
                        connection=self.connection_id,
                    )
            except Exception as e:
                logger.error("Health check failed", error=str(e), connection=self.connection_id)

    def _build_token_lookup(self) -> None:
        """Build reverse lookup from token_id to market info."""
        self.token_to_market = {}
        for cid, info in self.subscribed_markets.items():
            yes_token = info.get("yes_token_id")
            no_token = info.get("no_token_id")
            market_id = info["market_id"]

            if yes_token:
                self.token_to_market[yes_token] = {
                    "condition_id": cid,
                    "market_id": market_id,
                    "token_type": "YES",
                }
            if no_token:
                self.token_to_market[no_token] = {
                    "condition_id": cid,
                    "market_id": market_id,
                    "token_type": "NO",
                }

    async def _subscribe_tokens(self, token_ids: list[str]) -> None:
        """Subscribe to a list of token IDs."""
        if self.ws and token_ids:
            message = {
                "type": "market",
                "assets_ids": token_ids,
            }
            await self.ws.send(json.dumps(message))
            logger.info("Subscribed to tokens", count=len(token_ids), connection=self.connection_id)

    async def _update_subscriptions(self) -> None:
        """Subscribe to markets in T2+ tiers, prioritizing by tier (T4 first).

        Polymarket limits WebSocket connections to 500 instruments max.
        Each market uses 2 slots (YES + NO token), so max ~250 markets per connection.
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

            # Each market uses 2 subscription slots (YES + NO token)
            max_markets = MAX_SUBSCRIPTIONS // 2
            if len(markets) > max_markets:
                logger.warning(
                    "Limiting WebSocket subscriptions",
                    total_markets=len(markets),
                    max_markets=max_markets,
                    max_tokens=MAX_SUBSCRIPTIONS,
                    dropped=len(markets) - max_markets,
                )
                markets = markets[:max_markets]

            new_subscriptions = {
                m.condition_id: {
                    "yes_token_id": m.yes_token_id,
                    "no_token_id": m.no_token_id,
                    "market_id": m.id,
                }
                for m in markets
            }

        # Find removed and added markets
        removed = set(self.subscribed_markets.keys()) - set(new_subscriptions.keys())
        added = set(new_subscriptions.keys()) - set(self.subscribed_markets.keys())

        # Unsubscribe from removed markets (update Redis tracking)
        for cid in removed:
            await self._unsubscribe(cid)

        # Update subscribed_markets and rebuild token lookup
        self.subscribed_markets = new_subscriptions
        self._build_token_lookup()

        # Subscribe to new tokens
        if added:
            new_tokens = []
            for cid in added:
                info = new_subscriptions[cid]
                if info.get("yes_token_id"):
                    new_tokens.append(info["yes_token_id"])
                if info.get("no_token_id"):
                    new_tokens.append(info["no_token_id"])
            if new_tokens:
                await self._subscribe_tokens(new_tokens)
                # Update Redis tracking for connected markets
                for cid in added:
                    await self.redis.set_ws_connected(cid, True)

        logger.info(
            "Subscriptions updated",
            total_markets=len(self.subscribed_markets),
            total_tokens=len(self.token_to_market),
            added=len(added),
            removed=len(removed),
        )

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
        # Get market info from asset ID using reverse lookup
        asset_id = data.get("asset_id")
        if not asset_id:
            logger.debug("Trade event missing asset_id")
            return

        # Fast O(1) lookup using token_to_market dict
        token_info = self.token_to_market.get(asset_id)
        if not token_info:
            # Unknown asset ID - could be unsubscribed market
            return

        condition_id = token_info["condition_id"]
        market_id = token_info["market_id"]
        token_type = token_info["token_type"]  # "YES" or "NO"

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
                    token_type=token_type,
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

        # Track trade for rate monitoring
        self._record_trade()

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

        # Fast O(1) lookup using token_to_market dict
        token_info = self.token_to_market.get(asset_id)
        if token_info:
            condition_id = token_info["condition_id"]
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

        # Fast O(1) lookup using token_to_market dict
        token_info = self.token_to_market.get(asset_id)
        if token_info:
            await self.redis.set_price(token_info["condition_id"], float(price))

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
    Each market uses 2 subscription slots (YES + NO token).
    """

    def __init__(self, num_connections: int = 2):
        self.num_connections = num_connections
        self.collectors: list[WebSocketCollector] = []
        self.running = False

    async def start(self) -> None:
        """Start all WebSocket collectors in parallel."""
        self.running = True
        # Each market uses 2 slots (YES + NO token)
        max_markets = (self.num_connections * MAX_SUBSCRIPTIONS) // 2
        logger.info(
            "Starting multi-connection collector",
            num_connections=self.num_connections,
            max_tokens=self.num_connections * MAX_SUBSCRIPTIONS,
            max_markets=max_markets,
        )

        # Clear stale entries from Redis ws:connected set
        # This ensures we don't have leftover entries from previous sessions
        redis = RedisClient()
        try:
            await redis.clear_ws_connected()
        finally:
            await redis.close()

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
        """Split markets across connections, prioritizing by tier.

        Each market uses 2 subscription slots (YES + NO token), so max markets
        per connection is MAX_SUBSCRIPTIONS // 2.
        """
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

            # Extract data while session is active (include both token IDs)
            market_data = [
                {
                    "condition_id": m.condition_id,
                    "yes_token_id": m.yes_token_id,
                    "no_token_id": m.no_token_id,
                    "market_id": m.id,
                }
                for m in markets
            ]

        # Each market uses 2 slots (YES + NO token)
        total_token_capacity = self.num_connections * MAX_SUBSCRIPTIONS
        max_markets = total_token_capacity // 2

        if len(market_data) > max_markets:
            logger.warning(
                "Markets exceed total capacity",
                total_markets=len(market_data),
                max_markets=max_markets,
                total_tokens=total_token_capacity,
                dropped=len(market_data) - max_markets,
            )
            market_data = market_data[:max_markets]

        # Calculate max markets per connection (2 tokens per market)
        max_markets_per_conn = MAX_SUBSCRIPTIONS // 2

        # Distribute markets evenly across connections (round-robin for better balance)
        # This ensures each connection gets a mix of tiers rather than one getting all T4
        markets_per_connection: list[list[dict]] = [[] for _ in range(self.num_connections)]
        for idx, market in enumerate(market_data):
            conn_idx = idx % self.num_connections
            if len(markets_per_connection[conn_idx]) < max_markets_per_conn:
                markets_per_connection[conn_idx].append(market)

        for i, collector in enumerate(self.collectors):
            assigned = markets_per_connection[i]

            # Include both YES and NO token IDs
            collector.subscribed_markets = {
                m["condition_id"]: {
                    "yes_token_id": m["yes_token_id"],
                    "no_token_id": m["no_token_id"],
                    "market_id": m["market_id"],
                }
                for m in assigned
            }

            # Build token lookup for this collector
            collector._build_token_lookup()

            logger.info(
                "Assigned markets to connection",
                connection=i,
                markets=len(assigned),
                tokens=len(collector.token_to_market),
            )

    async def _reassignment_loop(self) -> None:
        """Periodically reassign markets to handle tier changes."""
        while self.running:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                # Track old subscriptions before reassignment
                old_subscriptions: dict[int, set[str]] = {
                    i: set(collector.subscribed_markets.keys())
                    for i, collector in enumerate(self.collectors)
                }

                await self._assign_markets_to_connections()

                # Update subscriptions on all collectors
                for i, collector in enumerate(self.collectors):
                    if collector.ws:
                        new_subs = set(collector.subscribed_markets.keys())
                        old_subs = old_subscriptions.get(i, set())

                        # Unsubscribe from removed markets (update Redis tracking)
                        removed = old_subs - new_subs
                        for cid in removed:
                            await collector._unsubscribe(cid)

                        # Subscribe to new markets (both YES and NO tokens)
                        added = new_subs - old_subs
                        if added:
                            new_tokens = []
                            for cid in added:
                                info = collector.subscribed_markets[cid]
                                if info.get("yes_token_id"):
                                    new_tokens.append(info["yes_token_id"])
                                if info.get("no_token_id"):
                                    new_tokens.append(info["no_token_id"])
                            if new_tokens:
                                await collector._subscribe_tokens(new_tokens)
                                # Update Redis tracking
                                for cid in added:
                                    await collector.redis.set_ws_connected(cid, True)

                        logger.info(
                            "Reassignment complete",
                            connection=i,
                            added_markets=len(added),
                            removed_markets=len(removed),
                            total_markets=len(new_subs),
                            total_tokens=len(collector.token_to_market),
                        )
            except Exception as e:
                logger.error("Market reassignment failed", error=str(e))

    def stop(self) -> None:
        """Stop all collectors."""
        self.running = False
        for collector in self.collectors:
            collector.stop()


async def run_collector() -> None:
    """Entry point for WebSocket collector service."""
    # Use multi-connection collector to handle many markets
    # 10 connections * 500 subscriptions = 5000 tokens = 2500 markets (2 tokens per market)
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
