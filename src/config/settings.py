"""
Application configuration using Pydantic Settings.

All settings loaded from environment variables with sensible defaults.
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ===========================================
    # Database
    # ===========================================
    database_url: str = "postgresql://postgres:postgres@localhost:5433/polymarket_ml"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ===========================================
    # Redis
    # ===========================================
    redis_url: str = "redis://localhost:6380/0"
    redis_trade_buffer_ttl: int = 7200  # 2 hours
    redis_trade_buffer_max: int = 10000

    # ===========================================
    # Celery
    # ===========================================
    celery_broker_url: str = "redis://localhost:6380/0"
    celery_result_backend: str = "redis://localhost:6380/1"

    # ===========================================
    # Polymarket APIs
    # ===========================================
    gamma_api_base: str = "https://gamma-api.polymarket.com"
    clob_api_base: str = "https://clob.polymarket.com"
    websocket_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # Rate limits (requests per second, conservative)
    gamma_rate_limit: float = 10.0  # 125/10s = 12.5/s, use 10
    clob_rate_limit: float = 15.0  # 200/10s = 20/s, use 15

    # ===========================================
    # Data Collection
    # ===========================================
    # Market filtering
    ml_volume_threshold: float = 100.0  # Min 24h volume to track (lowered for more data)
    ml_lookahead_hours: int = 336  # Track markets up to 2 weeks out

    # Tier boundaries (hours to resolution)
    tier_0_min_hours: float = 48.0  # > 48h
    tier_1_min_hours: float = 12.0  # 12-48h
    tier_2_min_hours: float = 4.0  # 4-12h
    tier_3_min_hours: float = 1.0  # 1-4h
    # tier_4: < 1h

    # Collection intervals (seconds)
    tier_0_interval: int = 3600  # 60 min
    tier_1_interval: int = 300  # 5 min
    tier_2_interval: int = 60  # 1 min
    tier_3_interval: int = 30  # 30 sec
    tier_4_interval: int = 15  # 15 sec

    # Orderbook collection (only for T2+)
    orderbook_enabled_tiers: list[int] = [2, 3, 4]

    # ===========================================
    # Whale Detection
    # ===========================================
    whale_tier_1_threshold: float = 500.0  # Large trade
    whale_tier_2_threshold: float = 2000.0  # Whale
    whale_tier_3_threshold: float = 10000.0  # Mega whale

    # ===========================================
    # WebSocket
    # ===========================================
    websocket_enabled_tiers: list[int] = [2, 3, 4]  # Only T2+ get WS
    websocket_reconnect_delay: float = 5.0
    websocket_max_reconnect_delay: float = 60.0
    websocket_num_connections: int = 10  # Number of parallel WS connections (500 subscriptions each, 2 per market = 2500 markets max)

    # ===========================================
    # Application
    # ===========================================
    log_level: str = "INFO"
    debug: bool = False

    # ===========================================
    # Executor (Trading)
    # ===========================================
    # Polymarket trading credentials
    polymarket_private_key: str = ""
    polymarket_funder_address: str = ""

    # Proxy for bypassing IP blocks (optional SOCKS5 URL)
    trading_proxy_url: str = ""
    # Fallback proxy servers (comma-separated hostnames, uses same credentials)
    trading_proxy_fallbacks: str = ""

    # Executor mode (paper or live)
    executor_mode: str = "paper"

    # Path to executor config.yaml
    executor_config_path: str = "config.yaml"

    # Paper trading starting balance
    paper_starting_balance: float = 10000.0

    # ===========================================
    # Telegram Alerts
    # ===========================================
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ===========================================
    # News APIs
    # ===========================================
    marketaux_api_key: str = ""  # Get from https://www.marketaux.com/

    # ===========================================
    # GRID Esports API
    # ===========================================
    grid_api_key: str = ""  # Get from https://grid.gg/


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience access
settings = get_settings()
