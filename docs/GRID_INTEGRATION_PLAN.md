# GRID Esports Data Integration - Implementation Plan

> **Purpose**: Comprehensive implementation plan for integrating GRID API data with the CSGO trading pipeline. This document serves as the source of truth if context is lost.

---

## Goal

Correlate CS2 game state (scores, round wins, map wins) with Polymarket price movements to:

1. **Measure price sensitivity**: When score changes from X to Y, how much does price move?
2. **Measure market latency**: How long after a round/map ends does Polymarket reprice?
3. **Build fair value model**: Given game state, what "should" the price be?
4. **Enable strategies**: Either front-run information OR fade mean reversion

---

## GRID API Access

**Tier**: Open Access (free)

| API | URL | Available |
|-----|-----|-----------|
| Central Data API | `https://api-op.grid.gg/central-data/graphql` | ✅ |
| Series State API | `https://api-op.grid.gg/live-data-feed/series-state/graphql` | ✅ |
| WebSocket Events | N/A | ❌ |

**Rate Limit**: 20 requests/minute
**Auth**: `x-api-key` header
**CS2 Title ID**: 28

---

## Key Constraints

1. **Max 1-2 matches tracked simultaneously** (simplifies rate limiting)
2. **Poll interval**: 3-6 seconds (excellent for round detection with 1-2 matches)
3. **No GRID WebSocket**: Must poll for state changes
4. **Price source**: CLOB API via `SyncCLOBClient.get_price(token_id)` returns `{bid, ask, mid}`

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     GRID Integration Flow                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────┐                                           │
│  │   GRIDMatcher    │────── Every 30 min (Celery) ────────┐    │
│  │ Match GRID→Poly  │                                      │    │
│  └──────────────────┘                                      ▼    │
│                                                     ┌───────────┐│
│         │                                           │ CSGOMatch ││
│         │ Sets grid_series_id                       │ + Market  ││
│         │ and grid_yes_team_id                      │ (event_id)││
│         ▼                                           └───────────┘│
│  ┌──────────────────┐                                     │     │
│  │   GRIDPoller     │◄────── Active matches (max 2) ──────┘     │
│  │ (Celery worker)  │                                           │
│  │ Poll every 3-6s  │                                           │
│  └────────┬─────────┘                                           │
│           │                                                      │
│           │ Detect score changes                                │
│           ▼                                                      │
│  ┌──────────────────┐     ┌──────────────────┐                  │
│  │    GRID API      │     │    CLOB API      │                  │
│  │  Series State    │     │  get_price()     │                  │
│  └────────┬─────────┘     └────────┬─────────┘                  │
│           │                        │                             │
│           └──────────┬─────────────┘                             │
│                      ▼                                           │
│           ┌──────────────────┐                                   │
│           │  Detect Change   │                                   │
│           │  Round/Map won?  │                                   │
│           └────────┬─────────┘                                   │
│                    │                                             │
│                    ▼                                             │
│    ┌───────────────────────────────┐                            │
│    │ For each related market       │ (via event_id)             │
│    │ - Series Winner (moneyline)   │                            │
│    │ - Map N Winner (child_money)  │                            │
│    └───────────────┬───────────────┘                            │
│                    │                                             │
│                    ▼                                             │
│           ┌──────────────────┐                                   │
│           │  CSGOGridEvent   │                                   │
│           │  (PostgreSQL)    │                                   │
│           └────────┬─────────┘                                   │
│                    │                                             │
│                    │ After 30s/1m/5m                             │
│                    ▼                                             │
│           ┌──────────────────┐                                   │
│           │  Price Filler    │ Queries csgo_price_ticks         │
│           │  (Celery task)   │                                   │
│           └──────────────────┘                                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Existing Infrastructure (Verified)

### CLOB Client (`src/fetchers/clob.py`)
```python
# Async client
clob = CLOBClient()
price = await clob.get_price(token_id)  # Returns {bid, ask, mid}
orderbook = await clob.get_orderbook(token_id)  # Returns {bids, asks}

# Sync client (for Celery tasks)
clob = SyncCLOBClient()
price = clob.get_price(token_id)  # Returns {bid, ask, mid}
```

