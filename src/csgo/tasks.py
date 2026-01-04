"""
CS:GO Pipeline Celery Tasks.

Periodic tasks for:
- Discovering and syncing CS:GO markets to csgo_matches table
- Enriching matches with Gamma API metadata
- Polling market status for in-play matches (lifecycle tracking)
"""

import logging
import traceback
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from celery import shared_task
from sqlalchemy import and_, or_

from src.db.database import get_session
from src.db.models import TaskRun, CSGOMatch, Market
from src.csgo.engine.models import CSGOPosition, CSGOPositionLeg, CSGOStrategyState
from src.fetchers.gamma import GammaClient, SyncGammaClient
from src.fetchers.clob import SyncCLOBClient

logger = logging.getLogger(__name__)


def _close_positions_on_resolution(db, match: CSGOMatch, yes_price: Decimal, no_price: Decimal) -> int:
    """
    Close all open positions for a resolved market.

    Creates FULL_EXIT leg records for audit trail.

    Args:
        db: Database session
        match: The resolved CSGOMatch
        yes_price: Final YES price (0 or 1)
        no_price: Final NO price (0 or 1)

    Returns:
        Number of positions closed
    """
    # Find the market ID from main markets table
    market = db.query(Market).filter(Market.condition_id == match.condition_id).first()
    if not market:
        logger.warning(f"[CSGO] No market found for condition_id {match.condition_id[:16]}...")
        return 0

    # Find all open positions on this market
    open_positions = db.query(CSGOPosition).filter(
        CSGOPosition.market_id == market.id,
        CSGOPosition.status == "open",
    ).all()

    if not open_positions:
        return 0

    closed_count = 0
    close_reason = f"market_resolved:{match.outcome or 'unknown'}"

    for position in open_positions:
        # Determine exit price based on token type
        if position.token_type == "YES":
            exit_price = float(yes_price)
        else:
            exit_price = float(no_price)

        # Store shares before closing (for leg record)
        shares_exited = float(position.remaining_shares)

        # Calculate P&L
        exit_value = shares_exited * exit_price
        cost_basis = shares_exited * float(position.avg_entry_price)
        realized_pnl = exit_value - cost_basis

        # Create FULL_EXIT leg record for audit trail
        leg = CSGOPositionLeg(
            position_id=position.id,
            leg_type="full_exit",
            shares_delta=Decimal(str(-shares_exited)),  # Negative for exits
            price=Decimal(str(exit_price)),
            cost_delta=Decimal(str(-cost_basis)),  # Cost returned
            realized_pnl=Decimal(str(realized_pnl)),
            trigger_price=Decimal(str(exit_price)),
            trigger_reason=close_reason,
        )
        db.add(leg)

        # Close the position
        position.remaining_shares = Decimal("0")
        position.current_price = Decimal(str(exit_price))
        position.realized_pnl = (position.realized_pnl or Decimal("0")) + Decimal(str(realized_pnl))
        position.unrealized_pnl = Decimal("0")
        position.status = "closed"
        position.close_reason = close_reason
        position.closed_at = datetime.now(timezone.utc)

        # Update strategy balance
        strategy_state = db.query(CSGOStrategyState).filter(
            CSGOStrategyState.strategy_name == position.strategy_name
        ).first()
        if strategy_state:
            strategy_state.available_usd = (strategy_state.available_usd or Decimal("0")) + Decimal(str(exit_value))
            strategy_state.total_realized_pnl = (strategy_state.total_realized_pnl or Decimal("0")) + Decimal(str(realized_pnl))

        closed_count += 1
        logger.info(
            f"[CSGO] Auto-closed {position.strategy_name} {position.token_type} position "
            f"on {match.team_yes} vs {match.team_no}: P&L ${realized_pnl:+.2f} (leg #{leg.id if leg.id else 'pending'})"
        )

    return closed_count


