"""
CS:GO Market Discovery.

Finds CS:GO markets in the database and syncs them to csgo_matches table.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.db.models import Market, CSGOMatch

logger = logging.getLogger(__name__)


def discover_csgo_markets(db: Session) -> list[Market]:
    """
    Find all active CS:GO match markets in the database.

    Filters:
    - category_l1 = 'ESPORTS'
    - category_l2 = 'CSGO'
    - question OR event_title contains 'vs' (match format)
    - Excludes misclassified markets (Call of Duty, HLTV awards, skins)

    This captures:
    - Moneyline: "Team A vs Team B (BO3)"
    - Map winners: "Team A vs Team B - Map 1 Winner"
    - O/U totals: "Games Total: O/U 2.5" (linked via event_title)

    Returns:
        List of Market objects that are CS:GO matches
    """
    query = (
        select(Market)
        .where(
            and_(
                Market.category_l1 == "ESPORTS",
                Market.category_l2 == "CSGO",
                # Match markets have 'vs' in question OR event_title (for O/U markets)
                or_(
                    Market.question.ilike("%vs%"),
                    Market.event_title.ilike("%vs%"),
                ),
                # Exclude misclassified markets (check both question and event_title)
                ~Market.question.ilike("%call of duty%"),
                ~Market.event_title.ilike("%call of duty%"),
                ~Market.question.ilike("%hltv%"),
                ~Market.question.ilike("%skin%"),
                Market.active == True,
            )
        )
        .order_by(Market.end_date.asc())
    )

    markets = db.execute(query).scalars().all()
    logger.info(f"Discovered {len(markets)} CS:GO markets")
    return list(markets)


def sync_csgo_matches(db: Session) -> dict:
    """
    Sync discovered CS:GO markets to the csgo_matches table.

    - Adds new markets not yet in csgo_matches
    - Returns stats on new/existing markets

    Returns:
        Dict with counts: {"new": N, "existing": M, "total": N+M}
    """
    markets = discover_csgo_markets(db)

    # Get existing condition_ids in csgo_matches
    existing_query = select(CSGOMatch.condition_id)
    existing_ids = set(db.execute(existing_query).scalars().all())

    new_count = 0
    for market in markets:
        if market.condition_id not in existing_ids:
            # Create new CSGOMatch entry (will be enriched later)
            match = CSGOMatch(
                market_id=market.id,
                gamma_id=market.gamma_id,
                condition_id=market.condition_id,
                end_date=market.end_date,
            )
            db.add(match)
            new_count += 1

    db.commit()

    stats = {
        "new": new_count,
        "existing": len(existing_ids),
        "total": len(markets),
    }
    logger.info(f"Synced CS:GO matches: {stats}")
    return stats


def get_csgo_matches(
    db: Session,
    upcoming_only: bool = False,
    hours_ahead: float = 6.0,
    include_enriched_only: bool = False,
) -> list[CSGOMatch]:
    """
    Get CS:GO matches from the csgo_matches table.

    Args:
        db: Database session
        upcoming_only: If True, only return matches starting within hours_ahead
        hours_ahead: Hours ahead to consider for upcoming matches
        include_enriched_only: If True, only return matches with team data

    Returns:
        List of CSGOMatch objects
    """
    query = select(CSGOMatch)

    conditions = []

    if upcoming_only:
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff = now + timedelta(hours=hours_ahead)
        conditions.append(CSGOMatch.game_start_time <= cutoff)
        conditions.append(CSGOMatch.game_start_time >= now)

    if include_enriched_only:
        conditions.append(CSGOMatch.team_yes.isnot(None))
        conditions.append(CSGOMatch.team_no.isnot(None))

    if conditions:
        query = query.where(and_(*conditions))

    query = query.order_by(CSGOMatch.game_start_time.asc())

    matches = db.execute(query).scalars().all()
    return list(matches)


def get_matches_for_subscription(db: Session, hours_ahead: float = 6.0) -> list[CSGOMatch]:
    """
    Get CS:GO matches that should be subscribed to WebSocket.

    CRITICAL: This function must NEVER exclude in-progress matches.
    The subscription bug that caused matches to disappear was caused by
    a filter that excluded matches after their start time.

    Criteria:
    - Upcoming: game_start_time within hours_ahead from now
    - In-play: game started less than 4 hours ago (BO3 matches can last 3h+)
    - Not resolved (game outcome not yet known)
    - Has gamma_id for API lookup

    Safety: Even if this function returns wrong results, the websocket.py
    will only unsubscribe matches that are resolved/closed, preventing
    mass-unsubscription bugs.

    Returns:
        List of CSGOMatch objects to subscribe
    """
    now = datetime.now(timezone.utc)
    from datetime import timedelta

    # Upcoming window: matches starting in next N hours
    upcoming_cutoff = now + timedelta(hours=hours_ahead)

    # In-play window: matches that started up to 6 hours ago
    # BO5 matches can last 5+ hours (5 maps × 60+ min each)
    in_play_cutoff = now - timedelta(hours=6)

    query = (
        select(CSGOMatch)
        .where(
            and_(
                # Include upcoming OR in-play matches
                CSGOMatch.game_start_time <= upcoming_cutoff,  # Not too far in future
                CSGOMatch.game_start_time >= in_play_cutoff,   # Not too far in past
                # Must not be finished
                CSGOMatch.resolved == False,
                # Must have API ID for data fetching
                CSGOMatch.gamma_id.isnot(None),
            )
        )
        .order_by(CSGOMatch.game_start_time.asc())
    )

    matches = db.execute(query).scalars().all()

    # Safety check: log a warning if we're excluding any subscribed, unresolved matches
    # This catches potential bugs in the filtering logic
    subscribed_unresolved = (
        db.execute(
            select(CSGOMatch).where(
                and_(
                    CSGOMatch.subscribed == True,
                    CSGOMatch.resolved == False,
                    CSGOMatch.closed == False,
                )
            )
        )
        .scalars()
        .all()
    )

    match_ids = {m.id for m in matches}
    excluded_active = [m for m in subscribed_unresolved if m.id not in match_ids]

    if excluded_active:
        # This should never happen - log loudly if it does
        logger.warning(
            f"SAFETY CHECK: {len(excluded_active)} subscribed unresolved matches "
            f"would be excluded by filter! IDs: {[m.id for m in excluded_active[:5]]}"
        )

    logger.info(f"Found {len(matches)} matches for WebSocket subscription")
    return list(matches)