### CSGOMatch Model (`src/db/models.py`)
```python
class CSGOMatch(Base):
    id, market_id, gamma_id, condition_id
    team_yes, team_no
    game_start_time, end_date
    tournament, format, market_type, group_item_title, map_number
    yes_price, no_price, best_bid, best_ask, spread
    subscribed, closed, resolved, accepting_orders, outcome
    gamma_data  # Full Gamma API response
```

### CSGOPriceTick Model (`src/db/models.py`)
```python
class CSGOPriceTick(Base):
    id, market_id, timestamp, token_type, event_type
    price, best_bid, best_ask, spread
    trade_size, trade_side, price_velocity_1m
```
**Retention**: 7 days (cleaned by daily task)

### Event ID Grouping (Verified)
Markets with same `event_id` share the match:
- `moneyline` → Series Winner
- `child_moneyline` → Map 1 Winner, Map 2 Winner
- `totals` → O/U 2.5
- `map_handicap` → Map Handicap

Query: `Market.event_id` links to `CSGOMatch` via `condition_id`

### Map Number Extraction
`group_item_title` contains map info:
- "Match Winner" → series market
- "Map 1 Winner" / "Game 1 Winner" → map 1
- "Map 2 Winner" / "Game 2 Winner" → map 2

---

## Database Schema

### 1. CSGOMatch Additions
```python
# Add to CSGOMatch model
grid_series_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
grid_yes_team_id: Mapped[Optional[str]] = mapped_column(String(50))
grid_match_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
```

### 2. CSGOGridEvent (New Table)
```python
class CSGOGridEvent(Base):
    __tablename__ = "csgo_grid_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Linking
    market_id: Mapped[int] = mapped_column(Integer, index=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)  # Polymarket event group
    grid_series_id: Mapped[str] = mapped_column(String(50), index=True)

    # Timing
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    grid_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Event type
    event_type: Mapped[str] = mapped_column(String(20))  # "round", "map", "series"
    winner: Mapped[str] = mapped_column(String(5))  # "YES", "NO"

    # Score BEFORE
    prev_round_yes: Mapped[int] = mapped_column(Integer)
    prev_round_no: Mapped[int] = mapped_column(Integer)
    prev_map_yes: Mapped[int] = mapped_column(Integer)
    prev_map_no: Mapped[int] = mapped_column(Integer)

    # Score AFTER
    new_round_yes: Mapped[int] = mapped_column(Integer)
    new_round_no: Mapped[int] = mapped_column(Integer)
    new_map_yes: Mapped[int] = mapped_column(Integer)
    new_map_no: Mapped[int] = mapped_column(Integer)

    # Context
    format: Mapped[str] = mapped_column(String(10))  # "bo1", "bo3", "bo5"
    map_number: Mapped[int] = mapped_column(Integer)
    map_name: Mapped[Optional[str]] = mapped_column(String(50))
    is_overtime: Mapped[bool] = mapped_column(Boolean, default=False)
    rounds_in_event: Mapped[int] = mapped_column(Integer, default=1)  # >1 if missed rounds

    # Derived (computed on insert)
    total_rounds_before: Mapped[int] = mapped_column(Integer)
    round_diff_before: Mapped[int] = mapped_column(Integer)  # YES - NO
    map_diff_before: Mapped[int] = mapped_column(Integer)

    # Price at detection (from CLOB)
    price_at_detection: Mapped[float] = mapped_column(Numeric(10, 6))
    spread_at_detection: Mapped[float] = mapped_column(Numeric(10, 6))
    best_bid_at_detection: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    best_ask_at_detection: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_source: Mapped[str] = mapped_column(String(20))  # "clob", "tick"

    # Price after (filled by task)
    price_after_30sec: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_after_1min: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_after_5min: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # Analysis helpers (computed on fill)
    price_move_30sec: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_move_1min: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_move_5min: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    move_direction_correct: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Market state
    market_accepting_orders: Mapped[bool] = mapped_column(Boolean)

    # Debug
    grid_state_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    notes: Mapped[Optional[str]] = mapped_column(Text)
```

