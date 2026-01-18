"""
GRID Integration API Routes.

Provides endpoints for viewing GRID event data and matched markets.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import CSGOGridEvent, CSGOMatch, GRIDPollerState, Market

router = APIRouter(prefix="/grid", tags=["GRID Integration"])


def serialize_decimal(val) -> Optional[float]:
    """Convert Decimal to float for JSON serialization."""
    if val is None:
        return None
    return float(val)


@router.get("/stats")
async def get_grid_stats(db: Session = Depends(get_db)):
    """
    Summary statistics for GRID integration.

    Returns event counts, polling status, and match linking stats.
    """
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)

    # Count events in last 24 hours
    events_24h = db.execute(
        select(func.count()).select_from(CSGOGridEvent).where(
            CSGOGridEvent.detected_at >= last_24h
        )
    ).scalar() or 0

    # Count active poller states (polled in last hour)
    last_hour = now - timedelta(hours=1)
    series_polling = db.execute(
        select(func.count()).select_from(GRIDPollerState).where(
            GRIDPollerState.last_poll_at >= last_hour
        )
    ).scalar() or 0

    # Get last poll timestamp
    last_poll = db.execute(
        select(GRIDPollerState.last_poll_at)
        .order_by(desc(GRIDPollerState.last_poll_at))
        .limit(1)
    ).scalar()

    # Count matched markets
    matches_linked = db.execute(
        select(func.count(func.distinct(CSGOMatch.grid_series_id)))
        .where(CSGOMatch.grid_series_id.isnot(None))
    ).scalar() or 0

    # Count total CSGOMatch records with GRID link
    markets_linked = db.execute(
        select(func.count()).select_from(CSGOMatch).where(
            CSGOMatch.grid_series_id.isnot(None)
        )
    ).scalar() or 0

    return {
        "events_24h": events_24h,
        "series_polling": series_polling,
        "last_poll_at": last_poll.isoformat() if last_poll else None,
        "matches_linked": matches_linked,
        "markets_linked": markets_linked,
    }


@router.get("/events")
async def get_grid_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None, description="Filter by event type: round, map, series"),
    db: Session = Depends(get_db),
):
    """
    List recent GRID events with price data.

    Returns paginated list of game state change events with prices
    at detection and after delays.
    """
    # Build query
    query = select(CSGOGridEvent)

    if event_type:
        query = query.where(CSGOGridEvent.event_type == event_type)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar() or 0

    # Get paginated results
    query = query.order_by(desc(CSGOGridEvent.detected_at)).offset(offset).limit(limit)
    events = db.execute(query).scalars().all()

    # Get market info for team names
    market_ids = [e.market_id for e in events]
    markets = {}
    if market_ids:
        market_query = select(Market).where(Market.id.in_(market_ids))
        for m in db.execute(market_query).scalars().all():
            markets[m.id] = m

        # Also get CSGOMatch for team names
        csgo_matches = {}
        csgo_query = select(CSGOMatch).where(CSGOMatch.market_id.in_(market_ids))
        for cm in db.execute(csgo_query).scalars().all():
            csgo_matches[cm.market_id] = cm
    else:
        csgo_matches = {}

    items = []
    for e in events:
        csgo = csgo_matches.get(e.market_id)
        items.append({
            "id": e.id,
            "market_id": e.market_id,
            "grid_series_id": e.grid_series_id,
            "event_type": e.event_type,
            "winner": e.winner,
            "detected_at": e.detected_at.isoformat(),
            "map_name": e.map_name,
            "map_number": e.map_number,
            "format": e.format,
            "is_overtime": e.is_overtime,
            "rounds_in_event": e.rounds_in_event,
            # Scores
            "prev_round_yes": e.prev_round_yes,
            "prev_round_no": e.prev_round_no,
            "new_round_yes": e.new_round_yes,
            "new_round_no": e.new_round_no,
            "prev_map_yes": e.prev_map_yes,
            "prev_map_no": e.prev_map_no,
            "new_map_yes": e.new_map_yes,
            "new_map_no": e.new_map_no,
            # Prices
            "price_at_detection": serialize_decimal(e.price_at_detection),
            "spread_at_detection": serialize_decimal(e.spread_at_detection),
            "price_after_30sec": serialize_decimal(e.price_after_30sec),
            "price_after_1min": serialize_decimal(e.price_after_1min),
            "price_after_5min": serialize_decimal(e.price_after_5min),
            "price_move_30sec": serialize_decimal(e.price_move_30sec),
            "price_move_1min": serialize_decimal(e.price_move_1min),
            "price_move_5min": serialize_decimal(e.price_move_5min),
            "move_direction_correct": e.move_direction_correct,
            # Team info from CSGOMatch
            "team_yes": csgo.team_yes if csgo else None,
            "team_no": csgo.team_no if csgo else None,
        })

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@router.get("/matches")
async def get_grid_matches(
    include_closed: bool = Query(False, description="Include closed matches"),
    db: Session = Depends(get_db),
):
    """
    List Polymarket markets linked to GRID series.

    Returns CSGOMatch records that have been matched to GRID series,
    deduplicated by grid_series_id.
    """
    query = (
        select(CSGOMatch)
        .where(CSGOMatch.grid_series_id.isnot(None))
    )

    if not include_closed:
        query = query.where(CSGOMatch.closed == False)

    query = query.order_by(desc(CSGOMatch.game_start_time))

    matches = db.execute(query).scalars().all()

    # Deduplicate by grid_series_id (keep first occurrence)
    seen_series = set()
    items = []
    for m in matches:
        if m.grid_series_id in seen_series:
            continue
        seen_series.add(m.grid_series_id)

        items.append({
            "id": m.id,
            "market_id": m.market_id,
            "team_yes": m.team_yes,
            "team_no": m.team_no,
            "grid_series_id": m.grid_series_id,
            "grid_yes_team_id": m.grid_yes_team_id,
            "grid_match_confidence": float(m.grid_match_confidence) if m.grid_match_confidence else None,
            "game_start_time": m.game_start_time.isoformat() if m.game_start_time else None,
            "format": m.format,
            "market_type": m.market_type,
            "closed": m.closed,
            "resolved": m.resolved,
        })

    return {
        "total": len(items),
        "items": items,
    }


@router.get("/poller-state")
async def get_grid_poller_state(db: Session = Depends(get_db)):
    """
    Current polling state for each series.

    Returns the last known state for each series being polled,
    useful for debugging and monitoring.
    """
    query = (
        select(GRIDPollerState)
        .order_by(desc(GRIDPollerState.last_poll_at))
    )

    states = db.execute(query).scalars().all()

    # Get CSGOMatch info for team names
    market_ids = [s.market_id for s in states]
    csgo_matches = {}
    if market_ids:
        csgo_query = select(CSGOMatch).where(CSGOMatch.market_id.in_(market_ids))
        for cm in db.execute(csgo_query).scalars().all():
            csgo_matches[cm.market_id] = cm

    items = []
    for s in states:
        csgo = csgo_matches.get(s.market_id)
        state_json = s.last_state_json or {}

        items.append({
            "series_id": s.series_id,
            "market_id": s.market_id,
            "team_yes": csgo.team_yes if csgo else None,
            "team_no": csgo.team_no if csgo else None,
            "last_poll_at": s.last_poll_at.isoformat() if s.last_poll_at else None,
            "polls_count": s.polls_count,
            # Extract key state info
            "format": state_json.get("format"),
            "finished": state_json.get("finished"),
            "yes_maps": state_json.get("yes_maps"),
            "no_maps": state_json.get("no_maps"),
            "games": state_json.get("games", []),
        })

    return {
        "total": len(items),
        "items": items,
    }
