"""
CS:GO Team Name Matcher

Parses team names from Polymarket market questions and matches them to
the csgo_teams leaderboard using fuzzy string matching.

Handles variations like:
- "FURIA" vs "FURIA Esports"
- "Natus Vincere" vs "NaVi" vs "NAVI"
- "Team Liquid" vs "Liquid"
"""

import re
from dataclasses import dataclass
from typing import Optional

import structlog
from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from src.db.models import CSGOTeam

logger = structlog.get_logger()


# Known team name aliases (lowercase -> canonical name)
TEAM_ALIASES = {
    "navi": "Natus Vincere",
    "na'vi": "Natus Vincere",
    "natus vincere": "Natus Vincere",
    "team liquid": "Liquid",
    "cloud9": "Cloud9",
    "c9": "Cloud9",
    "virtuspro": "Virtus.pro",
    "virtus pro": "Virtus.pro",
    "vp": "Virtus.pro",
    "ninjas in pyjamas": "NIP",
    "nip": "NIP",
    "g2 esports": "G2",
    "faze clan": "FaZe",
}


@dataclass
class TeamStats:
    """Team statistics from the leaderboard."""
    team_name: str
    wins: int
    losses: int
    total_matches: int
    win_rate: float  # 0.0 to 1.0


@dataclass
class ParsedMatch:
    """Parsed teams from a market question."""
    team_a: str
    team_b: str
    team_a_stats: Optional[TeamStats]
    team_b_stats: Optional[TeamStats]