### 3. GRIDPollerState (State Persistence)
```python
class GRIDPollerState(Base):
    __tablename__ = "grid_poller_state"

    series_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    market_id: Mapped[int] = mapped_column(Integer, index=True)
    last_state_json: Mapped[dict] = mapped_column(JSONB)
    last_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    polls_count: Mapped[int] = mapped_column(Integer, default=0)
```

---

## Components

### 1. GRIDClient (`src/csgo/grid/client.py`)

```python
import asyncio
import time
from dataclasses import dataclass
from typing import Optional
import aiohttp

@dataclass
class SeriesState:
    series_id: str
    started: bool
    finished: bool
    format: str  # "bo1", "bo3", "bo5"
    team_a_id: str
    team_a_name: str
    team_a_maps: int
    team_b_id: str
    team_b_name: str
    team_b_maps: int
    current_map_number: int
    current_map_name: Optional[str]
    team_a_rounds: int  # Current map
    team_b_rounds: int  # Current map
    raw_response: dict

class GRIDClient:
    """GRID API client with rate limiting."""

    CENTRAL_URL = "https://api.grid.gg/central-data/graphql"
    SERIES_STATE_URL = "https://api.grid.gg/live-data-feed/series-state/graphql"
    CS2_TITLE_ID = 28
    RATE_LIMIT = 20  # requests per minute

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._request_times: list[float] = []

    async def _rate_limit_wait(self):
        """Wait if necessary to respect rate limit."""
        now = time.time()
        # Remove requests older than 60 seconds
        self._request_times = [t for t in self._request_times if now - t < 60]

        if len(self._request_times) >= self.RATE_LIMIT:
            oldest = self._request_times[0]
            wait_time = 60 - (now - oldest) + 0.1
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        self._request_times.append(time.time())

    async def get_series_state(self, series_id: str) -> Optional[SeriesState]:
        """Get current state of a series."""
        query = """
        query GetState($id: ID!) {
            seriesState(id: $id) {
                id started finished format
                teams { id name score }
                games {
                    sequenceNumber started finished
                    map { name }
                    teams { id name score side }
                }
            }
        }
        """
        # Implementation...

    async def get_live_cs2_series(self) -> list[dict]:
        """Get all live CS2 series."""
        # Query Central Data API for live series
        # Implementation...

    async def get_upcoming_cs2_series(self, hours: int = 12) -> list[dict]:
        """Get upcoming CS2 series."""
        # Implementation...
```

### 2. GRIDMatcher (`src/csgo/grid/matcher.py`)

```python
from difflib import SequenceMatcher
from typing import Optional, Tuple

def normalize_team_name(name: str) -> str:
    """Normalize team name for matching."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [" esports", " gaming", " team", " org", " esport"]:
        name = name.replace(suffix, "")
    return name

def team_similarity(name1: str, name2: str) -> float:
    """Calculate similarity between two team names."""
    n1 = normalize_team_name(name1)
    n2 = normalize_team_name(name2)
    return SequenceMatcher(None, n1, n2).ratio()

class GRIDMatcher:
    """Match GRID series to Polymarket markets."""

    MATCH_THRESHOLD = 0.6  # 60% similarity required

    def match_series_to_market(
        self,
        grid_series: dict,
        csgo_match: CSGOMatch
    ) -> Optional[Tuple[str, float]]:
        """
        Try to match GRID series to Polymarket market.

        Returns:
            Tuple of (grid_yes_team_id, confidence) if matched, None otherwise
        """
        grid_team_a = grid_series["teams"][0]["name"]
        grid_team_b = grid_series["teams"][1]["name"]
        poly_yes = csgo_match.team_yes
        poly_no = csgo_match.team_no

        # Try matching A→YES, B→NO
        score_a_yes = team_similarity(grid_team_a, poly_yes)
        score_b_no = team_similarity(grid_team_b, poly_no)
        confidence_1 = (score_a_yes + score_b_no) / 2

        # Try matching A→NO, B→YES
        score_a_no = team_similarity(grid_team_a, poly_no)
        score_b_yes = team_similarity(grid_team_b, poly_yes)
        confidence_2 = (score_a_no + score_b_yes) / 2

        if confidence_1 >= self.MATCH_THRESHOLD and confidence_1 >= confidence_2:
            return (grid_series["teams"][0]["id"], confidence_1)
        elif confidence_2 >= self.MATCH_THRESHOLD:
            return (grid_series["teams"][1]["id"], confidence_2)

        return None

    def get_related_markets(self, match: CSGOMatch, db) -> list[CSGOMatch]:
        """Get all markets in same event (series + maps)."""
        market = db.query(Market).filter(
            Market.condition_id == match.condition_id
        ).first()

        if not market or not market.event_id:
            return [match]

        # Find all CSGOMatch records with same event_id
        related = db.query(CSGOMatch).join(
            Market, Market.condition_id == CSGOMatch.condition_id
        ).filter(
            Market.event_id == market.event_id
        ).all()

        return related
```

