"""
Strategy Loader - loads strategies from YAML config.

Reads strategies.yaml and instantiates strategy type classes with the
configured parameters. This enables adding new strategy variants by
editing YAML - no code changes needed.

Usage:
    from strategies.loader import load_strategies

    strategies = load_strategies()  # Returns list of Strategy instances
    strategies = load_strategies(enabled_only=True)  # Only enabled strategies
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

from strategies.base import Strategy
from strategies.types import STRATEGY_TYPES

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "strategies.yaml"


def load_strategies(
    config_path: Optional[Path] = None,
    enabled_only: bool = False,
    strategy_names: Optional[list[str]] = None,
) -> list[Strategy]:
    """
    Load strategies from YAML configuration.

    Args:
        config_path: Path to strategies.yaml (default: repo root)
        enabled_only: If True, only return strategies with enabled=True
        strategy_names: If provided, only return these strategy names

    Returns:
        List of instantiated Strategy objects
    """
    path = config_path or CONFIG_PATH

    if not path.exists():
        logger.error(f"Strategy config not found: {path}")
        return []

    with open(path) as f:
        config = yaml.safe_load(f)

    defaults = config.get("defaults", {})
    strategies = []

    for type_name, strategy_class in STRATEGY_TYPES.items():
        type_configs = config.get(type_name, [])

        if not type_configs:
            continue

        for strat_config in type_configs:
            # Merge defaults with strategy-specific config
            merged = {**defaults, **strat_config}
            name = merged.get("name")

            if not name:
                logger.warning(f"Strategy in {type_name} section missing 'name', skipping")
                continue

            # Filter by enabled flag
            if enabled_only and not merged.get("enabled", True):
                logger.debug(f"Skipping disabled strategy: {name}")
                continue

            # Filter by name list
            if strategy_names and name not in strategy_names:
                continue

            try:
                # Extract live flag before filtering for constructor
                is_live = merged.get("live", False)

                # Remove fields not accepted by constructor
                # (enabled is for filtering, allocated_usd is for balance table, live is set post-init)
                constructor_args = {
                    k: v for k, v in merged.items()
                    if k not in ("enabled", "allocated_usd", "live")
                }

                strategy = strategy_class(**constructor_args)

                # Set live flag on instance (allows per-strategy live trading)
                strategy.live = is_live

                strategies.append(strategy)
                live_marker = " [LIVE]" if is_live else ""
                logger.debug(f"Loaded strategy: {name} ({type_name}){live_marker}")

            except Exception as e:
                logger.error(f"Failed to load strategy {name}: {e}")
                continue

    logger.info(f"Loaded {len(strategies)} strategies from {path}")
    return strategies


def get_strategy_config(name: str, config_path: Optional[Path] = None) -> Optional[dict]:
    """
    Get raw config dict for a specific strategy.

    Args:
        name: Strategy name to look up
        config_path: Path to strategies.yaml

    Returns:
        Config dict with defaults merged, or None if not found
    """
    path = config_path or CONFIG_PATH

    if not path.exists():
        return None

    with open(path) as f:
        config = yaml.safe_load(f)

    defaults = config.get("defaults", {})

    for type_name in STRATEGY_TYPES.keys():
        type_configs = config.get(type_name, [])
        for strat_config in type_configs:
            if strat_config.get("name") == name:
                return {**defaults, **strat_config, "type": type_name}

    return None


def list_strategy_names(config_path: Optional[Path] = None) -> list[str]:
    """
    Get list of all strategy names in config.

    Returns:
        List of strategy name strings
    """
    path = config_path or CONFIG_PATH

    if not path.exists():
        return []

    with open(path) as f:
        config = yaml.safe_load(f)

    names = []
    for type_name in STRATEGY_TYPES.keys():
        type_configs = config.get(type_name, [])
        for strat_config in type_configs:
            name = strat_config.get("name")
            if name:
                names.append(name)

    return names


def get_strategy_by_name(
    name: str,
    config_path: Optional[Path] = None,
) -> Optional[Strategy]:
    """
    Load a single strategy by name.

    Args:
        name: Strategy name to load
        config_path: Path to strategies.yaml

    Returns:
        Strategy instance or None if not found
    """
    strategies = load_strategies(config_path=config_path, strategy_names=[name])
    return strategies[0] if strategies else None


def validate_config(config_path: Optional[Path] = None) -> list[str]:
    """
    Validate strategy configuration.

    Returns:
        List of error messages (empty if valid)
    """
    path = config_path or CONFIG_PATH
    errors = []

    if not path.exists():
        return [f"Config file not found: {path}"]

    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"Invalid YAML: {e}"]

    if not isinstance(config, dict):
        return ["Config must be a dictionary"]

    seen_names = set()

    for type_name in STRATEGY_TYPES.keys():
        type_configs = config.get(type_name, [])

        if not isinstance(type_configs, list):
            errors.append(f"'{type_name}' section must be a list")
            continue

        for i, strat_config in enumerate(type_configs):
            if not isinstance(strat_config, dict):
                errors.append(f"{type_name}[{i}]: must be a dictionary")
                continue

            name = strat_config.get("name")
            if not name:
                errors.append(f"{type_name}[{i}]: missing 'name' field")
                continue

            if name in seen_names:
                errors.append(f"Duplicate strategy name: {name}")
            seen_names.add(name)

    # Try loading each strategy to catch constructor errors
    strategies = load_strategies(config_path=path)
    loaded_names = {s.name for s in strategies}
    expected_names = seen_names - {n for n in seen_names if errors}

    for name in expected_names - loaded_names:
        errors.append(f"Strategy '{name}' failed to load (check logs)")

    return errors


if __name__ == "__main__":
    # Test loading
    logging.basicConfig(level=logging.DEBUG)

    print("Validating config...")
    errors = validate_config()
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("Config valid!")

    print("\nLoading strategies...")
    strategies = load_strategies()
    print(f"\nLoaded {len(strategies)} strategies:")
    for s in strategies:
        print(f"  - {s.name} ({type(s).__name__})")
