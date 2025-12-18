"""
Market discovery and tier management tasks.

These tasks run periodically to:
- Discover new markets from Gamma API
- Update tier assignments based on time to resolution
- Check for market resolutions
"""
import asyncio
import traceback
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task
from sqlalchemy import select, update
import structlog

from src.config.settings import settings
from src.db.database import get_session
from src.db.models import Market, TaskRun
from src.fetchers.gamma import GammaClient

logger = structlog.get_logger()


def calculate_tier(end_date: Optional[datetime]) -> int:
    """
    Calculate collection tier based on hours to resolution.

    Tier boundaries:
    - T0: > 48h
    - T1: 12-48h
    - T2: 4-12h
    - T3: 1-4h
    - T4: < 1h

    Args:
        end_date: Market resolution date

    Returns:
        Tier number (0-4)
    """
    if not end_date:
        return 0

    now = datetime.now(timezone.utc)
    hours_to_close = (end_date - now).total_seconds() / 3600

    if hours_to_close < 0:
        return 4  # Expired but not yet resolved
    elif hours_to_close < settings.tier_3_min_hours:  # < 1h
        return 4
    elif hours_to_close < settings.tier_2_min_hours:  # < 4h
        return 3
    elif hours_to_close < settings.tier_1_min_hours:  # < 12h
        return 2
    elif hours_to_close < settings.tier_0_min_hours:  # < 48h
        return 1
    else:
        return 0