### 3. GRIDPoller (`src/csgo/grid/poller.py`)

```python
class GRIDPoller:
    """Poll GRID API and detect score changes."""

    def __init__(self, client: GRIDClient, max_matches: int = 2):
        self.client = client
        self.max_matches = max_matches
        self.state_cache: dict[str, SeriesState] = {}

    def detect_changes(
        self,
        old_state: Optional[SeriesState],
        new_state: SeriesState
    ) -> list[dict]:
        """
        Compare states and detect score changes.

        Returns:
            List of detected events (round wins, map wins)
        """
        if old_state is None:
            return []  # First poll, no changes

        events = []

        # Detect round change on current map
        old_total_rounds = old_state.team_a_rounds + old_state.team_b_rounds
        new_total_rounds = new_state.team_a_rounds + new_state.team_b_rounds

        if new_total_rounds > old_total_rounds:
            # Round(s) were won
            rounds_diff = new_total_rounds - old_total_rounds

            # Determine who won (compare scores)
            if new_state.team_a_rounds > old_state.team_a_rounds:
                winner_team_id = new_state.team_a_id
            else:
                winner_team_id = new_state.team_b_id

            events.append({
                "event_type": "round",
                "winner_team_id": winner_team_id,
                "rounds_in_event": rounds_diff,
                "prev_round_a": old_state.team_a_rounds,
                "prev_round_b": old_state.team_b_rounds,
                "new_round_a": new_state.team_a_rounds,
                "new_round_b": new_state.team_b_rounds,
                "is_overtime": old_total_rounds >= 30,
            })

        # Detect map change
        if new_state.current_map_number > old_state.current_map_number:
            # Map was won
            if new_state.team_a_maps > old_state.team_a_maps:
                winner_team_id = new_state.team_a_id
            else:
                winner_team_id = new_state.team_b_id

            events.append({
                "event_type": "map",
                "winner_team_id": winner_team_id,
                "map_number": old_state.current_map_number,
                "map_name": old_state.current_map_name,
                "prev_map_a": old_state.team_a_maps,
                "prev_map_b": old_state.team_b_maps,
                "new_map_a": new_state.team_a_maps,
                "new_map_b": new_state.team_b_maps,
            })

        # Detect series end
        if new_state.finished and not old_state.finished:
            if new_state.team_a_maps > new_state.team_b_maps:
                winner_team_id = new_state.team_a_id
            else:
                winner_team_id = new_state.team_b_id

            events.append({
                "event_type": "series",
                "winner_team_id": winner_team_id,
            })

        return events

    async def get_current_price(self, market: CSGOMatch, db) -> dict:
        """Get current price from CLOB API."""
        from src.fetchers.clob import SyncCLOBClient

        clob = SyncCLOBClient()
        market_obj = db.query(Market).filter(
            Market.condition_id == market.condition_id
        ).first()

        if not market_obj or not market_obj.yes_token_id:
            return None

        price = clob.get_price(market_obj.yes_token_id)
        return {
            "yes_price": price["mid"],
            "best_bid": price["bid"],
            "best_ask": price["ask"],
            "spread": price["ask"] - price["bid"],
            "source": "clob",
        }
```

