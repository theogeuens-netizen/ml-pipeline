"""
Strategy Registry.

Auto-discovers strategies in the builtin/ directory and provides
factory methods for creating configured strategy instances.
"""

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Optional, Type

from .base import Strategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """
    Registry for trading strategies.

    Discovers and manages strategy classes.
    """

    def __init__(self):
        self._strategies: dict[str, Type[Strategy]] = {}
        self._instances: dict[str, Strategy] = {}

    def register(self, strategy_class: Type[Strategy]):
        """
        Register a strategy class.

        Args:
            strategy_class: Strategy class to register
        """
        name = strategy_class.name
        self._strategies[name] = strategy_class
        logger.debug(f"Registered strategy: {name}")

    def get_strategy_class(self, name: str) -> Optional[Type[Strategy]]:
        """
        Get a strategy class by name.

        Args:
            name: Strategy name

        Returns:
            Strategy class, or None if not found
        """
        return self._strategies.get(name)

    def create_strategy(self, name: str, config: Optional[dict] = None) -> Optional[Strategy]:
        """
        Create a configured strategy instance.

        Args:
            name: Strategy name
            config: Strategy configuration (params)

        Returns:
            Configured Strategy instance, or None if not found
        """
        strategy_class = self._strategies.get(name)
        if strategy_class is None:
            logger.warning(f"Strategy not found: {name}")
            return None

        strategy = strategy_class()
        if config:
            strategy.configure(config)

        return strategy

    def get_or_create_strategy(self, name: str, config: Optional[dict] = None) -> Optional[Strategy]:
        """
        Get an existing instance or create a new one.

        Caches instances for reuse.

        Args:
            name: Strategy name
            config: Strategy configuration

        Returns:
            Strategy instance
        """
        if name in self._instances:
            # Update config if provided
            if config:
                self._instances[name].configure(config)
            return self._instances[name]

        strategy = self.create_strategy(name, config)
        if strategy:
            self._instances[name] = strategy

        return strategy

    def list_strategies(self) -> list[str]:
        """
        List all registered strategy names.

        Returns:
            List of strategy names
        """
        return list(self._strategies.keys())

    def get_strategy_info(self, name: str) -> Optional[dict]:
        """
        Get information about a strategy.

        Args:
            name: Strategy name

        Returns:
            Dict with name, description, version, or None if not found
        """
        strategy_class = self._strategies.get(name)
        if strategy_class is None:
            return None

        return {
            "name": strategy_class.name,
            "description": strategy_class.description,
            "version": strategy_class.version,
        }

    def discover_builtin_strategies(self):
        """
        Auto-discover strategies in the builtin/ directory.

        Scans for Python modules and registers any Strategy subclasses found.
        """
        builtin_path = Path(__file__).parent / "builtin"
        if not builtin_path.exists():
            logger.warning(f"Builtin strategies directory not found: {builtin_path}")
            return

        logger.info(f"Discovering strategies in: {builtin_path}")

        # Import the builtin package
        try:
            import src.executor.strategies.builtin as builtin_pkg
        except ImportError as e:
            logger.error(f"Failed to import builtin package: {e}")
            return

        # Iterate through modules in the package
        for finder, module_name, is_pkg in pkgutil.iter_modules(builtin_pkg.__path__):
            if module_name.startswith("_"):
                continue

            try:
                module = importlib.import_module(f"src.executor.strategies.builtin.{module_name}")

                # Find Strategy subclasses in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, Strategy)
                        and attr is not Strategy
                        and hasattr(attr, "name")
                        and attr.name != "base"
                    ):
                        self.register(attr)

            except Exception as e:
                logger.error(f"Failed to load strategy module {module_name}: {e}")

        logger.info(f"Discovered {len(self._strategies)} strategies: {list(self._strategies.keys())}")

    def clear(self):
        """Clear all registered strategies and instances."""
        self._strategies.clear()
        self._instances.clear()


# Global registry instance
_registry: Optional[StrategyRegistry] = None


def get_registry() -> StrategyRegistry:
    """
    Get the global strategy registry.

    Initializes and discovers strategies on first call.

    Returns:
        StrategyRegistry instance
    """
    global _registry

    if _registry is None:
        _registry = StrategyRegistry()
        _registry.discover_builtin_strategies()

    return _registry


def register_strategy(strategy_class: Type[Strategy]):
    """
    Decorator to register a strategy class.

    Usage:
        @register_strategy
        class MyStrategy(Strategy):
            name = "my_strategy"
            ...
    """
    get_registry().register(strategy_class)
    return strategy_class
