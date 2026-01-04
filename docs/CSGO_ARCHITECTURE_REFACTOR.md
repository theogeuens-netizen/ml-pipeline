# CSGO Architecture Simplification Plan

## Current Problems

1. **Multiple data sources** - WebSocket prices (garbage), CLOB API (reliable), multiple caches
2. **Multiple containers** - csgo-websocket, csgo-executor, celery - each can fail independently
3. **Redis stream intermediary** - adds latency, connection issues, consumer group complexity
4. **Cache staleness** - router cache was 5min stale (now 30s, but still an issue)
5. **Garbage spread data** - WebSocket book events show full orderbook, not tight spread

## Proposed Architecture

### Single Source of Truth: `csgo_matches` Table

All price/spread data flows through ONE path:
```
CLOB REST API (every 15s) → Celery Task → csgo_matches table → Everything else
```

### Merged Container: `csgo-engine`

Combine websocket + executor into one process:
```
csgo-engine:
  - WebSocket connection (for EVENT detection only, not prices)
  - Strategy execution (gets prices from DB)
  - Position management
  - No Redis streams
```

---

## Implementation Phases

### Phase 1: Eliminate Price from WebSocket Ticks (30 min)
**Goal**: WebSocket only detects EVENTS, never provides prices

**Changes**:
1. In `src/csgo/websocket.py`:
   - Remove `spread_cache` entirely
   - Remove price from signal publishing
   - Only publish: `{market_id, event_type, timestamp, token_type, trade_size, trade_side}`

2. In `src/csgo/engine/router.py`:
   - Remove `mid_price` enrichment (no longer needed)
   - Get prices DIRECTLY from `csgo_matches` table on each tick
   - Add `_get_live_prices(market_id)` method that queries DB

**Result**: Strategies always see reliable CLOB prices, never garbage WebSocket prices

---

### Phase 2: Remove Redis Stream (1 hour)
**Goal**: Direct function calls instead of pub/sub

**Changes**:
1. Create `src/csgo/engine/unified.py`:
   ```python
   class CSGOEngine:
       """Unified CSGO trading engine - WebSocket + Strategies in one process."""

       def __init__(self):
           self.ws_collector = CSGOWebSocketHandler()  # Simplified, no Redis
           self.router = TickRouter()
           self.executor = TradeExecutor()

       async def on_websocket_event(self, event: dict):
           """Called directly by websocket handler - no Redis."""
           # Enrich with DB prices
           prices = self._get_prices_from_db(event["market_id"])
           tick = Tick(
               market_id=event["market_id"],
               event_type=event["event_type"],
               yes_price=prices["yes_price"],
               no_price=prices["no_price"],
               spread=prices["spread"],
               # ... other fields from event
           )

           # Route to strategies
           action = self.router.process_tick(tick)
           if action:
               self.executor.execute(action)
   ```

2. Simplify `src/csgo/websocket.py`:
   - Remove `publish_csgo_signal()` calls
   - Add callback: `on_event: Callable[[dict], None]`
   - Call callback directly when event received

3. Delete:
   - `src/csgo/signals.py` (Redis stream logic)
   - Consumer group management code

**Result**: Single process, no network between components

---

### Phase 3: Merge Docker Containers (30 min)
**Goal**: One container for CSGO trading

**Changes**:
1. Update `docker-compose.yml`:
   ```yaml
   csgo-engine:
     build: .
     command: python -m src.csgo.engine.unified
     depends_on:
       - postgres
       - redis  # Still used for main pipeline, not CSGO
     # Remove csgo-websocket and csgo-executor
   ```

2. Remove from docker-compose:
   - `csgo-websocket` service
   - `csgo-executor` service

3. Create `src/csgo/engine/unified.py` as main entry point

**Result**: One container, one process, simpler deployment

---

### Phase 4: Direct DB Queries (Optional, 15 min)
**Goal**: Eliminate router's match cache entirely

**Changes**:
1. In router, replace:
   ```python
   # OLD: Use stale cache
   match = self._match_cache.get(market_id)

   # NEW: Query DB directly (fast - only 3 rows for CSGO)
   match = self._query_match(market_id)
   ```

2. Remove `_match_cache`, `_load_match_cache()`, `_refresh_caches_loop()`

**Result**: Always fresh data, no staleness

---

## File Changes Summary

### Delete
- `src/csgo/signals.py` - Redis stream logic

### Modify
- `src/csgo/websocket.py` - Remove Redis publishing, add callback
- `src/csgo/engine/router.py` - Remove cache, query DB directly
- `docker-compose.yml` - Merge containers

### Create
- `src/csgo/engine/unified.py` - Combined WebSocket + Strategy engine

---

## Migration Steps

1. **Backup current state**
   ```bash
   pg_dump -U postgres polymarket_ml > backup_$(date +%Y%m%d).sql
   ```

2. **Implement Phase 1** (safest, biggest impact)
   - Test: Verify strategies see correct prices

3. **Implement Phase 2** (biggest simplification)
   - Test: Verify trades execute correctly

4. **Implement Phase 3** (cleanup)
   - Test: Full integration test

5. **Implement Phase 4** (optional)
   - Test: Verify no performance issues

---

## Rollback Plan

If issues arise:
1. Revert code changes via git
2. `docker-compose up -d` to restore old containers
3. Positions remain in DB, can be manually managed

---

## Success Criteria

After refactor:
- [ ] Single `csgo-engine` container handles everything
- [ ] No Redis streams for CSGO (main pipeline unaffected)
- [ ] All prices come from `csgo_matches` table
- [ ] WebSocket only provides event signals (trade happened, book changed)
- [ ] Strategies see correct prices (matching website)
- [ ] Spread data is accurate (2%, not 98%)
- [ ] System survives WebSocket disconnects gracefully
