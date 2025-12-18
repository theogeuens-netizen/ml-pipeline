"""
API client modules for Polymarket data fetching.

- GammaClient: Market discovery and metadata
- CLOBClient: Orderbook data and depth metrics
- BaseClient: Rate-limited HTTP client base class
"""

from src.fetchers.base import BaseClient, RateLimiter
from src.fetchers.gamma import GammaClient
from src.fetchers.clob import CLOBClient

__all__ = [
    "BaseClient",
    "RateLimiter",
    "GammaClient",
    "CLOBClient",
]
