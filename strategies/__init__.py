"""
Strategy loader for strategy-as-code.

Strategies are Python files in the strategies/ directory.
Each file must define a `strategy` instance at module level.

Usage:
    from strategies import load_strategy

    strategy = load_strategy("strategies/longshot_yes_v1.py")
    if strategy:
        for signal in strategy.scan(markets):
            print(signal)
"""

import importlib.util
import logging
from pathlib import Path
from typing import Optional

from .base import Strategy, Signal, Side, MarketData

logger = logging.getLogger(__name__)

__all__ = [
    "Strategy",
    "Signal",
    "Side",
    "MarketData",
    "load_strategy",
    "load_all_strategies",
]


def load_strategy(path: str) -> Optional[Strategy]:
    """
    Load a strategy instance from a Python file.

    The file must define a `strategy` variable at module level
    that is an instance of Strategy.

    Args:
        path: Path to the strategy file (e.g., "strategies/my_strategy.py")

    Returns:
        Strategy instance, or None if loading failed
    """
    p = Path(path)

    if not p.exists():
        logger.error(f"Strategy file not found: {path}")
        return None

    if not p.suffix == ".py":
        logger.error(f"Strategy file must be a .py file: {path}")
        return None

    try:
        # Create a unique module name based on file path
        module_name = f"strategies.loaded.{p.stem}"

        spec = importlib.util.spec_from_file_location(module_name, p)
        if spec is None or spec.loader is None:
            logger.error(f"Failed to create module spec for: {path}")
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Get the strategy instance
        strategy = getattr(module, "strategy", None)

        if strategy is None:
            logger.error(f"No 'strategy' variable defined in: {path}")
            return None

        if not isinstance(strategy, Strategy):
            logger.error(f"'strategy' is not a Strategy instance in: {path}")
            return None

        logger.info(f"Loaded strategy: {strategy.name} v{strategy.version} (SHA: {strategy.get_sha()})")
        return strategy

    except Exception as e:
        logger.error(f"Failed to load strategy from {path}: {e}", exc_info=True)
        return None


def load_all_strategies(directory: str = "strategies") -> list[Strategy]:
    """
    Load all strategy files from a directory.

    Skips files starting with underscore or named base.py.

    Args:
        directory: Path to strategies directory

    Returns:
        List of loaded Strategy instances
    """
    strategies = []
    p = Path(directory)

    if not p.exists():
        logger.error(f"Strategies directory not found: {directory}")
        return strategies

    for file in p.glob("*.py"):
        # Skip private files and base.py
        if file.name.startswith("_") or file.name == "base.py":
            continue

        strategy = load_strategy(str(file))
        if strategy:
            strategies.append(strategy)

    logger.info(f"Loaded {len(strategies)} strategies from {directory}")
    return strategies
