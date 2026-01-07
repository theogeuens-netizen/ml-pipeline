"""
CS:GO Strategy API endpoints.

Provides endpoints for:
- Team leaderboard
- Head-to-head records
- CS:GO positions with hedge status
- Active CS:GO markets meeting entry criteria
- Strategy performance metrics
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, desc, and_
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import CSGOTeam, CSGOH2H, CSGOMatch, Market, Snapshot
from src.executor.models import Position, PositionStatus
from src.services.csgo_team_matcher import CSGOTeamMatcher
from src.csgo.engine.models import (
    CSGOPosition, CSGOSpread, CSGOTrade, CSGOPositionLeg,
    CSGOPositionStatus, CSGOSpreadStatus, CSGOLegType
)

# Legacy: Old strategy prefixes for filtering executor positions
CSGO_STRATEGY_PREFIXES = ["csgo_"]

router = APIRouter(prefix="/csgo")


@router.get("/teams")
async def get_team_leaderboard(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("win_rate_pct", enum=["win_rate_pct", "total_matches", "team_name", "wins"]),
    order: str = Query("desc", enum=["asc", "desc"]),
    min_matches: int = Query(0, ge=0, description="Minimum total matches"),
    db: Session = Depends(get_db),
):
    """
    Get CS:GO team leaderboard with win rates.

    Returns team statistics sorted by win rate (default).
    """
    query = select(CSGOTeam)

    # Filter by minimum matches
    if min_matches > 0:
        query = query.where(CSGOTeam.total_matches >= min_matches)

    # Sort
    sort_column = getattr(CSGOTeam, sort_by)
    if order == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Paginate
    query = query.offset(offset).limit(limit)
    teams = db.execute(query).scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "teams": [
            {
                "team_name": t.team_name,
                "wins": t.wins,
                "losses": t.losses,
                "total_matches": t.total_matches,
                "win_rate_pct": float(t.win_rate_pct),
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in teams
        ],
    }


@router.get("/teams/{team_name}")
async def get_team_details(
    team_name: str,
    db: Session = Depends(get_db),
):
    """
    Get detailed stats for a specific team.

    Includes:
    - Basic stats from csgo_teams
    - Head-to-head records from csgo_h2h
    """
    # Get team
    team = db.execute(
        select(CSGOTeam).where(CSGOTeam.team_name == team_name)
    ).scalar()

    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_name}' not found")

    # Get H2H records (team can be in either position)
    h2h_query = select(CSGOH2H).where(
        (CSGOH2H.team1_name == team_name) | (CSGOH2H.team2_name == team_name)
    ).order_by(desc(CSGOH2H.total_matches))

    h2h_records = db.execute(h2h_query).scalars().all()

    # Format H2H with opponent perspective
    h2h_list = []
    for record in h2h_records:
        if record.team1_name == team_name:
            h2h_list.append({
                "opponent": record.team2_name,
                "wins": record.team1_wins,
                "losses": record.team2_wins,
                "total": record.total_matches,
            })
        else:
            h2h_list.append({
                "opponent": record.team1_name,
                "wins": record.team2_wins,
                "losses": record.team1_wins,
                "total": record.total_matches,
            })

    return {
        "team_name": team.team_name,
        "wins": team.wins,
        "losses": team.losses,
        "total_matches": team.total_matches,
        "win_rate_pct": float(team.win_rate_pct),
        "updated_at": team.updated_at.isoformat() if team.updated_at else None,
        "h2h_records": h2h_list,
    }


@router.get("/h2h")
async def get_head_to_head(
    team1: str = Query(..., description="First team name"),
    team2: str = Query(..., description="Second team name"),
    db: Session = Depends(get_db),
):
    """
    Get head-to-head record between two teams.
    """
    # Normalize order (team1 < team2 alphabetically in database)
    t1, t2 = (team1, team2) if team1 < team2 else (team2, team1)

    record = db.execute(
        select(CSGOH2H).where(
            and_(CSGOH2H.team1_name == t1, CSGOH2H.team2_name == t2)
        )
    ).scalar()

    if not record:
        return {
            "team1": team1,
            "team2": team2,
            "team1_wins": 0,
            "team2_wins": 0,
            "total_matches": 0,
            "found": False,
        }

    # Return in requested order
    if team1 < team2:
        return {
            "team1": team1,
            "team2": team2,
            "team1_wins": record.team1_wins,
            "team2_wins": record.team2_wins,
            "total_matches": record.total_matches,
            "found": True,
        }
    else:
        return {
            "team1": team1,
            "team2": team2,
            "team1_wins": record.team2_wins,
            "team2_wins": record.team1_wins,
            "total_matches": record.total_matches,
            "found": True,
        }


@router.get("/positions")
async def get_csgo_positions(
    status: Optional[str] = Query(None, enum=["open", "hedged", "closed"]),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get CS:GO strategy positions with hedge status.

    Returns positions enriched with:
    - team_yes, team_no: teams from csgo_matches
    - bet_on_team: which team we bet on (YES or NO side)
    - current prices for both sides
    - correct P&L calculation
    """
    # Get all market_ids from csgo_matches
    csgo_market_ids = db.execute(
        select(CSGOMatch.market_id).where(CSGOMatch.market_id.isnot(None))
    ).scalars().all()

    # Filter positions by CSGO market_ids
    query = select(Position).where(Position.market_id.in_(csgo_market_ids))

    # Status filter
    if status:
        if status == "open":
            query = query.where(Position.status == PositionStatus.OPEN.value)
        elif status == "hedged":
            query = query.where(Position.status == PositionStatus.HEDGED.value)
        elif status == "closed":
            query = query.where(Position.status == PositionStatus.CLOSED.value)

    # Order by most recent first
    query = query.order_by(desc(Position.created_at))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Paginate
    query = query.offset(offset).limit(limit)
    positions = db.execute(query).scalars().all()

    # Enrich with market data
    result = []

    for p in positions:
        # Get market with token IDs
        market = db.execute(
            select(Market).where(Market.id == p.market_id)
        ).scalar() if p.market_id else None

        # Get csgo_match for team names
        csgo_match = db.execute(
            select(CSGOMatch).where(CSGOMatch.market_id == p.market_id)
        ).scalar() if p.market_id else None

        # Determine which team we bet on by matching token_id
        bet_on_team = None
        bet_on_side = None  # YES or NO
        if market and p.token_id:
            if p.token_id == market.yes_token_id:
                bet_on_side = "YES"
                bet_on_team = csgo_match.team_yes if csgo_match else None
            elif p.token_id == market.no_token_id:
                bet_on_side = "NO"
                bet_on_team = csgo_match.team_no if csgo_match else None

        # Get current snapshot for price
        snapshot = None
        if p.market_id:
            snapshot = db.execute(
                select(Snapshot)
                .where(Snapshot.market_id == p.market_id)
                .order_by(desc(Snapshot.timestamp))
            ).scalars().first()

        # Current prices (YES price from snapshot, NO = 1 - YES)
        yes_price = float(snapshot.price) if snapshot and snapshot.price else None
        no_price = (1 - yes_price) if yes_price is not None else None

        # Current price of our position's token
        if bet_on_side == "YES":
            current_token_price = yes_price
        elif bet_on_side == "NO":
            current_token_price = no_price
        else:
            current_token_price = None

        # Calculate correct P&L: (current_price - entry_price) * shares
        size_shares = float(p.size_shares) if p.size_shares else 0
        entry_price = float(p.entry_price) if p.entry_price else 0
        if current_token_price is not None and size_shares > 0:
            unrealized_pnl = (current_token_price - entry_price) * size_shares
        else:
            unrealized_pnl = float(p.unrealized_pnl) if p.unrealized_pnl else 0

        result.append({
            "id": p.id,
            "strategy_name": p.strategy_name,
            "market_id": p.market_id,
            "market_question": market.question if market else None,
            # Team info from csgo_matches
            "team_yes": csgo_match.team_yes if csgo_match else None,
            "team_no": csgo_match.team_no if csgo_match else None,
            "bet_on_team": bet_on_team,
            "bet_on_side": bet_on_side,
            # Match detail from csgo_matches
            "market_type": csgo_match.market_type if csgo_match else None,
            "format": csgo_match.format if csgo_match else None,
            "group_item_title": csgo_match.group_item_title if csgo_match else None,
            "tournament": csgo_match.tournament if csgo_match else None,
            "game_start_time": csgo_match.game_start_time.isoformat() if csgo_match and csgo_match.game_start_time else None,
            # Prices
            "entry_price": entry_price,
            "current_token_price": current_token_price,
            "yes_price": yes_price,
            "no_price": no_price,
            # Position details
            "token_id": p.token_id,
            "side": p.side,
            "entry_time": p.entry_time.isoformat() if p.entry_time else None,
            "size_shares": size_shares,
            "cost_basis": float(p.cost_basis) if p.cost_basis else 0,
            "current_value": current_token_price * size_shares if current_token_price else None,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": float(p.realized_pnl) if p.realized_pnl else 0,
            "status": p.status,
            "close_reason": p.close_reason,
            "is_hedge": p.is_hedge,
            "hedge_position_id": p.hedge_position_id,
            "exit_price": float(p.exit_price) if p.exit_price else None,
            "exit_time": p.exit_time.isoformat() if p.exit_time else None,
        })

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "positions": result,
    }


