"""
CS:GO Market Enrichment via Gamma API.

Fetches detailed metadata from the Gamma API and updates csgo_matches table.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.db.models import CSGOMatch
from src.fetchers.gamma import SyncGammaClient

logger = logging.getLogger(__name__)


def parse_gamma_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """
    Parse datetime string from Gamma API.

    Handles multiple formats:
    - "2025-12-28 07:00:00+00" (gameStartTime format)
    - "2025-12-28T07:00:00Z" (ISO format)

    Args:
        dt_str: Datetime string from Gamma API

    Returns:
        Timezone-aware datetime or None if parsing fails
    """
    if not dt_str:
        return None

    try:
        # Handle ISO format with Z suffix
        if "T" in dt_str:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        # Handle space-separated format: "2025-12-28 07:00:00+00"
        # Convert to ISO format for parsing
        dt_str = dt_str.replace(" ", "T")
        if dt_str.endswith("+00"):
            dt_str = dt_str + ":00"
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse datetime: {dt_str}, error: {e}")
        return None


def parse_outcomes(outcomes_str: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Parse team names from Gamma API outcomes field.

    Args:
        outcomes_str: JSON string like '["Team A", "Team B"]'

    Returns:
        Tuple of (team_yes, team_no)
    """
    if not outcomes_str:
        return None, None

    try:
        outcomes = json.loads(outcomes_str)
        if isinstance(outcomes, list) and len(outcomes) >= 2:
            return outcomes[0], outcomes[1]
        return None, None
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to parse outcomes: {outcomes_str}, error: {e}")
        return None, None


