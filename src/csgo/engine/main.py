"""
CSGO Trading Engine - Main Entry Point.

This is the Docker entry point for the csgo-executor container.
It runs the tick router with registered strategies.

Usage:
    python -m src.csgo.engine.main

Environment variables:
    REDIS_URL: Redis connection URL
    DATABASE_URL: PostgreSQL connection URL
    TELEGRAM_BOT_TOKEN: Telegram bot token (optional)
    TELEGRAM_CHAT_ID: Telegram chat ID (optional)
    CSGO_STRATEGIES: Comma-separated list of strategies to enable (optional)
"""

import asyncio
import logging
import os
import signal
import sys
from typing import List

# Configure logging before imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

# Reduce noise from third-party libs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def get_enabled_strategies() -> List[str]:
    """
    Get list of enabled strategies from environment.

    Returns:
        List of strategy names to enable, or empty for all
    """
    strategies_env = os.environ.get("CSGO_STRATEGIES", "")
    if strategies_env:
        return [s.strip() for s in strategies_env.split(",") if s.strip()]
    return []


def load_strategies():
    """
    Load and instantiate strategy classes.

    Returns:
        List of CSGOStrategy instances
    """
    from src.csgo.engine.state import CSGOStateManager

    strategies = []
    state_manager = CSGOStateManager()

    # Import strategy classes
    try:
        from src.csgo.strategies.scalp import CSGOScalpStrategy
        strategies.append(CSGOScalpStrategy(state_manager))
    except ImportError as e:
        logger.warning(f"Could not load CSGOScalpStrategy: {e}")

    try:
        from src.csgo.strategies.favorite_hedge import CSGOFavoriteHedgeStrategy
        strategies.append(CSGOFavoriteHedgeStrategy(state_manager))
    except ImportError as e:
        logger.warning(f"Could not load CSGOFavoriteHedgeStrategy: {e}")

    try:
        from src.csgo.strategies.swing_rebalance import CSGOSwingRebalanceStrategy
        strategies.append(CSGOSwingRebalanceStrategy(state_manager))
    except ImportError as e:
        logger.warning(f"Could not load CSGOSwingRebalanceStrategy: {e}")

    try:
        from src.csgo.strategies.map_longshot import CSGOMapLongshotStrategy
        strategies.append(CSGOMapLongshotStrategy(state_manager))
    except ImportError as e:
        logger.warning(f"Could not load CSGOMapLongshotStrategy: {e}")

    # Filter by enabled list if specified
    enabled = get_enabled_strategies()
    if enabled:
        strategies = [s for s in strategies if s.name in enabled]

    return strategies


async def run_engine():
    """
    Main async entry point.

    Sets up the router with strategies and runs the main loop.
    """
    from src.csgo.engine.router import CSGOTickRouter
    from src.csgo.engine.state import CSGOStateManager
    from src.csgo.engine.positions import CSGOPositionManager
    from src.csgo.engine.executor import CSGOExecutor

    logger.info("=" * 60)
    logger.info("CSGO Trading Engine Starting")
    logger.info("=" * 60)

    # Initialize components
    state_manager = CSGOStateManager()
    position_manager = CSGOPositionManager(state_manager)
    executor = CSGOExecutor(state_manager, position_manager, enable_alerts=True)

    # Load strategies
    strategies = load_strategies()

    if not strategies:
        logger.warning("No strategies loaded! Engine will run but take no actions.")
        logger.info("Create strategies in src/csgo/strategies/ and import them in main.py")

    for strategy in strategies:
        logger.info(f"  - {strategy.name} v{strategy.version}")

    # Create router
    router = CSGOTickRouter(
        strategies=strategies,
        state_manager=state_manager,
        position_manager=position_manager,
        executor=executor,
    )

    # Setup shutdown handler
    loop = asyncio.get_running_loop()

    def shutdown_handler():
        logger.info("Shutdown signal received")
        router.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_handler)

    # Run the router
    try:
        await router.run()
    finally:
        logger.info("Engine shutdown complete")
        stats = router.get_stats()
        logger.info(f"Final stats: {stats}")


def main():
    """
    Sync entry point for running as module.
    """
    try:
        asyncio.run(run_engine())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Engine crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
