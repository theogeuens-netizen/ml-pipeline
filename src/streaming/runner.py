"""
Streaming executor runner.

Main entry point that orchestrates:
1. Market selection (refresh every 5 min)
2. WebSocket management
3. Strategy evaluation on book updates
4. Signal execution
5. Position synchronization
"""

import asyncio
import logging
import signal as sig
import sys
from datetime import datetime, timezone
from typing import Optional

import structlog

from src.db.database import get_session
from src.db.redis import RedisClient

from .config import StreamingConfig, load_streaming_config
from .executor import StreamingExecutor
from .market_selector import get_streaming_markets
from .state import StreamingStateManager
from .strategy import StreamingBookImbalanceStrategy
from .websocket import StreamingWebSocket

logger = logging.getLogger(__name__)


class StreamingRunner:
    """
    Main runner for streaming executor.

    Orchestrates all components and manages the main event loop.
    """

    def __init__(self, config: Optional[StreamingConfig] = None):
        """
        Initialize runner.

        Args:
            config: Optional config override (loads from YAML if None)
        """
        self.config = config or load_streaming_config()
        self.state = StreamingStateManager()
        self.strategy = StreamingBookImbalanceStrategy(self.config)
        self.executor = StreamingExecutor(self.config)

        # WebSocket with book update handler
        self.websocket = StreamingWebSocket(
            config=self.config,
            state=self.state,
            on_book_update=self._on_book_update,
        )

        self.running = False
        self.start_time: Optional[datetime] = None

        # Redis for stats publishing
        self._redis: Optional[RedisClient] = None

    @property
    def redis(self) -> RedisClient:
        """Lazy Redis client initialization."""
        if self._redis is None:
            self._redis = RedisClient()
        return self._redis

    async def run(self):
        """
        Main run loop.

        Starts WebSocket and background tasks, runs until stopped.
        """
        self.running = True
        self.start_time = datetime.now(timezone.utc)

        mode = "PAPER" if not self.config.live else "LIVE"
        logger.info(f"Starting streaming executor: {self.config.name}")
        logger.info(f"Mode: {mode}")

        if self.config.live:
            logger.warning("=" * 60)
            logger.warning("  LIVE MODE - TRADING WITH REAL MONEY")
            logger.warning("=" * 60)

        # Initial setup
        await self._refresh_markets()
        await self._sync_positions()

        # Start background tasks
        tasks = [
            asyncio.create_task(self.websocket.start(), name="websocket"),
            asyncio.create_task(self._market_refresh_loop(), name="market_refresh"),
            asyncio.create_task(self._position_sync_loop(), name="position_sync"),
            asyncio.create_task(self._stats_publish_loop(), name="stats_publish"),
        ]

        try:
            # Wait for any task to complete (shouldn't happen normally)
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )

            # If a task completed unexpectedly, log it
            for task in done:
                if task.exception():
                    logger.error(f"Task {task.get_name()} failed: {task.exception()}")

        except asyncio.CancelledError:
            logger.info("Runner cancelled")
        finally:
            # Cancel all tasks
            for task in tasks:
                task.cancel()

            # Wait for cancellation
            await asyncio.gather(*tasks, return_exceptions=True)

            self.websocket.stop()
            logger.info("Streaming executor stopped")

    async def _on_book_update(self, token_id: str, bids: list, asks: list):
        """
        Handle orderbook update from WebSocket.

        Called for every "book" event. Must be fast to not block
        the WebSocket message processing.

        Args:
            token_id: Token that was updated
            bids: List of bid levels
            asks: List of ask levels
        """
        # Update in-memory state
        self.state.update_orderbook(token_id, bids, asks)

        # Get market info for this token
        market = self.state.get_market_for_token(token_id)
        if market is None:
            return

        # Get orderbook state
        book = self.state.get_orderbook(token_id)
        if book is None:
            return

        # Quick imbalance check to avoid expensive evaluation
        imbalance = book.imbalance
        if abs(imbalance) < self.config.min_imbalance:
            return

        # Evaluate strategy
        signal = self.strategy.evaluate(book, market, self.state)

        if signal:
            # Execute in background to not block book processing
            asyncio.create_task(self._execute_signal(signal))

    async def _execute_signal(self, signal):
        """
        Execute signal in background.

        This runs in a separate task to not block WebSocket processing.
        """
        try:
            result = self.executor.execute(signal, self.state)

            if result.success:
                logger.info(
                    f"Trade executed: {signal.token_side} on market {signal.market_id}, "
                    f"price=${result.executed_price:.4f}, position={result.position_id}"
                )
            else:
                logger.debug(f"Signal rejected: {result.reason}")

        except Exception as e:
            logger.error(f"Execution error: {e}", exc_info=True)

    async def _refresh_markets(self):
        """
        Refresh market selection and update subscriptions.

        Queries database for CRYPTO markets <4h and updates
        WebSocket subscriptions.
        """
        try:
            with get_session() as db:
                markets = get_streaming_markets(db, self.config)

            # Update state
            self.state.set_markets(markets)

            # Get tokens to subscribe
            token_ids = self.state.get_subscribed_tokens()

            logger.info(
                f"Selected {len(markets)} markets, {len(token_ids)} tokens "
                f"(categories={self.config.categories}, <{self.config.max_hours_to_close}h)"
            )

            # Update WebSocket subscriptions
            await self.websocket.update_subscriptions(token_ids)

        except Exception as e:
            logger.error(f"Market refresh failed: {e}", exc_info=True)

    async def _market_refresh_loop(self):
        """Periodically refresh market selection."""
        while self.running:
            await asyncio.sleep(self.config.subscription_refresh_interval)
            if self.running:
                await self._refresh_markets()

    async def _sync_positions(self):
        """Sync open positions from database."""
        try:
            with get_session() as db:
                self.state.sync_positions_from_db(
                    db,
                    self.config.name,
                    is_paper=not self.config.live,
                )

            position_count = self.state.get_position_count(self.config.name)
            logger.info(f"Synced positions: {position_count} open")

        except Exception as e:
            logger.error(f"Position sync failed: {e}")

    async def _position_sync_loop(self):
        """Periodically sync positions from database."""
        while self.running:
            await asyncio.sleep(60)  # Every minute
            if self.running:
                await self._sync_positions()

    async def _stats_publish_loop(self):
        """Periodically publish stats to Redis for monitoring."""
        while self.running:
            await asyncio.sleep(10)  # Every 10 seconds
            if self.running:
                await self._publish_stats()

    async def _publish_stats(self):
        """Publish stats to Redis for monitoring dashboard."""
        try:
            stats = {
                "strategy": self.config.name,
                "mode": "paper" if not self.config.live else "live",
                "markets": str(len(self.state.market_info)),
                "tokens": str(len(self.state.token_to_market)),
                "positions": str(self.state.get_position_count(self.config.name)),
                "signals_generated": str(self.state.stats["signals_generated"]),
                "signals_executed": str(self.state.stats["signals_executed"]),
                "ws_connected": "1" if self.websocket.connected else "0",
                "ws_messages": str(self.websocket.stats["messages_received"]),
                "ws_book_updates": str(self.websocket.stats["book_updates"]),
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

            # Store in Redis hash
            await asyncio.to_thread(
                self.redis.client.hset,
                "streaming:stats",
                mapping=stats,
            )

        except Exception as e:
            logger.debug(f"Failed to publish stats: {e}")

    def stop(self):
        """Stop the runner."""
        logger.info("Stopping streaming executor")
        self.running = False
        self.websocket.stop()


async def main():
    """Entry point for streaming executor."""
    runner = StreamingRunner()

    # Setup signal handlers for graceful shutdown
    def shutdown_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        runner.stop()

    sig.signal(sig.SIGTERM, shutdown_handler)
    sig.signal(sig.SIGINT, shutdown_handler)

    await runner.run()


def setup_logging():
    """Configure logging for the executor."""
    # Basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    # Structlog
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Reduce noise from websockets library
    logging.getLogger("websockets").setLevel(logging.WARNING)


if __name__ == "__main__":
    setup_logging()
    logger.info("Starting Streaming Book Imbalance Executor...")
    asyncio.run(main())
