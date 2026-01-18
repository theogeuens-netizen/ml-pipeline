"""
Streaming executor configuration.

Loads configuration from strategies.yaml under `streaming_book_imbalance` section.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Default config path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "strategies.yaml"


@dataclass
class StreamingConfig:
    """Configuration for streaming book imbalance strategy."""

    # Identity
    name: str = "streaming_imbalance_crypto"
    enabled: bool = True
    live: bool = False  # Paper by default - seamless switch to live

    # Strategy parameters
    min_imbalance: float = 0.5  # |imbalance| >= 50% to trigger
    yes_price_min: float = 0.30  # Price zone minimum
    yes_price_max: float = 0.70  # Price zone maximum
    max_spread: float = 0.02  # 2% max spread

    # Market selection
    categories: list = field(default_factory=lambda: ["CRYPTO"])
    max_hours_to_close: float = 4.0  # Only markets <4h to expiry
    min_minutes_to_close: float = 2.0  # Safety buffer before resolution

    # Position management
    max_positions: int = 5  # Max concurrent positions
    fixed_size_usd: float = 1.1  # Fixed USD size per trade
    cooldown_minutes: float = 60.0  # Minutes between entries on same market

    # Safety thresholds (stricter than polling executor)
    max_signal_age_seconds: float = 5.0  # 5s max (vs 120s for polling)
    max_price_deviation: float = 0.03  # 3% max (vs 5% for polling)
    max_fee_rate_bps: int = 200  # 2% max fee

    # WebSocket settings
    subscription_refresh_interval: int = 300  # Refresh subscriptions every 5 min
    reconnect_delay: float = 5.0  # Initial reconnect delay
    max_reconnect_delay: float = 60.0  # Max reconnect delay


def load_streaming_config(config_path: Optional[Path] = None) -> StreamingConfig:
    """
    Load streaming configuration from strategies.yaml.

    Args:
        config_path: Path to strategies.yaml (uses default if None)

    Returns:
        StreamingConfig with loaded settings
    """
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        logger.warning(f"Config file not found: {path}, using defaults")
        return StreamingConfig()

    try:
        with open(path) as f:
            config_data = yaml.safe_load(f)

        # Look for streaming_book_imbalance section
        streaming_configs = config_data.get("streaming_book_imbalance", [])

        if not streaming_configs:
            logger.warning("No streaming_book_imbalance config found, using defaults")
            return StreamingConfig()

        # Use first enabled config
        for cfg in streaming_configs:
            if cfg.get("enabled", True):
                return _parse_config(cfg)

        logger.warning("No enabled streaming config found, using defaults")
        return StreamingConfig()

    except Exception as e:
        logger.error(f"Failed to load streaming config: {e}")
        return StreamingConfig()


def _parse_config(cfg: dict) -> StreamingConfig:
    """Parse config dict into StreamingConfig."""
    return StreamingConfig(
        name=cfg.get("name", "streaming_imbalance_crypto"),
        enabled=cfg.get("enabled", True),
        live=cfg.get("live", False),
        min_imbalance=cfg.get("min_imbalance", 0.5),
        yes_price_min=cfg.get("yes_price_min", 0.30),
        yes_price_max=cfg.get("yes_price_max", 0.70),
        max_spread=cfg.get("max_spread", 0.02),
        categories=cfg.get("categories", ["CRYPTO"]),
        max_hours_to_close=cfg.get("max_hours_to_close", 4.0),
        min_minutes_to_close=cfg.get("min_minutes_to_close", 2.0),
        max_positions=cfg.get("max_positions", 5),
        fixed_size_usd=cfg.get("fixed_size_usd", 1.1),
        cooldown_minutes=cfg.get("cooldown_minutes", 60.0),
        max_signal_age_seconds=cfg.get("max_signal_age_seconds", 5.0),
        max_price_deviation=cfg.get("max_price_deviation", 0.03),
        max_fee_rate_bps=cfg.get("max_fee_rate_bps", 200),
        subscription_refresh_interval=cfg.get("subscription_refresh_interval", 300),
        reconnect_delay=cfg.get("reconnect_delay", 5.0),
        max_reconnect_delay=cfg.get("max_reconnect_delay", 60.0),
    )
