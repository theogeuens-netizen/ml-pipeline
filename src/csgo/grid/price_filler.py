"""
GRID Event Price Filler.

Background task to fill in delayed prices (30s, 1m, 5m) for
GRID events after the appropriate time has passed.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from src.db.database import get_session
from src.db.models import CSGOGridEvent, Market
from src.fetchers.clob import SyncCLOBClient

logger = logging.getLogger(__name__)

# Time thresholds for filling prices
FILL_THRESHOLDS = {
    "30sec": timedelta(seconds=30),
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
}

# Grace period - wait a bit extra to ensure price has settled
GRACE_PERIOD = timedelta(seconds=5)


class PriceFiller:
    """
    Fills delayed prices for GRID events.

    Usage:
        filler = PriceFiller()
        filled = filler.fill_pending()
    """

    def __init__(self):
        self.clob_client = SyncCLOBClient()
        # Cache market token IDs
        self._token_cache: dict[int, str] = {}

    def get_token_id(self, session: Session, market_id: int) -> Optional[str]:
        """Get YES token ID for a market (cached)."""
        if market_id in self._token_cache:
            return self._token_cache[market_id]

        market = session.execute(
            select(Market).where(Market.id == market_id)
        ).scalar_one_or_none()

        if market and market.yes_token_id:
            self._token_cache[market_id] = market.yes_token_id
            return market.yes_token_id

        return None

    def get_price(self, token_id: str) -> Optional[Decimal]:
        """Get current mid price from CLOB."""
        try:
            price_data = self.clob_client.get_price(token_id)
            return Decimal(str(price_data.get("mid", 0)))
        except Exception as e:
            logger.warning(f"Failed to get price for {token_id}: {e}")
            return None

    def get_events_to_fill(self, session: Session) -> list[CSGOGridEvent]:
        """
        Get events that need price filling.

        Returns events where at least one delayed price field is NULL
        and enough time has passed.
        """
        now = datetime.now(timezone.utc)

        # Events that need 30sec fill
        cutoff_30s = now - FILL_THRESHOLDS["30sec"] - GRACE_PERIOD

        stmt = (
            select(CSGOGridEvent)
            .where(
                or_(
                    # Need 30sec fill
                    and_(
                        CSGOGridEvent.price_after_30sec.is_(None),
                        CSGOGridEvent.detected_at <= cutoff_30s,
                    ),
                    # Need 1min fill
                    and_(
                        CSGOGridEvent.price_after_1min.is_(None),
                        CSGOGridEvent.detected_at <= now - FILL_THRESHOLDS["1min"] - GRACE_PERIOD,
                    ),
                    # Need 5min fill
                    and_(
                        CSGOGridEvent.price_after_5min.is_(None),
                        CSGOGridEvent.detected_at <= now - FILL_THRESHOLDS["5min"] - GRACE_PERIOD,
                    ),
                )
            )
            .order_by(CSGOGridEvent.detected_at)
            .limit(100)  # Process in batches
        )

        result = session.execute(stmt)
        return list(result.scalars().all())

    def fill_event(self, session: Session, event: CSGOGridEvent) -> dict:
        """
        Fill pending price fields for an event.

        Returns dict with fields that were filled.
        """
        now = datetime.now(timezone.utc)
        time_elapsed = now - event.detected_at
        filled = {}

        # Get token ID
        token_id = self.get_token_id(session, event.market_id)
        if not token_id:
            logger.warning(f"No token ID for market {event.market_id}")
            return filled

        # Get current price
        current_price = self.get_price(token_id)
        if current_price is None:
            return filled

        initial_price = event.price_at_detection

        # Fill 30sec if needed and enough time passed
        if (
            event.price_after_30sec is None
            and time_elapsed >= FILL_THRESHOLDS["30sec"]
        ):
            event.price_after_30sec = current_price
            if initial_price:
                event.price_move_30sec = current_price - initial_price
            filled["30sec"] = current_price

        # Fill 1min if needed
        if (
            event.price_after_1min is None
            and time_elapsed >= FILL_THRESHOLDS["1min"]
        ):
            event.price_after_1min = current_price
            if initial_price:
                event.price_move_1min = current_price - initial_price
            filled["1min"] = current_price

        # Fill 5min if needed
        if (
            event.price_after_5min is None
            and time_elapsed >= FILL_THRESHOLDS["5min"]
        ):
            event.price_after_5min = current_price
            if initial_price:
                event.price_move_5min = current_price - initial_price

            # Calculate if move was in expected direction
            # YES winner should see price go up, NO winner should see price go down
            if initial_price and event.price_move_5min:
                price_went_up = event.price_move_5min > 0
                event.move_direction_correct = (
                    (event.winner == "YES" and price_went_up) or
                    (event.winner == "NO" and not price_went_up)
                )

            filled["5min"] = current_price

        return filled

    def fill_pending(self) -> dict:
        """
        Fill all pending delayed prices.

        Returns:
            Summary dict with counts
        """
        filled_30s = 0
        filled_1m = 0
        filled_5m = 0
        errors = 0

        with get_session() as session:
            events = self.get_events_to_fill(session)

            if not events:
                return {
                    "events_processed": 0,
                    "filled_30sec": 0,
                    "filled_1min": 0,
                    "filled_5min": 0,
                    "errors": 0,
                }

            for event in events:
                try:
                    filled = self.fill_event(session, event)
                    if "30sec" in filled:
                        filled_30s += 1
                    if "1min" in filled:
                        filled_1m += 1
                    if "5min" in filled:
                        filled_5m += 1
                except Exception as e:
                    logger.error(f"Error filling event {event.id}: {e}")
                    errors += 1

            session.commit()

        total_filled = filled_30s + filled_1m + filled_5m
        if total_filled > 0:
            logger.info(
                f"Filled prices: 30s={filled_30s}, 1m={filled_1m}, 5m={filled_5m}"
            )

        return {
            "events_processed": len(events),
            "filled_30sec": filled_30s,
            "filled_1min": filled_1m,
            "filled_5min": filled_5m,
            "errors": errors,
        }


def fill_grid_event_prices() -> dict:
    """
    Convenience function for Celery task.

    Returns:
        Summary of filling operation
    """
    filler = PriceFiller()
    return filler.fill_pending()
