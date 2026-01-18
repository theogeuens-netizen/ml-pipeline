"""
WebSocket handler for streaming executor.

Connects to Polymarket WebSocket and subscribes to orderbook updates
for selected CRYPTO markets.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

from .config import StreamingConfig
from .state import StreamingStateManager

logger = logging.getLogger(__name__)

# Polymarket WebSocket endpoint
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Maximum tokens per connection (Polymarket limit)
MAX_TOKENS_PER_CONNECTION = 500


class StreamingWebSocket:
    """
    Dedicated WebSocket connection for streaming executor.

    Connects to Polymarket WebSocket and subscribes to "book" channel
    for selected token IDs. Handles reconnection automatically.
    """

    def __init__(
        self,
        config: StreamingConfig,
        state: StreamingStateManager,
        on_book_update: Callable[[str, list, list], Awaitable[None]],
    ):
        """
        Initialize WebSocket handler.

        Args:
            config: Streaming configuration
            state: State manager
            on_book_update: Callback for book updates (token_id, bids, asks)
        """
        self.config = config
        self.state = state
        self.on_book_update = on_book_update

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.connected = False
        self.subscribed_tokens: Set[str] = set()
        self.reconnect_delay = config.reconnect_delay

        # Stats
        self.stats = {
            "messages_received": 0,
            "book_updates": 0,
            "errors": 0,
            "reconnects": 0,
        }
        self.last_message_at: Optional[datetime] = None

    async def start(self):
        """
        Start WebSocket connection with auto-reconnect.

        Runs until stop() is called.
        """
        self.running = True
        logger.info("Starting streaming WebSocket")

        while self.running:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                logger.info("WebSocket cancelled")
                break
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"WebSocket error: {e}")

            if self.running:
                self.stats["reconnects"] += 1
                logger.info(f"Reconnecting in {self.reconnect_delay:.1f}s")
                await asyncio.sleep(self.reconnect_delay)
                # Exponential backoff
                self.reconnect_delay = min(
                    self.reconnect_delay * 1.5,
                    self.config.max_reconnect_delay,
                )

    async def _connect_and_run(self):
        """Connect to WebSocket and process messages."""
        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                self.ws = ws
                self.connected = True
                self.reconnect_delay = self.config.reconnect_delay  # Reset backoff
                logger.info("Streaming WebSocket connected")

                # Subscribe to current tokens
                if self.subscribed_tokens:
                    await self._subscribe(list(self.subscribed_tokens))

                # Process messages
                async for message in ws:
                    if not self.running:
                        break
                    await self._handle_message(message)

        except ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
        finally:
            self.connected = False
            self.ws = None

    async def _handle_message(self, message: str | bytes):
        """
        Process incoming WebSocket message.

        Messages can be:
        - JSON array of events
        - Single JSON event
        - MessagePack binary (if server sends binary)
        - "PING"/"PONG" heartbeats
        """
        self.stats["messages_received"] += 1
        self.last_message_at = datetime.now(timezone.utc)

        try:
            # Handle binary (msgpack)
            if isinstance(message, bytes):
                try:
                    import msgpack
                    data = msgpack.unpackb(message, raw=False)
                except ImportError:
                    logger.warning("msgpack not installed, skipping binary message")
                    return
            else:
                # Handle text
                if message in ("PING", "PONG"):
                    return

                data = json.loads(message)

            # Process event(s)
            if isinstance(data, list):
                for event in data:
                    await self._process_event(event)
            else:
                await self._process_event(data)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse message: {e}")
        except Exception as e:
            logger.warning(f"Failed to handle message: {e}")

    async def _process_event(self, data: dict):
        """
        Process single WebSocket event.

        We're interested in "book" events which contain orderbook snapshots.
        """
        event_type = data.get("event_type")

        if event_type == "book":
            token_id = data.get("asset_id")

            if token_id and token_id in self.subscribed_tokens:
                # Extract bids and asks
                # Polymarket uses "buys" and "sells" in some responses
                bids = data.get("bids") or data.get("buys") or []
                asks = data.get("asks") or data.get("sells") or []

                self.stats["book_updates"] += 1

                # Call handler
                await self.on_book_update(token_id, bids, asks)

    async def update_subscriptions(self, token_ids: list[str]):
        """
        Update subscribed tokens.

        Args:
            token_ids: New list of token IDs to subscribe to
        """
        new_tokens = set(token_ids[:MAX_TOKENS_PER_CONNECTION])

        # Calculate changes
        to_add = new_tokens - self.subscribed_tokens

        # Update tracked set
        self.subscribed_tokens = new_tokens

        # Subscribe to new tokens
        if self.ws and to_add:
            await self._subscribe(list(to_add))

        logger.info(f"Subscriptions updated: {len(self.subscribed_tokens)} tokens")

    async def _subscribe(self, token_ids: list[str]):
        """
        Send subscription message to WebSocket.

        Args:
            token_ids: Token IDs to subscribe to
        """
        if not self.ws or not token_ids:
            return

        # Limit to max per connection
        tokens = token_ids[:MAX_TOKENS_PER_CONNECTION]

        try:
            message = {
                "type": "market",
                "assets_ids": tokens,
            }
            await self.ws.send(json.dumps(message))
            logger.info(f"Subscribed to {len(tokens)} tokens")

        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")

    def stop(self):
        """Stop WebSocket connection."""
        logger.info("Stopping streaming WebSocket")
        self.running = False

    def get_stats(self) -> dict:
        """Get WebSocket statistics."""
        return {
            "connected": self.connected,
            "tokens_subscribed": len(self.subscribed_tokens),
            "messages_received": self.stats["messages_received"],
            "book_updates": self.stats["book_updates"],
            "errors": self.stats["errors"],
            "reconnects": self.stats["reconnects"],
            "last_message": (
                self.last_message_at.isoformat() if self.last_message_at else None
            ),
        }
