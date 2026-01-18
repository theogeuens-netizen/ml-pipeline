"""
Market categorization tasks.

Primary approach: Rule-based categorization (instant, free)
Secondary: Claude Haiku for uncategorized markets
Validation: Claude verifies rule accuracy periodically

Tasks:
- categorize_with_rules: Fast rule matching (hourly)
- categorize_with_claude: Claude Haiku for rule misses (hourly, 10 min after rules)
- validate_rule_accuracy: Verify rule quality (every 6 hours)
- suggest_rule_improvements: Analyze patterns for new rules (weekly)
"""

import json
import random
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from celery import shared_task
from sqlalchemy import select, update, text, func
import structlog

from src.db.database import get_session
from src.db.models import Market, TaskRun, CategorizationRule

logger = structlog.get_logger()

# Batch sizes for Claude categorization (2 batches of 100 = 200 per hour)
CLAUDE_BATCH_SIZE = 100
VALIDATION_SAMPLE_SIZE = 50


@shared_task(name="src.tasks.categorization.categorize_with_rules")
def categorize_with_rules(limit: int = 2000) -> dict:
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


@shared_task(
    name="src.tasks.categorization.categorize_with_claude",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def categorize_with_claude(self, batch_size: int = CLAUDE_BATCH_SIZE) -> dict:
    """
    Categorize uncategorized markets using Claude Haiku.

    Runs after rule-based categorization to handle remaining markets.
    Uses small batches to control costs and token limits.

    Args:
        batch_size: Number of markets per Claude call

    Returns:
        Dictionary with categorization stats
    """
    task_run_id = _start_task_run("categorize_with_claude")

    try:
        # Get uncategorized active markets
        with get_session() as session:
            markets = session.execute(
                select(Market).where(
                    Market.active == True,
                    Market.category_l1 == None,
                )
                .order_by(Market.id.desc())  # Newest first
                .limit(batch_size)
            ).scalars().all()

            if not markets:
                _complete_task_run(task_run_id, "success", 0, 0)
                logger.info("No uncategorized markets for Claude")
                return {"checked": 0, "categorized": 0, "errors": 0}

            market_data = [
                {
                    "id": m.id,
                    "question": m.question,
                    "description": m.description or "",
                }
                for m in markets
            ]

        # Call Claude
        from src.services.claude_categorizer import get_claude_categorizer

        categorizer = get_claude_categorizer(model="haiku")
        result = categorizer.categorize_batch(market_data)

        # Save results
        saved = 0
        now = datetime.now(timezone.utc)

        with get_session() as session:
            for cat in result.results:
                session.execute(
                    update(Market)
                    .where(Market.id == cat.market_id)
                    .values(
                        category_l1=cat.l1,
                        category_l2=cat.l2,
                        category_l3=cat.l3,
                        categorization_method="claude",
                        categorization_confidence=cat.confidence,
                        categorized_at=now,
                    )
                )
                saved += 1
            session.commit()

        _complete_task_run(task_run_id, "success", len(market_data), saved)

        stats = {
            "checked": result.total,
            "categorized": saved,
            "valid": result.valid_count,
            "invalid": result.invalid_count,
            "errors": len(result.errors),
        }

        logger.info("Claude categorization complete", **stats)
        return stats

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Claude categorization failed", error=str(e))
        raise self.retry(exc=e)


@shared_task(name="src.tasks.categorization.validate_rule_accuracy")
def validate_rule_accuracy(sample_per_rule: int = VALIDATION_SAMPLE_SIZE) -> dict:
    """
    Validate rule accuracy by checking samples with Claude.

    For each active rule:
    1. Sample N markets categorized by that rule
    2. Ask Claude to verify the categorization
    3. Update rule accuracy metrics
    4. Store validation results

    Args:
        sample_per_rule: Number of markets to validate per rule

    Returns:
        Dictionary with validation results per rule
    """
    task_run_id = _start_task_run("validate_rule_accuracy")

    try:
        from src.services.claude_categorizer import get_claude_categorizer

        categorizer = get_claude_categorizer(model="haiku")  # Haiku for bulk validation
        results = {}

        with get_session() as session:
            # Get all enabled rules
            rules = session.execute(
                select(CategorizationRule).where(CategorizationRule.enabled == True)
            ).scalars().all()

            for rule in rules:
                # Sample markets categorized by this rule
                markets = session.execute(
                    select(Market)
                    .where(
                        Market.matched_rule_id == rule.id,
                        Market.active == True,
                    )
                    .order_by(func.random())
                    .limit(sample_per_rule)
                ).scalars().all()

                if not markets:
                    continue

                correct = 0
                total = 0

                for market in markets:
                    # Validate with Claude
                    validation = categorizer.validate_categorization(
                        market_id=market.id,
                        question=market.question,
                        description=market.description or "",
                        current_l1=market.category_l1,
                        current_l2=market.category_l2,
                        current_l3=market.category_l3,
                    )

                    if validation.get("error"):
                        continue

                    total += 1
                    is_correct = validation.get("is_correct", False)

                    if is_correct:
                        correct += 1

                    # Store validation result
                    _store_validation(
                        session,
                        rule_id=rule.id,
                        market_id=market.id,
                        rule_l1=market.category_l1,
                        rule_l2=market.category_l2,
                        rule_l3=market.category_l3,
                        claude_l1=validation.get("correct_l1", market.category_l1),
                        claude_l2=validation.get("correct_l2", market.category_l2),
                        claude_l3=validation.get("correct_l3", market.category_l3),
                        is_match=is_correct,
                        mismatch_reason=validation.get("reasoning") if not is_correct else None,
                    )

                # Update rule accuracy
                if total > 0:
                    accuracy = correct / total
                    session.execute(
                        update(CategorizationRule)
                        .where(CategorizationRule.id == rule.id)
                        .values(
                            times_validated=CategorizationRule.times_validated + total,
                            times_correct=CategorizationRule.times_correct + correct,
                        )
                    )
                    results[rule.name] = {
                        "sampled": total,
                        "correct": correct,
                        "accuracy": round(accuracy * 100, 1),
                    }

            session.commit()

        _complete_task_run(task_run_id, "success", sum(r["sampled"] for r in results.values()), 0)

        logger.info("Rule validation complete", rules_validated=len(results), results=results)
        return results

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Rule validation failed", error=str(e))
        raise


@shared_task(name="src.tasks.categorization.suggest_rule_improvements")
def suggest_rule_improvements(min_occurrences: int = 10) -> dict:
    """
    Analyze Claude categorizations to suggest new rules or improvements.

    1. Group Claude-categorized markets by L1/L2
    2. Extract common keywords from questions
    3. If pattern appears 10+ times, suggest a new rule
    4. Store suggestions for human review

    Args:
        min_occurrences: Minimum occurrences to suggest a rule

    Returns:
        Dictionary with suggestions
    """
    task_run_id = _start_task_run("suggest_rule_improvements")

    try:
        suggestions = []

        with get_session() as session:
            # Find common L1/L2 patterns in Claude-categorized markets
            pattern_query = text("""
                SELECT
                    category_l1,
                    category_l2,
                    COUNT(*) as count,
                    array_agg(DISTINCT id) as market_ids
                FROM markets
                WHERE categorization_method = 'claude'
                  AND category_l1 IS NOT NULL
                  AND active = true
                GROUP BY category_l1, category_l2
                HAVING COUNT(*) >= :min_count
                ORDER BY count DESC
            """)

            patterns = session.execute(pattern_query, {"min_count": min_occurrences}).fetchall()

            for pattern in patterns:
                l1, l2, count, market_ids = pattern

                # Check if rule already exists
                existing = session.execute(
                    select(CategorizationRule).where(
                        CategorizationRule.l1 == l1,
                        CategorizationRule.l2 == l2,
                        CategorizationRule.enabled == True,
                    )
                ).scalar_one_or_none()

                if existing:
                    continue  # Rule exists, skip

                # Get sample questions for keyword extraction
                sample_markets = session.execute(
                    select(Market.id, Market.question)
                    .where(Market.id.in_(market_ids[:20]))
                ).fetchall()

                # Extract common words (simple keyword extraction)
                keywords = _extract_common_keywords([m.question for m in sample_markets])

                if keywords:
                    suggestion = {
                        "l1": l1,
                        "l2": l2,
                        "occurrence_count": count,
                        "suggested_keywords": keywords[:10],  # Top 10 keywords
                        "example_markets": [
                            {"id": m.id, "question": m.question}
                            for m in sample_markets[:5]
                        ],
                    }
                    suggestions.append(suggestion)

                    # Store in database
                    session.execute(text("""
                        INSERT INTO rule_suggestions
                        (suggested_l1, suggested_l2, suggested_keywords, example_markets, occurrence_count)
                        VALUES (:l1, :l2, :keywords, :examples, :count)
                    """), {
                        "l1": l1,
                        "l2": l2,
                        "keywords": json.dumps(keywords[:10]),
                        "examples": json.dumps([{"id": m.id, "question": m.question} for m in sample_markets[:5]]),
                        "count": count,
                    })

            session.commit()

        _complete_task_run(task_run_id, "success", len(suggestions), len(suggestions))

        logger.info("Rule suggestions complete", suggestions=len(suggestions))
        return {"suggestions": suggestions, "count": len(suggestions)}

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Rule suggestion failed", error=str(e))
        raise


def _extract_common_keywords(questions: list[str]) -> list[str]:
    """Extract common keywords from a list of questions."""
    import re
    from collections import Counter

    # Common words to ignore
    stopwords = {
        "the", "a", "an", "is", "are", "will", "be", "to", "of", "and", "or",
        "in", "on", "at", "by", "for", "with", "vs", "vs.", "versus",
        "win", "winner", "match", "game", "market", "price", "above", "below",
    }

    word_counts = Counter()

    for q in questions:
        # Extract words
        words = re.findall(r'\b[a-zA-Z]{3,}\b', q.lower())
        # Filter stopwords
        words = [w for w in words if w not in stopwords]
        word_counts.update(words)

    # Return words that appear in multiple questions
    min_freq = max(2, len(questions) // 3)
    common = [word for word, count in word_counts.most_common(20) if count >= min_freq]

    return common


def _store_validation(
    session,
    rule_id: int,
    market_id: int,
    rule_l1: str,
    rule_l2: str,
    rule_l3: str,
    claude_l1: str,
    claude_l2: str,
    claude_l3: str,
    is_match: bool,
    mismatch_reason: Optional[str] = None,
) -> None:
    """Store a validation result."""
    mismatch_type = None
    if not is_match:
        if rule_l1 != claude_l1:
            mismatch_type = "l1"
        elif rule_l2 != claude_l2:
            mismatch_type = "l2"
        else:
            mismatch_type = "l3"

    session.execute(text("""
        INSERT INTO rule_validations
        (rule_id, market_id, rule_l1, rule_l2, rule_l3,
         claude_l1, claude_l2, claude_l3, is_match, mismatch_type, mismatch_reason)
        VALUES (:rule_id, :market_id, :rule_l1, :rule_l2, :rule_l3,
                :claude_l1, :claude_l2, :claude_l3, :is_match, :mismatch_type, :mismatch_reason)
    """), {
        "rule_id": rule_id,
        "market_id": market_id,
        "rule_l1": rule_l1,
        "rule_l2": rule_l2,
        "rule_l3": rule_l3,
        "claude_l1": claude_l1,
        "claude_l2": claude_l2,
        "claude_l3": claude_l3,
        "is_match": is_match,
        "mismatch_type": mismatch_type,
        "mismatch_reason": mismatch_reason,
    })


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