def _log_task_run(task_name: str, task_id: str, success: bool, duration_ms: int, error: str = None):
    """Log task execution to database."""
    with get_session() as db:
        run = TaskRun(
            task_name=task_name,
            task_id=task_id or "manual",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            status="success" if success else "failure",
            error_message=error[:1000] if error else None,
        )
        db.add(run)
        db.commit()


@shared_task(
    name="src.csgo.tasks.sync_csgo_markets",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def sync_csgo_markets_task(self):
    """
    Discover CS:GO markets and sync to csgo_matches table.

    This task:
    1. Queries the markets table for CS:GO match markets
    2. Creates entries in csgo_matches for new markets
    3. Does NOT enrich (separate task for that)

    Runs every 10 minutes via Celery beat.
    """
    from src.csgo.discovery import sync_csgo_matches

    start = datetime.now(timezone.utc)
    task_name = "sync_csgo_markets"

    try:
        with get_session() as db:
            stats = sync_csgo_matches(db)

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(f"CS:GO market sync complete: {stats}")
        _log_task_run(task_name, self.request.id, True, duration_ms)

        return stats

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"CS:GO market sync failed: {error_msg}")
        _log_task_run(task_name, self.request.id, False, duration_ms, error_msg)
        raise self.retry(exc=e)


@shared_task(
    name="src.csgo.tasks.enrich_csgo_matches",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def enrich_csgo_matches_task(self):
    """
    Enrich CS:GO matches with Gamma API metadata.

    This task:
    1. Finds csgo_matches without team data
    2. Fetches game_start_time, teams, tournament from Gamma API
    3. Updates the csgo_matches records

    Runs every 10 minutes via Celery beat.
    """
    from src.csgo.enrichment import enrich_all_csgo_matches

    start = datetime.now(timezone.utc)
    task_name = "enrich_csgo_matches"

    try:
        with get_session() as db:
            stats = enrich_all_csgo_matches(db, only_unenriched=True)

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(f"CS:GO enrichment complete: {stats}")
        _log_task_run(task_name, self.request.id, True, duration_ms)

        return stats

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"CS:GO enrichment failed: {error_msg}")
        _log_task_run(task_name, self.request.id, False, duration_ms, error_msg)
        raise self.retry(exc=e)


@shared_task(
    name="src.csgo.tasks.refresh_csgo_game_times",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def refresh_csgo_game_times_task(self):
    """
    Refresh game start times for upcoming CS:GO matches.

    This task:
    1. Finds matches with game_start_time in the next 6 hours
    2. Re-fetches from Gamma API to catch schedule changes
    3. Only updates if game_start_override is False

    Runs every 30 minutes via Celery beat.
    """
    from datetime import timedelta

    from sqlalchemy import and_

    from src.csgo.enrichment import enrich_csgo_market
    from src.db.models import CSGOMatch

    start = datetime.now(timezone.utc)
    task_name = "refresh_csgo_game_times"

    try:
        with get_session() as db:
            # Find upcoming matches (next 6 hours)
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(hours=6)

            matches = (
                db.query(CSGOMatch)
                .filter(
                    and_(
                        CSGOMatch.game_start_time <= cutoff,
                        CSGOMatch.game_start_time >= now,
                        CSGOMatch.game_start_override == False,
                        CSGOMatch.gamma_id.isnot(None),
                    )
                )
                .all()
            )

            refreshed = 0
            for match in matches:
                result = enrich_csgo_market(match.gamma_id, db)
                if result:
                    refreshed += 1

            stats = {"refreshed": refreshed, "total_upcoming": len(matches)}

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(f"CS:GO game time refresh complete: {stats}")
        _log_task_run(task_name, self.request.id, True, duration_ms)

        return stats

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"CS:GO game time refresh failed: {error_msg}")
        _log_task_run(task_name, self.request.id, False, duration_ms, error_msg)
        raise self.retry(exc=e)


