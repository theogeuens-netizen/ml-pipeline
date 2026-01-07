"""
Task monitoring endpoints.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import TaskRun

router = APIRouter()


@router.get("/tasks/status")
async def get_task_status(db: Session = Depends(get_db)):
    """
    Get status of all task types including last run and success rate.
    """
    # Get distinct task names
    task_names = db.execute(
        select(TaskRun.task_name).distinct()
    ).scalars().all()

    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(days=1)

    tasks = {}
    for task_name in task_names:
        # Last run
        last_run = db.execute(
            select(TaskRun)
            .where(TaskRun.task_name == task_name)
            .order_by(desc(TaskRun.started_at))
            .limit(1)
        ).scalar_one_or_none()

        # Runs in last 24h
        runs_24h = db.execute(
            select(func.count(TaskRun.id)).where(
                TaskRun.task_name == task_name,
                TaskRun.started_at >= one_day_ago,
            )
        ).scalar()

        # Successful runs in last 24h
        success_24h = db.execute(
            select(func.count(TaskRun.id)).where(
                TaskRun.task_name == task_name,
                TaskRun.started_at >= one_day_ago,
                TaskRun.status == "success",
            )
        ).scalar()

        # Average duration in last 24h
        avg_duration = db.execute(
            select(func.avg(TaskRun.duration_ms)).where(
                TaskRun.task_name == task_name,
                TaskRun.started_at >= one_day_ago,
                TaskRun.duration_ms.isnot(None),
            )
        ).scalar()

        tasks[task_name] = {
            "last_run": last_run.started_at.isoformat() if last_run else None,
            "last_status": last_run.status if last_run else None,
            "runs_24h": runs_24h,
            "success_24h": success_24h,
            "success_rate_24h": (success_24h / runs_24h * 100) if runs_24h > 0 else 0,
            "avg_duration_ms": int(avg_duration) if avg_duration else None,
        }

    return {
        "timestamp": now.isoformat(),
        "tasks": tasks,
    }


@router.get("/tasks/runs")
async def get_task_runs(
    task_name: Optional[str] = Query(None, description="Filter by task name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    tier: Optional[int] = Query(None, ge=0, le=4, description="Filter by tier"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get task run history.
    """
    query = select(TaskRun)

    if task_name:
        query = query.where(TaskRun.task_name == task_name)
    if status:
        query = query.where(TaskRun.status == status)
    if tier is not None:
        query = query.where(TaskRun.tier == tier)

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Get runs
    query = query.order_by(desc(TaskRun.started_at)).offset(offset).limit(limit)
    runs = db.execute(query).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r.id,
                "task_name": r.task_name,
                "task_id": r.task_id,
                "tier": r.tier,
                "started_at": r.started_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "duration_ms": r.duration_ms,
                "status": r.status,
                "markets_processed": r.markets_processed,
                "rows_inserted": r.rows_inserted,
                "error_message": r.error_message,
            }
            for r in runs
        ],
    }


@router.get("/tasks/errors")
async def get_recent_errors(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Get recent task errors.
    """
    query = (
        select(TaskRun)
        .where(TaskRun.status == "failed")
        .order_by(desc(TaskRun.started_at))
        .limit(limit)
    )
    errors = db.execute(query).scalars().all()

    return {
        "count": len(errors),
        "items": [
            {
                "id": r.id,
                "task_name": r.task_name,
                "tier": r.tier,
                "started_at": r.started_at.isoformat(),
                "error_message": r.error_message,
                "error_traceback": r.error_traceback,
            }
            for r in errors
        ],
    }
