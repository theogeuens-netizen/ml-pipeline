"""
Categorization monitoring endpoints (Codex/LLM runs).

Exposes run history and aggregate metrics for frontend dashboards.
Tables are created lazily (idempotent) to avoid impacting other systems.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import CategorizationRule
from src.services.categorization_tables import ensure_tables_sqlalchemy

router = APIRouter()


def _ensure_tables(db: Session) -> None:
    """Ensure tracking tables exist before querying."""
    ensure_tables_sqlalchemy(db)


@router.get("/categorization/runs")
async def list_categorization_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    """List recent categorization runs with token usage and counts."""
    _ensure_tables(db)

    where = ""
    params = {"limit": limit, "offset": offset}
    if status:
        where = "WHERE status = :status"
        params["status"] = status

    rows = db.execute(
        text(
            f"""
            SELECT run_id, started_at, completed_at, model, batch_size,
                   markets_fetched, markets_sent, markets_saved, quarantined,
                   retry_count, status, prompt_tokens, completion_tokens, total_tokens, error
            FROM categorization_runs
            {where}
            ORDER BY started_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "run_id": str(r.run_id),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "model": r.model,
                "batch_size": r.batch_size,
                "markets_fetched": r.markets_fetched,
                "markets_sent": r.markets_sent,
                "markets_saved": r.markets_saved,
                "quarantined": r.quarantined,
                "retry_count": r.retry_count,
                "status": r.status,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "error": r.error,
            }
        )

    return {"items": items, "limit": limit, "offset": offset}


@router.get("/categorization/metrics")
async def categorization_metrics(db: Session = Depends(get_db)):
    """Aggregate metrics for dashboards (last run, last success, 24h stats)."""
    _ensure_tables(db)
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    last_run = db.execute(
        text(
            """
            SELECT run_id, started_at, completed_at, status, markets_saved, total_tokens
            FROM categorization_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
    ).fetchone()

    last_success = db.execute(
        text(
            """
            SELECT run_id, started_at, completed_at, markets_saved, total_tokens
            FROM categorization_runs
            WHERE status = 'success'
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
    ).fetchone()

    counts_24h = db.execute(
        text(
            """
            SELECT
                COUNT(*) AS runs_24h,
                COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success_24h,
                COALESCE(SUM(markets_saved), 0) AS markets_saved_24h,
                COALESCE(SUM(total_tokens), 0) AS tokens_24h
            FROM categorization_runs
            WHERE started_at >= :since
            """
        ),
        {"since": since},
    ).fetchone()

    quarantine_24h = db.execute(
        text(
            """
            SELECT COUNT(*) AS quarantined
            FROM categorization_quarantine
            WHERE created_at >= :since
            """
        ),
        {"since": since},
    ).scalar()

    def _serialize_run(row):
        if not row:
            return None
        return {
            "run_id": str(row.run_id),
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "markets_saved": row.markets_saved,
            "total_tokens": row.total_tokens,
            "status": getattr(row, "status", None),
        }

    return {
        "timestamp": now.isoformat(),
        "last_run": _serialize_run(last_run),
        "last_success": _serialize_run(last_success),
        "runs_24h": counts_24h.runs_24h if counts_24h else 0,
        "success_24h": counts_24h.success_24h if counts_24h else 0,
        "markets_saved_24h": counts_24h.markets_saved_24h if counts_24h else 0,
        "tokens_24h": counts_24h.tokens_24h if counts_24h else 0,
        "quarantined_24h": quarantine_24h or 0,
    }


@router.get("/categorization/rules")
async def categorization_rules(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List rule-based categorization stats (accuracy, match counts)."""
    rules = (
        db.query(CategorizationRule)
        .order_by(CategorizationRule.times_matched.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for r in rules:
        accuracy = (r.times_correct / r.times_validated) if r.times_validated else None
        items.append(
            {
                "id": r.id,
                "name": r.name,
                "l1": r.l1,
                "l2": r.l2,
                "times_matched": r.times_matched,
                "times_validated": r.times_validated,
                "times_correct": r.times_correct,
                "accuracy": accuracy,
                "enabled": r.enabled,
            }
        )

    return {"items": items, "limit": limit, "offset": offset}
