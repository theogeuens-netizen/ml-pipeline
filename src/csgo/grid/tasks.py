"""
GRID Integration Celery Tasks.

Tasks for:
- Matching GRID series to Polymarket markets
- Polling GRID API for state changes
- Filling delayed prices for events
"""

import logging
from datetime import datetime, timezone

from celery import shared_task

from src.config.settings import settings

logger = logging.getLogger(__name__)

# Retryable errors for tasks
RETRYABLE_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
)


@shared_task(
    bind=True,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    time_limit=120,
)
def match_grid_series(self):
    """
    Match GRID series to Polymarket CSGO markets.

    Runs periodically to link new markets with GRID data.
    """
    from src.csgo.grid.matcher import GRIDMatcher

    if not settings.grid_api_key:
        logger.warning("GRID API key not configured, skipping match task")
        return {"status": "skipped", "reason": "no_api_key"}

    try:
        matcher = GRIDMatcher(api_key=settings.grid_api_key)
        matches = matcher.match_unmatched_markets(
            hours_before=24,
            hours_after=72,
            time_window_hours=48,
        )
        saved = matcher.save_matches(matches)

        logger.info(f"GRID matching complete: {saved} new matches saved")
        return {
            "status": "success",
            "matches_found": len(matches),
            "matches_saved": saved,
        }

    except Exception as e:
        logger.error(f"GRID matching failed: {e}")
        raise


@shared_task(
    bind=True,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    time_limit=60,
)
def poll_grid_state(self):
    """
    Poll GRID API for live match state changes.

    Should run frequently (every 3-5 seconds) during live matches.
    Records events with prices for analysis.
    """
    from src.csgo.grid.poller import GRIDPoller

    if not settings.grid_api_key:
        logger.debug("GRID API key not configured, skipping poll")
        return {"status": "skipped", "reason": "no_api_key"}

    try:
        poller = GRIDPoller(api_key=settings.grid_api_key)
        result = poller.poll_once()

        if result["events"] > 0:
            logger.info(
                f"GRID poll: {result['events']} events detected "
                f"({result['matches']} series polled)"
            )

        return {
            "status": "success",
            "series_polled": result["matches"],
            "events_detected": result["events"],
            "errors": result["errors"],
        }

    except Exception as e:
        logger.error(f"GRID poll failed: {e}")
        raise


@shared_task(
    bind=True,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    time_limit=120,
)
def fill_grid_prices(self):
    """
    Fill delayed prices (30s, 1m, 5m) for GRID events.

    Runs periodically to update price_after_* fields.
    """
    from src.csgo.grid.price_filler import fill_grid_event_prices

    try:
        result = fill_grid_event_prices()

        total_filled = (
            result["filled_30sec"] +
            result["filled_1min"] +
            result["filled_5min"]
        )

        if total_filled > 0:
            logger.info(
                f"GRID price fill: {total_filled} prices filled "
                f"(30s={result['filled_30sec']}, 1m={result['filled_1min']}, "
                f"5m={result['filled_5min']})"
            )

        return {
            "status": "success",
            "events_processed": result["events_processed"],
            "filled_30sec": result["filled_30sec"],
            "filled_1min": result["filled_1min"],
            "filled_5min": result["filled_5min"],
            "errors": result["errors"],
        }

    except Exception as e:
        logger.error(f"GRID price fill failed: {e}")
        raise


@shared_task(
    bind=True,
    time_limit=300,
)
def cleanup_grid_state(self, days_old: int = 7):
    """
    Clean up old GRID poller state records.

    Removes state for series that finished more than `days_old` days ago.
    """
    from datetime import timedelta

    from sqlalchemy import delete

    from src.db.database import get_session
    from src.db.models import GRIDPollerState

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)

    try:
        with get_session() as session:
            result = session.execute(
                delete(GRIDPollerState).where(
                    GRIDPollerState.last_poll_at < cutoff
                )
            )
            deleted = result.rowcount
            session.commit()

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old GRID poller state records")

        return {"status": "success", "deleted": deleted}

    except Exception as e:
        logger.error(f"GRID state cleanup failed: {e}")
        raise
