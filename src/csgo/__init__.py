"""
CS:GO Real-Time Trading Pipeline.

This package provides:
- Market discovery: Find CS:GO markets in the database
- Enrichment: Fetch detailed metadata from Gamma API
- WebSocket: Dedicated real-time data subscription
- Signals: Redis streams for strategy consumption
"""

from src.csgo.discovery import (
    discover_csgo_markets,
    get_csgo_matches,
    get_matches_for_subscription,
    sync_csgo_matches,
)
from src.csgo.enrichment import (
    enrich_all_csgo_matches,
    enrich_csgo_market,
    parse_gamma_response,
)
from src.csgo.signals import (
    consume_csgo_signals,
    get_recent_signals,
    get_stream_stats,
    publish_csgo_signal,
)
from src.csgo.websocket import CSGOWebSocketCollector, run_csgo_collector

__all__ = [
    # Discovery
    "discover_csgo_markets",
    "get_csgo_matches",
    "get_matches_for_subscription",
    "sync_csgo_matches",
    # Enrichment
    "enrich_csgo_market",
    "enrich_all_csgo_matches",
    "parse_gamma_response",
    # WebSocket
    "CSGOWebSocketCollector",
    "run_csgo_collector",
    # Signals
    "publish_csgo_signal",
    "consume_csgo_signals",
    "get_recent_signals",
    "get_stream_stats",
]
