"""
Alert tasks for Telegram notifications.

Includes:
- Daily summary of trading activity
- Error aggregation and reporting
"""

import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task

from src.alerts.telegram import alert_daily_summary
from src.db.database import get_session
from src.executor.models import (
    Position,
    PositionStatus,
    ExecutorTrade,
    Signal,
    PaperBalance,
)

logger = logging.getLogger(__name__)


@shared_task(name="src.tasks.alerts.send_daily_summary")
def send_daily_summary():
    """
    Send daily trading summary via Telegram.

    Calculates:
    - Current balance
    - Today's P&L
    - Open positions count
    - Today's trade count
    - Win rate (if positions closed today)

    Scheduled to run at 9:00 AM UTC daily.
    """
    logger.info("Generating daily summary...")

    try:
        with get_session() as db:
            # Get today's start
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            yesterday_start = today_start - timedelta(days=1)

            # Get balance
            balance_row = db.query(PaperBalance).first()
            if not balance_row:
                logger.warning("No paper balance found, skipping daily summary")
                return {"status": "skipped", "reason": "no_balance"}

            balance = float(balance_row.balance_usd)
            starting = float(balance_row.starting_balance_usd)

            # Get open positions count
            from sqlalchemy import select, func

            open_positions = db.execute(
                select(func.count(Position.id)).where(
                    Position.status == PositionStatus.OPEN.value
                )
            ).scalar() or 0

            # Get today's trades count
            trades_today = db.execute(
                select(func.count(ExecutorTrade.id)).where(
                    ExecutorTrade.timestamp >= today_start
                )
            ).scalar() or 0

            # Calculate today's P&L from positions closed today
            closed_today = db.execute(
                select(Position).where(
                    Position.status == PositionStatus.CLOSED.value,
                    Position.exit_time >= today_start,
                )
            ).scalars().all()

            pnl_today = sum(float(p.realized_pnl or 0) for p in closed_today)

            # Add unrealized P&L from open positions
            open_pnl = db.execute(
                select(func.sum(Position.unrealized_pnl)).where(
                    Position.status == PositionStatus.OPEN.value
                )
            ).scalar() or 0
            pnl_today += float(open_pnl)

            # Calculate win rate from closed positions (all time)
            all_closed = db.execute(
                select(Position).where(
                    Position.status == PositionStatus.CLOSED.value
                )
            ).scalars().all()

            winning = len([p for p in all_closed if float(p.realized_pnl or 0) > 0])
            losing = len([p for p in all_closed if float(p.realized_pnl or 0) < 0])
            win_rate = winning / (winning + losing) if (winning + losing) > 0 else None

            # Send alert
            success = alert_daily_summary(
                balance=balance,
                pnl_today=pnl_today,
                open_positions=open_positions,
                trades_today=trades_today,
                win_rate=win_rate,
            )

            result = {
                "status": "sent" if success else "failed",
                "balance": balance,
                "pnl_today": pnl_today,
                "open_positions": open_positions,
                "trades_today": trades_today,
                "win_rate": win_rate,
            }

            logger.info(f"Daily summary: {result}")
            return result

    except Exception as e:
        logger.error(f"Failed to send daily summary: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
