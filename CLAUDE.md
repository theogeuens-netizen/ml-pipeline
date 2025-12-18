# CLAUDE.md - AI Working Memory

> **Purpose**: This file is the "source of truth" for AI-assisted development. Read this file at the start of every session to understand current state, priorities, and context.

---

## Project Goal

**Build a Polymarket ML trading system from scratch.**

Timeline: Working XGBoost model in 1-2 months (mid-February 2025)

Strategy:
1. **Phase 1**: Build robust data collection infrastructure + monitoring dashboard
2. **Accumulate data**: Collect ~65 features per snapshot for 1-2 months
3. **Phase 2+**: Train ML models once we have sufficient resolved markets

**Key insight**: We need resolved markets (with known outcomes) to train. Data collection started Dec 17, 2024. Markets resolving in Jan-Feb 2025 will be our training set.

---

## Quick Status

| Component | Status | Last Updated |
|-----------|--------|--------------|
| Project Structure | COMPLETE | Dec 17, 2024 |
| Database Schema | COMPLETE | Dec 17, 2024 |
| REST API Clients (Gamma/CLOB) | COMPLETE | Dec 17, 2024 |
| WebSocket Collector | COMPLETE | Dec 17, 2024 |
| Celery Tasks | COMPLETE | Dec 17, 2024 |
| Redis Caching | COMPLETE | Dec 17, 2024 |
| FastAPI Backend | COMPLETE | Dec 17, 2024 |
| Frontend Dashboard | COMPLETE | Dec 17, 2024 |
| Monitoring Dashboard | COMPLETE | Dec 17, 2024 |
| Database Browser | COMPLETE | Dec 17, 2024 |
| Orderbook Snapshots | COMPLETE | Dec 17, 2024 |
| ML Pipeline | NOT STARTED | - |
| Strategies | NOT STARTED | - |

**Current Phase**: 1 OPERATIONAL - Data collection running at ~100% success rate

---

## Current System Stats (Dec 18, 2024)

| Metric | Value |
|--------|-------|
| Markets tracked | **9,269** |
| Snapshots collected | **1,148,567** |
| Trades collected | **75,938** |
| Orderbook snapshots | **494,959** |
| Task success rate | **99.1%** |
| WebSocket status | **HEALTHY** |
| Trades per minute | **~380** (was ~10 before 4-connection fix) |
| WebSocket connections | **4 parallel** (2000 market capacity) |

---

## Configuration

Current settings (`src/config/settings.py`):
- **Volume threshold**: $100 (filter low-activity markets)
- **Lookahead window**: 336 hours (2 weeks)
- **Orderbook concurrency**: 100 parallel fetches
- **Metrics concurrency**: 150 parallel fetches
- **WebSocket enabled tiers**: T2, T3, T4
- **WebSocket connections**: 4 parallel (configurable via `websocket_num_connections`)
- **Orderbook enabled tiers**: T2, T3, T4

---

## Docker Services (10 containers)

```bash
docker-compose ps
```

| Service | Purpose | Health Check |
|---------|---------|--------------|
| postgres | Database (port 5433) | pg_isready |
| redis | Cache/broker (port 6380) | redis-cli ping |
| api | FastAPI backend (port 8000) | curl /health |
| celery-worker | Discovery + default tasks | celery inspect ping |
| celery-snapshots | Snapshot tasks worker 1 | celery inspect ping |
| celery-snapshots-2 | Snapshot tasks worker 2 | celery inspect ping |
| celery-beat | Task scheduler | - |
| websocket-collector | Real-time trade collection | Redis ws:last_activity |
| frontend | React dashboard (port 80) | - |
| flower | Celery monitoring (port 5555) | - |

---

## Key Files

| File | Purpose |
|------|---------|
| `src/tasks/snapshots.py` | Snapshot + orderbook collection tasks |
| `src/tasks/discovery.py` | Market discovery and tier assignment |
| `src/tasks/celery_app.py` | Celery configuration and beat schedule |
| `src/collectors/websocket.py` | WebSocket trade collector with health checks |
| `src/fetchers/gamma.py` | Gamma API client (sync + async) |
| `src/fetchers/clob.py` | CLOB API client (sync + async) |
| `src/db/redis.py` | Redis client with activity tracking |
| `src/api/routes/monitoring.py` | Monitoring API endpoints |
| `src/api/routes/database.py` | Database browser API |
| `frontend/src/pages/Monitoring.tsx` | System monitoring dashboard |
| `frontend/src/pages/Database.tsx` | Database browser UI |

---

## Quick Commands