def _parse_outcome_prices(outcome_prices_str: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Parse outcome prices from Gamma API string format."""
    try:
        import json
        prices = json.loads(outcome_prices_str)
        if len(prices) >= 2:
            yes_price = Decimal(str(prices[0])) if prices[0] else None
            no_price = Decimal(str(prices[1])) if prices[1] else None
            return yes_price, no_price
    except (json.JSONDecodeError, ValueError, IndexError):
        pass
    return None, None


def _update_match_from_clob(
    match: CSGOMatch,
    clob: SyncCLOBClient,
    yes_token_id: str,
    now: datetime
) -> bool:
    """
    Update CSGOMatch prices from CLOB API (orderbook source of truth).

    CLOB API provides real-time prices directly from the orderbook:
    - /midpoint: Current mid price
    - /spread: Current bid-ask spread
    - /book: Full orderbook with depth (for future use)

    Returns True if any changes were made.
    """
    changed = False

    try:
        # Get real-time mid price from CLOB orderbook
        yes_price = clob.get_midpoint(yes_token_id)
        if yes_price is not None:
            match.yes_price = Decimal(str(yes_price))
            match.no_price = Decimal(str(1 - yes_price))
            changed = True

        # Get real-time spread from CLOB orderbook
        spread = clob.get_spread(yes_token_id)
        if spread is not None:
            match.spread = Decimal(str(spread))
            changed = True

    except Exception as e:
        logger.warning(f"[CSGO] CLOB price fetch failed for {yes_token_id[:20]}...: {e}")

    # Always update last_status_check
    match.last_status_check = now

    return changed


def _update_match_lifecycle_from_gamma(match: CSGOMatch, market_data: dict, now: datetime) -> bool:
    """
    Update CSGOMatch lifecycle fields from Gamma API.

    Gamma API is used for:
    - closed/closedTime: Market closed for trading
    - umaResolutionStatus: Market resolved (outcome known)
    - acceptingOrders: Whether orders can be placed
    - volume/liquidity: Aggregate stats (less critical)

    Returns True if any changes were made.
    """
    changed = False

    # Lifecycle fields
    new_closed = market_data.get("closed", False)
    if new_closed and not match.closed:
        match.closed = True
        match.closed_at = GammaClient.parse_datetime(market_data.get("closedTime")) or now
        changed = True
        logger.info(f"[CSGO] Market {match.condition_id[:16]}... closed at {match.closed_at}")

    # Check for resolution via UMA status
    uma_status = market_data.get("umaResolutionStatus", "")
    if uma_status == "resolved" and not match.resolved:
        match.resolved = True
        changed = True

        # Extract outcome from outcomePrices
        outcome_prices = market_data.get("outcomePrices", "[]")
        yes_price, no_price = _parse_outcome_prices(outcome_prices)

        if yes_price is not None and no_price is not None:
            # Winner is the side with price = 1
            outcomes = market_data.get("outcomes", "[]")
            try:
                import json
                outcome_list = json.loads(outcomes)
                if len(outcome_list) >= 2:
                    if yes_price >= Decimal("0.99"):
                        match.outcome = outcome_list[0]  # YES team won
                    elif no_price >= Decimal("0.99"):
                        match.outcome = outcome_list[1]  # NO team won
            except (json.JSONDecodeError, IndexError):
                pass

        logger.info(f"[CSGO] Market {match.condition_id[:16]}... resolved: {match.outcome}")

    # Accepting orders
    new_accepting = market_data.get("acceptingOrders", True)
    if match.accepting_orders != new_accepting:
        match.accepting_orders = new_accepting
        changed = True

    # Volume/liquidity from Gamma (aggregate stats)
    volume = market_data.get("volumeNum")
    if volume is not None:
        match.volume_total = Decimal(str(volume))
        changed = True

    volume_24h = market_data.get("volume24hr")
    if volume_24h is not None:
        match.volume_24h = Decimal(str(volume_24h))
        changed = True

    liquidity = market_data.get("liquidityNum")
    if liquidity is not None:
        match.liquidity = Decimal(str(liquidity))
        changed = True

    return changed


@shared_task(
    name="src.csgo.tasks.refresh_csgo_volume",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def refresh_csgo_volume_task(self):
    """
    Refresh volume/liquidity for upcoming CSGO matches.

    Polls Gamma API for matches starting in the next 12 hours.
    Runs every 5 minutes via Celery beat.
    """
    start = datetime.now(timezone.utc)
    task_name = "refresh_csgo_volume"

    try:
        gamma = SyncGammaClient()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=12)

        with get_session() as db:
            # Find upcoming matches (next 12 hours) that haven't resolved
            matches = (
                db.query(CSGOMatch)
                .filter(
                    and_(
                        CSGOMatch.resolved == False,
                        CSGOMatch.closed == False,
                        CSGOMatch.gamma_id.isnot(None),
                        or_(
                            CSGOMatch.game_start_time.is_(None),
                            CSGOMatch.game_start_time <= cutoff,
                        ),
                    )
                )
                .all()
            )

            updated = 0
            errors = 0

            for match in matches:
                try:
                    market_data = gamma.get_market_by_id(match.gamma_id)
                    if not market_data:
                        errors += 1
                        continue

                    # Update volume fields
                    volume = market_data.get("volumeNum") or market_data.get("volume")
                    if volume is not None:
                        match.volume_total = Decimal(str(volume))

                    volume_24h = market_data.get("volume24hr")
                    if volume_24h is not None:
                        match.volume_24h = Decimal(str(volume_24h))

                    liquidity = market_data.get("liquidityNum") or market_data.get("liquidity")
                    if liquidity is not None:
                        match.liquidity = Decimal(str(liquidity))

                    updated += 1

                except Exception as e:
                    logger.warning(f"[CSGO] Volume refresh failed for match {match.id}: {e}")
                    errors += 1

            db.commit()

            stats = {"updated": updated, "errors": errors, "total": len(matches)}

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(f"CS:GO volume refresh complete: {stats}")
        _log_task_run(task_name, self.request.id, True, duration_ms)

        return stats

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"CS:GO volume refresh failed: {error_msg}")
        _log_task_run(task_name, self.request.id, False, duration_ms, error_msg)
        raise self.retry(exc=e)


@shared_task(
    name="src.csgo.tasks.poll_csgo_market_status",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def poll_csgo_market_status_task(self):
    """
    Poll CLOB + Gamma APIs for market status of active CSGO matches.

    Data sources:
    - CLOB API: Prices and spread (real-time from orderbook)
    - Gamma API: Lifecycle (closed, resolved, accepting_orders)

    Runs every 5 seconds for near real-time prices.

    Targets:
    - All subscribed, unresolved matches
    """
    start = datetime.now(timezone.utc)
    task_name = "poll_csgo_market_status"

    try:
        clob = SyncCLOBClient()
        gamma = SyncGammaClient()
        now = datetime.now(timezone.utc)

        with get_session() as db:
            # Find matches that need status polling
            # Simple rule: poll all subscribed, unresolved matches
            matches = (
                db.query(CSGOMatch)
                .filter(
                    and_(
                        CSGOMatch.resolved == False,
                        CSGOMatch.subscribed == True,
                    )
                )
                .all()
            )

            stats = {
                "checked": 0,
                "updated": 0,
                "closed": 0,
                "resolved": 0,
                "clob_errors": 0,
                "gamma_errors": 0,
            }

            for match in matches:
                try:
                    # Get YES token ID from markets table for CLOB lookup
                    market = db.query(Market).filter(
                        Market.condition_id == match.condition_id
                    ).first()

                    if not market or not market.yes_token_id:
                        logger.warning(f"[CSGO] No token_id for match {match.id}")
                        stats["clob_errors"] += 1
                        continue

                    # === CLOB API: Prices (real-time from orderbook) ===
                    price_changed = _update_match_from_clob(
                        match, clob, market.yes_token_id, now
                    )

                    stats["checked"] += 1

                    # === Gamma API: Lifecycle (closed, resolved) ===
                    was_closed = match.closed
                    was_resolved = match.resolved

                    if match.gamma_id:
                        market_data = gamma.get_market_by_id(match.gamma_id)
                        if market_data:
                            lifecycle_changed = _update_match_lifecycle_from_gamma(
                                match, market_data, now
                            )
                        else:
                            stats["gamma_errors"] += 1
                            lifecycle_changed = False
                    else:
                        lifecycle_changed = False

                    changed = price_changed or lifecycle_changed

                    if changed:
                        stats["updated"] += 1

                    if match.closed and not was_closed:
                        stats["closed"] += 1
                        # Auto-unsubscribe closed markets
                        match.subscribed = False

                    if match.resolved and not was_resolved:
                        stats["resolved"] += 1
                        # Auto-unsubscribe resolved markets
                        match.subscribed = False

                        # Auto-close all open positions on this market
                        if market_data:
                            outcome_prices = market_data.get("outcomePrices", "[]")
                            yes_price, no_price = _parse_outcome_prices(outcome_prices)
                            if yes_price is not None and no_price is not None:
                                closed_count = _close_positions_on_resolution(
                                    db, match, yes_price, no_price
                                )
                                if closed_count > 0:
                                    logger.info(
                                        f"[CSGO] Auto-closed {closed_count} positions on "
                                        f"{match.team_yes} vs {match.team_no} resolution"
                                    )

                except Exception as e:
                    logger.error(f"[CSGO] Error polling match {match.id}: {e}")
                    stats["clob_errors"] += 1

            db.commit()

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(f"CS:GO market status poll complete: {stats}")
        _log_task_run(task_name, self.request.id, True, duration_ms)

        return stats

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"CS:GO market status poll failed: {error_msg}")
        _log_task_run(task_name, self.request.id, False, duration_ms, error_msg)
        raise self.retry(exc=e)


@shared_task(
    name="src.csgo.tasks.cleanup_csgo_price_ticks",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def cleanup_csgo_price_ticks_task(self, retention_days: int = 7):
    """
    Clean up old csgo_price_ticks records.

    Deletes ticks older than retention_days to prevent table bloat.
    Default: 7 days retention (sufficient for strategy backtesting/charts).

    Runs daily at 4:00 AM UTC via Celery beat.
    """
    from src.db.models import CSGOPriceTick

    start = datetime.now(timezone.utc)
    task_name = "cleanup_csgo_price_ticks"

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        with get_session() as db:
            # Delete in batches to avoid long-running transactions
            deleted_total = 0
            batch_size = 10000

            while True:
                # Get IDs of old ticks in this batch
                old_tick_ids = (
                    db.query(CSGOPriceTick.id)
                    .filter(CSGOPriceTick.timestamp < cutoff)
                    .limit(batch_size)
                    .all()
                )

                if not old_tick_ids:
                    break

                ids_to_delete = [t.id for t in old_tick_ids]
                deleted = db.query(CSGOPriceTick).filter(
                    CSGOPriceTick.id.in_(ids_to_delete)
                ).delete(synchronize_session=False)

                db.commit()
                deleted_total += deleted

                if deleted < batch_size:
                    break

        stats = {"deleted": deleted_total, "retention_days": retention_days}

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(f"CS:GO price tick cleanup complete: {stats}")
        _log_task_run(task_name, self.request.id, True, duration_ms)

        return stats

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"CS:GO price tick cleanup failed: {error_msg}")
        _log_task_run(task_name, self.request.id, False, duration_ms, error_msg)
        raise self.retry(exc=e)
