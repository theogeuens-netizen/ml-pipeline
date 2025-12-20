"""
Market categorization tasks.

Primary approach: Rule-based categorization (instant, free)
Fallback: Claude Max via /categorize slash command
"""

import traceback
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task
from sqlalchemy import select
import structlog

from src.db.database import get_session
from src.db.models import Market, TaskRun

logger = structlog.get_logger()


@shared_task(name="src.tasks.categorization.categorize_with_rules")
def categorize_with_rules(limit: int = 1000) -> dict:
    """
    Categorize uncategorized markets using rule-based matching.

    This is the primary categorization method - instant and free.
    Markets that don't match any rule are left for Claude via /categorize.

    Runs hourly via Celery beat.

    Args:
        limit: Maximum number of markets to process per run

    Returns:
        Dictionary with categorization stats
    """
    task_run_id = _start_task_run("categorize_with_rules")

    try:
        # Get uncategorized active markets
        with get_session() as session:
            markets = session.execute(
                select(Market).where(
                    Market.active == True,
                    Market.category_l1 == None,
                )
                .limit(limit)
            ).scalars().all()

            if not markets:
                _complete_task_run(task_run_id, "success", 0, 0)
                logger.info("No uncategorized markets found")
                return {"checked": 0, "matched": 0, "remaining": 0}

            market_data = [
                {
                    "id": m.id,
                    "question": m.question,
                    "description": m.description or "",
                    "event_title": m.event_title or "",
                }
                for m in markets
            ]

        # Run rule categorizer
        from src.services.rule_categorizer import get_rule_categorizer

        categorizer = get_rule_categorizer()
        matched, unmatched = categorizer.categorize_batch(market_data)

        # Save results
        saved = 0
        if matched:
            saved = categorizer.save_results(matched)

        _complete_task_run(task_run_id, "success", len(market_data), saved)

        result = {
            "checked": len(market_data),
            "matched": len(matched),
            "remaining": len(unmatched),
        }

        logger.info(
            "Rule categorization complete",
            **result,
        )

        return result

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Rule categorization failed", error=str(e))
        raise


# === Task Run Tracking ===


def _start_task_run(task_name: str) -> int:
    """Create a task run record and return its ID."""
    with get_session() as session:
        run = TaskRun(
            task_name=task_name,
            task_id="",
            tier=None,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        session.add(run)
        session.commit()
        return run.id


def _complete_task_run(run_id: int, status: str, markets: int, rows: int) -> None:
    """Mark a task run as complete."""
    with get_session() as session:
        run = session.get(TaskRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
            run.status = status
            run.markets_processed = markets
            run.rows_inserted = rows
            session.commit()


def _fail_task_run(run_id: int, error: Exception) -> None:
    """Mark a task run as failed."""
    with get_session() as session:
        run = session.get(TaskRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
            run.status = "failed"
            run.error_message = str(error)
            run.error_traceback = traceback.format_exc()
            session.commit()
