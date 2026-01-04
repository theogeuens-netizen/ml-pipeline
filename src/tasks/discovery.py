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
from sqlalchemy import select, update, func, or_
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


def derive_outcome(outcome_prices_str: Optional[str]) -> Optional[str]:
    """
    Derive YES/NO/INVALID from outcomePrices string.

    Resolution prices are:
    - YES won: ["1", "0"]
    - NO won: ["0", "1"]
    - INVALID: ["0", "0"] or similar edge cases

    Args:
        outcome_prices_str: JSON string like '["1", "0"]'

    Returns:
        "YES", "NO", "INVALID", or None if not determinable
    """
    if not outcome_prices_str:
        return None
    try:
        import json
        prices = json.loads(outcome_prices_str)
        yes_price = float(prices[0])
        no_price = float(prices[1]) if len(prices) > 1 else 0
        if yes_price > 0.99:
            return "YES"
        elif no_price > 0.99:
            return "NO"
        elif yes_price < 0.01 and no_price < 0.01:
            return "INVALID"
    except (json.JSONDecodeError, IndexError, TypeError, ValueError):
        pass
    return None


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
                    # Update gamma_id if missing (for existing markets before this field was added)
                    if existing.gamma_id is None and market_data.get("id"):
                        existing.gamma_id = int(market_data.get("id"))
                    rows_updated += 1
                else:
                    # Insert new market
                    now = datetime.now(timezone.utc)
                    new_market = Market(
                        condition_id=condition_id,
                        gamma_id=int(market_data.get("id")) if market_data.get("id") else None,
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


@shared_task(name="src.tasks.discovery.capture_all_resolutions")
def capture_all_resolutions() -> dict:
    """
    Capture resolution outcomes for ALL markets past their end_date.

    This is for ML training data - we need outcomes for all markets,
    not just those with positions.

    Runs every 10 minutes, processes markets in batches.

    Returns:
        Dictionary with checked and resolved counts
    """
    from datetime import timedelta
    from src.fetchers.gamma import SyncGammaClient

    now = datetime.now(timezone.utc)
    client = SyncGammaClient()
    resolved_count = 0
    checked_count = 0

    # Only check markets that ended recently (last 14 days)
    # Older markets are likely removed from API anyway
    cutoff = now - timedelta(days=14)

    try:
        with get_session() as session:
            # Find markets past end_date but within recent window
            markets = session.execute(
                select(Market).where(
                    Market.resolved == False,
                    Market.outcome == None,
                    Market.end_date < now,
                    Market.end_date > cutoff,  # Only recent markets
                    Market.gamma_id != None,  # Need gamma_id for lookup
                ).order_by(Market.end_date.desc())  # Check most recent first
                .limit(100)  # Process in batches to avoid rate limiting
            ).scalars().all()

            if not markets:
                return {"checked": 0, "resolved": 0}

            for market in markets:
                checked_count += 1

                # Fetch from Gamma API using reliable gamma_id
                market_data = client.get_market_by_id(market.gamma_id)

                if market_data is None:
                    # Market removed from API, mark as UNKNOWN
                    market.outcome = "UNKNOWN"
                    market.resolved = True
                    market.resolved_at = now
                    market.active = False
                    resolved_count += 1
                    logger.info(
                        "Market removed from API",
                        market=market.slug,
                        gamma_id=market.gamma_id,
                    )
                    continue

                # Check if resolved
                uma_status = market_data.get("umaResolutionStatus")
                if uma_status == "resolved":
                    # Parse outcome from prices
                    outcome = derive_outcome(market_data.get("outcomePrices"))
                    if outcome:
                        market.outcome = outcome
                        market.resolved = True
                        market.resolved_at = now
                        market.active = False
                        resolved_count += 1
                        logger.info(
                            "Captured resolution",
                            market=market.slug,
                            outcome=outcome,
                            gamma_id=market.gamma_id,
                        )
                    else:
                        # UMA resolved but prices indeterminate (0.5/0.5)
                        market.outcome = "UNKNOWN"
                        market.resolved = True
                        market.resolved_at = now
                        market.active = False
                        resolved_count += 1
                        logger.info(
                            "Captured resolution (indeterminate prices)",
                            market=market.slug,
                            outcome="UNKNOWN",
                            gamma_id=market.gamma_id,
                        )

                # Update lifecycle fields regardless
                market.closed = market_data.get("closed", False)
                market.accepting_orders = market_data.get("acceptingOrders", True)
                if uma_status != market.uma_resolution_status:
                    market.uma_resolution_status = uma_status
                    market.uma_status_updated_at = now

            session.commit()

        logger.info(
            "capture_all_resolutions complete",
            checked=checked_count,
            resolved=resolved_count,
        )
        return {"checked": checked_count, "resolved": resolved_count}

    finally:
        client.close()


@shared_task(name="src.tasks.discovery.check_resolutions")
def check_resolutions() -> dict:
    """
    Close positions on resolved markets and update balances.

    This task now primarily reads pre-captured outcomes from the database
    (populated by capture_all_resolutions), with API fallback for edge cases.

    Returns:
        Dictionary with resolved count and P&L
    """
    from src.fetchers.gamma import SyncGammaClient
    from src.executor.portfolio.positions import PositionManager
    from src.executor.models import PaperBalance, StrategyBalance, Position
    from src.alerts.telegram import alert_position_closed
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    position_manager = PositionManager(is_paper=True)
    resolved_count = 0
    positions_closed = 0
    total_pnl = 0.0

    with get_session() as session:
        # Get market IDs with open positions
        markets_with_positions = session.execute(
            select(Position.market_id).where(Position.status == "open")
        ).scalars().all()
        markets_with_positions_set = set(markets_with_positions)

        if not markets_with_positions_set:
            return {"checked": 0, "resolved": 0, "positions_closed": 0, "total_pnl": 0}

        # PHASE 1: Markets with pre-captured outcomes (no API calls needed)
        markets_with_outcomes = session.execute(
            select(Market).where(
                Market.id.in_(markets_with_positions_set),
                Market.outcome.in_(["YES", "NO", "INVALID"]),  # Outcome already captured
            )
        ).scalars().all()

        for market in markets_with_outcomes:
            outcome = market.outcome
            result = _close_positions_for_market(
                session, market, outcome, position_manager
            )
            resolved_count += 1
            positions_closed += result["positions_closed"]
            total_pnl += result["total_pnl"]

        session.commit()

    # PHASE 2: Markets needing API lookup (fallback for positions without outcome)
    with get_session() as session:
        # Get markets with open positions that don't have outcome yet
        markets_needing_lookup = session.execute(
            select(Market).where(
                Market.id.in_(markets_with_positions_set),
                Market.outcome == None,
                Market.end_date < now,
            )
        ).scalars().all()

        if markets_needing_lookup:
            client = SyncGammaClient()
            try:
                for market in markets_needing_lookup:
                    # Use gamma_id for direct lookup (most reliable)
                    market_data = None
                    if market.gamma_id:
                        market_data = client.get_market_by_id(market.gamma_id)

                    # Fallback to slug
                    if market_data is None and market.slug:
                        market_data = client.get_market_by_slug(market.slug)

                    if market_data is None:
                        # Market removed from API
                        market.outcome = "UNKNOWN"
                        market.resolved = True
                        market.resolved_at = now
                        market.active = False
                        logger.info(
                            "Market removed from API (fallback)",
                            market=market.slug,
                            gamma_id=market.gamma_id,
                        )
                        continue

                    # Check if resolved
                    uma_status = market_data.get("umaResolutionStatus")
                    if uma_status == "resolved":
                        outcome = derive_outcome(market_data.get("outcomePrices"))
                        if outcome:
                            market.outcome = outcome
                            market.resolved = True
                            market.resolved_at = now
                            market.active = False

                            # Refresh market in session for position closure
                            session.flush()

                            result = _close_positions_for_market(
                                session, market, outcome, position_manager
                            )
                            resolved_count += 1
                            positions_closed += result["positions_closed"]
                            total_pnl += result["total_pnl"]

                    # Update lifecycle fields
                    market.closed = market_data.get("closed", False)
                    market.accepting_orders = market_data.get("acceptingOrders", True)
                    if uma_status != market.uma_resolution_status:
                        market.uma_resolution_status = uma_status
                        market.uma_status_updated_at = now

                session.commit()
            finally:
                client.close()

    logger.info(
        "check_resolutions complete",
        resolved=resolved_count,
        positions_closed=positions_closed,
        total_pnl=total_pnl,
    )
    return {
        "checked": len(markets_with_positions_set),
        "resolved": resolved_count,
        "positions_closed": positions_closed,
        "total_pnl": total_pnl,
    }


def _close_positions_for_market(session, market, outcome, position_manager) -> dict:
    """
    Helper to close positions and update balances for a resolved market.

    Returns:
        Dictionary with positions_closed count and total_pnl
    """
    from src.executor.models import PaperBalance, StrategyBalance
    from src.alerts.telegram import alert_position_closed
    from decimal import Decimal

    positions_closed = 0
    total_pnl = 0.0

    # Mark market as resolved
    market.resolved = True
    market.active = False
    market.resolved_at = datetime.now(timezone.utc)

    # Close positions and calculate P&L
    results = position_manager.close_positions_on_resolution(
        market_id=market.id,
        outcome=outcome,
        db=session,
    )

    # Update balances with payouts
    if results:
        paper_balance = session.query(PaperBalance).first()
        if paper_balance:
            for result in results:
                # Add payout to balance
                paper_balance.balance_usd += Decimal(str(result["payout"]))
                total_pnl += result["pnl"]
                positions_closed += 1

                # Update strategy-specific balance
                strategy_name = result["strategy_name"]
                strategy_balance = session.query(StrategyBalance).filter(
                    StrategyBalance.strategy_name == strategy_name
                ).first()

                if strategy_balance:
                    strategy_balance.current_usd = float(strategy_balance.current_usd) + result["payout"]
                    strategy_balance.realized_pnl = float(strategy_balance.realized_pnl) + result["pnl"]
                    strategy_balance.position_count = max(0, strategy_balance.position_count - 1)
                    strategy_balance.trade_count += 1

                    if result["pnl"] > 0:
                        strategy_balance.win_count += 1
                    elif result["pnl"] < 0:
                        strategy_balance.loss_count += 1

                    strategy_balance.total_pnl = float(strategy_balance.realized_pnl) + float(strategy_balance.unrealized_pnl)

                    # Update water marks
                    current_value = float(strategy_balance.current_usd)
                    if current_value > float(strategy_balance.high_water_mark):
                        strategy_balance.high_water_mark = current_value
                    if current_value < float(strategy_balance.low_water_mark):
                        strategy_balance.low_water_mark = current_value

                    # Update max drawdown
                    if float(strategy_balance.high_water_mark) > 0:
                        drawdown_usd = float(strategy_balance.high_water_mark) - current_value
                        drawdown_pct = drawdown_usd / float(strategy_balance.high_water_mark)
                        if drawdown_usd > float(strategy_balance.max_drawdown_usd):
                            strategy_balance.max_drawdown_usd = drawdown_usd
                            strategy_balance.max_drawdown_pct = drawdown_pct

                    logger.info(
                        "Strategy balance updated on resolution",
                        strategy=strategy_name,
                        pnl=result["pnl"],
                        new_balance=strategy_balance.current_usd,
                    )

                # Send Telegram alert
                try:
                    alert_position_closed(
                        strategy=result["strategy_name"],
                        market=market.slug,
                        side=result["side"],
                        outcome=outcome,
                        cost_basis=result["cost_basis"],
                        payout=result["payout"],
                        pnl=result["pnl"],
                        market_id=market.id,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send Telegram alert: {e}")

            # Update paper balance totals
            new_balance = float(paper_balance.balance_usd)
            paper_balance.total_pnl = new_balance - float(paper_balance.starting_balance_usd)
            if new_balance > float(paper_balance.high_water_mark):
                paper_balance.high_water_mark = new_balance
            if new_balance < float(paper_balance.low_water_mark):
                paper_balance.low_water_mark = new_balance

        logger.info(
            "Market resolved",
            market=market.slug,
            outcome=outcome,
            positions_closed=len(results) if results else 0,
        )

    return {"positions_closed": positions_closed, "total_pnl": total_pnl}


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
    Delete old task_runs, tier_transitions, and csgo_price_ticks records.

    This is operational/diagnostic data, NOT market data.
    Keeps last 7 days of records for debugging and chart display.

    Returns:
        Dictionary with deleted counts
    """
    from datetime import timedelta
    from src.db.models import CSGOPriceTick

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    with get_session() as session:
        # Count before delete for reporting
        old_task_runs = session.execute(
            select(func.count(TaskRun.id)).where(TaskRun.started_at < cutoff)
        ).scalar() or 0

        old_transitions = session.execute(
            select(func.count(TierTransition.id)).where(TierTransition.transitioned_at < cutoff)
        ).scalar() or 0

        old_price_ticks = session.execute(
            select(func.count(CSGOPriceTick.id)).where(CSGOPriceTick.timestamp < cutoff)
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

        if old_price_ticks > 0:
            # Delete old csgo_price_ticks records
            session.execute(
                CSGOPriceTick.__table__.delete().where(CSGOPriceTick.timestamp < cutoff)
            )

        if old_task_runs > 0 or old_transitions > 0 or old_price_ticks > 0:
            session.commit()
            logger.info(
                "Cleaned up old records",
                task_runs_deleted=old_task_runs,
                tier_transitions_deleted=old_transitions,
                price_ticks_deleted=old_price_ticks,
                cutoff=cutoff.isoformat(),
            )

    return {
        "task_runs_deleted": old_task_runs,
        "tier_transitions_deleted": old_transitions,
        "price_ticks_deleted": old_price_ticks,
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
