"""
Configuration loader with YAML support and hot-reload.

Supports:
- Loading from config.yaml
- Environment variable interpolation (${VAR_NAME})
- Hot-reload when config file changes
- Merging with defaults
"""

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from .schema import ExecutorConfig, StrategyConfig
from .defaults import get_default_config, STRATEGY_DEFAULTS

logger = logging.getLogger(__name__)

# Global config instance
_config: Optional[ExecutorConfig] = None
_config_lock = threading.Lock()
_config_path: Optional[Path] = None
_config_mtime: float = 0


def _interpolate_env_vars(value: Any) -> Any:
    """
    Recursively interpolate environment variables in config values.

    Supports ${VAR_NAME} syntax with optional default: ${VAR_NAME:-default}
    """
    if isinstance(value, str):
        # Pattern: ${VAR_NAME} or ${VAR_NAME:-default}
        pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'

        def replace(match):
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(var_name, default)

        return re.sub(pattern, replace, value)

    elif isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [_interpolate_env_vars(v) for v in value]

    return value


def _merge_strategy_defaults(strategies: dict) -> dict:
    """Merge user strategy config with defaults."""
    result = {}

    # Start with defaults
    for name, default_config in STRATEGY_DEFAULTS.items():
        result[name] = default_config.model_copy()

    # Override with user config
    for name, user_config in strategies.items():
        if name in result:
            # Merge params
            default = result[name]
            merged_params = {**default.params, **user_config.get("params", {})}
            result[name] = StrategyConfig(
                enabled=user_config.get("enabled", default.enabled),
                params=merged_params,
                execution=user_config.get("execution"),
                sizing=user_config.get("sizing"),
            )
        else:
            # New strategy (user-defined)
            result[name] = StrategyConfig(**user_config)

    return result


def load_config(config_path: Optional[str] = None) -> ExecutorConfig:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to config.yaml. If None, uses default path.

    Returns:
        ExecutorConfig instance
    """
    global _config, _config_path, _config_mtime

    # Determine config path
    if config_path is None:
        # Look in project root
        project_root = Path(__file__).parent.parent.parent.parent
        config_path = project_root / "config.yaml"
    else:
        config_path = Path(config_path)

    _config_path = config_path

    # Check if file exists
    if not config_path.exists():
        logger.warning(f"Config file not found at {config_path}, using defaults")
        with _config_lock:
            _config = get_default_config()
            return _config

    # Load and parse YAML
    try:
        with open(config_path, "r") as f:
            raw_config = yaml.safe_load(f)

        if raw_config is None:
            raw_config = {}

        # Store mtime for hot-reload detection
        _config_mtime = config_path.stat().st_mtime

        # Interpolate environment variables
        config_data = _interpolate_env_vars(raw_config)

        # Get defaults
        defaults = get_default_config()

        # Merge strategies with defaults
        if "strategies" in config_data:
            config_data["strategies"] = _merge_strategy_defaults(config_data["strategies"])
        else:
            config_data["strategies"] = {
                name: cfg.model_dump() for name, cfg in STRATEGY_DEFAULTS.items()
            }

        # Merge with defaults for missing top-level keys
        merged = defaults.model_dump()
        for key, value in config_data.items():
            if key == "strategies":
                merged["strategies"] = value
            elif isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key].update(value)
            else:
                merged[key] = value

        # Create config instance
        with _config_lock:
            _config = ExecutorConfig(**merged)
            logger.info(f"Loaded config from {config_path}")
            logger.info(f"Mode: {_config.mode.value}, Strategies enabled: {[n for n, s in _config.strategies.items() if s.enabled]}")
            return _config

    except Exception as e:
        logger.error(f"Failed to load config from {config_path}: {e}")
        logger.warning("Using default configuration")
        with _config_lock:
            _config = get_default_config()
            return _config


def get_config() -> ExecutorConfig:
    """
    Get the current configuration.

    Loads from file on first call, returns cached instance thereafter.
    """
    global _config

    if _config is None:
        return load_config()

    return _config


def reload_config() -> ExecutorConfig:
    """
    Force reload configuration from file.

    Returns:
        Updated ExecutorConfig instance
    """
    global _config_path

    logger.info("Reloading configuration...")
    return load_config(str(_config_path) if _config_path else None)


def check_config_changed() -> bool:
    """
    Check if the config file has been modified.

    Returns:
        True if file has changed since last load
    """
    global _config_path, _config_mtime

    if _config_path is None or not _config_path.exists():
        return False

    current_mtime = _config_path.stat().st_mtime
    return current_mtime > _config_mtime


def update_config(updates: dict) -> ExecutorConfig:
    """
    Update configuration in memory and optionally save to file.

    Args:
        updates: Dictionary of config updates

    Returns:
        Updated ExecutorConfig instance
    """
    global _config

    with _config_lock:
        if _config is None:
            _config = get_default_config()

        # Create updated config
        current = _config.model_dump()

        # Deep merge updates
        for key, value in updates.items():
            if isinstance(value, dict) and key in current and isinstance(current[key], dict):
                current[key].update(value)
            else:
                current[key] = value

        _config = ExecutorConfig(**current)
        logger.info(f"Config updated: {list(updates.keys())}")
        return _config


def save_config(config_path: Optional[str] = None) -> bool:
    """
    Save current configuration to YAML file.

    Args:
        config_path: Path to save to. If None, uses current config path.

    Returns:
        True if saved successfully
    """
    global _config, _config_path

    if _config is None:
        logger.warning("No config to save")
        return False

    path = Path(config_path) if config_path else _config_path
    if path is None:
        logger.error("No config path specified")
        return False

    try:
        config_dict = _config.model_dump()

        # Convert enums to strings for YAML
        config_dict["mode"] = _config.mode.value
        config_dict["execution"]["default_order_type"] = _config.execution.default_order_type.value
        config_dict["sizing"]["method"] = _config.sizing.method.value

        with open(path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Config saved to {path}")
        return True

    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False