class CSGOTeamMatcher:
    """
    Matches team names from market questions to the leaderboard.

    Usage:
        matcher = CSGOTeamMatcher(db_session)

        # Parse and match teams from a market question
        result = matcher.parse_and_match("Counter-Strike: FURIA vs G2")
        if result:
            print(f"{result.team_a} ({result.team_a_stats.win_rate:.1%}) vs "
                  f"{result.team_b} ({result.team_b_stats.win_rate:.1%})")
    """

    # Regex pattern for parsing CS:GO market questions
    QUESTION_PATTERN = re.compile(
        r"Counter[- ]?Strike[:\s]*(.+?)\s+vs\.?\s+(.+)",
        re.IGNORECASE
    )

    def __init__(self, db: Optional[Session] = None):
        """
        Initialize the matcher.

        Args:
            db: SQLAlchemy session. If None, team lookups will return None.
        """
        self.db = db
        self._team_cache: dict[str, TeamStats] = {}
        self._team_names: list[str] = []

        if db:
            self._load_teams()

    def _load_teams(self):
        """Load all teams into memory for fast fuzzy matching."""
        teams = self.db.query(CSGOTeam).all()
        self._team_names = [t.team_name for t in teams]
        self._team_cache = {
            t.team_name.lower(): TeamStats(
                team_name=t.team_name,
                wins=t.wins,
                losses=t.losses,
                total_matches=t.total_matches,
                win_rate=float(t.win_rate_pct) / 100.0,
            )
            for t in teams
        }
        logger.info("Loaded CS:GO teams for matching", count=len(teams))

    def parse_teams(self, question: str) -> Optional[tuple[str, str]]:
        """
        Parse team names from a market question.

        Args:
            question: Market question like "Counter-Strike: FURIA vs G2"

        Returns:
            Tuple of (team_a, team_b) or None if parse failed
        """
        match = self.QUESTION_PATTERN.match(question.strip())
        if not match:
            return None

        team_a = match.group(1).strip()
        team_b = match.group(2).strip()

        # Clean up team names - remove (BO3), (BO1), etc. suffixes
        team_a = re.sub(r'\s*\(BO\d+\)\s*$', '', team_a).strip()
        team_b = re.sub(r'\s*\(BO\d+\)\s*$', '', team_b).strip()

        return (team_a, team_b)

    def match_team(self, name: str, min_score: int = 80) -> Optional[TeamStats]:
        """
        Match a team name to the leaderboard.

        Args:
            name: Team name to match
            min_score: Minimum fuzzy match score (0-100)

        Returns:
            TeamStats if matched, None otherwise
        """
        if not self._team_names:
            return None

        name_lower = name.lower().strip()

        # Try exact match first (case-insensitive)
        if name_lower in self._team_cache:
            return self._team_cache[name_lower]

        # Try alias lookup
        if name_lower in TEAM_ALIASES:
            canonical = TEAM_ALIASES[name_lower].lower()
            if canonical in self._team_cache:
                return self._team_cache[canonical]

        # Try fuzzy match
        result = process.extractOne(
            name,
            self._team_names,
            scorer=fuzz.ratio,
            score_cutoff=min_score,
        )

        if result:
            matched_name, score, _ = result
            logger.debug(
                "Fuzzy matched team",
                input=name,
                matched=matched_name,
                score=score,
            )
            return self._team_cache.get(matched_name.lower())

        logger.warning("Failed to match team", team=name)
        return None

    def parse_and_match(self, question: str) -> Optional[ParsedMatch]:
        """
        Parse teams from question and match both to leaderboard.

        Args:
            question: Market question like "Counter-Strike: FURIA vs G2"

        Returns:
            ParsedMatch with team stats, or None if parse failed
        """
        parsed = self.parse_teams(question)
        if not parsed:
            return None

        team_a, team_b = parsed
        stats_a = self.match_team(team_a)
        stats_b = self.match_team(team_b)

        return ParsedMatch(
            team_a=team_a,
            team_b=team_b,
            team_a_stats=stats_a,
            team_b_stats=stats_b,
        )

    def get_winrate_diff(
        self,
        question: str,
    ) -> Optional[tuple[str, float, float]]:
        """
        Get the favorite and win rate differential for a match.

        Args:
            question: Market question

        Returns:
            Tuple of (favorite_side, winrate_diff, favorite_winrate) or None
            - favorite_side: "A" or "B" indicating which team is favorite
            - winrate_diff: Positive float (favorite_wr - underdog_wr)
            - favorite_winrate: Win rate of the favorite (0.0 to 1.0)
        """
        match = self.parse_and_match(question)
        if not match or not match.team_a_stats or not match.team_b_stats:
            return None

        wr_a = match.team_a_stats.win_rate
        wr_b = match.team_b_stats.win_rate

        if wr_a >= wr_b:
            return ("A", wr_a - wr_b, wr_a)
        else:
            return ("B", wr_b - wr_a, wr_b)

    def is_csgo_market(self, question: str) -> bool:
        """Check if a market question is a CS:GO match."""
        return self.QUESTION_PATTERN.match(question.strip()) is not None

    def get_h2h(
        self,
        team1: str,
        team2: str,
    ) -> Optional[tuple[int, int, int]]:
        """
        Get head-to-head record between two teams.

        Args:
            team1: First team name
            team2: Second team name

        Returns:
            Tuple of (team1_wins, team2_wins, total_matches) or None
        """
        if not self.db:
            return None

        # Import here to avoid circular dependency
        from src.db.models import CSGOH2H

        # Normalize order (team1 < team2 alphabetically)
        if team1 > team2:
            team1, team2 = team2, team1
            swapped = True
        else:
            swapped = False

        record = self.db.query(CSGOH2H).filter(
            CSGOH2H.team1_name == team1,
            CSGOH2H.team2_name == team2,
        ).first()

        if not record:
            return None

        if swapped:
            return (record.team2_wins, record.team1_wins, record.total_matches)
        else:
            return (record.team1_wins, record.team2_wins, record.total_matches)

    def update_winrates_from_polymarket(self) -> dict:
        """
        Update team win rates using resolved CS:GO markets from polymarket-ml database.

        Queries resolved CS:GO matches (BO3 only, not individual map winners),
        parses team names, determines winner, and updates csgo_teams table.

        Returns:
            Dict with update statistics
        """
        if not self.db:
            return {"error": "No database session"}

        from datetime import datetime
        from src.db.models import Market

        stats = {
            "resolved_matches": 0,
            "parsed": 0,
            "updated_teams": 0,
            "new_teams": 0,
            "skipped": 0,
            "errors": [],
        }

        # Get resolved CS:GO matches (BO3 only, not map winners)
        resolved_markets = self.db.query(Market).filter(
            Market.resolved == True,
            Market.outcome.isnot(None),
            Market.question.ilike("%counter-strike%"),
            Market.question.ilike("%(BO3)%"),  # Only BO3 matches
            ~Market.question.ilike("%Map%Winner%"),  # Exclude individual maps
        ).all()

        stats["resolved_matches"] = len(resolved_markets)
        logger.info(f"Found {len(resolved_markets)} resolved CS:GO BO3 matches")

        # Accumulate wins/losses by team first (to handle duplicates)
        team_changes: dict[str, dict] = {}  # team_name -> {"wins": N, "losses": M}

        for market in resolved_markets:
            try:
                # Parse team names
                parsed = self.parse_teams(market.question)
                if not parsed:
                    stats["skipped"] += 1
                    continue

                team_a, team_b = parsed
                stats["parsed"] += 1

                # Determine winner based on outcome
                # YES = Team A wins, NO = Team B wins
                if market.outcome == "YES":
                    winner = team_a
                    loser = team_b
                elif market.outcome == "NO":
                    winner = team_b
                    loser = team_a
                else:
                    stats["skipped"] += 1
                    continue

                # Accumulate changes
                for team_name, is_winner in [(winner, True), (loser, False)]:
                    clean_name = team_name.strip()
                    if clean_name not in team_changes:
                        team_changes[clean_name] = {"wins": 0, "losses": 0}
                    if is_winner:
                        team_changes[clean_name]["wins"] += 1
                    else:
                        team_changes[clean_name]["losses"] += 1

            except Exception as e:
                stats["errors"].append(f"{market.question[:50]}: {str(e)}")
                logger.error(f"Error processing market {market.id}: {e}")

        # Now apply accumulated changes
        for team_name, changes in team_changes.items():
            try:
                # Try to find existing team
                team = self.db.query(CSGOTeam).filter(
                    CSGOTeam.team_name == team_name
                ).first()

                if team:
                    team.wins += changes["wins"]
                    team.losses += changes["losses"]
                    team.total_matches = team.wins + team.losses
                    team.win_rate_pct = (team.wins / team.total_matches) * 100 if team.total_matches > 0 else 0
                    team.updated_at = datetime.utcnow()
                    stats["updated_teams"] += 1
                else:
                    # Create new team
                    total = changes["wins"] + changes["losses"]
                    new_team = CSGOTeam(
                        team_name=team_name,
                        wins=changes["wins"],
                        losses=changes["losses"],
                        total_matches=total,
                        win_rate_pct=(changes["wins"] / total) * 100 if total > 0 else 0,
                    )
                    self.db.add(new_team)
                    stats["new_teams"] += 1
                    logger.info(f"Created new team: {team_name}")

            except Exception as e:
                stats["errors"].append(f"Team {team_name}: {str(e)}")
                logger.error(f"Error updating team {team_name}: {e}")

        # Commit changes
        try:
            self.db.commit()
            # Refresh cache
            self._load_teams()
            logger.info(
                f"Updated win rates from Polymarket",
                parsed=stats["parsed"],
                updated=stats["updated_teams"],
                new=stats["new_teams"],
            )
        except Exception as e:
            self.db.rollback()
            stats["errors"].append(f"Commit failed: {str(e)}")
            logger.error(f"Failed to commit updates: {e}")

        return stats