```bash
# Start all services
cd /home/theo/polymarket-ml && docker-compose up -d

# Check service status
docker-compose ps

# View logs
docker-compose logs -f celery-snapshots
docker-compose logs -f websocket-collector

# Check database stats
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT
    (SELECT COUNT(*) FROM markets WHERE active = true) as markets,
    (SELECT COUNT(*) FROM snapshots) as snapshots,
    (SELECT COUNT(*) FROM trades) as trades,
    (SELECT COUNT(*) FROM orderbook_snapshots) as orderbook_snapshots;
"

# Check task success rate
docker-compose exec -T postgres psql -U postgres -d polymarket_ml -c "
SELECT status, COUNT(*) FROM task_runs
WHERE started_at > NOW() - INTERVAL '1 hour'
GROUP BY status;
"

# Check WebSocket health
curl -s http://localhost:8000/api/monitoring/health | python3 -m json.tool

# Check WebSocket coverage
curl -s http://localhost:8000/api/monitoring/websocket-coverage | python3 -m json.tool

# Restart Celery workers
docker-compose restart celery-snapshots celery-snapshots-2 celery-beat
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      DATA COLLECTION SYSTEM                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │   GAMMA     │    │    CLOB     │    │     WEBSOCKET       │ │
│  │    API      │    │    API      │    │   (Real-time)       │ │
│  │             │    │             │    │                     │ │
│  │ • Markets   │    │ • Orderbook │    │ • Trades            │ │
│  │ • Prices    │    │ • Depth     │    │ • Price changes     │ │
│  │ • Volume    │    │             │    │ • Book updates      │ │
│  └──────┬──────┘    └──────┬──────┘    └──────────┬──────────┘ │
│         │                  │                       │            │
│         └────────────┬─────┴───────────────────────┘            │
│                      ▼                                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              CELERY WORKERS (3 workers)                    │ │
│  │  • celery-worker: discovery, tier updates                  │ │
│  │  • celery-snapshots: T0-T4 snapshot collection             │ │
│  │  • celery-snapshots-2: T3-T4 batch processing              │ │
│  └────────────────────────────────────────────────────────────┘ │
│                      │                                          │
│         ┌────────────┼────────────┐                            │
│         ▼            ▼            ▼                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │ POSTGRES │  │  REDIS   │  │ FRONTEND │                     │
│  │          │  │          │  │          │                     │
│  │ markets  │  │ cache    │  │ React    │                     │
│  │ snapshots│  │ buffers  │  │ dashboard│                     │
│  │ trades   │  │ metrics  │  │ + monitor│                     │
│  │ orderbook│  │ ws:*     │  │          │                     │
│  └──────────┘  └──────────┘  └──────────┘                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Collection Schedule

| Tier | Hours to Resolution | Interval | Markets | Expected/hr |
|------|---------------------|----------|---------|-------------|
| T0 | > 48h | 60 min | ~1,600 | ~1,600 |
| T1 | 12-48h | 5 min | ~450 | ~5,400 |
| T2 | 4-12h | 1 min | ~170 | ~10,200 |
| T3 | 1-4h | 30 sec | ~60 | ~7,200 |
| T4 | < 1h | 15 sec | ~90 | ~21,600 |

---

## Session Log

### Session 1 - Dec 17, 2024 (Initial Setup)
- Created project structure
- Built all core components
- Frontend dashboard operational
- Initial data collection started

### Session 2 - Dec 17, 2024 (Optimization)
- Expanded market filters: $100 volume, 2-week lookahead
- Added second celery-snapshots worker
- Implemented T3/T4 batch tasks for parallelization
- Increased concurrency limits (100 orderbook, 150 metrics)
- Discovered async/Celery event loop issue causing ~27% task failures
- Market count increased from 507 to 2,553 (5x)

### Session 3 - Dec 17, 2024 (Reliability & Monitoring)
- **FIXED**: Converted all Celery tasks to synchronous HTTP calls
- Task success rate improved from ~73% to **99.1%**
- Added WebSocket health detection with auto-reconnect (120s stale threshold)
- Added Redis activity tracking (`ws:last_activity`)
- Added Docker health checks to all services
- **NEW**: Monitoring dashboard (`/monitoring`) with:
  - WebSocket status (LIVE/STALE/DISCONNECTED)
  - Task error rates
  - Field completeness metrics by category and tier
  - WebSocket coverage verification (subscribed vs should-subscribe)
  - Subscription health (identifies stale/silent markets)
  - Recent errors with tracebacks
- **NEW**: Database browser (`/database`) with:
  - All tables browsable with pagination
  - Sortable columns, horizontal scroll for wide tables
  - Row detail view
- **FIXED**: Orderbook snapshots now being collected (was empty)
  - Updated `snapshot_tier`, `snapshot_tier_batch`, `snapshot_market`
  - Raw bids/asks stored as JSONB
  - Summary stats: depth, levels, largest orders (wall detection)

### Session 4 - Dec 18, 2024 (WebSocket Fix)
- **ROOT CAUSE FOUND**: WebSocket was subscribing to 628 markets but Polymarket limits to **500 instruments max per connection**
- **FIXED**: Added MAX_SUBSCRIPTIONS=500 limit in `src/collectors/websocket.py`
  - Prioritizes T4 markets first (most time-critical), then T3, then T2
  - Logs warning when dropping markets due to limit
- **NEW**: Added `cleanup_stale_markets` task in `src/tasks/discovery.py`
  - Runs every 10 minutes via Celery beat
  - Deactivates T4 markets with end_date > 1 hour in past
  - Deactivates T4 markets with no trades in last hour (dead markets)
  - Frees up WebSocket slots for active markets
- **RESULT**: WebSocket now receiving ~10+ trades/minute (was 0)
- Docker restart policy was already correct (`restart: unless-stopped`)

### Session 5 - Dec 18, 2024 (Production Hardening)
Major audit and hardening to make pipeline production-grade for autonomous operation.

**API Clients (`src/fetchers/base.py`)**:
- **NEW**: Circuit breaker pattern - opens after 5 consecutive failures, auto-recovers after 30s
- **NEW**: Exponential backoff with jitter - prevents thundering herd on retries
- **NEW**: Separate connect/read timeouts (10s/30s)
- **NEW**: Request ID tracking for log correlation
- **NEW**: Smart retry logic - only retries transient errors (429, 5xx), not client errors (4xx)
- **NEW**: Response size validation - rejects responses > 10MB to prevent OOM
- **NEW**: JSON parse error handling

**Gamma Client (`src/fetchers/gamma.py`)**:
- **NEW**: MAX_PAGES=100 pagination limit prevents infinite loops
- **NEW**: Graceful 404/422 handling for delisted/resolved markets (no longer logged as errors)

**Database (`src/db/database.py`)**:
- **NEW**: Connection retry with exponential backoff (1s, 2s, 4s)
- **NEW**: `pool_recycle=3600` recycles connections hourly
- **NEW**: Data validation utilities: `validate_price()`, `validate_volume()`, `validate_timestamp()`
- **NEW**: `get_session_with_retry()` context manager for critical operations

**Redis (`src/db/redis.py`)**:
- **NEW**: `@redis_retry_sync` decorator with auto-reconnect
- **NEW**: Socket timeouts (connect: 5s, read: 10s)
- **NEW**: Graceful JSON parse error handling - corrupt entries skipped, not crashed
- **NEW**: `ping()` health check method

**Celery Tasks (`src/tasks/celery_app.py`, `src/tasks/snapshots.py`)**:
- **NEW**: `task_acks_late=True` - prevents task loss on worker crash
- **NEW**: `task_reject_on_worker_lost=True` - requeues if worker dies
- **NEW**: Auto-retry with exponential backoff on transient errors
- **NEW**: Task decorators with `autoretry_for=RETRYABLE_ERRORS`
- **NEW**: `_safe_price()` validator for price range [0,1]
- **NEW**: `validate_snapshot_data()` before DB inserts

**WebSocket (`src/collectors/websocket.py`)**:
- **NEW**: Trade data validation (price range, positive size, valid side)
- **NEW**: Graceful degradation - Redis failures don't stop trade processing
- **NEW**: Connection ID in all logs for multi-connection debugging
- **NEW**: Invalid trade detection and logging

**Results**:
- System now handles API outages, DB connection drops, Redis failures
- Auto-recovery from transient failures without manual intervention
- Structured logging with context for remote debugging
- 0% error rate after restart with new code

### Session 6 - Dec 18, 2024 (WebSocket Scaling)
**Problem**: Only capturing ~10 trades/minute despite 1,293 eligible markets
**Root Cause**: 2 WebSocket connections × 500 limit = 1,000 capacity, but 1,293 markets needed

**Fixes**:
- **Increased connections**: 2 → 4 parallel WebSocket connections (configurable via `websocket_num_connections`)
- **Fixed connection initialization**: Added `collector.running = True` for managed collectors
- **Improved market distribution**: Round-robin assignment ensures each connection gets mix of tiers

**Results**:
- WebSocket coverage: 0 missing markets (was 73 missing)
- Trade rate: **~380 trades/minute** (was ~10)
- All 4 connections actively receiving data (verified via logs)
- Capacity: 2,000 markets (can increase further if needed)

---

### Session 7 - Dec 18, 2024 (Autonomous Operation Hardening)
**Goal**: Make system run reliably for days/weeks without intervention

**Fixes**:
- **Docker log rotation**: Added `max-size: 50m, max-file: 3` to all containers (prevents log disk fill)
- **Critical health endpoint**: New `/api/monitoring/critical` returns HTTP 503 on failures, checks:
  - Disk usage (warn >90%, critical >95%)
  - PostgreSQL connection pool exhaustion
  - Redis connectivity and memory
  - WebSocket activity (trades in last 5 min)
  - Task success rate (warn <90%, critical <80%)
  - Celery queue backlog (warn >500, critical >1000)
- **Staggered WebSocket reconnection**: Connections 0-3 stagger by 0/2/4/6s on startup and 0/3/6/9s on reconnect (prevents data gaps when all reconnect at once)
- **task_runs cleanup**: Daily job at 3:30 AM UTC deletes records >7 days old (operational data, not market data)
- **Connection pool monitoring**: Added to critical health endpoint

**Results**:
- System status: **healthy** with 100% task success rate
- All 4 WebSocket connections stagger correctly on startup
- Log files capped at 150MB total per container

---

## Monitoring Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/monitoring/health` | System health (WebSocket, tasks, trades/min) |
| `GET /api/monitoring/critical` | **Returns 503 on failures** - for external monitoring |
| `GET /api/monitoring/websocket-coverage` | Compare subscribed vs should-subscribe |
| `GET /api/monitoring/subscription-health` | Verify markets receiving data |
| `GET /api/monitoring/connections` | DB pool, Redis, WebSocket connection details |
| `GET /api/monitoring/field-completeness` | Field population % by category/tier |
| `GET /api/monitoring/errors?limit=50` | Recent task errors with tracebacks |
| `GET /api/database/tables` | List all tables with row counts |
| `GET /api/database/tables/{name}` | Browse table data with pagination |

