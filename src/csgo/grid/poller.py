"""
GRID State Poller.

Polls GRID API for game state changes, detects events,
and records them with Polymarket prices for analysis.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.csgo.grid.client import SeriesState, SyncGRIDClient
from src.db.database import get_session
from src.db.models import CSGOGridEvent, CSGOMatch, GRIDPollerState, Market
from src.fetchers.clob import SyncCLOBClient

logger = logging.getLogger(__name__)


@dataclass
class GameScore:
    """Score state for a game/map."""
    yes_rounds: int
    no_rounds: int
    finished: bool
    map_name: Optional[str]


@dataclass
class SeriesScore:
    """Full score state for a series."""
    yes_maps: int
    no_maps: int
    games: list[GameScore]
    finished: bool
    format: str

    @property
    def current_game(self) -> Optional[GameScore]:
        """Get the current (most recent unfinished) game."""
        for game in reversed(self.games):
            if not game.finished:
                return game
        # All finished, return last
        return self.games[-1] if self.games else None

    @property
    def current_map_number(self) -> int:
        """Get the current map number (1-indexed)."""
        for i, game in enumerate(self.games, 1):
            if not game.finished:
                return i
        return len(self.games)


@dataclass
class ScoreEvent:
    """A detected score change event."""
    event_type: str  # "round", "map", "series"
    winner: str  # "YES" or "NO"
    prev_score: SeriesScore
    new_score: SeriesScore
    map_number: int
    map_name: Optional[str]
    rounds_changed: int  # >1 if we missed some polls


def extract_series_score(state: SeriesState, yes_team_id: str) -> SeriesScore:
    """
    Extract normalized score from GRID SeriesState.

    Args:
        state: GRID SeriesState
        yes_team_id: GRID team ID that maps to Polymarket YES

    Returns:
        SeriesScore with YES/NO oriented scores
    """
    # Determine which team index is YES
    yes_idx = 0
    no_idx = 1
    if state.teams and len(state.teams) > 1:
        if state.teams[1].id == yes_team_id:
            yes_idx = 1
            no_idx = 0

    # Extract map scores (with defensive checks)
    yes_maps = 0
    no_maps = 0
    if state.teams and len(state.teams) > yes_idx:
        yes_maps = state.teams[yes_idx].score
    if state.teams and len(state.teams) > no_idx:
        no_maps = state.teams[no_idx].score

    # Extract game scores
    games = []
    for game in state.games:
        yes_rounds = 0
        no_rounds = 0
        if game.teams and len(game.teams) > 1:
            # Game teams may be in different order than series teams
            # Match by team ID
            for t in game.teams:
                if t.id == yes_team_id:
                    yes_rounds = t.score
                else:
                    no_rounds = t.score

        games.append(GameScore(
            yes_rounds=yes_rounds,
            no_rounds=no_rounds,
            finished=game.finished,
            map_name=game.map_name,
        ))

    return SeriesScore(
        yes_maps=yes_maps,
        no_maps=no_maps,
        games=games,
        finished=state.finished,
        format=state.format_short,
    )


def detect_events(prev: SeriesScore, new: SeriesScore) -> list[ScoreEvent]:
    """
    Detect score change events between two states.

    Args:
        prev: Previous score state
        new: New score state

    Returns:
        List of detected events (may be empty, or multiple if we missed polls)
    """
    events = []

    # Check for map win
    map_idx = prev.yes_maps + prev.no_maps
    map_name = None
    if new.games and len(new.games) > map_idx:
        map_name = new.games[map_idx].map_name

    if new.yes_maps > prev.yes_maps:
        events.append(ScoreEvent(
            event_type="map",
            winner="YES",
            prev_score=prev,
            new_score=new,
            map_number=map_idx + 1,
            map_name=map_name,
            rounds_changed=0,
        ))
    elif new.no_maps > prev.no_maps:
        events.append(ScoreEvent(
            event_type="map",
            winner="NO",
            prev_score=prev,
            new_score=new,
            map_number=map_idx + 1,
            map_name=map_name,
            rounds_changed=0,
        ))

    # Check for series win
    if new.finished and not prev.finished:
        winner = "YES" if new.yes_maps > new.no_maps else "NO"
        events.append(ScoreEvent(
            event_type="series",
            winner=winner,
            prev_score=prev,
            new_score=new,
            map_number=new.yes_maps + new.no_maps,
            map_name=None,
            rounds_changed=0,
        ))

    # Check for round wins in current game
    prev_game = prev.current_game
    new_game = new.current_game

    if prev_game and new_game:
        # Same map, check round changes
        prev_map_idx = prev.current_map_number - 1
        new_map_idx = new.current_map_number - 1

        if prev_map_idx == new_map_idx:
            yes_diff = new_game.yes_rounds - prev_game.yes_rounds
            no_diff = new_game.no_rounds - prev_game.no_rounds

            if yes_diff > 0:
                events.append(ScoreEvent(
                    event_type="round",
                    winner="YES",
                    prev_score=prev,
                    new_score=new,
                    map_number=new.current_map_number,
                    map_name=new_game.map_name,
                    rounds_changed=yes_diff,
                ))
            if no_diff > 0:
                events.append(ScoreEvent(
                    event_type="round",
                    winner="NO",
                    prev_score=prev,
                    new_score=new,
                    map_number=new.current_map_number,
                    map_name=new_game.map_name,
                    rounds_changed=no_diff,
                ))

    return events


class GRIDPoller:
    """
    Polls GRID API for live match state changes.

    Usage:
        poller = GRIDPoller(api_key)
        poller.poll_once()  # Single poll cycle
        poller.run_loop(interval=3)  # Continuous polling
    """

    def __init__(self, api_key: Optional[str] = None):
        self.grid_client = SyncGRIDClient(api_key)
        self.clob_client = SyncCLOBClient()
        self._state_cache: dict[str, SeriesScore] = {}

    def get_tracked_matches(self, session: Session) -> list[CSGOMatch]:
        """
        Get CSGO matches that are linked to GRID and active.

        Args:
            session: Database session

        Returns:
            List of CSGOMatch records with GRID series IDs
        """
        stmt = (
            select(CSGOMatch)
            .where(CSGOMatch.grid_series_id.is_not(None))
            .where(CSGOMatch.closed == False)
            .where(CSGOMatch.resolved == False)
        )
        result = session.execute(stmt)
        return list(result.scalars().all())

    def load_state(self, session: Session, series_id: str) -> Optional[dict]:
        """Load persisted state for a series."""
        stmt = select(GRIDPollerState).where(GRIDPollerState.series_id == series_id)
        result = session.execute(stmt)
        state_record = result.scalar_one_or_none()
        return state_record.last_state_json if state_record else None

    def save_state(
        self,
        session: Session,
        series_id: str,
        market_id: int,
        state: SeriesScore,
    ):
        """Persist state for a series."""
        state_json = {
            "yes_maps": state.yes_maps,
            "no_maps": state.no_maps,
            "finished": state.finished,
            "format": state.format,
            "games": [
                {
                    "yes_rounds": g.yes_rounds,
                    "no_rounds": g.no_rounds,
                    "finished": g.finished,
                    "map_name": g.map_name,
                }
                for g in state.games
            ],
        }

        stmt = select(GRIDPollerState).where(GRIDPollerState.series_id == series_id)
        result = session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.last_state_json = state_json
            existing.last_poll_at = datetime.now(timezone.utc)
            existing.polls_count += 1
        else:
            new_state = GRIDPollerState(
                series_id=series_id,
                market_id=market_id,
                last_state_json=state_json,
                last_poll_at=datetime.now(timezone.utc),
                polls_count=1,
            )
            session.add(new_state)

    def state_from_json(self, data: dict) -> SeriesScore:
        """Reconstruct SeriesScore from JSON."""
        return SeriesScore(
            yes_maps=data.get("yes_maps", 0),
            no_maps=data.get("no_maps", 0),
            finished=data.get("finished", False),
            format=data.get("format", ""),
            games=[
                GameScore(
                    yes_rounds=g.get("yes_rounds", 0),
                    no_rounds=g.get("no_rounds", 0),
                    finished=g.get("finished", False),
                    map_name=g.get("map_name"),
                )
                for g in data.get("games", [])
            ],
        )

    def get_price(self, token_id: str) -> tuple[Decimal, Decimal, Decimal]:
        """
        Get current price from CLOB.

        Returns:
            (mid_price, bid, ask)
        """
        try:
            price_data = self.clob_client.get_price(token_id)
            return (
                Decimal(str(price_data.get("mid", 0))),
                Decimal(str(price_data.get("bid", 0))),
                Decimal(str(price_data.get("ask", 0))),
            )
        except Exception as e:
            logger.warning(f"Failed to get price for {token_id}: {e}")
            return Decimal(0), Decimal(0), Decimal(0)

    def record_event(
        self,
        session: Session,
        match: CSGOMatch,
        event: ScoreEvent,
        price: Decimal,
        bid: Decimal,
        ask: Decimal,
    ):
        """Record a detected event to the database."""
        # Get event_id from the market
        market_stmt = select(Market).where(Market.id == match.market_id)
        market = session.execute(market_stmt).scalar_one_or_none()
        event_id = market.event_id if market else ""

        prev = event.prev_score
        new = event.new_score
        prev_game = prev.current_game or GameScore(0, 0, False, None)
        new_game = new.current_game or GameScore(0, 0, False, None)

        grid_event = CSGOGridEvent(
            market_id=match.market_id,
            event_id=event_id,
            grid_series_id=match.grid_series_id,
            detected_at=datetime.now(timezone.utc),
            event_type=event.event_type,
            winner=event.winner,
            prev_round_yes=prev_game.yes_rounds,
            prev_round_no=prev_game.no_rounds,
            prev_map_yes=prev.yes_maps,
            prev_map_no=prev.no_maps,
            new_round_yes=new_game.yes_rounds,
            new_round_no=new_game.no_rounds,
            new_map_yes=new.yes_maps,
            new_map_no=new.no_maps,
            format=new.format,
            map_number=event.map_number,
            map_name=event.map_name,
            is_overtime=(new_game.yes_rounds + new_game.no_rounds) > 24,
            rounds_in_event=max(1, event.rounds_changed),
            total_rounds_before=prev_game.yes_rounds + prev_game.no_rounds,
            round_diff_before=prev_game.yes_rounds - prev_game.no_rounds,
            map_diff_before=prev.yes_maps - prev.no_maps,
            price_at_detection=price,
            spread_at_detection=ask - bid if ask and bid else Decimal(0),
            best_bid_at_detection=bid,
            best_ask_at_detection=ask,
            price_source="clob",
        )
        session.add(grid_event)
        logger.info(
            f"Recorded {event.event_type} event: {event.winner} won "
            f"(map {event.map_number}, price={price})"
        )

    def poll_series(
        self,
        session: Session,
        match: CSGOMatch,
    ) -> list[ScoreEvent]:
        """
        Poll a single series for state changes.

        Args:
            session: Database session
            match: CSGOMatch with GRID series ID

        Returns:
            List of detected events
        """
        series_id = match.grid_series_id
        yes_team_id = match.grid_yes_team_id

        if not series_id or not yes_team_id:
            return []

        # Get current state from GRID
        state = self.grid_client.get_series_state(series_id)
        if not state:
            logger.warning(f"Failed to get state for series {series_id}")
            return []

        # Extract normalized score
        new_score = extract_series_score(state, yes_team_id)

        # Get previous state
        prev_json = self.load_state(session, series_id)
        if prev_json:
            prev_score = self.state_from_json(prev_json)
        else:
            # First poll - no previous state, just record current
            self.save_state(session, series_id, match.market_id, new_score)
            logger.info(f"Initial state recorded for series {series_id}")
            return []

        # Detect events
        events = detect_events(prev_score, new_score)

        # Record events with prices
        if events:
            # Get market's YES token for price
            market_stmt = select(Market).where(Market.id == match.market_id)
            market = session.execute(market_stmt).scalar_one_or_none()

            if market and market.yes_token_id:
                price, bid, ask = self.get_price(market.yes_token_id)
                for event in events:
                    self.record_event(session, match, event, price, bid, ask)

        # Save new state
        self.save_state(session, series_id, match.market_id, new_score)

        return events

    def poll_once(self) -> dict:
        """
        Run a single poll cycle for all tracked matches.

        Returns:
            Summary dict with counts
        """
        events_detected = 0
        matches_polled = 0
        errors = 0

        with get_session() as session:
            matches = self.get_tracked_matches(session)

            if not matches:
                logger.debug("No tracked matches with GRID links")
                return {"matches": 0, "events": 0, "errors": 0}

            # Group by series_id to avoid duplicate API calls
            series_to_matches: dict[str, list[CSGOMatch]] = {}
            for match in matches:
                sid = match.grid_series_id
                if sid:
                    if sid not in series_to_matches:
                        series_to_matches[sid] = []
                    series_to_matches[sid].append(match)

            # Poll each unique series
            for series_id, series_matches in series_to_matches.items():
                try:
                    # Use first match for polling (they share the same series)
                    events = self.poll_series(session, series_matches[0])
                    events_detected += len(events)
                    matches_polled += 1
                except Exception as e:
                    logger.error(f"Error polling series {series_id}: {e}")
                    errors += 1

            session.commit()

        return {
            "matches": matches_polled,
            "events": events_detected,
            "errors": errors,
        }

    def run_loop(self, interval: float = 3.0, max_iterations: Optional[int] = None):
        """
        Run continuous polling loop.

        Args:
            interval: Seconds between polls (default 3s for rate limit)
            max_iterations: Stop after this many iterations (None = forever)
        """
        import time

        iterations = 0
        logger.info(f"Starting GRID poller loop (interval={interval}s)")

        while max_iterations is None or iterations < max_iterations:
            try:
                result = self.poll_once()
                if result["events"] > 0:
                    logger.info(
                        f"Poll cycle: {result['matches']} matches, "
                        f"{result['events']} events, {result['errors']} errors"
                    )
            except Exception as e:
                logger.error(f"Poll cycle error: {e}")

            iterations += 1
            time.sleep(interval)

        logger.info(f"Poller stopped after {iterations} iterations")