@router.get("/markets/active")
async def get_active_csgo_markets(
    hours_ahead: float = Query(2.0, description="Markets starting within N hours"),
    min_favorite_price: float = Query(0.65),
    max_favorite_price: float = Query(0.80),
    db: Session = Depends(get_db),
):
    """
    Get active CS:GO markets meeting entry criteria.

    Returns markets with:
    - Team names
    - Win rate differential
    - Signal strength (none, base, strong, very_strong)
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)

    # Get active CS:GO markets
    query = select(Market).where(
        and_(
            Market.active == True,
            Market.end_date != None,
            Market.end_date <= cutoff,
            Market.end_date > now,
        )
    )

    markets = db.execute(query).scalars().all()

    # Filter to CS:GO and analyze
    matcher = CSGOTeamMatcher(db)
    opportunities = []

    for market in markets:
        # Check if CS:GO
        if not matcher.is_csgo_market(market.question):
            continue

        # Get latest snapshot
        snapshot = db.execute(
            select(Snapshot)
            .where(Snapshot.market_id == market.id)
            .order_by(desc(Snapshot.timestamp))
        ).scalars().first()

        if not snapshot:
            continue

        # Get teams and win rate diff
        result = matcher.get_winrate_diff(market.question)
        if not result:
            continue

        favorite_side, winrate_diff, favorite_winrate = result
        parsed = matcher.parse_and_match(market.question)

        # Calculate favorite price
        current_price = float(snapshot.price)
        if favorite_side == "A":
            favorite_price = current_price
        else:
            favorite_price = 1 - current_price

        # Check price range
        if favorite_price < min_favorite_price or favorite_price > max_favorite_price:
            continue

        # Determine signal strength
        if winrate_diff >= 0.25:
            signal_strength = "very_strong"
        elif winrate_diff >= 0.15:
            signal_strength = "strong"
        elif winrate_diff > 0:
            signal_strength = "base"
        else:
            signal_strength = "none"

        hours_to_close = (market.end_date - now).total_seconds() / 3600 if market.end_date else None

        opportunities.append({
            "market_id": market.id,
            "question": market.question,
            "team_a": parsed.team_a if parsed else None,
            "team_b": parsed.team_b if parsed else None,
            "team_a_winrate": parsed.team_a_stats.win_rate if parsed and parsed.team_a_stats else None,
            "team_b_winrate": parsed.team_b_stats.win_rate if parsed and parsed.team_b_stats else None,
            "favorite_side": favorite_side,
            "favorite_price": favorite_price,
            "winrate_diff": winrate_diff,
            "signal_strength": signal_strength,
            "hours_to_close": hours_to_close,
            "best_bid": float(snapshot.best_bid) if snapshot.best_bid else None,
            "best_ask": float(snapshot.best_ask) if snapshot.best_ask else None,
        })

    # Sort by signal strength and win rate diff
    opportunities.sort(key=lambda x: (-["none", "base", "strong", "very_strong"].index(x["signal_strength"]), -x["winrate_diff"]))

    return {
        "total": len(opportunities),
        "hours_ahead": hours_ahead,
        "opportunities": opportunities,
    }


@router.get("/matches")
async def get_all_csgo_matches(
    db: Session = Depends(get_db),
):
    """
    Get ALL active CS:GO matches in the scanner.

    Returns all CS:GO markets with:
    - Team names and win rates
    - Current prices
    - Hours to close
    - meets_criteria: whether it meets entry conditions
    - Criteria breakdown for each condition
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    # Get all active markets
    query = select(Market).where(
        and_(
            Market.active == True,
            Market.end_date != None,
            Market.end_date > now,
        )
    ).order_by(Market.end_date)

    markets = db.execute(query).scalars().all()

    # Filter to CS:GO and analyze
    matcher = CSGOTeamMatcher(db)
    matches = []

    # Entry criteria thresholds
    MIN_PRICE = 0.65
    MAX_PRICE = 0.80
    MAX_HOURS = 12.0

    for market in markets:
        # Check if CS:GO
        if not matcher.is_csgo_market(market.question):
            continue

        # Get latest snapshot
        snapshot = db.execute(
            select(Snapshot)
            .where(Snapshot.market_id == market.id)
            .order_by(desc(Snapshot.timestamp))
        ).scalars().first()

        current_price = float(snapshot.price) if snapshot and snapshot.price else None
        best_bid = float(snapshot.best_bid) if snapshot and snapshot.best_bid else None
        best_ask = float(snapshot.best_ask) if snapshot and snapshot.best_ask else None

        # Calculate hours to close
        hours_to_close = (market.end_date - now).total_seconds() / 3600 if market.end_date else None

        # Get teams and win rate diff
        result = matcher.get_winrate_diff(market.question)
        parsed = matcher.parse_and_match(market.question)

        team_a = parsed.team_a if parsed else None
        team_b = parsed.team_b if parsed else None
        team_a_winrate = parsed.team_a_stats.win_rate if parsed and parsed.team_a_stats else None
        team_b_winrate = parsed.team_b_stats.win_rate if parsed and parsed.team_b_stats else None

        # Default values
        favorite_side = None
        favorite_price = None
        winrate_diff = None
        signal_strength = "none"
        size_usd = None

        if result:
            favorite_side, winrate_diff, _ = result

            # Calculate favorite price
            if current_price is not None:
                if favorite_side == "A":
                    favorite_price = current_price
                else:
                    favorite_price = 1 - current_price

            # Determine signal strength and size
            if winrate_diff >= 0.25:
                signal_strength = "very_strong"
                size_usd = 20.0
            elif winrate_diff >= 0.15:
                signal_strength = "strong"
                size_usd = 15.0
            elif winrate_diff > 0:
                signal_strength = "base"
                size_usd = 10.0

        # Check each criterion
        has_teams = team_a is not None and team_b is not None
        has_winrates = team_a_winrate is not None and team_b_winrate is not None
        price_in_range = favorite_price is not None and MIN_PRICE <= favorite_price <= MAX_PRICE
        time_in_range = hours_to_close is not None and hours_to_close <= MAX_HOURS
        has_edge = winrate_diff is not None and winrate_diff > 0

        # Overall criteria check
        meets_criteria = has_teams and has_winrates and price_in_range and time_in_range and has_edge

        matches.append({
            "market_id": market.id,
            "question": market.question,
            "team_a": team_a,
            "team_b": team_b,
            "team_a_winrate": team_a_winrate,
            "team_b_winrate": team_b_winrate,
            "favorite_side": favorite_side,
            "favorite_price": favorite_price,
            "current_price": current_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "winrate_diff": winrate_diff,
            "signal_strength": signal_strength,
            "size_usd": size_usd,
            "hours_to_close": hours_to_close,
            "end_date": market.end_date.isoformat() if market.end_date else None,
            # Criteria breakdown
            "meets_criteria": meets_criteria,
            "criteria": {
                "has_teams": has_teams,
                "has_winrates": has_winrates,
                "price_in_range": price_in_range,
                "time_in_range": time_in_range,
                "has_edge": has_edge,
            },
        })

    # Sort by hours to close (soonest first)
    matches.sort(key=lambda x: x["hours_to_close"] if x["hours_to_close"] is not None else 9999)

    return {
        "total": len(matches),
        "meeting_criteria": sum(1 for m in matches if m["meets_criteria"]),
        "matches": matches,
    }


