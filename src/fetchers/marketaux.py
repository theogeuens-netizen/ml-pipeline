"""
Marketaux API client for financial and crypto news.

API Documentation: https://www.marketaux.com/documentation

Free tier limits:
- 100 requests/day
- No historical access
- Real-time only

Usage:
    client = MarketauxClient()
    if client.enabled:
        articles = client.fetch_news(symbols=["BTCUSD", "ETHUSD"])
"""

import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog

from src.config.settings import settings

logger = structlog.get_logger()

# API base URL
MARKETAUX_API_BASE = "https://api.marketaux.com/v1"

# Rate limiting: 100 requests/day = ~4.2/hour, be conservative
# We'll fetch every 15 minutes = 96/day, leaving margin
RATE_LIMIT_PER_SECOND = 0.1  # Very conservative

# Crypto-focused symbols to track
CRYPTO_SYMBOLS = [
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "XRPUSD",
    "DOGEUSD",
    "ADAUSD",
    "MATICUSD",
    "LINKUSD",
    "AVAXUSD",
    "DOTUSD",
]

# Map Marketaux sentiment to our categories
SENTIMENT_CATEGORIES = {
    "Bearish": -1.0,
    "Somewhat-Bearish": -0.5,
    "Neutral": 0.0,
    "Somewhat-Bullish": 0.5,
    "Bullish": 1.0,
}


class MarketauxClient:
    """
    Synchronous Marketaux API client.

    Use for Celery tasks - no async event loop issues.
    """

    def __init__(self):
        """Initialize client with API key from settings."""
        self.api_key = settings.marketaux_api_key
        self.enabled = bool(self.api_key)
        self.base_url = MARKETAUX_API_BASE
        self.last_request_time = 0.0

        if not self.enabled:
            logger.warning("Marketaux client disabled - no API key configured")
            logger.info("Set MARKETAUX_API_KEY in .env to enable news collection")

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.monotonic() - self.last_request_time
        min_interval = 1.0 / RATE_LIMIT_PER_SECOND
        if elapsed < min_interval:
            sleep_time = min_interval - elapsed
            logger.debug("Rate limiting", sleep_seconds=round(sleep_time, 2))
            time.sleep(sleep_time)
        self.last_request_time = time.monotonic()

    def fetch_news(
        self,
        symbols: Optional[list[str]] = None,
        filter_entities: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Fetch latest news articles.

        Args:
            symbols: List of symbols to filter (e.g., ["BTCUSD", "ETHUSD"])
            filter_entities: Include entity extraction in response
            limit: Max articles to return (max 50 on free tier)

        Returns:
            List of article dictionaries with:
            - uuid: Unique article ID
            - title: Article headline
            - description: Article snippet
            - url: Source URL
            - published_at: ISO timestamp
            - sentiment_score: -1 to 1
            - entities: List of {symbol, name, type}
        """
        if not self.enabled:
            logger.debug("Marketaux disabled, skipping fetch")
            return []

        self._rate_limit()

        # Use crypto symbols by default
        if symbols is None:
            symbols = CRYPTO_SYMBOLS

        params = {
            "api_token": self.api_key,
            "symbols": ",".join(symbols),
            "filter_entities": str(filter_entities).lower(),
            "limit": min(limit, 50),  # Free tier max
            "language": "en",
        }

        try:
            response = httpx.get(
                f"{self.base_url}/news/all",
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            articles = data.get("data", [])
            logger.info(
                "Fetched news articles",
                count=len(articles),
                symbols=symbols[:3],  # Log first 3 symbols
            )

            return self._transform_articles(articles)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("Marketaux API key invalid or expired")
            elif e.response.status_code == 429:
                logger.warning("Marketaux rate limit exceeded (100/day)")
            else:
                logger.error(
                    "Marketaux API error",
                    status=e.response.status_code,
                    response=e.response.text[:200],
                )
            return []

        except Exception as e:
            logger.error("Marketaux fetch failed", error=str(e))
            return []

    def _transform_articles(self, articles: list[dict]) -> list[dict[str, Any]]:
        """
        Transform Marketaux response to our schema.

        Maps:
        - uuid -> source_id
        - title -> title
        - description -> snippet
        - url -> url
        - published_at -> published_at (parsed to datetime)
        - entities -> entities, symbols
        - sentiment_score from entities
        """
        result = []

        for article in articles:
            try:
                # Parse published timestamp
                published_str = article.get("published_at", "")
                published_at = None
                if published_str:
                    # Marketaux uses ISO format
                    published_at = datetime.fromisoformat(
                        published_str.replace("Z", "+00:00")
                    )

                # Extract entities and sentiment
                entities = article.get("entities", [])
                symbols = []
                sentiment_scores = []

                for entity in entities:
                    if entity.get("symbol"):
                        symbols.append(entity["symbol"])

                    # Collect sentiment scores
                    sentiment_label = entity.get("sentiment_score")
                    if sentiment_label in SENTIMENT_CATEGORIES:
                        sentiment_scores.append(SENTIMENT_CATEGORIES[sentiment_label])

                # Average sentiment across entities, or None if no sentiments
                avg_sentiment = None
                if sentiment_scores:
                    avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)

                result.append({
                    "source": "marketaux",
                    "source_id": article.get("uuid"),
                    "title": article.get("title", ""),
                    "snippet": article.get("description", ""),
                    "url": article.get("url"),
                    "published_at": published_at,
                    "sentiment_score": avg_sentiment,
                    "category": "CRYPTO",  # All Marketaux fetches are crypto-focused
                    "symbols": symbols if symbols else None,
                    "entities": entities if entities else None,
                    "raw_response": article,
                })

            except Exception as e:
                logger.warning(
                    "Failed to transform article",
                    error=str(e),
                    article_id=article.get("uuid"),
                )
                continue

        return result


# Singleton client instance
_client: Optional[MarketauxClient] = None


def get_client() -> MarketauxClient:
    """Get the singleton Marketaux client instance."""
    global _client
    if _client is None:
        _client = MarketauxClient()
    return _client