@shared_task(name="src.tasks.discovery.discover_markets")
def discover_markets() -> dict:
    """
    Discover new markets and update existing ones.

    This task:
    1. Fetches all active markets from Gamma API
    2. Filters by volume threshold and orderbook availability
    3. Inserts new markets or updates existing ones
    4. Assigns initial tier based on time to resolution

    Returns:
        Dictionary with markets_processed and rows_inserted counts
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_discover_markets_async())
    finally:
        loop.close()


async def _discover_markets_async() -> dict:
    """Async implementation of market discovery."""
    task_run_id = _start_task_run("discover_markets")

    try:
        # Fetch all active markets from Gamma
        client = GammaClient()
        markets = await client.get_all_active_markets()
        await client.close()

        markets_processed = 0
        rows_inserted = 0
        rows_updated = 0

        with get_session() as session:
            for market_data in markets:
                # Parse market data
                yes_price, _ = GammaClient.parse_outcome_prices(market_data)
                yes_token, no_token = GammaClient.parse_token_ids(market_data)
                condition_id = market_data.get("conditionId")

                if not condition_id:
                    continue

                # Volume filter moved to T0→T1 transition (in update_market_tiers)
                # This allows markets to build volume before being filtered out
                volume_24h = float(market_data.get("volume24hr") or 0)

                # Filter: must have orderbook enabled
                if not market_data.get("enableOrderBook", False):
                    continue

                # Parse end date
                end_date = GammaClient.parse_datetime(market_data.get("endDate"))

                # Check lookahead window
                if end_date:
                    hours_to_close = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_to_close > settings.ml_lookahead_hours:
                        continue  # Too far out
                    if hours_to_close < -24:
                        continue  # Already resolved long ago

                # Calculate tier
                tier = calculate_tier(end_date)

                # Check if market exists
                existing = session.execute(
                    select(Market).where(Market.condition_id == condition_id)
                ).scalar_one_or_none()

                # Extract event info from nested events array
                events = market_data.get("events", [])
                event_data = events[0] if events else {}
                event_id = event_data.get("id")
                event_slug = event_data.get("slug")
                event_title = event_data.get("title")

                if existing:
                    # Update existing market
                    existing.tier = tier
                    existing.active = market_data.get("active", True)
                    existing.updated_at = datetime.now(timezone.utc)
                    # Update volatile fields
                    if yes_token and not existing.yes_token_id:
                        existing.yes_token_id = yes_token
                    if no_token and not existing.no_token_id:
                        existing.no_token_id = no_token
                    # Update event info if missing
                    if event_id and not existing.event_id:
                        existing.event_id = str(event_id)
                    if event_slug and not existing.event_slug:
                        existing.event_slug = event_slug
                    if event_title and not existing.event_title:
                        existing.event_title = event_title
                    rows_updated += 1
                else:
                    # Insert new market
                    new_market = Market(
                        condition_id=condition_id,
                        slug=market_data.get("slug", ""),
                        question=market_data.get("question", ""),
                        description=market_data.get("description"),
                        # Event grouping (from nested events array)
                        event_id=str(event_id) if event_id else None,
                        event_slug=event_slug,
                        event_title=event_title,
                        # Token IDs
                        yes_token_id=yes_token,
                        no_token_id=no_token,
                        # Timing
                        start_date=GammaClient.parse_datetime(market_data.get("startDate")),
                        end_date=end_date,
                        created_at=GammaClient.parse_datetime(market_data.get("createdAt")),
                        # Initial state
                        initial_price=yes_price,
                        initial_spread=market_data.get("spread"),
                        initial_volume=volume_24h,
                        initial_liquidity=market_data.get("liquidityNum"),
                        # Collection tracking
                        tier=tier,
                        active=market_data.get("active", True),
                        # Metadata
                        category=market_data.get("category"),
                        neg_risk=market_data.get("negRisk", False),
                        competitive=market_data.get("competitive"),
                        enable_order_book=market_data.get("enableOrderBook", True),
                    )
                    session.add(new_market)
                    rows_inserted += 1

                markets_processed += 1

            session.commit()

        _complete_task_run(task_run_id, "success", markets_processed, rows_inserted)
        logger.info(
            "Discovery complete",
            markets_processed=markets_processed,
            new=rows_inserted,
            updated=rows_updated,
        )
        return {
            "markets_processed": markets_processed,
            "rows_inserted": rows_inserted,
            "rows_updated": rows_updated,
        }

    except Exception as e:
        _fail_task_run(task_run_id, e)
        logger.error("Discovery failed", error=str(e))
        raise


@shared_task(name="src.tasks.discovery.update_market_tiers")
def update_market_tiers() -> dict:
    """
    Reassign tiers for all active markets based on current time.

    As markets approach their resolution time, they move to higher tiers
    for more frequent data collection.

    Volume filter is applied at T0→T1 transition:
    - Markets with <$100 24h volume are deactivated instead of promoted
    - This gives markets time to build volume before being filtered

    Returns:
        Dictionary with updated count and deactivated count
    """
    from src.db.redis import SyncRedisClient

    redis_client = SyncRedisClient()

    try:
        # Get cached Gamma data for volume lookup
        gamma_cache = redis_client.get_gamma_markets_cache() or []
        volume_by_condition = {
            m.get("conditionId"): float(m.get("volume24hr") or 0)
            for m in gamma_cache
        }

        with get_session() as session:
            markets = session.execute(
                select(Market).where(Market.active == True, Market.resolved == False)
            ).scalars().all()

            updated = 0
            deactivated = 0
            tier_counts = {i: 0 for i in range(5)}

            for market in markets:
                new_tier = calculate_tier(market.end_date)

                # Volume filter at T0→T1 transition
                if market.tier == 0 and new_tier >= 1:
                    volume_24h = volume_by_condition.get(market.condition_id, 0)
                    if volume_24h < settings.ml_volume_threshold:
                        # Deactivate low-volume market instead of promoting
                        market.active = False
                        deactivated += 1
                        logger.debug(
                            "Market deactivated (low volume at T0→T1)",
                            market=market.slug,
                            volume_24h=volume_24h,
                            threshold=settings.ml_volume_threshold,
                        )
                        continue

                tier_counts[new_tier] += 1

                if market.tier != new_tier:
                    logger.debug(
                        "Tier change",
                        market=market.slug,
                        old_tier=market.tier,
                        new_tier=new_tier,
                    )
                    market.tier = new_tier
                    updated += 1

            session.commit()

            logger.info(
                "Tiers updated",
                updated=updated,
                deactivated=deactivated,
                tier_distribution=tier_counts,
            )
            return {"updated": updated, "deactivated": deactivated, "tier_distribution": tier_counts}
    finally:
        redis_client.close()


@shared_task(name="src.tasks.discovery.check_resolutions")
def check_resolutions() -> dict:
    """
    Check for resolved markets and update their status.

    This task queries Gamma API for markets that may have resolved
    and updates their outcome information. Markets that return 404
    (removed from API) are also marked as resolved.

    Returns:
        Dictionary with resolved count
    """
    from src.fetchers.gamma import SyncGammaClient

    with get_session() as session:
        # Get active markets that have passed their end date
        now = datetime.now(timezone.utc)
        markets = session.execute(
            select(Market).where(
                Market.active == True,
                Market.resolved == False,
                Market.end_date < now,
            )
        ).scalars().all()

        if not markets:
            return {"checked": 0, "resolved": 0}

        market_data_list = [(m.id, m.condition_id, m.slug) for m in markets]

    # Fetch current status from Gamma using sync client
    client = SyncGammaClient()
    resolved_count = 0

    try:
        for market_id, condition_id, slug in market_data_list:
            market_data = client.get_market(condition_id)

            # If market returns None (404/removed) or is marked resolved, mark it resolved
            should_resolve = False
            outcome = "UNKNOWN"

            if market_data is None:
                # Market removed from API = resolved
                should_resolve = True
                outcome = "UNKNOWN"  # Can't determine outcome if removed
            elif market_data.get("resolved", False):
                should_resolve = True
                yes_price, _ = GammaClient.parse_outcome_prices(market_data)
                if yes_price > 0.99:
                    outcome = "YES"
                elif yes_price < 0.01:
                    outcome = "NO"
            elif market_data.get("closed", False):
                # Closed but not resolved yet - mark inactive
                should_resolve = True
                outcome = "PENDING"

            if should_resolve:
                with get_session() as session:
                    db_market = session.get(Market, market_id)
                    if db_market:
                        db_market.resolved = True
                        db_market.active = False
                        db_market.resolved_at = datetime.now(timezone.utc)
                        db_market.outcome = outcome
                        session.commit()
                        resolved_count += 1
                        logger.info(
                            "Market resolved",
                            market=slug,
                            outcome=outcome,
                        )

    finally:
        client.close()

    logger.info(
        "Resolution check complete",
        checked=len(market_data_list),
        resolved=resolved_count,
    )
    return {"checked": len(market_data_list), "resolved": resolved_count}


@shared_task(name="src.tasks.discovery.cleanup_stale_markets")
def cleanup_stale_markets() -> dict:
    """
    Deactivate stale T4 markets that are dead or expired.

    A T4 market is considered stale if:
    1. end_date is more than 1 hour in the past (expired/unresolved)
    2. No trades received in the last hour (dead market)

    These markets are deactivated to:
    - Free up WebSocket subscription slots (limited to 500)
    - Avoid collecting useless data
    - Keep the system focused on active markets

    Returns:
        Dictionary with deactivated_expired and deactivated_no_trades counts
    """
    from sqlalchemy import func, text

    from datetime import timedelta

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    deactivated_expired = 0
    deactivated_no_trades = 0

    with get_session() as session:
        # 1. Deactivate T4 markets with end_date > 1 hour in the past
        expired_markets = session.execute(
            select(Market).where(
                Market.tier == 4,
                Market.active == True,
                Market.resolved == False,
                Market.end_date < one_hour_ago,
            )
        ).scalars().all()

        for market in expired_markets:
            market.active = False
            deactivated_expired += 1
            logger.info(
                "Deactivated expired T4 market",
                market=market.slug,
                end_date=market.end_date.isoformat() if market.end_date else None,
            )

        # 2. Deactivate T4 markets with no trades in last hour
        # Get T4 markets that have been tracked for at least 1 hour
        t4_markets = session.execute(
            select(Market).where(
                Market.tier == 4,
                Market.active == True,
                Market.resolved == False,
                Market.tracking_started_at < one_hour_ago,  # Tracked for > 1 hour
            )
        ).scalars().all()

        for market in t4_markets:
            # Check for trades in last hour using raw SQL for efficiency
            trade_count = session.execute(
                text("""
                    SELECT COUNT(*) FROM trades
                    WHERE market_id = :market_id
                    AND timestamp > :cutoff
                """),
                {"market_id": market.id, "cutoff": one_hour_ago}
            ).scalar()

            if trade_count == 0:
                market.active = False
                deactivated_no_trades += 1
                logger.info(
                    "Deactivated T4 market (no trades in 1 hour)",
                    market=market.slug,
                )

        session.commit()

    logger.info(
        "Stale market cleanup complete",
        deactivated_expired=deactivated_expired,
        deactivated_no_trades=deactivated_no_trades,
    )
    return {
        "deactivated_expired": deactivated_expired,
        "deactivated_no_trades": deactivated_no_trades,
    }


# === Task Run Tracking ===


def _start_task_run(task_name: str, tier: Optional[int] = None) -> int:
    """Create a task run record and return its ID."""
    with get_session() as session:
        run = TaskRun(
            task_name=task_name,
            task_id=str(discover_markets.request.id) if hasattr(discover_markets, "request") else "",
            tier=tier,
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