### 4. Price Filler Task (`src/csgo/grid/tasks.py`)

```python
@shared_task(name="src.csgo.grid.tasks.fill_grid_event_prices")
def fill_grid_event_prices_task():
    """
    Fill price_after_30sec, price_after_1min, price_after_5min.

    Queries csgo_price_ticks for historical prices.
    """
    with get_session() as db:
        # Find events needing 30sec fill
        events_30sec = db.query(CSGOGridEvent).filter(
            CSGOGridEvent.price_after_30sec.is_(None),
            CSGOGridEvent.detected_at < datetime.now(timezone.utc) - timedelta(seconds=35),
        ).all()

        for event in events_30sec:
            target_time = event.detected_at + timedelta(seconds=30)
            tick = db.query(CSGOPriceTick).filter(
                CSGOPriceTick.market_id == event.market_id,
                CSGOPriceTick.timestamp >= target_time - timedelta(seconds=5),
                CSGOPriceTick.timestamp <= target_time + timedelta(seconds=5),
            ).order_by(
                func.abs(extract('epoch', CSGOPriceTick.timestamp - target_time))
            ).first()

            if tick:
                event.price_after_30sec = tick.price
                event.price_move_30sec = tick.price - event.price_at_detection

        # Similar for 1min and 5min...

        db.commit()
```

---

## Multi-Market Event Recording

When a round is won, record events for ALL related markets:

```python
def record_events_for_all_markets(
    event: dict,
    grid_state: SeriesState,
    primary_match: CSGOMatch,
    db
):
    """Record grid event for all markets in the same event group."""

    # Get all related markets (same event_id)
    related_markets = get_related_markets(primary_match, db)

    for market in related_markets:
        # Determine winner (YES or NO) for this specific market
        # The grid_yes_team_id tells us which GRID team = Polymarket YES
        winner = "YES" if event["winner_team_id"] == market.grid_yes_team_id else "NO"

        # Get current price for THIS market
        price_data = get_current_price(market, db)

        # Determine if this event is relevant to this market type
        # Round events: relevant to current map's market + series market
        # Map events: relevant to series market only (map market is now resolved)

        # ... create CSGOGridEvent record
```

---

## Celery Integration

### Beat Schedule
```python
# In src/tasks/celery_app.py
CELERY_BEAT_SCHEDULE = {
    # ... existing tasks ...

    'grid-match-series': {
        'task': 'src.csgo.grid.tasks.match_grid_series',
        'schedule': timedelta(minutes=30),
    },
    'grid-fill-prices': {
        'task': 'src.csgo.grid.tasks.fill_grid_event_prices',
        'schedule': timedelta(minutes=5),
    },
}
```

### Poller as Celery Worker
```python
# In src/csgo/grid/worker.py
@shared_task(name="src.csgo.grid.tasks.grid_poller_loop")
def grid_poller_loop_task():
    """
    Main polling loop - runs continuously as a Celery worker.

    Call this once, it loops internally with asyncio.
    """
    import asyncio
    asyncio.run(_poller_main())

async def _poller_main():
    """Async poller main loop."""
    client = GRIDClient(api_key=settings.grid_api_key)
    poller = GRIDPoller(client, max_matches=2)

    while True:
        try:
            with get_session() as db:
                # Get active matches with grid_series_id
                matches = db.query(CSGOMatch).filter(
                    CSGOMatch.grid_series_id.isnot(None),
                    CSGOMatch.closed == False,
                    CSGOMatch.resolved == False,
                ).limit(poller.max_matches).all()

                for match in matches:
                    state = await client.get_series_state(match.grid_series_id)
                    old_state = poller.state_cache.get(match.grid_series_id)

                    events = poller.detect_changes(old_state, state)

                    for event in events:
                        price_data = await poller.get_current_price(match, db)
                        record_events_for_all_markets(event, state, match, db)

                    poller.state_cache[match.grid_series_id] = state

                # Sleep based on number of matches
                sleep_time = 3 * len(matches) if matches else 30
                await asyncio.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Poller error: {e}")
            await asyncio.sleep(10)
```