@router.get("/performance")
async def get_csgo_performance(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """
    Get CS:GO strategy performance metrics.

    Returns:
    - Total trades, win rate, P&L
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get all market_ids from csgo_matches
    csgo_market_ids = db.execute(
        select(CSGOMatch.market_id).where(CSGOMatch.market_id.isnot(None))
    ).scalars().all()

    # Filter positions by CSGO market_ids and time
    positions = db.execute(
        select(Position).where(
            and_(
                Position.market_id.in_(csgo_market_ids),
                Position.created_at >= cutoff,
            )
        )
    ).scalars().all()

    # Calculate metrics
    open_positions = [p for p in positions if p.status == PositionStatus.OPEN.value]
    closed_positions = [p for p in positions if p.status == PositionStatus.CLOSED.value]
    wins = sum(1 for p in closed_positions if float(p.realized_pnl or 0) > 0)
    losses = sum(1 for p in closed_positions if float(p.realized_pnl or 0) < 0)
    win_rate = wins / len(closed_positions) if closed_positions else 0

    total_realized = sum(float(p.realized_pnl or 0) for p in closed_positions)
    total_unrealized = sum(float(p.unrealized_pnl or 0) for p in open_positions)

    return {
        "days": days,
        "summary": {
            "total": len(positions),
            "open": len(open_positions),
            "hedged": 0,  # Will be implemented with new strategies
            "stopped": 0,
            "resolved": len(closed_positions),
            "total_pnl": total_realized + total_unrealized,
            "unrealized_pnl": total_unrealized,
            "realized_pnl": total_realized,
        },
        "period_stats": {
            "total_positions": len(positions),
            "closed_positions": len(closed_positions),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_realized_pnl": total_realized,
        },
    }


@router.post("/refresh-data")
async def refresh_team_data(
    db: Session = Depends(get_db),
):
    """
    Trigger refresh of CS:GO team data from CSVs.

    This runs the import script to update team stats.
    """
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "import_csgo_data.py"

    if not script_path.exists():
        raise HTTPException(status_code=500, detail="Import script not found")

    try:
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr,
            }

        return {
            "success": True,
            "output": result.stdout,
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Import script timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-polymarket")
async def sync_from_polymarket(
    db: Session = Depends(get_db),
):
    """
    Update team win rates from resolved CS:GO matches in polymarket-ml database.

    This parses resolved BO3 matches and updates the csgo_teams table with
    new wins/losses.
    """
    matcher = CSGOTeamMatcher(db)
    result = matcher.update_winrates_from_polymarket()

    return {
        "success": len(result.get("errors", [])) == 0,
        "resolved_matches": result.get("resolved_matches", 0),
        "parsed": result.get("parsed", 0),
        "updated_teams": result.get("updated_teams", 0),
        "new_teams": result.get("new_teams", 0),
        "skipped": result.get("skipped", 0),
        "errors": result.get("errors", []),
    }


# ============================================================================
# CS:GO Pipeline Endpoints (csgo_matches table)
# ============================================================================


@router.get("/pipeline/events")
async def get_pipeline_events(
    upcoming_only: bool = Query(False, description="Only show upcoming events"),
    hours_ahead: float = Query(24.0, description="Hours ahead for upcoming filter"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Get CS:GO events with nested markets.

    Groups markets by event (same team matchup + game start time).
    Each event contains: moneyline, map winners, totals, etc.
    """
    from datetime import timedelta
    from collections import defaultdict

    query = select(CSGOMatch).where(
        CSGOMatch.team_yes.isnot(None),
        CSGOMatch.team_no.isnot(None),
    )

    if upcoming_only:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        in_play_cutoff = now - timedelta(hours=8)  # Extended for longer matches (BO5 can be 6+ hours)
        query = query.where(
            CSGOMatch.game_start_time >= in_play_cutoff,
            CSGOMatch.game_start_time <= cutoff,
            CSGOMatch.resolved == False,
        )

    query = query.order_by(CSGOMatch.game_start_time.asc().nullslast())
    matches = db.execute(query).scalars().all()

    # Group by event - first pass: group non-totals by (teams, start_hour)
    # Totals markets (Over/Under) need to be matched to their parent event by start time
    events_dict = defaultdict(list)
    totals_by_start = defaultdict(list)  # start_hour -> list of totals markets

    for match in matches:
        start_hour = match.game_start_time.replace(minute=0, second=0, microsecond=0) if match.game_start_time else None

        # Totals markets have "Over" vs "Under" - group separately first
        if match.market_type == 'totals' or match.team_yes in ('Over', 'Under'):
            totals_by_start[start_hour].append(match)
        else:
            # Normal markets - group by teams + start time
            event_key = (match.team_yes, match.team_no, start_hour)
            events_dict[event_key].append(match)

    # Second pass: attach totals to their parent events
    for (team_yes, team_no, start_hour), event_matches in events_dict.items():
        # Find totals with matching start time
        if start_hour in totals_by_start:
            # Add totals to this event (assume one totals per start time)
            for totals_match in totals_by_start[start_hour]:
                event_matches.append(totals_match)
            # Remove so we don't add to multiple events
            del totals_by_start[start_hour]

    # Build response
    events = []
    for (team_yes, team_no, start_hour), event_matches in events_dict.items():
        # Find main moneyline market
        main_market = next(
            (m for m in event_matches if m.market_type == 'moneyline'),
            event_matches[0]
        )

        # Categorize sub-markets
        markets = []
        for m in event_matches:
            market_label = m.group_item_title or m.market_type or 'main'
            markets.append({
                "id": m.id,
                "market_id": m.market_id,
                "market_type": m.market_type,
                "label": market_label,
                "group_item_title": m.group_item_title,
                "format": m.format,
                "current_price": float(m.yes_price) if m.yes_price else None,
                "spread": float(m.spread) if m.spread else None,
                "volume": float(m.volume_total) if m.volume_total else None,
                "volume_24h": float(m.volume_24h) if m.volume_24h else None,
                "liquidity": float(m.liquidity) if m.liquidity else None,
                "subscribed": m.subscribed,
                "closed": m.closed,
                "resolved": m.resolved,
            })

        # Sort: moneyline first, then by label
        markets.sort(key=lambda x: (0 if x["market_type"] == "moneyline" else 1, x["label"]))

        now = datetime.now(timezone.utc)
        game_start = main_market.game_start_time
        is_live = game_start and game_start <= now and not main_market.resolved

        events.append({
            "event_key": f"{team_yes}_vs_{team_no}_{start_hour.isoformat() if start_hour else 'unknown'}",
            "team_yes": team_yes,
            "team_no": team_no,
            "tournament": main_market.tournament,
            "format": main_market.format,
            "game_start_time": game_start.isoformat() if game_start else None,
            "is_live": is_live,
            "main_price": float(main_market.yes_price) if main_market.yes_price else None,
            "main_spread": float(main_market.spread) if main_market.spread else None,
            "market_count": len(markets),
            "markets": markets,
        })

    # Sort by game start time
    events.sort(key=lambda x: x["game_start_time"] or "9999")

    return {
        "total": len(events),
        "events": events[:limit],
    }


@router.get("/pipeline/matches")
async def get_pipeline_matches(
    upcoming_only: bool = Query(False, description="Only show upcoming matches"),
    hours_ahead: float = Query(24.0, description="Hours ahead for upcoming filter"),
    enriched_only: bool = Query(False, description="Only show matches with team data"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get CS:GO matches from the real-time pipeline.

    Returns matches from the csgo_matches table with:
    - Team names (from Gamma API enrichment)
    - Game start time (exact time from Gamma API)
    - Tournament info
    - Subscription status
    """
    from datetime import timedelta

    query = select(CSGOMatch)
    conditions = []

    if upcoming_only:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        # Include both upcoming AND in-progress matches (started within 4h, not resolved)
        in_play_cutoff = now - timedelta(hours=4)
        conditions.append(CSGOMatch.game_start_time >= in_play_cutoff)
        conditions.append(CSGOMatch.game_start_time <= cutoff)
        conditions.append(CSGOMatch.resolved == False)

    if enriched_only:
        conditions.append(CSGOMatch.team_yes.isnot(None))
        conditions.append(CSGOMatch.team_no.isnot(None))

    if conditions:
        query = query.where(and_(*conditions))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()

    # Order by game start time
    query = query.order_by(CSGOMatch.game_start_time.asc().nullslast())

    # Paginate
    query = query.offset(offset).limit(limit)
    matches = db.execute(query).scalars().all()

    # Get current prices for each match
    result = []
    for match in matches:
        # Get market info for tier/subscription status
        market = None
        if match.market_id:
            market = db.execute(
                select(Market).where(Market.id == match.market_id)
            ).scalar()

        # Get latest snapshot for price info
        snapshot = None
        if match.market_id:
            snapshot = db.execute(
                select(Snapshot)
                .where(Snapshot.market_id == match.market_id)
                .order_by(desc(Snapshot.timestamp))
            ).scalars().first()

        # Calculate spread
        spread = None
        if snapshot and snapshot.best_bid is not None and snapshot.best_ask is not None:
            spread = float(snapshot.best_ask) - float(snapshot.best_bid)

        # Prefer CSGO-independent data from csgo_matches, fall back to snapshots
        yes_price = float(match.yes_price) if match.yes_price is not None else (float(snapshot.price) if snapshot and snapshot.price else None)
        csgo_spread = float(match.spread) if match.spread is not None else spread
        csgo_volume = float(match.volume_24h) if match.volume_24h is not None else (float(snapshot.volume_24h) if snapshot and snapshot.volume_24h else None)

        result.append({
            "id": match.id,
            "market_id": match.market_id,
            "gamma_id": match.gamma_id,
            "condition_id": match.condition_id,
            "team_yes": match.team_yes,
            "team_no": match.team_no,
            "game_start_time": match.game_start_time.isoformat() if match.game_start_time else None,
            "game_start_override": match.game_start_override,
            "end_date": match.end_date.isoformat() if match.end_date else None,
            "tournament": match.tournament,
            "format": match.format,
            "market_type": match.market_type,
            "group_item_title": match.group_item_title,
            "subscribed": match.subscribed,
            # Lifecycle fields (CSGO-independent)
            "closed": match.closed,
            "resolved": match.resolved,
            "outcome": match.outcome,
            "accepting_orders": match.accepting_orders,
            "last_status_check": match.last_status_check.isoformat() if match.last_status_check else None,
            # Market data (prefer CSGO-independent, fall back to snapshots)
            "tier": market.tier if market else None,
            "current_price": yes_price,
            "best_bid": float(snapshot.best_bid) if snapshot and snapshot.best_bid else None,
            "best_ask": float(snapshot.best_ask) if snapshot and snapshot.best_ask else None,
            "spread": csgo_spread,
            "volume_24h": csgo_volume,
            "liquidity": float(match.liquidity) if match.liquidity else None,
            "created_at": match.created_at.isoformat() if match.created_at else None,
            "updated_at": match.updated_at.isoformat() if match.updated_at else None,
        })

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "matches": result,
    }


@router.get("/pipeline/matches/{match_id}")
async def get_pipeline_match(
    match_id: int,
    db: Session = Depends(get_db),
):
    """
    Get a single CS:GO match from the pipeline.
    """
    match = db.execute(
        select(CSGOMatch).where(CSGOMatch.id == match_id)
    ).scalar()

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    # Get market info
    market = None
    if match.market_id:
        market = db.execute(
            select(Market).where(Market.id == match.market_id)
        ).scalar()

    # Get latest snapshot
    snapshot = None
    if match.market_id:
        snapshot = db.execute(
            select(Snapshot)
            .where(Snapshot.market_id == match.market_id)
            .order_by(desc(Snapshot.timestamp))
        ).scalars().first()

    return {
        "id": match.id,
        "market_id": match.market_id,
        "gamma_id": match.gamma_id,
        "condition_id": match.condition_id,
        "team_yes": match.team_yes,
        "team_no": match.team_no,
        "game_start_time": match.game_start_time.isoformat() if match.game_start_time else None,
        "game_start_override": match.game_start_override,
        "end_date": match.end_date.isoformat() if match.end_date else None,
        "tournament": match.tournament,
        "format": match.format,
        "market_type": match.market_type,
        "group_item_title": match.group_item_title,
        "game_id": match.game_id,
        "subscribed": match.subscribed,
        "gamma_data": match.gamma_data,
        "current_price": float(snapshot.price) if snapshot and snapshot.price else None,
        "best_bid": float(snapshot.best_bid) if snapshot and snapshot.best_bid else None,
        "best_ask": float(snapshot.best_ask) if snapshot and snapshot.best_ask else None,
        "market_question": market.question if market else None,
        "created_at": match.created_at.isoformat() if match.created_at else None,
        "updated_at": match.updated_at.isoformat() if match.updated_at else None,
    }


@router.patch("/pipeline/matches/{match_id}")
async def update_pipeline_match(
    match_id: int,
    game_start_time: Optional[str] = Query(None, description="New game start time (ISO format)"),
    db: Session = Depends(get_db),
):
    """
    Update a CS:GO match in the pipeline.

    Primarily used for overriding game start times when Gamma API data is wrong.
    """
    match = db.execute(
        select(CSGOMatch).where(CSGOMatch.id == match_id)
    ).scalar()

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    if game_start_time:
        try:
            # Parse ISO format
            new_time = datetime.fromisoformat(game_start_time.replace("Z", "+00:00"))
            match.game_start_time = new_time
            match.game_start_override = True
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid datetime format: {e}")

    db.commit()
    db.refresh(match)

    return {
        "id": match.id,
        "game_start_time": match.game_start_time.isoformat() if match.game_start_time else None,
        "game_start_override": match.game_start_override,
        "message": "Match updated successfully",
    }


@router.get("/pipeline/signals")
async def get_pipeline_signals(
    count: int = Query(50, ge=1, le=500),
    condition_id: Optional[str] = Query(None, description="Filter by condition ID"),
):
    """
    Get recent signals from the CS:GO Redis stream.
    """
    import asyncio
    from src.csgo.signals import get_recent_signals, get_stream_stats

    # Run async functions
    loop = asyncio.get_event_loop()
    signals = await get_recent_signals(count=count, condition_id=condition_id)
    stats = await get_stream_stats()

    return {
        "stream_stats": stats,
        "count": len(signals),
        "signals": signals,
    }


@router.post("/pipeline/sync")
async def trigger_pipeline_sync(
    refresh_volume: bool = Query(False, description="Refresh volume/liquidity for all matches"),
    db: Session = Depends(get_db),
):
    """
    Trigger a manual sync of CS:GO markets to the pipeline.

    This runs the discovery and enrichment process immediately.
    Use refresh_volume=true to update volume/liquidity for all matches.
    """
    from src.csgo.discovery import sync_csgo_matches
    from src.csgo.enrichment import enrich_all_csgo_matches

    # Sync new markets
    sync_stats = sync_csgo_matches(db)

    # Enrich matches (all if refresh_volume, otherwise only new)
    enrich_stats = enrich_all_csgo_matches(db, only_unenriched=not refresh_volume)

    return {
        "sync": sync_stats,
        "enrichment": enrich_stats,
    }


@router.get("/pipeline/status")
async def get_pipeline_status(
    db: Session = Depends(get_db),
):
    """
    Get overall CS:GO pipeline status.

    Returns:
    - Total matches in database
    - Upcoming matches (within 6 hours)
    - Subscribed matches
    - Stream stats
    - Strategy stats
    """
    from datetime import timedelta
    from src.csgo.signals import get_stream_stats

    now = datetime.now(timezone.utc)

    # Total matches
    total_matches = db.execute(
        select(func.count()).select_from(CSGOMatch)
    ).scalar()

    # Upcoming matches (within 6 hours)
    upcoming_cutoff = now + timedelta(hours=6)
    upcoming_matches = db.execute(
        select(func.count()).select_from(CSGOMatch).where(
            and_(
                CSGOMatch.game_start_time >= now,
                CSGOMatch.game_start_time <= upcoming_cutoff,
            )
        )
    ).scalar()

    # Currently subscribed
    subscribed_matches = db.execute(
        select(func.count()).select_from(CSGOMatch).where(
            CSGOMatch.subscribed == True
        )
    ).scalar()

    # In-play matches (started within 4h, not resolved)
    in_play_cutoff = now - timedelta(hours=4)
    in_play_matches = db.execute(
        select(func.count()).select_from(CSGOMatch).where(
            and_(
                CSGOMatch.game_start_time >= in_play_cutoff,
                CSGOMatch.game_start_time <= now,
                CSGOMatch.resolved == False,
                CSGOMatch.closed == False,
            )
        )
    ).scalar()

    # CRITICAL: Check for unsubscribed in-play matches (subscription bug detector)
    unsubscribed_in_play = db.execute(
        select(func.count()).select_from(CSGOMatch).where(
            and_(
                CSGOMatch.game_start_time >= in_play_cutoff,
                CSGOMatch.game_start_time <= now,
                CSGOMatch.resolved == False,
                CSGOMatch.closed == False,
                CSGOMatch.subscribed == False,
            )
        )
    ).scalar()

    # Subscription health warning
    subscription_healthy = unsubscribed_in_play == 0

    # Enriched matches (have team names)
    enriched_matches = db.execute(
        select(func.count()).select_from(CSGOMatch).where(
            and_(
                CSGOMatch.team_yes.isnot(None),
                CSGOMatch.team_no.isnot(None),
            )
        )
    ).scalar()

    # Get stream stats
    try:
        stream_stats = await get_stream_stats()
    except Exception as e:
        stream_stats = {"error": str(e)}

    # Get loaded strategies
    from strategies.loader import load_strategies
    all_strategies = load_strategies()
    csgo_strategies = [s for s in all_strategies if s.name.startswith("csgo_")]

    return {
        "status": "operational" if subscription_healthy else "degraded",
        "matches": {
            "total": total_matches,
            "enriched": enriched_matches,
            "upcoming_6h": upcoming_matches,
            "in_play": in_play_matches,
            "subscribed": subscribed_matches,
        },
        "subscription_health": {
            "healthy": subscription_healthy,
            "unsubscribed_in_play": unsubscribed_in_play,
            "warning": None if subscription_healthy else f"{unsubscribed_in_play} in-play match(es) are NOT subscribed - data loss!",
        },
        "stream": stream_stats,
        "strategies": {
            "loaded": len(csgo_strategies),
            "names": [s.name for s in csgo_strategies],
        },
    }


@router.get("/positions/list")
async def get_position_list(
    db: Session = Depends(get_db),
):
    """
    Get list of CSGO markets for dropdown selector.

    Includes:
    - Ongoing/recent matches (for live viewing)
    - Markets with CSGO engine positions
    - Markets with old executor positions
    """
    from sqlalchemy import or_
    from datetime import timedelta
    from src.csgo.engine.models import CSGOPosition

    result = []
    seen_market_ids = set()

    # 1. Get ongoing/recent matches (last 6h, next 6h) - for live viewing
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=6)
    future_cutoff = now + timedelta(hours=6)

    ongoing_matches = db.execute(
        select(CSGOMatch)
        .where(
            CSGOMatch.game_start_time >= recent_cutoff,
            CSGOMatch.game_start_time <= future_cutoff,
            CSGOMatch.resolved == False,
        )
        .order_by(CSGOMatch.game_start_time.desc())
        .limit(20)
    ).scalars().all()

    for m in ongoing_matches:
        if m.market_id and m.market_id not in seen_market_ids:
            seen_market_ids.add(m.market_id)
            label = f"{m.team_yes} vs {m.team_no}"
            if m.group_item_title:
                label += f" ({m.group_item_title})"
            result.append({
                "id": 0,  # No position ID for matches without positions
                "market_id": m.market_id,
                "label": label,
                "status": "live" if m.game_start_time and m.game_start_time <= now else "upcoming",
            })

    # 2. Get CSGO engine positions (new system)
    csgo_positions = db.execute(
        select(CSGOPosition)
        .order_by(desc(CSGOPosition.opened_at))
        .limit(50)
    ).scalars().all()

    for p in csgo_positions:
        if p.market_id and p.market_id not in seen_market_ids:
            seen_market_ids.add(p.market_id)
            label = f"{p.team_yes} vs {p.team_no}" if p.team_yes and p.team_no else f"Market #{p.market_id}"
            # Get group_item_title from csgo_match
            csgo_match = db.execute(
                select(CSGOMatch).where(CSGOMatch.market_id == p.market_id)
            ).scalar()
            if csgo_match and csgo_match.group_item_title:
                label += f" ({csgo_match.group_item_title})"
            result.append({
                "id": p.id,
                "market_id": p.market_id,
                "label": label,
                "status": p.status,
            })

    # 3. Get old executor positions (legacy system)
    csgo_conditions = [Position.strategy_name.like(f"{prefix}%") for prefix in CSGO_STRATEGY_PREFIXES]
    old_positions = db.execute(
        select(Position)
        .where(or_(*csgo_conditions))
        .order_by(desc(Position.created_at))
        .limit(30)
    ).scalars().all()

    for p in old_positions:
        if p.market_id and p.market_id not in seen_market_ids:
            seen_market_ids.add(p.market_id)
            csgo_match = db.execute(
                select(CSGOMatch).where(CSGOMatch.market_id == p.market_id)
            ).scalar()
            label = f"#{p.id}"
            if csgo_match:
                label = f"{csgo_match.team_yes} vs {csgo_match.team_no}"
                if csgo_match.group_item_title:
                    label += f" ({csgo_match.group_item_title})"
            result.append({
                "id": p.id,
                "market_id": p.market_id,
                "label": label,
                "status": p.status,
            })

    return {"positions": result}


@router.get("/price-history/{market_id}")
async def get_price_history(
    market_id: int,
    db: Session = Depends(get_db),
):
    """
    Get price history for a market with trade execution markers.

    Time range: game_start - 2 hours to game_start + 5 hours
    This captures pre-game trading, game start, and post-game resolution.

    Data sources:
    - csgo_price_ticks: High-frequency data (5-second bars) for recent matches
    - snapshots: Fallback for older matches without high-freq data

    Returns:
    - price_data: 5-second OHLC bars (if high-freq available) or minute-level snapshots
    - trades: execution times and prices for overlay markers
    """
    from datetime import timedelta
    from sqlalchemy import func, text
    from src.executor.models import ExecutorTrade
    from src.db.models import CSGOPriceTick

    # Get csgo_match info first to determine time range
    csgo_match = db.execute(
        select(CSGOMatch).where(CSGOMatch.market_id == market_id)
    ).scalar()

    # Get market for token IDs
    market = db.execute(
        select(Market).where(Market.id == market_id)
    ).scalar()

    if not market:
        raise HTTPException(status_code=404, detail=f"Market {market_id} not found")

    # Calculate time range based on game start time
    if csgo_match and csgo_match.game_start_time:
        game_start = csgo_match.game_start_time
        start_time = game_start - timedelta(hours=2)
        end_time = game_start + timedelta(hours=5)
    else:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=7)

    # Try to get high-frequency data from csgo_price_ticks first
    tick_count = db.execute(
        select(func.count(CSGOPriceTick.id))
        .where(
            CSGOPriceTick.market_id == market_id,
            CSGOPriceTick.timestamp >= start_time,
            CSGOPriceTick.timestamp <= end_time,
        )
    ).scalar()

    price_data = []
    data_source = "snapshots"

    # Always start with snapshots for the full time range (minute-level baseline)
    snapshots = db.execute(
        select(Snapshot)
        .where(
            Snapshot.market_id == market_id,
            Snapshot.timestamp >= start_time,
            Snapshot.timestamp <= end_time,
        )
        .order_by(Snapshot.timestamp)
    ).scalars().all()

    for s in snapshots:
        price_data.append({
            "timestamp": s.timestamp.isoformat(),
            "yes_price": float(s.price) if s.price else None,
            "no_price": (1 - float(s.price)) if s.price else None,
            "best_bid": float(s.best_bid) if s.best_bid else None,
            "best_ask": float(s.best_ask) if s.best_ask else None,
            "spread": (float(s.best_ask) - float(s.best_bid)) if (s.best_bid and s.best_ask) else None,
            "volume_24h": float(s.volume_24h) if s.volume_24h else None,
            "source": "snapshot",
        })

    # If we have high-frequency tick data, merge it in (higher resolution where available)
    if tick_count and tick_count > 10:
        data_source = "ticks+snapshots"

        # Query raw ticks
        ticks = db.execute(
            select(CSGOPriceTick)
            .where(
                CSGOPriceTick.market_id == market_id,
                CSGOPriceTick.timestamp >= start_time,
                CSGOPriceTick.timestamp <= end_time,
            )
            .order_by(CSGOPriceTick.timestamp)
        ).scalars().all()

        if ticks:
            # Get the time range covered by ticks
            tick_start = ticks[0].timestamp
            tick_end = ticks[-1].timestamp

            # Remove snapshots that fall within the tick range (ticks have higher resolution)
            price_data = [p for p in price_data if
                          datetime.fromisoformat(p["timestamp"].replace('+00:00', '+00:00')) < tick_start or
                          datetime.fromisoformat(p["timestamp"].replace('+00:00', '+00:00')) > tick_end]

            # Aggregate ticks to 5-second bars
            # Use PRICE field (last trade price) converted to YES-equivalent
            # Filter out extreme prices (< 5% or > 95%) which are orderbook edge trades
            bars = {}
            for tick in ticks:
                ts = tick.timestamp
                bucket_ts = ts.replace(microsecond=0, second=(ts.second // 5) * 5)
                bucket_key = bucket_ts.isoformat()

                if bucket_key not in bars:
                    bars[bucket_key] = {"yes_prices": []}

                bar = bars[bucket_key]
                if tick.price is not None:
                    price = float(tick.price)
                    # Convert to YES-equivalent price
                    if tick.token_type == "YES":
                        yes_price = price
                    else:
                        # NO token price -> YES price = 1 - NO_price
                        yes_price = 1 - price

                    # Filter out extreme prices (orderbook edge trades)
                    if 0.05 <= yes_price <= 0.95:
                        bar["yes_prices"].append(yes_price)

            # Convert bars to price_data format with EMA smoothing
            # First pass: compute raw median prices per bucket
            raw_prices = []
            for bucket_key in sorted(bars.keys()):
                bar = bars[bucket_key]
                if bar["yes_prices"]:
                    sorted_prices = sorted(bar["yes_prices"])
                    mid_idx = len(sorted_prices) // 2
                    raw_prices.append((bucket_key, sorted_prices[mid_idx]))

            # Second pass: apply EMA smoothing to reduce spikes
            # EMA = alpha * current + (1-alpha) * previous
            # alpha=0.1 gives strong smoothing to produce clean chart
            alpha = 0.1
            ema_price = None
            for bucket_key, raw_price in raw_prices:
                if ema_price is None:
                    ema_price = raw_price
                else:
                    ema_price = alpha * raw_price + (1 - alpha) * ema_price

                yes_price = ema_price
                no_price = 1 - yes_price

                price_data.append({
                    "timestamp": bucket_key,
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "best_bid": None,  # bid/ask data unreliable in ticks
                    "best_ask": None,
                    "spread": None,
                    "volume_24h": None,
                    "source": "tick",
                })

            # Re-sort price_data by timestamp after merging
            price_data.sort(key=lambda x: x["timestamp"])

    # Get trades for this market from both old executor and new CSGO engine
    trades = []

    # 1. Old executor trades (Position/ExecutorTrade)
    positions = db.execute(
        select(Position).where(Position.market_id == market_id)
    ).scalars().all()
    position_ids = [p.id for p in positions]

    if position_ids:
        executor_trades = db.execute(
            select(ExecutorTrade)
            .where(ExecutorTrade.position_id.in_(position_ids))
            .order_by(ExecutorTrade.timestamp)
        ).scalars().all()

        for t in executor_trades:
            position = next((p for p in positions if p.id == t.position_id), None)
            bet_on_team = None
            bet_on_side = None
            strategy_name = None
            if position and market:
                strategy_name = position.strategy_name
                if position.token_id == market.yes_token_id:
                    bet_on_side = "YES"
                    bet_on_team = csgo_match.team_yes if csgo_match else None
                elif position.token_id == market.no_token_id:
                    bet_on_side = "NO"
                    bet_on_team = csgo_match.team_no if csgo_match else None

            trades.append({
                "timestamp": t.timestamp.isoformat(),
                "side": t.side,
                "price": float(t.price) if t.price else None,
                "size_usd": float(t.size_usd) if t.size_usd else None,
                "position_id": t.position_id,
                "bet_on_team": bet_on_team,
                "bet_on_side": bet_on_side,
                "strategy_name": strategy_name,
                "spread": None,  # Old executor doesn't track spread
                "slippage": None,
            })

    # 2. CSGO Engine trades (CSGOPosition/CSGOTrade) - has spread/slippage data
    from src.csgo.engine.models import CSGOPosition, CSGOTrade

    csgo_positions = db.execute(
        select(CSGOPosition).where(CSGOPosition.market_id == market_id)
    ).scalars().all()
    csgo_position_ids = [p.id for p in csgo_positions]

    if csgo_position_ids:
        csgo_trades = db.execute(
            select(CSGOTrade)
            .where(CSGOTrade.position_id.in_(csgo_position_ids))
            .order_by(CSGOTrade.created_at)
        ).scalars().all()

        for t in csgo_trades:
            csgo_pos = next((p for p in csgo_positions if p.id == t.position_id), None)
            bet_on_team = None
            bet_on_side = None

            if csgo_pos:
                bet_on_side = csgo_pos.token_type  # 'YES' or 'NO'
                if bet_on_side == "YES":
                    bet_on_team = csgo_match.team_yes if csgo_match else None
                else:
                    bet_on_team = csgo_match.team_no if csgo_match else None

            trades.append({
                "timestamp": t.created_at.isoformat() if t.created_at else None,
                "side": t.side,
                "price": float(t.price) if t.price else None,
                "size_usd": float(t.cost_usd) if t.cost_usd else None,
                "position_id": t.position_id,
                "bet_on_team": bet_on_team,
                "bet_on_side": bet_on_side,
                "strategy_name": csgo_pos.strategy_name if csgo_pos else None,
                "spread": float(t.spread) if t.spread else None,
                "slippage": float(t.slippage) if t.slippage else None,
            })

    # Sort all trades by timestamp
    trades.sort(key=lambda x: x["timestamp"] if x["timestamp"] else "")

    return {
        "market_id": market_id,
        "match_info": {
            "team_yes": csgo_match.team_yes if csgo_match else None,
            "team_no": csgo_match.team_no if csgo_match else None,
            "format": csgo_match.format if csgo_match else None,
            "group_item_title": csgo_match.group_item_title if csgo_match else None,
            "tournament": csgo_match.tournament if csgo_match else None,
            "game_start_time": csgo_match.game_start_time.isoformat() if csgo_match and csgo_match.game_start_time else None,
        } if csgo_match else None,
        "price_data": price_data,
        "trades": trades,
        "data_points": len(price_data),
        "data_source": data_source,
    }


@router.get("/spreads/breakdown")
async def get_spreads_breakdown(
    status: Optional[str] = Query(None, enum=["open", "closed"]),
    strategy: Optional[str] = Query(None, description="Filter by strategy name"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Get full breakdown of spread positions with all legs.

    Returns spreads with:
    - Entry and exit legs with timestamps and prices
    - P&L per leg and running total
    - Trigger reasons for each action
    - Spread/slippage at execution time
    """
    query = select(CSGOSpread).order_by(desc(CSGOSpread.opened_at))

    if status:
        query = query.where(CSGOSpread.status == status)
    if strategy:
        query = query.where(CSGOSpread.strategy_name == strategy)

    query = query.limit(limit)
    spreads = db.execute(query).scalars().all()

    result = []
    for spread in spreads:
        # Get YES and NO positions for this spread
        yes_pos = db.execute(
            select(CSGOPosition).where(
                CSGOPosition.spread_id == spread.id,
                CSGOPosition.token_type == "YES"
            )
        ).scalar()

        no_pos = db.execute(
            select(CSGOPosition).where(
                CSGOPosition.spread_id == spread.id,
                CSGOPosition.token_type == "NO"
            )
        ).scalar()

        # Get all legs for both positions
        yes_legs = []
        no_legs = []

        if yes_pos:
            legs = db.execute(
                select(CSGOPositionLeg)
                .where(CSGOPositionLeg.position_id == yes_pos.id)
                .order_by(CSGOPositionLeg.created_at)
            ).scalars().all()
            yes_legs = [
                {
                    "type": l.leg_type,
                    "shares": float(l.shares_delta) if l.shares_delta else 0,
                    "price": float(l.price) if l.price else None,
                    "pnl": float(l.realized_pnl) if l.realized_pnl else 0,
                    "reason": l.trigger_reason,
                    "time": l.created_at.strftime("%H:%M:%S") if l.created_at else None,
                }
                for l in legs
            ]

        if no_pos:
            legs = db.execute(
                select(CSGOPositionLeg)
                .where(CSGOPositionLeg.position_id == no_pos.id)
                .order_by(CSGOPositionLeg.created_at)
            ).scalars().all()
            no_legs = [
                {
                    "type": l.leg_type,
                    "shares": float(l.shares_delta) if l.shares_delta else 0,
                    "price": float(l.price) if l.price else None,
                    "pnl": float(l.realized_pnl) if l.realized_pnl else 0,
                    "reason": l.trigger_reason,
                    "time": l.created_at.strftime("%H:%M:%S") if l.created_at else None,
                }
                for l in legs
            ]

        # Get trades with spread/slippage
        trades = []
        position_ids = []
        if yes_pos:
            position_ids.append(yes_pos.id)
        if no_pos:
            position_ids.append(no_pos.id)

        if position_ids:
            trade_records = db.execute(
                select(CSGOTrade)
                .where(CSGOTrade.position_id.in_(position_ids))
                .order_by(CSGOTrade.created_at)
            ).scalars().all()

            for t in trade_records:
                pos = yes_pos if t.position_id == (yes_pos.id if yes_pos else None) else no_pos
                trades.append({
                    "token": pos.token_type if pos else "?",
                    "side": t.side,
                    "shares": float(t.shares) if t.shares else 0,
                    "price": float(t.price) if t.price else None,
                    "cost": float(t.cost_usd) if t.cost_usd else 0,
                    "spread": float(t.spread) if t.spread else None,
                    "slippage": float(t.slippage) if t.slippage else None,
                    "time": t.created_at.strftime("%H:%M:%S") if t.created_at else None,
                })

        # Calculate totals
        yes_pnl = float(yes_pos.realized_pnl or 0) + float(yes_pos.unrealized_pnl or 0) if yes_pos else 0
        no_pnl = float(no_pos.realized_pnl or 0) + float(no_pos.unrealized_pnl or 0) if no_pos else 0
        total_pnl = yes_pnl + no_pnl

        result.append({
            "id": spread.id,
            "strategy": spread.strategy_name,
            "match": f"{spread.team_yes} vs {spread.team_no}",
            "status": spread.status,
            "cost_basis": float(spread.total_cost_basis) if spread.total_cost_basis else 0,
            "opened_at": spread.opened_at.strftime("%Y-%m-%d %H:%M:%S") if spread.opened_at else None,
            "closed_at": spread.closed_at.strftime("%Y-%m-%d %H:%M:%S") if spread.closed_at else None,
            # Summary
            "yes_pnl": round(yes_pnl, 2),
            "no_pnl": round(no_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            # Detailed breakdown
            "yes_legs": yes_legs,
            "no_legs": no_legs,
            "trades": trades,
        })

    # Summary stats
    total_spreads = len(result)
    open_count = sum(1 for r in result if r["status"] == "open")
    closed_count = sum(1 for r in result if r["status"] == "closed")
    total_realized = sum(r["total_pnl"] for r in result if r["status"] == "closed")

    return {
        "summary": {
            "total": total_spreads,
            "open": open_count,
            "closed": closed_count,
            "total_pnl": round(total_realized, 2),
        },
        "spreads": result,
    }


# ============================================================================
# CS:GO Analytics Endpoints (New)
# ============================================================================


@router.get("/engine/strategy/{strategy_name}/analytics")
async def get_strategy_analytics(
    strategy_name: str,
    db: Session = Depends(get_db),
):
    """
    Get deep analytics for a specific strategy.

    Returns:
    - Capital utilization over time
    - Average entry → exit price spread
    - Average slippage per trade
    - P&L by market type
    - Time-in-position distribution
    """
    from datetime import timedelta
    from collections import defaultdict

    # Get strategy state
    from src.csgo.engine.models import CSGOStrategyState
    strategy_state = db.execute(
        select(CSGOStrategyState).where(CSGOStrategyState.strategy_name == strategy_name)
    ).scalar()

    if not strategy_state:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    # Get all positions for this strategy
    positions = db.execute(
        select(CSGOPosition)
        .where(CSGOPosition.strategy_name == strategy_name)
        .order_by(CSGOPosition.opened_at)
    ).scalars().all()

    # Get all trades for analysis
    position_ids = [p.id for p in positions]
    trades = []
    if position_ids:
        trades = db.execute(
            select(CSGOTrade)
            .where(CSGOTrade.position_id.in_(position_ids))
            .order_by(CSGOTrade.created_at)
        ).scalars().all()

    # Get all legs for analysis
    legs = []
    if position_ids:
        legs = db.execute(
            select(CSGOPositionLeg)
            .where(CSGOPositionLeg.position_id.in_(position_ids))
            .order_by(CSGOPositionLeg.created_at)
        ).scalars().all()

    # Calculate metrics

    # 1. Average slippage
    slippages = [float(t.slippage) for t in trades if t.slippage is not None]
    avg_slippage = sum(slippages) / len(slippages) if slippages else 0

    # 2. Average spread at execution
    spreads = [float(t.spread) for t in trades if t.spread is not None]
    avg_spread = sum(spreads) / len(spreads) if spreads else 0

    # 3. Entry → Exit price spread for closed positions
    entry_exit_spreads = []
    for pos in positions:
        if pos.status == CSGOPositionStatus.CLOSED.value:
            pos_legs = [l for l in legs if l.position_id == pos.id]
            entry_legs = [l for l in pos_legs if l.leg_type == CSGOLegType.ENTRY.value]
            exit_legs = [l for l in pos_legs if l.leg_type in [CSGOLegType.FULL_EXIT.value, CSGOLegType.PARTIAL_EXIT.value]]

            if entry_legs and exit_legs:
                avg_entry = sum(float(l.price) for l in entry_legs) / len(entry_legs)
                avg_exit = sum(float(l.price) for l in exit_legs) / len(exit_legs)
                entry_exit_spreads.append(avg_exit - avg_entry)

    avg_entry_exit_spread = sum(entry_exit_spreads) / len(entry_exit_spreads) if entry_exit_spreads else 0

    # 4. P&L by market type
    pnl_by_market_type = defaultdict(lambda: {"pnl": 0, "count": 0})
    for pos in positions:
        if pos.status == CSGOPositionStatus.CLOSED.value:
            # Get csgo_match for market type
            csgo_match = db.execute(
                select(CSGOMatch).where(CSGOMatch.market_id == pos.market_id)
            ).scalar()
            market_type = csgo_match.market_type if csgo_match else "unknown"
            pnl = float(pos.realized_pnl or 0)
            pnl_by_market_type[market_type]["pnl"] += pnl
            pnl_by_market_type[market_type]["count"] += 1

    # 5. Time-in-position distribution (for closed positions)
    hold_times = []
    for pos in positions:
        if pos.status == CSGOPositionStatus.CLOSED.value and pos.opened_at and pos.closed_at:
            hold_time_mins = (pos.closed_at - pos.opened_at).total_seconds() / 60
            hold_times.append(hold_time_mins)

    # Bucket hold times
    hold_time_buckets = {"<5min": 0, "5-15min": 0, "15-30min": 0, "30-60min": 0, ">60min": 0}
    for ht in hold_times:
        if ht < 5:
            hold_time_buckets["<5min"] += 1
        elif ht < 15:
            hold_time_buckets["5-15min"] += 1
        elif ht < 30:
            hold_time_buckets["15-30min"] += 1
        elif ht < 60:
            hold_time_buckets["30-60min"] += 1
        else:
            hold_time_buckets[">60min"] += 1

    # 6. Capital utilization over time (sample last 20 trades)
    capital_timeline = []
    running_capital = float(strategy_state.allocated_usd)
    for trade in trades[-20:]:
        if trade.side == "BUY":
            running_capital -= float(trade.cost_usd)
        else:
            running_capital += float(trade.cost_usd)
        capital_timeline.append({
            "timestamp": trade.created_at.isoformat() if trade.created_at else None,
            "available_capital": round(running_capital, 2),
        })

    return {
        "strategy_name": strategy_name,
        "summary": {
            "allocated_usd": float(strategy_state.allocated_usd),
            "available_usd": float(strategy_state.available_usd),
            "total_realized_pnl": float(strategy_state.total_realized_pnl),
            "total_unrealized_pnl": float(strategy_state.total_unrealized_pnl),
            "trade_count": strategy_state.trade_count,
            "win_count": strategy_state.win_count,
            "loss_count": strategy_state.loss_count,
            "max_drawdown": float(strategy_state.max_drawdown_usd),
        },
        "execution_quality": {
            "avg_slippage_pct": round(avg_slippage * 100, 3),
            "avg_spread_pct": round(avg_spread * 100, 3),
            "avg_entry_exit_spread_pct": round(avg_entry_exit_spread * 100, 3),
        },
        "pnl_by_market_type": dict(pnl_by_market_type),
        "hold_time_distribution": hold_time_buckets,
        "capital_timeline": capital_timeline,
        "position_count": len(positions),
        "closed_positions": sum(1 for p in positions if p.status == CSGOPositionStatus.CLOSED.value),
    }


@router.get("/engine/performance-by-market")
async def get_performance_by_market(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Get P&L aggregated by market (match).

    Returns per-match:
    - Total P&L (all strategies combined)
    - Contributing strategies and their individual P&L
    - Position count, total turnover
    """
    from collections import defaultdict

    # Get all closed positions
    positions = db.execute(
        select(CSGOPosition)
        .where(CSGOPosition.status == CSGOPositionStatus.CLOSED.value)
        .order_by(desc(CSGOPosition.closed_at))
    ).scalars().all()

    # Group by market_id
    markets = defaultdict(lambda: {
        "positions": [],
        "total_pnl": 0,
        "strategies": defaultdict(lambda: {"pnl": 0, "count": 0}),
        "turnover": 0,
    })

    for pos in positions:
        market_id = pos.market_id
        pnl = float(pos.realized_pnl or 0)

        markets[market_id]["positions"].append(pos)
        markets[market_id]["total_pnl"] += pnl
        markets[market_id]["strategies"][pos.strategy_name]["pnl"] += pnl
        markets[market_id]["strategies"][pos.strategy_name]["count"] += 1
        markets[market_id]["turnover"] += float(pos.cost_basis or 0)

    # Enrich with match info and format response
    result = []
    for market_id, data in list(markets.items())[:limit]:
        # Get csgo_match info
        csgo_match = db.execute(
            select(CSGOMatch).where(CSGOMatch.market_id == market_id)
        ).scalar()

        strategies_list = [
            {"name": name, "pnl": round(s["pnl"], 2), "positions": s["count"]}
            for name, s in data["strategies"].items()
        ]
        strategies_list.sort(key=lambda x: x["pnl"], reverse=True)

        # Find best and worst strategy
        best_strategy = strategies_list[0] if strategies_list else None
        worst_strategy = strategies_list[-1] if len(strategies_list) > 1 else None

        result.append({
            "market_id": market_id,
            "match": f"{csgo_match.team_yes} vs {csgo_match.team_no}" if csgo_match else f"Market #{market_id}",
            "format": csgo_match.format if csgo_match else None,
            "market_type": csgo_match.market_type if csgo_match else None,
            "group_item_title": csgo_match.group_item_title if csgo_match else None,
            "game_start_time": csgo_match.game_start_time.isoformat() if csgo_match and csgo_match.game_start_time else None,
            "total_pnl": round(data["total_pnl"], 2),
            "position_count": len(data["positions"]),
            "turnover": round(data["turnover"], 2),
            "strategies": strategies_list,
            "best_strategy": best_strategy["name"] if best_strategy else None,
            "worst_strategy": worst_strategy["name"] if worst_strategy and worst_strategy["pnl"] < 0 else None,
        })

    # Sort by absolute P&L (most impactful first)
    result.sort(key=lambda x: abs(x["total_pnl"]), reverse=True)

    # Summary stats
    total_markets = len(result)
    profitable_markets = sum(1 for r in result if r["total_pnl"] > 0)
    losing_markets = sum(1 for r in result if r["total_pnl"] < 0)
    total_pnl = sum(r["total_pnl"] for r in result)

    return {
        "summary": {
            "total_markets": total_markets,
            "profitable": profitable_markets,
            "losing": losing_markets,
            "total_pnl": round(total_pnl, 2),
        },
        "markets": result,
    }


@router.get("/engine/exit-quality")
async def get_exit_quality(
    limit: int = Query(50, ge=1, le=200),
    strategy: Optional[str] = Query(None, description="Filter by strategy"),
    db: Session = Depends(get_db),
):
    """
    Analyze exit quality for closed positions.

    Returns per position:
    - Entry price, exit price
    - Best price during hold (from price ticks)
    - Market resolution price (1.0 or 0.0)
    - "Left on table" amounts
    - Exit quality score (0-100)
    """
    from src.db.models import CSGOPriceTick

    # Get closed positions
    query = select(CSGOPosition).where(
        CSGOPosition.status == CSGOPositionStatus.CLOSED.value
    )
    if strategy:
        query = query.where(CSGOPosition.strategy_name == strategy)
    query = query.order_by(desc(CSGOPosition.closed_at)).limit(limit)

    positions = db.execute(query).scalars().all()

    result = []
    for pos in positions:
        # Get entry and exit legs
        legs = db.execute(
            select(CSGOPositionLeg)
            .where(CSGOPositionLeg.position_id == pos.id)
            .order_by(CSGOPositionLeg.created_at)
        ).scalars().all()

        entry_legs = [l for l in legs if l.leg_type == CSGOLegType.ENTRY.value]
        exit_legs = [l for l in legs if l.leg_type in [CSGOLegType.FULL_EXIT.value, CSGOLegType.PARTIAL_EXIT.value]]

        if not entry_legs or not exit_legs:
            continue

        entry_price = float(entry_legs[0].price)
        exit_price = float(exit_legs[-1].price)  # Use last exit price

        # Get csgo_match for resolution info
        csgo_match = db.execute(
            select(CSGOMatch).where(CSGOMatch.market_id == pos.market_id)
        ).scalar()

        # Determine resolution price based on outcome
        resolution_price = None
        if csgo_match and csgo_match.resolved and csgo_match.outcome:
            # If position was on YES token, resolution = 1.0 if YES won
            if pos.token_type == "YES":
                resolution_price = 1.0 if csgo_match.outcome == "Yes" else 0.0
            else:
                resolution_price = 1.0 if csgo_match.outcome == "No" else 0.0

        # Find best price during hold from price ticks
        best_price_during_hold = None
        if pos.opened_at and pos.closed_at:
            # Query price ticks for this market during hold period
            ticks = db.execute(
                select(CSGOPriceTick)
                .where(
                    CSGOPriceTick.market_id == pos.market_id,
                    CSGOPriceTick.timestamp >= pos.opened_at,
                    CSGOPriceTick.timestamp <= pos.closed_at,
                    CSGOPriceTick.token_type == pos.token_type,
                )
            ).scalars().all()

            if ticks:
                # For a long position, best exit was highest price
                prices = [float(t.price) for t in ticks if t.price is not None]
                if prices:
                    best_price_during_hold = max(prices)

        # Calculate "left on table" metrics
        left_on_table_vs_best = None
        if best_price_during_hold is not None:
            left_on_table_vs_best = (best_price_during_hold - exit_price) * float(pos.initial_shares)

        left_on_table_vs_resolution = None
        if resolution_price is not None:
            left_on_table_vs_resolution = (resolution_price - exit_price) * float(pos.initial_shares)

        # Calculate exit quality score (0-100)
        # Based on how close exit was to best available price
        exit_quality_score = None
        if best_price_during_hold is not None and best_price_during_hold > entry_price:
            # Score = how much of the potential gain we captured
            potential_gain = best_price_during_hold - entry_price
            actual_gain = exit_price - entry_price
            if potential_gain > 0:
                exit_quality_score = min(100, max(0, int((actual_gain / potential_gain) * 100)))

        # Determine if we were on the winning side
        winner = None
        if resolution_price is not None:
            winner = resolution_price == 1.0

        result.append({
            "position_id": pos.id,
            "strategy": pos.strategy_name,
            "match": f"{pos.team_yes} vs {pos.team_no}" if pos.team_yes and pos.team_no else f"Market #{pos.market_id}",
            "token_type": pos.token_type,
            "entry_price_pct": round(entry_price * 100, 1),
            "exit_price_pct": round(exit_price * 100, 1),
            "best_price_during_hold_pct": round(best_price_during_hold * 100, 1) if best_price_during_hold else None,
            "resolution_price_pct": round(resolution_price * 100, 1) if resolution_price else None,
            "left_on_table_vs_best": round(left_on_table_vs_best, 2) if left_on_table_vs_best else None,
            "left_on_table_vs_resolution": round(left_on_table_vs_resolution, 2) if left_on_table_vs_resolution else None,
            "exit_quality_score": exit_quality_score,
            "winner": winner,
            "realized_pnl": round(float(pos.realized_pnl or 0), 2),
            "hold_time_mins": round((pos.closed_at - pos.opened_at).total_seconds() / 60, 1) if pos.opened_at and pos.closed_at else None,
            "exit_reason": pos.close_reason,
        })

    # Calculate summary stats
    scores = [r["exit_quality_score"] for r in result if r["exit_quality_score"] is not None]
    left_vs_best = [r["left_on_table_vs_best"] for r in result if r["left_on_table_vs_best"] is not None]
    left_vs_resolution = [r["left_on_table_vs_resolution"] for r in result if r["left_on_table_vs_resolution"] is not None]

    # Strategy breakdown
    strategy_quality = defaultdict(lambda: {"scores": [], "left_on_table": []})
    for r in result:
        if r["exit_quality_score"] is not None:
            strategy_quality[r["strategy"]]["scores"].append(r["exit_quality_score"])
        if r["left_on_table_vs_best"] is not None:
            strategy_quality[r["strategy"]]["left_on_table"].append(r["left_on_table_vs_best"])

    strategy_summary = []
    for name, data in strategy_quality.items():
        avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else None
        total_left = sum(data["left_on_table"]) if data["left_on_table"] else 0
        strategy_summary.append({
            "strategy": name,
            "avg_exit_quality": round(avg_score, 1) if avg_score else None,
            "total_left_on_table": round(total_left, 2),
            "position_count": len(data["scores"]),
        })
    strategy_summary.sort(key=lambda x: x["avg_exit_quality"] or 0, reverse=True)

    return {
        "summary": {
            "total_positions": len(result),
            "avg_exit_quality": round(sum(scores) / len(scores), 1) if scores else None,
            "total_left_on_table_vs_best": round(sum(left_vs_best), 2) if left_vs_best else 0,
            "total_left_on_table_vs_resolution": round(sum(left_vs_resolution), 2) if left_vs_resolution else 0,
            "winners": sum(1 for r in result if r["winner"] is True),
            "losers": sum(1 for r in result if r["winner"] is False),
        },
        "strategy_breakdown": strategy_summary,
        "positions": result,
    }
