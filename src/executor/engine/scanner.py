"""
Market Scanner.

Fetches and filters markets for strategy scanning.
Uses the existing markets table from data collection.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from src.db.models import Market, Snapshot
from src.executor.config import ExecutorConfig, get_config
from src.executor.strategies.base import MarketData

logger = logging.getLogger(__name__)


class MarketScanner:
    """
    Scans and prepares markets for strategy analysis.

    Uses the existing markets and snapshots tables from data collection.
    """

    def __init__(self, config: Optional[ExecutorConfig] = None):
        """
        Initialize market scanner.

        Args:
            config: Executor configuration
        """
        self.config = config or get_config()

    def get_scannable_markets(
        self,
        db: Optional[Session] = None,
    ) -> list[MarketData]:
        """
        Get all markets eligible for strategy scanning.

        Applies global filters:
        - Active markets only
        - Has token IDs
        - Meets minimum liquidity
        - Not in excluded keywords list

        Args:
            db: Optional database session

        Returns:
            List of MarketData objects
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            filters = self.config.filters

            # Base query for active markets with token IDs
            query = db.query(Market).filter(
                Market.active == True,
                Market.resolved == False,
                Market.yes_token_id.isnot(None),
            )

            # Execute query
            markets = query.all()

            logger.debug(f"Found {len(markets)} active markets with token IDs")

            # Convert to MarketData and apply filters
            result = []
            now = datetime.now(timezone.utc)

            for market in markets:
                # Apply keyword filter
                if self._is_excluded(market, filters.excluded_keywords):
                    continue

                # Get latest snapshot for current price data
                snapshot = self._get_latest_snapshot(db, market.id)

                # Build MarketData
                market_data = self._build_market_data(market, snapshot, now)

                # Apply liquidity filter
                if market_data.liquidity is not None:
                    if market_data.liquidity < filters.min_liquidity_usd:
                        continue

                result.append(market_data)

            logger.info(f"Prepared {len(result)} markets for scanning")
            return result

        finally:
            if close_db:
                db.close()

    def _is_excluded(self, market: Market, excluded_keywords: list[str]) -> bool:
        """Check if market should be excluded based on keywords."""
        question_lower = market.question.lower()
        for keyword in excluded_keywords:
            if keyword.lower() in question_lower:
                return True
        return False

    def _get_latest_snapshot(
        self,
        db: Session,
        market_id: int,
    ) -> Optional[Snapshot]:
        """Get the most recent snapshot for a market."""
        return db.query(Snapshot).filter(
            Snapshot.market_id == market_id
        ).order_by(
            Snapshot.timestamp.desc()
        ).first()

    def _build_market_data(
        self,
        market: Market,
        snapshot: Optional[Snapshot],
        now: datetime,
    ) -> MarketData:
        """Build MarketData from Market and Snapshot."""
        # Calculate hours to close
        hours_to_close = None
        if market.end_date:
            delta = market.end_date - now
            hours_to_close = delta.total_seconds() / 3600

        # Get price data from snapshot or market
        if snapshot:
            price = float(snapshot.price)
            best_bid = float(snapshot.best_bid) if snapshot.best_bid else None
            best_ask = float(snapshot.best_ask) if snapshot.best_ask else None
            spread = float(snapshot.spread) if snapshot.spread else None
            volume_24h = float(snapshot.volume_24h) if snapshot.volume_24h else None
            liquidity = float(snapshot.liquidity) if snapshot.liquidity else None
        else:
            price = float(market.initial_price) if market.initial_price else 0.5
            best_bid = None
            best_ask = None
            spread = float(market.initial_spread) if market.initial_spread else None
            volume_24h = None
            liquidity = float(market.initial_liquidity) if market.initial_liquidity else None

        return MarketData(
            id=market.id,
            condition_id=market.condition_id,
            question=market.question,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            price=price,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            hours_to_close=hours_to_close,
            end_date=market.end_date,
            volume_24h=volume_24h,
            liquidity=liquidity,
            category=market.category,
            event_id=market.event_id,
            event_title=market.event_title,
            raw={
                "tier": market.tier,
                "snapshot_count": market.snapshot_count,
            },
        )

    def get_markets_by_event(
        self,
        event_id: str,
        db: Optional[Session] = None,
    ) -> list[MarketData]:
        """
        Get all markets for a specific event.

        Useful for term structure strategy.

        Args:
            event_id: Event ID
            db: Optional database session

        Returns:
            List of MarketData objects for the event
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            markets = db.query(Market).filter(
                Market.event_id == event_id,
                Market.active == True,
                Market.resolved == False,
            ).all()

            now = datetime.now(timezone.utc)
            result = []

            for market in markets:
                snapshot = self._get_latest_snapshot(db, market.id)
                market_data = self._build_market_data(market, snapshot, now)
                result.append(market_data)

            return result

        finally:
            if close_db:
                db.close()

    def get_price_history(
        self,
        market_id: int,
        hours: int = 24,
        db: Optional[Session] = None,
    ) -> list[float]:
        """
        Get price history for a market.

        Useful for mean reversion strategy.

        Args:
            market_id: Market ID
            hours: Hours of history to fetch
            db: Optional database session

        Returns:
            List of prices (oldest first)
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

            snapshots = db.query(Snapshot).filter(
                Snapshot.market_id == market_id,
                Snapshot.timestamp >= cutoff,
            ).order_by(
                Snapshot.timestamp.asc()
            ).all()

            return [float(s.price) for s in snapshots]

        finally:
            if close_db:
                db.close()

    def enrich_with_history(
        self,
        markets: list[MarketData],
        hours: int = 24,
        db: Optional[Session] = None,
    ) -> list[MarketData]:
        """
        Add price history to market data.

        Args:
            markets: List of MarketData to enrich
            hours: Hours of history to add
            db: Optional database session

        Returns:
            Same list with price_history populated
        """
        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            for market in markets:
                market.price_history = self.get_price_history(
                    market.id, hours, db
                )
            return markets
        finally:
            if close_db:
                db.close()
