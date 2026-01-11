"""
GRID Series Matcher.

Matches GRID series to Polymarket CSGO markets by fuzzy
matching team names. Stores matches with confidence scores.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.csgo.grid.client import GRIDClient, SeriesSummary, SyncGRIDClient
from src.db.database import get_session
from src.db.models import CSGOMatch

logger = logging.getLogger(__name__)

# Minimum similarity score (0-100) to consider a match
MIN_MATCH_SCORE = 70

# Team name normalization patterns
TEAM_NAME_REPLACEMENTS = {
    "esports": "",
    "gaming": "",
    "team": "",
    "esport": "",
    " gg": "",
    ".gg": "",
    "e-sports": "",
}

# Common team name aliases (normalized form -> canonical form)
TEAM_ALIASES = {
    "natus vincere": "navi",
    "ninjas in pyjamas": "nip",
    "astralis talent": "astralis",
    "faze clan": "faze",
    "complexity gaming": "col",
    "mousesports": "mouz",
    "fnatic rising": "fnatic",
}


@dataclass
class MatchResult:
    """Result of matching a GRID series to a Polymarket market."""
    csgo_match_id: int
    grid_series_id: str
    polymarket_yes_team: str
    grid_team_a_name: str
    grid_team_b_name: str
    grid_yes_team_id: str  # Which GRID team maps to Polymarket YES
    confidence: float  # 0-1
    matched_team: str  # Which team was matched (for debugging)


def normalize_team_name(name: str) -> str:
    """Normalize team name for fuzzy matching."""
    if not name:
        return ""

    # Lowercase
    name = name.lower().strip()

    # Remove common suffixes/words
    for pattern, replacement in TEAM_NAME_REPLACEMENTS.items():
        name = name.replace(pattern, replacement)

    # Remove extra whitespace
    name = " ".join(name.split())

    # Apply aliases (canonical names for common variations)
    if name in TEAM_ALIASES:
        name = TEAM_ALIASES[name]

    return name


def calculate_team_match_score(poly_team: str, grid_team: str) -> float:
    """
    Calculate similarity score between Polymarket and GRID team names.

    Returns score 0-100.
    """
    poly_norm = normalize_team_name(poly_team)
    grid_norm = normalize_team_name(grid_team)

    if not poly_norm or not grid_norm:
        return 0.0

    # Try multiple fuzzy matching algorithms and take best
    scores = [
        fuzz.ratio(poly_norm, grid_norm),
        fuzz.partial_ratio(poly_norm, grid_norm),
        fuzz.token_sort_ratio(poly_norm, grid_norm),
        fuzz.token_set_ratio(poly_norm, grid_norm),
    ]

    return max(scores)


def match_series_to_market(
    csgo_match: CSGOMatch,
    grid_series: list[SeriesSummary],
    time_window_hours: float = 24.0,
) -> Optional[MatchResult]:
    """
    Find the best matching GRID series for a Polymarket CSGO market.

    Args:
        csgo_match: Polymarket CSGO match
        grid_series: List of GRID series to search
        time_window_hours: Only match if times are within this window

    Returns:
        MatchResult if a good match found, None otherwise
    """
    if not csgo_match.team_yes or not csgo_match.team_no:
        logger.debug(f"Match {csgo_match.id} missing team names")
        return None

    best_match: Optional[MatchResult] = None
    best_score = 0.0

    for series in grid_series:
        # Check time proximity - REQUIRE both timestamps to prevent false matches
        # (same teams can play multiple times in a week)
        if csgo_match.game_start_time and series.start_time:
            time_diff = abs(
                (csgo_match.game_start_time - series.start_time).total_seconds() / 3600
            )
            if time_diff > time_window_hours:
                continue
        elif csgo_match.game_start_time or series.start_time:
            # One has time, one doesn't - risky to match, skip
            logger.debug(
                f"Skipping {series.team_a_name} vs {series.team_b_name}: "
                f"missing timestamp (poly={csgo_match.game_start_time}, grid={series.start_time})"
            )
            continue
        # If BOTH are None, allow match (rare, but possible for TBD matches)

        # Score match for team_yes against both GRID teams
        score_a = calculate_team_match_score(csgo_match.team_yes, series.team_a_name)
        score_b = calculate_team_match_score(csgo_match.team_yes, series.team_b_name)

        # Also check team_no to confirm (should match the other team)
        if score_a > score_b:
            primary_score = score_a
            secondary_score = calculate_team_match_score(
                csgo_match.team_no, series.team_b_name
            )
            grid_yes_team_id = series.team_a_id
            matched_team = series.team_a_name
        else:
            primary_score = score_b
            secondary_score = calculate_team_match_score(
                csgo_match.team_no, series.team_a_name
            )
            grid_yes_team_id = series.team_b_id
            matched_team = series.team_b_name

        # Combined score - both teams should match well
        combined_score = (primary_score + secondary_score) / 2

        if combined_score > best_score and combined_score >= MIN_MATCH_SCORE:
            best_score = combined_score
            best_match = MatchResult(
                csgo_match_id=csgo_match.id,
                grid_series_id=series.series_id,
                polymarket_yes_team=csgo_match.team_yes,
                grid_team_a_name=series.team_a_name,
                grid_team_b_name=series.team_b_name,
                grid_yes_team_id=grid_yes_team_id,
                confidence=combined_score / 100.0,
                matched_team=matched_team,
            )

    return best_match


class GRIDMatcher:
    """
    Matches GRID series to Polymarket CSGO markets.

    Usage:
        matcher = GRIDMatcher()
        matches = matcher.match_unmatched_markets()
        matcher.save_matches(matches)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.client = SyncGRIDClient(api_key)

    def get_unmatched_markets(
        self,
        session: Session,
        hours_until_start: float = 48.0,
    ) -> list[CSGOMatch]:
        """
        Get CSGO markets that don't have GRID matches yet.

        Args:
            session: Database session
            hours_until_start: Only get markets starting within this many hours

        Returns:
            List of unmatched CSGOMatch records
        """
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_until_start)

        stmt = (
            select(CSGOMatch)
            .where(CSGOMatch.grid_series_id.is_(None))
            .where(CSGOMatch.closed == False)
            .where(CSGOMatch.resolved == False)
            .where(
                (CSGOMatch.game_start_time.is_(None)) |
                (CSGOMatch.game_start_time <= cutoff)
            )
            .order_by(CSGOMatch.game_start_time)
        )

        result = session.execute(stmt)
        return list(result.scalars().all())

    def match_unmatched_markets(
        self,
        hours_before: float = 12.0,
        hours_after: float = 48.0,
        time_window_hours: float = 24.0,
    ) -> list[MatchResult]:
        """
        Find GRID matches for all unmatched Polymarket markets.

        Args:
            hours_before: Fetch GRID series from this many hours ago
            hours_after: Fetch GRID series up to this many hours ahead
            time_window_hours: Only match if start times within this window

        Returns:
            List of successful matches
        """
        # Fetch GRID series
        grid_series = self.client.get_cs2_series(
            hours_before=hours_before,
            hours_after=hours_after,
        )

        if not grid_series:
            logger.warning("No GRID series found")
            return []

        logger.info(f"Fetched {len(grid_series)} GRID series")

        matches = []

        # Keep session open while processing
        with get_session() as session:
            unmatched = self.get_unmatched_markets(
                session,
                hours_until_start=hours_after,
            )

            if not unmatched:
                logger.info("No unmatched markets found")
                return []

            logger.info(f"Found {len(unmatched)} unmatched markets")

            # Match each market (within session context)
            for csgo_match in unmatched:
                result = match_series_to_market(
                    csgo_match,
                    grid_series,
                    time_window_hours=time_window_hours,
                )

                if result:
                    matches.append(result)
                    logger.info(
                        f"Matched: {csgo_match.team_yes} vs {csgo_match.team_no} "
                        f"-> GRID {result.grid_series_id} "
                        f"({result.grid_team_a_name} vs {result.grid_team_b_name}) "
                        f"confidence={result.confidence:.2f}"
                    )

        logger.info(f"Matched {len(matches)} of {len(unmatched)} markets")
        return matches

    def save_matches(self, matches: list[MatchResult]) -> int:
        """
        Save match results to database.

        Args:
            matches: List of match results to save

        Returns:
            Number of matches saved
        """
        if not matches:
            return 0

        saved = 0
        with get_session() as session:
            for match in matches:
                stmt = (
                    select(CSGOMatch)
                    .where(CSGOMatch.id == match.csgo_match_id)
                )
                result = session.execute(stmt)
                csgo_match = result.scalar_one_or_none()

                if csgo_match:
                    csgo_match.grid_series_id = match.grid_series_id
                    csgo_match.grid_yes_team_id = match.grid_yes_team_id
                    csgo_match.grid_match_confidence = Decimal(str(match.confidence))
                    saved += 1

            session.commit()

        logger.info(f"Saved {saved} GRID matches to database")
        return saved

    def run(self) -> int:
        """
        Run the full matching process.

        Returns:
            Number of new matches saved
        """
        matches = self.match_unmatched_markets()
        return self.save_matches(matches)
