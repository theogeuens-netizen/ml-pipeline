"""
News collection tasks.

Fetches news from external APIs and stores in database.
Currently supports:
- Marketaux (crypto-focused, 100 req/day free tier)

GDELT is accessed directly via BigQuery - no collection needed.
"""

import traceback
from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
import structlog

from src.db.database import get_session
from src.db.models import NewsItem, TaskRun
from src.fetchers.marketaux import get_client as get_marketaux_client

logger = structlog.get_logger()


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def fetch_marketaux_news(self) -> dict:
    """
    Fetch latest crypto news from Marketaux API.

    Runs every 15 minutes. Free tier allows 100 requests/day.
    96 requests/day at 15-min intervals leaves margin for errors.

    Returns:
        Dict with fetch statistics
    """
    task_name = "fetch_marketaux_news"
    started_at = datetime.now(timezone.utc)
    status = "success"
    error_message = None
    articles_fetched = 0
    articles_inserted = 0

    try:
        client = get_marketaux_client()

        if not client.enabled:
            logger.info("Marketaux disabled - skipping fetch")
            return {
                "status": "skipped",
                "reason": "API key not configured",
            }

        # Fetch latest news
        articles = client.fetch_news()
        articles_fetched = len(articles)

        if not articles:
            logger.info("No new articles from Marketaux")
            return {
                "status": "success",
                "fetched": 0,
                "inserted": 0,
            }

        # Insert into database (upsert to handle duplicates)
        with get_session() as session:
            for article in articles:
                # Use upsert to avoid duplicates based on source_id
                stmt = insert(NewsItem).values(
                    source=article["source"],
                    source_id=article["source_id"],
                    title=article["title"],
                    snippet=article["snippet"],
                    url=article["url"],
                    published_at=article["published_at"],
                    fetched_at=datetime.now(timezone.utc),
                    sentiment_score=article["sentiment_score"],
                    category=article["category"],
                    symbols=article["symbols"],
                    entities=article["entities"],
                    raw_response=article["raw_response"],
                ).on_conflict_do_nothing(
                    index_elements=["source_id"]
                )

                result = session.execute(stmt)
                if result.rowcount > 0:
                    articles_inserted += 1

            session.commit()

        logger.info(
            "Marketaux news fetch complete",
            fetched=articles_fetched,
            inserted=articles_inserted,
        )

        return {
            "status": "success",
            "fetched": articles_fetched,
            "inserted": articles_inserted,
        }

    except Exception as e:
        status = "failed"
        error_message = f"{type(e).__name__}: {str(e)}"
        logger.error(
            "Marketaux news fetch failed",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        raise

    finally:
        # Log task run
        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        try:
            with get_session() as session:
                task_run = TaskRun(
                    task_name=task_name,
                    task_id=self.request.id or "manual",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    duration_ms=duration_ms,
                    status=status,
                    error_message=error_message,
                    markets_processed=articles_fetched,  # Repurpose for articles
                    rows_inserted=articles_inserted,
                )
                session.add(task_run)
                session.commit()
        except Exception as log_error:
            logger.warning(f"Failed to log task run: {log_error}")
