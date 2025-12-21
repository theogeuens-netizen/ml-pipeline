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
from sqlalchemy import select, update, func
import structlog

from src.config.settings import settings
from src.db.database import get_session
from src.db.models import Market, TaskRun, TierTransition
from src.fetchers.gamma import GammaClient
from src.services.market_lifecycle import (
    get_trading_status,
    get_uma_status,
    log_state_transition,
)

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
        seen_condition_ids = set()  # Track condition_ids to avoid duplicates in API response

        with get_session() as session:
            for market_data in markets:
                # Parse market data
                yes_price, _ = GammaClient.parse_outcome_prices(market_data)
                yes_token, no_token = GammaClient.parse_token_ids(market_data)
                condition_id = market_data.get("conditionId")

                if not condition_id:
                    continue

                # Skip duplicates within this batch (API sometimes returns duplicates)
                if condition_id in seen_condition_ids:
                    continue
                seen_condition_ids.add(condition_id)

                # Volume filter at T1→T2 transition (in update_market_tiers)
                # Markets have until 12h before close to build volume
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

                    # Update lifecycle fields (always update, these change over time)
                    now = datetime.now(timezone.utc)
                    new_closed = market_data.get("closed", False)
                    new_accepting_orders = market_data.get("acceptingOrders", True)
                    new_uma_status = market_data.get("umaResolutionStatus")

                    # Capture old state for transition logging
                    old_trading = get_trading_status(
                        existing.active, existing.closed,
                        existing.accepting_orders, existing.resolved
                    )
                    old_uma = get_uma_status(existing.uma_resolution_status)

                    # Track when closed status changes
                    if new_closed and not existing.closed:
                        existing.closed_at = GammaClient.parse_datetime(market_data.get("closedTime")) or now
                    existing.closed = new_closed

                    # Track when accepting_orders changes
                    if new_accepting_orders != existing.accepting_orders:
                        existing.accepting_orders_updated_at = GammaClient.parse_datetime(
                            market_data.get("acceptingOrdersTimestamp")
                        ) or now
                    existing.accepting_orders = new_accepting_orders

                    # Track UMA resolution status changes
                    if new_uma_status != existing.uma_resolution_status:
                        existing.uma_status_updated_at = now
                    existing.uma_resolution_status = new_uma_status

                    # Log state transition if any lifecycle field changed
                    new_trading = get_trading_status(
                        existing.active, existing.closed,
                        existing.accepting_orders, existing.resolved
                    )
                    new_uma = get_uma_status(existing.uma_resolution_status)

                    if old_trading != new_trading or old_uma != new_uma:
                        log_state_transition(
                            market_id=existing.id,
                            condition_id=existing.condition_id,
                            slug=existing.slug,
                            old_trading=old_trading,
                            new_trading=new_trading,
                            old_uma=old_uma,
                            new_uma=new_uma,
                        )

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
                    now = datetime.now(timezone.utc)
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
                        # Lifecycle status (from Gamma API)
                        closed=market_data.get("closed", False),
                        closed_at=GammaClient.parse_datetime(market_data.get("closedTime")),
                        accepting_orders=market_data.get("acceptingOrders", True),
                        accepting_orders_updated_at=GammaClient.parse_datetime(market_data.get("acceptingOrdersTimestamp")),
                        uma_resolution_status=market_data.get("umaResolutionStatus"),
                        uma_status_updated_at=now if market_data.get("umaResolutionStatus") else None,
                        # Collection tracking
                        tier=tier,
                        active=market_data.get("active", True),
                        tracking_started_at=now,  # Set when first discovered
                        # Metadata
                        category=market_data.get("category"),
                        neg_risk=market_data.get("negRisk", False),
                        competitive=market_data.get("competitive"),
                        enable_order_book=market_data.get("enableOrderBook", True),
                    )
                    session.add(new_market)
                    rows_inserted += 1

                markets_processed += 1

                # Commit in batches to avoid very large transactions
                if markets_processed % 1000 == 0:
                    session.commit()
                    logger.debug(
                        "Discovery progress",
                        processed=markets_processed,
                        new=rows_inserted,
                        updated=rows_updated,
                    )

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

    Volume filter is applied at T1→T2 transition:
    - Markets with <$100 24h volume are deactivated instead of promoted
    - This gives markets until 12h before close to build volume
    - Saves WebSocket bandwidth (only T2+ gets WebSocket connections)

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
            now = datetime.now(timezone.utc)

            for market in markets:
                new_tier = calculate_tier(market.end_date)
                hours_to_close = None
                if market.end_date:
                    hours_to_close = (market.end_date - now).total_seconds() / 3600

                # Volume filter at T1→T2 transition (before WebSocket)
                # Markets have until 12h before close to build volume
                if market.tier <= 1 and new_tier >= 2:
                    volume_24h = volume_by_condition.get(market.condition_id, 0)
                    if volume_24h < settings.ml_volume_threshold:
                        # Record deactivation as tier transition
                        transition = TierTransition(
                            market_id=market.id,
                            condition_id=market.condition_id,
                            market_slug=market.slug,
                            from_tier=market.tier,
                            to_tier=-1,
                            transitioned_at=now,
                            hours_to_close=hours_to_close,
                            reason="low_volume",
                        )
                        session.add(transition)
                        # Deactivate low-volume market instead of promoting to T2+
                        # This saves WebSocket bandwidth (only T2+ gets WS)
                        market.active = False
                        deactivated += 1
                        logger.debug(
                            "Market deactivated (low volume at T1→T2)",
                            market=market.slug,
                            volume_24h=volume_24h,
                            threshold=settings.ml_volume_threshold,
                        )
                        continue

                tier_counts[new_tier] += 1

                if market.tier != new_tier:
                    # Record tier transition
                    transition = TierTransition(
                        market_id=market.id,
                        condition_id=market.condition_id,
                        market_slug=market.slug,
                        from_tier=market.tier,
                        to_tier=new_tier,
                        transitioned_at=now,
                        hours_to_close=hours_to_close,
                        reason="time",
                    )
                    session.add(transition)
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
    Check for resolved markets, update status, close positions, and update balance.

    This task:
    1. Queries Gamma API for markets that may have resolved
    2. Determines outcome (YES/NO/UNKNOWN)
    3. Closes any open positions on resolved markets
    4. Calculates P&L and updates paper_balances
    5. Sends Telegram alerts for position closures

    Returns:
        Dictionary with resolved count and P&L
    """
    from src.fetchers.gamma import SyncGammaClient, GammaClient
    from src.executor.portfolio.positions import PositionManager
    from src.executor.models import PaperBalance
    from src.alerts.telegram import alert_position_closed

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
            return {"checked": 0, "resolved": 0, "positions_closed": 0, "total_pnl": 0}

        market_data_list = [(m.id, m.condition_id, m.slug) for m in markets]

    # Fetch closed markets from Gamma API first - this gives us resolution prices
    # before markets are removed from the API
    client = SyncGammaClient()
    position_manager = PositionManager(is_paper=True)
    resolved_count = 0
    positions_closed = 0
    total_pnl = 0.0

    # Fetch closed markets to get resolution data
    closed_markets = client.get_closed_markets(limit=500)
    closed_by_condition = {m.get("conditionId"): m for m in closed_markets}
    logger.info("Fetched closed markets from API", count=len(closed_markets))

    try:
        for market_id, condition_id, slug in market_data_list:
            # First check if we have this market in the closed markets response
            market_data = closed_by_condition.get(condition_id)

            # Fall back to single-market fetch if not in closed list
            if market_data is None:
                market_data = client.get_market(condition_id)

            # Determine outcome based on API response
            should_resolve = False
            outcome = "UNKNOWN"

            if market_data is None:
                # Market removed from API = resolved but can't determine outcome
                should_resolve = True
                outcome = "UNKNOWN"
                logger.info("Market removed from API", market=slug, condition_id=condition_id[:16] if condition_id else "N/A")
            else:
                # First, update lifecycle fields in the database
                # This captures closed/accepting_orders/uma_resolution_status even if not resolved
                with get_session() as update_session:
                    db_market = update_session.get(Market, market_id)
                    if db_market:
                        now = datetime.now(timezone.utc)
                        new_closed = market_data.get("closed", False)
                        new_accepting = market_data.get("acceptingOrders", True)
                        new_uma_status = market_data.get("umaResolutionStatus")

                        # Capture old state for transition logging
                        old_trading = get_trading_status(
                            db_market.active, db_market.closed,
                            db_market.accepting_orders, db_market.resolved
                        )
                        old_uma = get_uma_status(db_market.uma_resolution_status)

                        # Track when closed changes
                        if new_closed and not db_market.closed:
                            db_market.closed_at = GammaClient.parse_datetime(market_data.get("closedTime")) or now
                            logger.info("Market closed", market=slug, closed_at=db_market.closed_at)
                        db_market.closed = new_closed

                        # Track accepting_orders changes
                        if new_accepting != db_market.accepting_orders:
                            db_market.accepting_orders_updated_at = now
                            logger.info("Market accepting_orders changed", market=slug, accepting_orders=new_accepting)
                        db_market.accepting_orders = new_accepting

                        # Track UMA status changes
                        if new_uma_status != db_market.uma_resolution_status:
                            db_market.uma_status_updated_at = now
                            logger.info("Market UMA status changed", market=slug,
                                       old_status=db_market.uma_resolution_status, new_status=new_uma_status)
                        db_market.uma_resolution_status = new_uma_status

                        # Log state transition if any lifecycle field changed
                        new_trading = get_trading_status(
                            db_market.active, db_market.closed,
                            db_market.accepting_orders, db_market.resolved
                        )
                        new_uma = get_uma_status(db_market.uma_resolution_status)

                        if old_trading != new_trading or old_uma != new_uma:
                            log_state_transition(
                                market_id=db_market.id,
                                condition_id=db_market.condition_id,
                                slug=db_market.slug,
                                old_trading=old_trading,
                                new_trading=new_trading,
                                old_uma=old_uma,
                                new_uma=new_uma,
                            )

                        update_session.commit()

                # Now determine if this market should be marked as resolved
                uma_status = market_data.get("umaResolutionStatus")
                is_resolved_flag = market_data.get("resolved", False)
                is_closed_flag = market_data.get("closed", False)
                yes_price, no_price = GammaClient.parse_outcome_prices(market_data)

                # Check UMA resolution status first (most reliable)
                if uma_status == "resolved" or is_resolved_flag:
                    # Market is definitively resolved - use prices to determine outcome
                    if yes_price > 0.99:
                        outcome = "YES"
                        should_resolve = True
                    elif no_price > 0.99:  # Equivalent to yes_price < 0.01
                        outcome = "NO"
                        should_resolve = True
                    elif yes_price < 0.01 and no_price < 0.01:
                        # Both prices near 0 = voided/invalid market
                        outcome = "INVALID"
                        should_resolve = True
                    else:
                        # UMA says resolved but prices aren't settled yet
                        # This shouldn't happen, log warning but wait
                        outcome = "PENDING"
                        should_resolve = False
                        logger.warning("UMA resolved but prices not settled",
                                      market=slug, yes_price=yes_price, no_price=no_price)
                elif uma_status == "proposed":
                    # In 2-hour challenge window - DON'T resolve yet
                    outcome = "PENDING"
                    should_resolve = False
                    logger.debug("Market in UMA challenge window", market=slug, uma_status=uma_status)
                elif uma_status == "disputed":
                    # Market is disputed - DON'T resolve, wait for DVM vote
                    outcome = "PENDING"
                    should_resolve = False
                    logger.info("Market disputed, waiting for DVM vote", market=slug)
                elif is_closed_flag and not uma_status:
                    # Closed but no UMA status yet - use price fallback
                    if yes_price > 0.99:
                        outcome = "YES"
                        should_resolve = True
                    elif no_price > 0.99:
                        outcome = "NO"
                        should_resolve = True
                    elif yes_price < 0.01 and no_price < 0.01:
                        outcome = "INVALID"
                        should_resolve = True
                    else:
                        # Closed but prices not settled - waiting for resolution
                        outcome = "PENDING"
                        should_resolve = False
                else:
                    # Not closed, not resolved
                    outcome = "PENDING"
                    should_resolve = False

                logger.debug(
                    "Resolution check",
                    market=slug,
                    resolved=is_resolved_flag,
                    closed=is_closed_flag,
                    uma_status=uma_status,
                    yes_price=yes_price,
                    no_price=no_price,
                    outcome=outcome,
                )

            # Process if we have a definitive outcome (YES, NO, or INVALID)
            # Skip PENDING (still settling) and UNKNOWN (can't determine)
            if should_resolve and outcome in ("YES", "NO", "INVALID"):
                with get_session() as session:
                    db_market = session.get(Market, market_id)
                    if db_market:
                        db_market.resolved = True
                        db_market.active = False
                        db_market.resolved_at = datetime.now(timezone.utc)
                        db_market.outcome = outcome

                        # Close positions and calculate P&L
                        results = position_manager.close_positions_on_resolution(
                            market_id=market_id,
                            outcome=outcome,
                            db=session,
                        )

                        # Update paper balance with payouts
                        if results:
                            paper_balance = session.query(PaperBalance).first()
                            if paper_balance:
                                for result in results:
                                    # Add payout to balance
                                    paper_balance.balance_usd += result["payout"]
                                    total_pnl += result["pnl"]
                                    positions_closed += 1

                                    # Send Telegram alert
                                    try:
                                        alert_position_closed(
                                            strategy=result["strategy_name"],
                                            market=slug,
                                            side=result["side"],
                                            outcome=outcome,
                                            cost_basis=result["cost_basis"],
                                            payout=result["payout"],
                                            pnl=result["pnl"],
                                            market_id=market_id,
                                        )
                                    except Exception as e:
                                        logger.warning(f"Failed to send Telegram alert: {e}")

                                # After processing all results, update total_pnl and high/low water marks
                                # At this point positions are closed, so total value = cash only
                                new_balance = float(paper_balance.balance_usd)
                                paper_balance.total_pnl = new_balance - float(paper_balance.starting_balance_usd)

                                # Update high/low water marks (after position close, total = cash)
                                if new_balance > float(paper_balance.high_water_mark):
                                    paper_balance.high_water_mark = new_balance
                                if new_balance < float(paper_balance.low_water_mark):
                                    paper_balance.low_water_mark = new_balance

                        session.commit()
                        resolved_count += 1
                        logger.info(
                            "Market resolved",
                            market=slug,
                            outcome=outcome,
                            positions_closed=len(results) if results else 0,
                        )

    finally:
        client.close()

    logger.info(
        "Resolution check complete",
        checked=len(market_data_list),
        resolved=resolved_count,
        positions_closed=positions_closed,
        total_pnl=total_pnl,
    )
    return {
        "checked": len(market_data_list),
        "resolved": resolved_count,
        "positions_closed": positions_closed,
        "total_pnl": total_pnl,
    }


@shared_task(name="src.tasks.discovery.cleanup_stale_markets")
def cleanup_stale_markets() -> dict:
    """
    Deactivate stale markets that are resolved, dead, expired, or delisted.

    Markets are deactivated if:
    1. resolved == True (market has ended)
    2. end_date is more than 1 hour in the past and unresolved (expired)
    3. T4 market with no trades in the last hour (dead market)
    4. Market no longer in Gamma API (delisted/removed)

    These markets are deactivated to:
    - Free up WebSocket subscription slots (limited to 500)
    - Avoid collecting useless data
    - Keep the system focused on active markets

    Returns:
        Dictionary with deactivation counts by reason
    """
    from sqlalchemy import func, text
    from src.db.redis import SyncRedisClient

    from datetime import timedelta

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    deactivated_resolved = 0
    deactivated_expired = 0
    deactivated_no_trades = 0
    deactivated_missing_from_api = 0

    # Get gamma cache to check if markets still exist in API
    redis_client = SyncRedisClient()
    gamma_cache = redis_client.get_gamma_markets_cache() or []
    gamma_condition_ids = {m.get("conditionId") for m in gamma_cache}
    redis_client.close()

    with get_session() as session:
        # 1. Deactivate all resolved markets (highest priority cleanup)
        resolved_markets = session.execute(
            select(Market).where(
                Market.active == True,
                Market.resolved == True,
            )
        ).scalars().all()

        for market in resolved_markets:
            # Record deactivation as tier transition
            transition = TierTransition(
                market_id=market.id,
                condition_id=market.condition_id,
                market_slug=market.slug,
                from_tier=market.tier,
                to_tier=-1,
                transitioned_at=now,
                hours_to_close=None,
                reason="resolved",
            )
            session.add(transition)
            market.active = False
            deactivated_resolved += 1

        if deactivated_resolved > 0:
            logger.info(
                "Deactivated resolved markets",
                count=deactivated_resolved,
            )

        # 2. Deactivate unresolved markets with end_date > 1 hour in the past
        expired_markets = session.execute(
            select(Market).where(
                Market.active == True,
                Market.resolved == False,
                Market.end_date < one_hour_ago,
            )
        ).scalars().all()

        for market in expired_markets:
            # Record deactivation as tier transition
            transition = TierTransition(
                market_id=market.id,
                condition_id=market.condition_id,
                market_slug=market.slug,
                from_tier=market.tier,
                to_tier=-1,
                transitioned_at=now,
                hours_to_close=None,
                reason="expired",
            )
            session.add(transition)
            market.active = False
            deactivated_expired += 1
            logger.info(
                "Deactivated expired market",
                market=market.slug,
                end_date=market.end_date.isoformat() if market.end_date else None,
            )

        # 3. Deactivate T4 markets with no trades in last hour
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
                # Record deactivation as tier transition
                transition = TierTransition(
                    market_id=market.id,
                    condition_id=market.condition_id,
                    market_slug=market.slug,
                    from_tier=market.tier,
                    to_tier=-1,
                    transitioned_at=now,
                    hours_to_close=None,
                    reason="no_trades",
                )
                session.add(transition)
                market.active = False
                deactivated_no_trades += 1
                logger.info(
                    "Deactivated T4 market (no trades in 1 hour)",
                    market=market.slug,
                )

        # 4. Deactivate markets no longer in Gamma API (only if cache is populated)
        if gamma_condition_ids:
            # Get all active unresolved markets
            active_markets = session.execute(
                select(Market).where(
                    Market.active == True,
                    Market.resolved == False,
                )
            ).scalars().all()

            for market in active_markets:
                if market.condition_id not in gamma_condition_ids:
                    # Record deactivation as tier transition
                    transition = TierTransition(
                        market_id=market.id,
                        condition_id=market.condition_id,
                        market_slug=market.slug,
                        from_tier=market.tier,
                        to_tier=-1,
                        transitioned_at=now,
                        hours_to_close=None,
                        reason="delisted",
                    )
                    session.add(transition)
                    market.active = False
                    deactivated_missing_from_api += 1
                    logger.info(
                        "Deactivated market (missing from Gamma API)",
                        market=market.slug,
                        condition_id=market.condition_id[:16],
                    )

        session.commit()

    logger.info(
        "Stale market cleanup complete",
        deactivated_resolved=deactivated_resolved,
        deactivated_expired=deactivated_expired,
        deactivated_no_trades=deactivated_no_trades,
        deactivated_missing_from_api=deactivated_missing_from_api,
    )
    return {
        "deactivated_resolved": deactivated_resolved,
        "deactivated_expired": deactivated_expired,
        "deactivated_no_trades": deactivated_no_trades,
        "deactivated_missing_from_api": deactivated_missing_from_api,
    }


@shared_task(name="src.tasks.discovery.cleanup_old_task_runs")
def cleanup_old_task_runs() -> dict:
    """
    Delete old task_runs and tier_transitions records to prevent unbounded table growth.

    This is operational/diagnostic data, NOT market data.
    Keeps last 7 days of records for debugging purposes.

    Returns:
        Dictionary with deleted counts
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    with get_session() as session:
        # Count before delete for reporting
        old_task_runs = session.execute(
            select(func.count(TaskRun.id)).where(TaskRun.started_at < cutoff)
        ).scalar() or 0

        old_transitions = session.execute(
            select(func.count(TierTransition.id)).where(TierTransition.transitioned_at < cutoff)
        ).scalar() or 0

        if old_task_runs > 0:
            # Delete old task_runs records
            session.execute(
                TaskRun.__table__.delete().where(TaskRun.started_at < cutoff)
            )

        if old_transitions > 0:
            # Delete old tier_transitions records
            session.execute(
                TierTransition.__table__.delete().where(TierTransition.transitioned_at < cutoff)
            )

        if old_task_runs > 0 or old_transitions > 0:
            session.commit()
            logger.info(
                "Cleaned up old records",
                task_runs_deleted=old_task_runs,
                tier_transitions_deleted=old_transitions,
                cutoff=cutoff.isoformat(),
            )

    return {
        "task_runs_deleted": old_task_runs,
        "tier_transitions_deleted": old_transitions,
        "cutoff": cutoff.isoformat(),
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
