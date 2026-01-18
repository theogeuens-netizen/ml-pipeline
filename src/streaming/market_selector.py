"""
Market selection for streaming executor.

Queries database for CRYPTO markets <4h to expiry.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .config import StreamingConfig
from .state import MarketInfo

logger = logging.getLogger(__name__)


def get_streaming_markets(
    db: Session,
    config: StreamingConfig,
) -> list[MarketInfo]:
    """
    Query markets eligible for streaming execution.

    Selection criteria:
    - category_l1 in config.categories (default: CRYPTO)
    - hours_to_close < config.max_hours_to_close (default: 4h)
    - hours_to_close > config.min_minutes_to_close / 60 (default: 2 min)
    - active = true
    - resolved = false
    - has both yes_token_id and no_token_id

    Args:
        db: Database session
        config: Streaming configuration

    Returns:
        List of MarketInfo for eligible markets
    """
    from src.db.models import Market

    now = datetime.now(timezone.utc)

    # Time bounds
    max_end_date = now + timedelta(hours=config.max_hours_to_close)
    min_end_date = now + timedelta(minutes=config.min_minutes_to_close)

    try:
        # Query eligible markets
        query = db.query(Market).filter(
            Market.category_l1.in_(config.categories),
            Market.active == True,
            Market.resolved == False,
            Market.yes_token_id.isnot(None),
            Market.no_token_id.isnot(None),
            Market.end_date.isnot(None),
            Market.end_date <= max_end_date,
            Market.end_date > min_end_date,
        )

        markets = query.all()

        result = []
        for m in markets:
            # Calculate hours to close
            if m.end_date:
                # Handle timezone
                end_date = m.end_date
                if end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)

                hours_to_close = (end_date - now).total_seconds() / 3600

                if hours_to_close > 0:
                    result.append(
                        MarketInfo(
                            id=m.id,
                            condition_id=m.condition_id or "",
                            yes_token_id=m.yes_token_id,
                            no_token_id=m.no_token_id,
                            question=m.question[:100] if m.question else "",
                            hours_to_close=hours_to_close,
                            category_l1=m.category_l1 or "",
                        )
                    )

        logger.info(
            f"Selected {len(result)} markets for streaming "
            f"(categories={config.categories}, <{config.max_hours_to_close}h)"
        )

        return result

    except Exception as e:
        logger.error(f"Failed to query streaming markets: {e}")
        return []


def get_market_by_token(
    db: Session,
    token_id: str,
) -> Optional[MarketInfo]:
    """
    Get market info for a specific token ID.

    Args:
        db: Database session
        token_id: Token ID (YES or NO)

    Returns:
        MarketInfo if found, None otherwise
    """
    from src.db.models import Market

    try:
        market = (
            db.query(Market)
            .filter(
                (Market.yes_token_id == token_id) | (Market.no_token_id == token_id)
            )
            .first()
        )

        if not market:
            return None

        now = datetime.now(timezone.utc)
        hours_to_close = 0.0

        if market.end_date:
            end_date = market.end_date
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            hours_to_close = max(0, (end_date - now).total_seconds() / 3600)

        return MarketInfo(
            id=market.id,
            condition_id=market.condition_id or "",
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            question=market.question[:100] if market.question else "",
            hours_to_close=hours_to_close,
            category_l1=market.category_l1 or "",
        )

    except Exception as e:
        logger.error(f"Failed to get market for token {token_id}: {e}")
        return None
