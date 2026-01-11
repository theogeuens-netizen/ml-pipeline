"""
GRID API Client.

Provides access to GRID Central Data API and Series State API
with rate limiting and error handling.

Endpoints:
- Central Data API: https://api-op.grid.gg/central-data/graphql
- Series State API: https://api-op.grid.gg/live-data-feed/series-state/graphql

Rate Limit: 20 requests/minute
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class TeamState:
    """Team state within a game/series."""
    id: str
    name: str
    score: int  # Round score for games, map score for series
    side: Optional[str] = None  # "terrorists" or "counter-terrorists" for games
    won: bool = False


@dataclass
class GameState:
    """Individual game (map) state."""
    sequence_number: int  # 1, 2, 3
    started: bool
    finished: bool
    map_name: Optional[str]
    teams: list[TeamState] = field(default_factory=list)

    @property
    def team_a(self) -> Optional[TeamState]:
        return self.teams[0] if len(self.teams) > 0 else None

    @property
    def team_b(self) -> Optional[TeamState]:
        return self.teams[1] if len(self.teams) > 1 else None


@dataclass
class SeriesState:
    """Complete series state from GRID API."""
    series_id: str
    started: bool
    finished: bool
    format: str  # "best-of-1", "best-of-3", "best-of-5"
    teams: list[TeamState] = field(default_factory=list)
    games: list[GameState] = field(default_factory=list)
    raw_response: Optional[dict] = None

    @property
    def team_a(self) -> Optional[TeamState]:
        return self.teams[0] if len(self.teams) > 0 else None

    @property
    def team_b(self) -> Optional[TeamState]:
        return self.teams[1] if len(self.teams) > 1 else None

    @property
    def current_game(self) -> Optional[GameState]:
        """Get the current (most recent started but not finished) game."""
        for game in reversed(self.games):
            if game.started and not game.finished:
                return game
        # If all games are finished, return the last one
        if self.games:
            return self.games[-1]
        return None

    @property
    def format_short(self) -> str:
        """Get format as bo1, bo3, bo5."""
        mapping = {
            "best-of-1": "bo1",
            "best-of-3": "bo3",
            "best-of-5": "bo5",
        }
        return mapping.get(self.format, self.format)


@dataclass
class SeriesSummary:
    """Summary of a series from Central Data API."""
    series_id: str
    start_time: Optional[datetime]
    format: str
    tournament: str
    team_a_id: str
    team_a_name: str
    team_b_id: str
    team_b_name: str


class GRIDClient:
    """
    GRID API client with rate limiting.

    Rate limit: 20 requests per minute (1 request per 3 seconds).
    """

    CENTRAL_URL = "https://api-op.grid.gg/central-data/graphql"
    SERIES_STATE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"
    CS2_TITLE_ID = "28"
    RATE_LIMIT = 20  # requests per minute

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.grid_api_key
        if not self.api_key:
            raise ValueError("GRID API key not configured. Set GRID_API_KEY env var.")

        self._request_times: list[float] = []
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def close(self):
        """Close the client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _rate_limit_wait(self):
        """Wait if necessary to respect rate limit."""
        now = time.time()
        # Remove requests older than 60 seconds
        self._request_times = [t for t in self._request_times if now - t < 60]

        if len(self._request_times) >= self.RATE_LIMIT:
            oldest = self._request_times[0]
            wait_time = 60 - (now - oldest) + 0.1
            if wait_time > 0:
                logger.debug(f"Rate limit: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)

        self._request_times.append(time.time())

    async def _execute_query(
        self,
        url: str,
        query: str,
        variables: Optional[dict] = None,
    ) -> Optional[dict]:
        """Execute a GraphQL query with rate limiting."""
        await self._rate_limit_wait()

        client = await self._get_client()
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                logger.error(f"GRID API error: {response.status_code} {response.text}")
                return None

            data = response.json()

            if "errors" in data:
                logger.error(f"GRID GraphQL error: {data['errors']}")
                return None

            return data.get("data")

        except httpx.TimeoutException:
            logger.error("GRID API timeout")
            return None
        except Exception as e:
            logger.error(f"GRID API error: {e}")
            return None

    async def get_series_state(self, series_id: str) -> Optional[SeriesState]:
        """
        Get current state of a series from Series State API.

        Args:
            series_id: GRID series ID

        Returns:
            SeriesState object or None if failed
        """
        query = """
        query GetSeriesState($id: ID!) {
            seriesState(id: $id) {
                id
                started
                finished
                format
                teams {
                    id
                    name
                    score
                    won
                }
                games {
                    sequenceNumber
                    started
                    finished
                    map {
                        name
                    }
                    teams {
                        id
                        name
                        score
                        side
                        won
                    }
                }
            }
        }
        """

        data = await self._execute_query(
            self.SERIES_STATE_URL,
            query,
            {"id": series_id},
        )

        if not data or not data.get("seriesState"):
            return None

        state = data["seriesState"]

        # Parse teams
        teams = []
        for t in state.get("teams", []):
            teams.append(TeamState(
                id=t.get("id", ""),
                name=t.get("name", ""),
                score=t.get("score", 0),
                won=t.get("won", False),
            ))

        # Parse games
        games = []
        for g in state.get("games", []):
            game_teams = []
            for t in g.get("teams", []):
                game_teams.append(TeamState(
                    id=t.get("id", ""),
                    name=t.get("name", ""),
                    score=t.get("score", 0),
                    side=t.get("side"),
                    won=t.get("won", False),
                ))

            games.append(GameState(
                sequence_number=g.get("sequenceNumber", 0),
                started=g.get("started", False),
                finished=g.get("finished", False),
                map_name=g.get("map", {}).get("name") if g.get("map") else None,
                teams=game_teams,
            ))

        return SeriesState(
            series_id=state.get("id", series_id),
            started=state.get("started", False),
            finished=state.get("finished", False),
            format=state.get("format", ""),
            teams=teams,
            games=games,
            raw_response=state,
        )

    async def get_cs2_series(
        self,
        hours_before: float = 12,
        hours_after: float = 48,
    ) -> list[SeriesSummary]:
        """
        Get CS2 series from Central Data API.

        Args:
            hours_before: Include series started up to this many hours ago
            hours_after: Include series starting up to this many hours in future

        Returns:
            List of SeriesSummary objects
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        start_after = (now - timedelta(hours=hours_before)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        start_before = (now + timedelta(hours=hours_after)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

        query = """
        query GetCS2Series($titleId: ID!, $startAfter: String!, $startBefore: String!) {
            allSeries(
                filter: {
                    titleId: $titleId
                    startTimeScheduled: {
                        gte: $startAfter
                        lte: $startBefore
                    }
                }
                orderBy: StartTimeScheduled
                first: 50
            ) {
                totalCount
                edges {
                    node {
                        id
                        startTimeScheduled
                        format {
                            name
                            nameShortened
                        }
                        tournament {
                            name
                        }
                        teams {
                            baseInfo {
                                id
                                name
                            }
                        }
                    }
                }
            }
        }
        """

        data = await self._execute_query(
            self.CENTRAL_URL,
            query,
            {
                "titleId": self.CS2_TITLE_ID,
                "startAfter": start_after,
                "startBefore": start_before,
            },
        )

        if not data or not data.get("allSeries"):
            return []

        results = []
        for edge in data["allSeries"].get("edges", []):
            node = edge.get("node", {})
            teams = node.get("teams", [])

            if len(teams) < 2:
                continue

            team_a = teams[0].get("baseInfo", {})
            team_b = teams[1].get("baseInfo", {})

            # Parse start time
            start_time = None
            if node.get("startTimeScheduled"):
                try:
                    start_time = datetime.fromisoformat(
                        node["startTimeScheduled"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            results.append(SeriesSummary(
                series_id=node.get("id", ""),
                start_time=start_time,
                format=node.get("format", {}).get("nameShortened", ""),
                tournament=node.get("tournament", {}).get("name", ""),
                team_a_id=team_a.get("id", ""),
                team_a_name=team_a.get("name", ""),
                team_b_id=team_b.get("id", ""),
                team_b_name=team_b.get("name", ""),
            ))

        logger.info(f"Found {len(results)} CS2 series from GRID API")
        return results


# Synchronous wrapper for use in Celery tasks
class SyncGRIDClient:
    """
    Synchronous GRID API client for Celery tasks.

    Wraps the async client with asyncio.run().
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.grid_api_key

    def get_series_state(self, series_id: str) -> Optional[SeriesState]:
        """Get series state synchronously."""
        async def _get():
            client = GRIDClient(self.api_key)
            try:
                return await client.get_series_state(series_id)
            finally:
                await client.close()

        return asyncio.run(_get())

    def get_cs2_series(
        self,
        hours_before: float = 12,
        hours_after: float = 48,
    ) -> list[SeriesSummary]:
        """Get CS2 series synchronously."""
        async def _get():
            client = GRIDClient(self.api_key)
            try:
                return await client.get_cs2_series(hours_before, hours_after)
            finally:
                await client.close()

        return asyncio.run(_get())