---

## Implementation Phases

### Phase 0: API Verification
- [ ] Test GRID Series State API with real series_id
- [ ] Verify response schema matches expectations
- [ ] Confirm map sequenceNumber = Polymarket map_number

### Phase 1: Database Migration
- [ ] Add columns to CSGOMatch
- [ ] Create CSGOGridEvent table
- [ ] Create GRIDPollerState table
- [ ] Run alembic migration

### Phase 2: GRIDClient
- [ ] Implement rate limiting (token bucket)
- [ ] Implement get_series_state()
- [ ] Implement get_live_cs2_series()
- [ ] Add error handling and retries
- [ ] Test against real API

### Phase 3: GRIDMatcher
- [ ] Implement team name normalization
- [ ] Implement fuzzy matching
- [ ] Implement match logic
- [ ] Create Celery task
- [ ] Test with real matches

### Phase 4: GRIDPoller
- [ ] Implement state management
- [ ] Implement change detection
- [ ] Implement multi-market recording
- [ ] Implement price fetching from CLOB
- [ ] Create Celery worker task
- [ ] Test with live match

### Phase 5: Price Filler
- [ ] Implement 30sec fill
- [ ] Implement 1min fill
- [ ] Implement 5min fill
- [ ] Compute price_move and direction
- [ ] Create Celery task

### Phase 6: Integration
- [ ] Add Celery beat schedules
- [ ] Add settings for GRID_API_KEY
- [ ] Test full flow
- [ ] Deploy

---

## Analysis Queries (Post-Collection)

### Are We Fast Enough to Front-Run?
```sql
SELECT
    CASE
        WHEN winner = 'YES' THEN SIGN(price_move_30sec)
        ELSE -SIGN(price_move_30sec)
    END as move_direction,
    COUNT(*) as n,
    AVG(ABS(price_move_30sec)) as avg_move
FROM csgo_grid_events
WHERE price_move_30sec IS NOT NULL
GROUP BY move_direction;
```

### Mean Reversion Detection
```sql
SELECT
    event_type,
    AVG(price_move_30sec) as move_30s,
    AVG(price_move_1min) as move_1m,
    AVG(price_move_5min) as move_5m,
    AVG(CASE WHEN SIGN(price_move_1min) != SIGN(price_move_5min) THEN 1 ELSE 0 END) as reversion_rate
FROM csgo_grid_events
WHERE price_move_5min IS NOT NULL
GROUP BY event_type;
```

### Price Sensitivity by Game State
```sql
SELECT
    round_diff_before,
    total_rounds_before,
    winner,
    AVG(price_move_5min) as avg_price_change,
    COUNT(*) as n
FROM csgo_grid_events
WHERE event_type = 'round' AND price_move_5min IS NOT NULL
GROUP BY round_diff_before, total_rounds_before, winner
HAVING COUNT(*) >= 3
ORDER BY ABS(avg_price_change) DESC;
```

---

## Environment Variables

Add to `.env`:
```bash
GRID_API_KEY=your_api_key_here
```

Add to `src/config/settings.py`:
```python
grid_api_key: str = Field(default="", env="GRID_API_KEY")
```

---

## Files to Create

```
src/csgo/grid/
├── __init__.py
├── client.py      # GRIDClient
├── matcher.py     # GRIDMatcher
├── poller.py      # GRIDPoller
├── tasks.py       # Celery tasks
└── models.py      # CSGOGridEvent, GRIDPollerState (or add to db/models.py)
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| GRID detection slower than market | Measure, pivot to mean reversion strategy |
| Team matching errors | Confidence threshold, manual review, logging |
| Rate limit exceeded | Max 2 matches, adaptive sleep |
| Poller crashes | State persistence, Celery auto-restart |
| No matches during testing | Wait for matches, test with mocks first |

---

## Next Steps

1. **Get GRID API key** from environment
2. **Test GRID API** manually with curl/python
3. **Implement Phase 1** (database migration)
4. **Proceed through phases** sequentially

---

*Last Updated: 2026-01-09*