---

## Database Tables

| Table | Purpose | Row Count |
|-------|---------|-----------|
| markets | Market metadata, tier, token IDs | ~9,300 |
| snapshots | Price/volume/orderbook features | ~1,148,000 |
| trades | Individual trades from WebSocket | ~76,000 |
| orderbook_snapshots | Full orderbook storage (JSONB) | ~495,000 |
| task_runs | Task execution history | varies |
| whale_events | Large trade tracking | - |

---

## Notes for Next Session

- Data collection is stable and running at **100% task success rate**
- WebSocket capturing **~380 trades/minute** across 4 connections
- System hardened for autonomous operation (log rotation, health checks, staggered reconnects)
- Use `/api/monitoring/critical` for external uptime monitoring (returns 503 on failure)
- **Disk usage at 70%** - will need to move data to cold storage eventually or add disk space
- **Next priorities**:
  1. Monitor data accumulation over coming days/weeks
  2. Begin ML pipeline design once sufficient resolved markets exist
  3. Consider adding alerting for degraded WebSocket status
  4. May want to add data export functionality for ML training

---

## Thinking Patterns

### Before Writing Any Code
- State the approach in 2-3 sentences
- Identify the hardest part and address it first
- Check if similar code exists in the codebase—don't reinvent

### During Implementation
- Build the simplest version that could work first
- Test each component in isolation before integrating
- When something breaks, add a test that reproduces it before fixing

### Code Review Yourself
After writing code, ask:
- What happens with empty input?
- What happens with massive input?
- What happens if the network/database fails?
- Is there any state that could get corrupted?
- Would I understand this code in 6 months?

## Problem-Solving Mode

When debugging:
1. Reproduce the exact failure first
2. Form a hypothesis about the cause
3. Find evidence that confirms OR refutes it
4. Only then make changes

When stuck:
- Reduce to the smallest failing case
- Add logging/prints to trace actual vs expected behavior
- Question your assumptions—what do you THINK is true but haven't verified?

## Judgment Calls

- If a task is ambiguous, ask one clarifying question rather than guessing
- If you spot a bug unrelated to the current task, mention it but don't fix it unless asked
- If the requested approach seems wrong, say why and propose an alternative
- If something will take >100 lines, outline the structure first and get confirmation

## Quality Bar

- No function without a reason to exist
- No parameter without a caller that needs it
- No abstraction until there are 3+ concrete cases
- No optimization without measurement showing it matters