def parse_tournament(description: Optional[str]) -> Optional[str]:
    """
    Extract tournament name from market description.

    Example: "...in the VOID Masters Playoffs, BO1..." -> "VOID Masters Playoffs"

    Args:
        description: Market description from Gamma API

    Returns:
        Tournament name or None
    """
    if not description:
        return None

    # Pattern: "in the <tournament>," or "in the <tournament>."
    match = re.search(r"in the (.+?)(?:,|\.|\?)", description, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


def parse_format(question: Optional[str]) -> Optional[str]:
    """
    Extract match format (BO1, BO3, BO5) from market question.

    Args:
        question: Market question from Gamma API

    Returns:
        Format string or None
    """
    if not question:
        return None

    question_upper = question.upper()
    if "(BO5)" in question_upper or "BO5" in question_upper:
        return "BO5"
    elif "(BO3)" in question_upper or "BO3" in question_upper:
        return "BO3"
    elif "(BO1)" in question_upper or "BO1" in question_upper:
        return "BO1"

    return None


def parse_gamma_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Parse Gamma API response into structured fields for CSGOMatch.

    Args:
        data: Raw Gamma API response

    Returns:
        Dict with parsed fields ready for CSGOMatch update
    """
    team_yes, team_no = parse_outcomes(data.get("outcomes"))

    # Parse volume and liquidity
    volume = None
    liquidity = None
    try:
        if data.get("volume"):
            volume = float(data["volume"])
        if data.get("liquidity"):
            liquidity = float(data["liquidity"])
    except (ValueError, TypeError):
        pass

    return {
        "team_yes": team_yes,
        "team_no": team_no,
        "game_start_time": parse_gamma_datetime(data.get("gameStartTime")),
        "end_date": parse_gamma_datetime(data.get("endDate")),
        "tournament": parse_tournament(data.get("description")),
        "format": parse_format(data.get("question")),
        "market_type": data.get("sportsMarketType"),
        "group_item_title": data.get("groupItemTitle"),
        "game_id": data.get("gameId"),
        "volume_24h": volume,
        "liquidity": liquidity,
        "gamma_data": data,  # Store full response for debugging
    }


def enrich_csgo_market(gamma_id: int, db: Session) -> Optional[CSGOMatch]:
    """
    Fetch Gamma API data for a CS:GO market and update the csgo_matches table.

    Args:
        gamma_id: Gamma API numeric ID
        db: Database session

    Returns:
        Updated CSGOMatch or None if fetch failed
    """
    client = SyncGammaClient()

    # Fetch from Gamma API
    data = client.get_market_by_id(gamma_id)
    if not data:
        logger.warning(f"Failed to fetch Gamma data for gamma_id={gamma_id}")
        return None

    # Parse response
    parsed = parse_gamma_response(data)

    # Find matching CSGOMatch
    match = db.query(CSGOMatch).filter(CSGOMatch.gamma_id == gamma_id).first()
    if not match:
        logger.warning(f"No CSGOMatch found for gamma_id={gamma_id}")
        return None

    # Update fields (skip game_start_time if manually overridden)
    match.team_yes = parsed["team_yes"]
    match.team_no = parsed["team_no"]
    if not match.game_start_override:
        match.game_start_time = parsed["game_start_time"]
    match.end_date = parsed["end_date"]
    match.tournament = parsed["tournament"]
    match.format = parsed["format"]
    match.market_type = parsed["market_type"]
    match.group_item_title = parsed["group_item_title"]
    match.game_id = parsed["game_id"]
    match.volume_24h = parsed["volume_24h"]
    match.liquidity = parsed["liquidity"]
    match.gamma_data = parsed["gamma_data"]

    db.commit()
    db.refresh(match)

    logger.info(
        f"Enriched CS:GO match: {match.team_yes} vs {match.team_no}, "
        f"start={match.game_start_time}, volume=${parsed['volume_24h'] or 0:.0f}"
    )
    return match


def enrich_all_csgo_matches(db: Session, only_unenriched: bool = True) -> dict:
    """
    Enrich all CS:GO matches in the database.

    Args:
        db: Database session
        only_unenriched: If True, only enrich matches without team data

    Returns:
        Dict with counts: {"enriched": N, "failed": M, "skipped": K}
    """
    query = db.query(CSGOMatch).filter(CSGOMatch.gamma_id.isnot(None))

    if only_unenriched:
        # Only enrich if teams are not set
        query = query.filter(CSGOMatch.team_yes.is_(None))

    matches = query.all()

    enriched = 0
    failed = 0
    skipped = 0

    client = SyncGammaClient()

    for match in matches:
        if not match.gamma_id:
            skipped += 1
            continue

        data = client.get_market_by_id(match.gamma_id)
        if not data:
            failed += 1
            continue

        parsed = parse_gamma_response(data)

        # Update fields
        match.team_yes = parsed["team_yes"]
        match.team_no = parsed["team_no"]
        if not match.game_start_override:
            match.game_start_time = parsed["game_start_time"]
        match.end_date = parsed["end_date"]
        match.tournament = parsed["tournament"]
        match.format = parsed["format"]
        match.market_type = parsed["market_type"]
        match.group_item_title = parsed["group_item_title"]
        match.game_id = parsed["game_id"]
        match.volume_24h = parsed["volume_24h"]
        match.liquidity = parsed["liquidity"]
        match.gamma_data = parsed["gamma_data"]

        enriched += 1

    db.commit()

    stats = {"enriched": enriched, "failed": failed, "skipped": skipped}
    logger.info(f"Enrichment complete: {stats}")
    return stats


def propagate_format_to_children(db: Session) -> int:
    """
    Propagate format (BO1/BO3/BO5) from moneyline markets to their child markets.

    Child markets (map winners, totals) don't get format from the API directly,
    but strategies need it for filtering. This copies format from the parent
    moneyline market to children with the same teams and start time.

    Returns:
        Number of child markets updated
    """
    # Find moneyline markets with format set
    moneylines = (
        db.query(CSGOMatch)
        .filter(
            CSGOMatch.market_type == "moneyline",
            CSGOMatch.format.isnot(None),
        )
        .all()
    )

    updated = 0
    for ml in moneylines:
        # Find child markets with same teams and start time but no format
        children = (
            db.query(CSGOMatch)
            .filter(
                CSGOMatch.team_yes == ml.team_yes,
                CSGOMatch.team_no == ml.team_no,
                CSGOMatch.game_start_time == ml.game_start_time,
                CSGOMatch.market_type != "moneyline",
                CSGOMatch.format.is_(None),
            )
            .all()
        )

        for child in children:
            child.format = ml.format
            updated += 1
            logger.debug(
                f"Propagated format {ml.format} to {child.market_type}: "
                f"{child.team_yes} vs {child.team_no}"
            )

    db.commit()

    if updated > 0:
        logger.info(f"Propagated format to {updated} child markets")

    return updated
