# BUILD_MANIFEST.md - Implementation Checklist

> **Purpose**: Granular tracking of every component for Phase 1 (Data Collection + Dashboard).
>
> **Goal**: Start collecting data TODAY (Dec 17, 2024). Have working XGBoost model by mid-February 2025.
>
> **Last Updated**: Dec 17, 2024

---

## Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete
- `[!]` Blocked / Needs fix
- `[-]` Deferred to later phase

---

## Phase 1 Overview

```
Phase 1: Data Collection + Monitoring Dashboard
├── 1.1 Foundation (DB, config, Docker)        ✅ COMPLETE
├── 1.2 REST Data Ingestion (Gamma, CLOB)      ✅ COMPLETE
├── 1.3 WebSocket Data Ingestion               ✅ COMPLETE
├── 1.4 Redis Layer (buffers, metrics)         ✅ COMPLETE
├── 1.5 FastAPI Backend                        ✅ COMPLETE
├── 1.6 Frontend Dashboard                     ✅ COMPLETE
└── 1.7 Deployment (Docker Compose)            ✅ COMPLETE (with known issue)
```

**Known Issue**: Async/Celery event loop errors cause ~27% task failures. Needs sync conversion.

---

## 1.1 Foundation ✅

### Project Structure
- [x] Root directory `/home/theo/polymarket-ml`
- [x] `src/` Python package structure
- [x] `frontend/` React app structure
- [x] `alembic/` migrations directory
- [x] `CLAUDE.md` - AI working memory
- [x] `BUILD_MANIFEST.md` - This file

### Configuration
- [x] `requirements.txt` - Python dependencies
- [x] `src/config/__init__.py`
- [x] `src/config/settings.py` - Pydantic BaseSettings
  - [x] Database settings
  - [x] Redis settings
  - [x] Celery settings
  - [x] API base URLs
  - [x] Rate limits
  - [x] Tier boundaries
  - [x] Collection intervals
  - [x] Whale thresholds

### Database Models (`src/db/models.py`)
- [x] All models implemented (Market, Snapshot, Trade, OrderbookSnapshot, WhaleEvent, TaskRun)

### Database Infrastructure
- [x] `src/db/__init__.py`
- [x] `src/db/database.py` - Engine, session factory
- [x] Indexes configured
- [-] Table partitioning (deferred - not needed yet)

### Migrations
- [x] `alembic.ini` configuration
- [x] `alembic/env.py` setup
- [x] Initial migration with all tables

### Docker
- [x] `Dockerfile` - Python application
- [x] `docker-compose.yml` with all services

---

## 1.2 REST Data Ingestion ✅

### Gamma API Client (`src/fetchers/gamma.py`)
- [x] `GammaClient` class
- [x] Rate limiting
- [x] Retry logic
- [x] All methods implemented
- [x] Response parsing

### CLOB API Client (`src/fetchers/clob.py`)
- [x] `CLOBClient` class
- [x] Rate limiting
- [x] `get_orderbook(token_id)` method
- [x] Orderbook depth calculation
- [x] Wall detection helpers

### Celery Tasks (`src/tasks/`)

#### Discovery Task (`src/tasks/discovery.py`)
- [x] `discover_markets` task
- [x] `update_market_tiers` task
- [x] `check_resolutions` task

#### Snapshot Tasks (`src/tasks/snapshots.py`)
- [!] `snapshot_tier` task - **NEEDS SYNC CONVERSION**
- [!] `snapshot_tier_batch` task - **NEEDS SYNC CONVERSION**
- [x] `warm_gamma_cache` task
- [x] Tier-specific scheduled tasks (T0-T4)

#### Celery Configuration (`src/tasks/celery_app.py`)
- [x] Celery app setup
- [x] Beat schedule configuration
- [x] Queue definitions (default, snapshots, discovery)

---

## 1.3 WebSocket Data Ingestion ✅

### WebSocket Collector (`src/collectors/websocket.py`)
- [x] `WebSocketCollector` class
- [x] Connection lifecycle management
- [x] Reconnection logic
- [x] Batch subscription for markets
- [x] Trade event handling
- [x] Orderbook caching to Redis
- [x] Price change handling
- [x] Whale detection

### Metrics Computer (`src/collectors/metrics.py`)
- [x] `compute_all_metrics(condition_id)` function
- [x] Trade metrics (count, volume, vwap)
- [x] Whale metrics (count, volume, net flow)

---

## 1.4 Redis Layer ✅

### Redis Client (`src/db/redis.py`)
- [x] `RedisClient` class
- [x] Trade buffer operations
- [x] Metrics cache operations
- [x] Tier membership tracking
- [x] WebSocket health tracking
- [x] Orderbook caching
- [x] Price caching
- [x] Gamma markets cache

---

## 1.5 FastAPI Backend ✅

### App Setup (`src/api/main.py`)
- [x] FastAPI app initialization
- [x] CORS middleware
- [x] Router registration

### Routes
- [x] Health routes (`/health`)
- [x] Stats routes (`/api/stats`)
- [x] Markets routes (`/api/markets`)
- [x] Tasks routes (`/api/tasks`)
- [x] Data quality routes (`/api/data-quality`)

---

## 1.6 Frontend Dashboard ✅

### Setup
- [x] React + TypeScript + Vite
- [x] Tailwind CSS
- [x] React Router

### Pages
- [x] Dashboard page (stats, tier distribution)
- [x] Markets page (list, filters)
- [x] Data Quality page
- [x] Tasks page

---

## 1.7 Deployment ✅

### Docker Compose Services (10 containers)
- [x] `postgres` - PostgreSQL 16
- [x] `redis` - Redis 7
- [x] `api` - FastAPI backend
- [x] `celery-worker` - Discovery tasks
- [x] `celery-snapshots` - Snapshot tasks worker 1
- [x] `celery-snapshots-2` - Snapshot tasks worker 2
- [x] `celery-beat` - Celery scheduler
- [x] `websocket-collector` - WebSocket service
- [x] `frontend` - React app (nginx)
- [x] `flower` - Celery monitoring

### Status
- [x] All services start with docker-compose up
- [x] Data persists across restarts
- [x] System recovers from crashes
- [!] ~43% snapshot coverage (needs async→sync fix)

---

## Current Stats (Dec 17, 2024)

| Metric | Value |
|--------|-------|
| Markets tracked | 2,553 |
| Snapshots collected | ~42,000 |
| Trades collected | ~1,900 |
| Task success rate | ~53% |
| **Coverage rate** | **~43%** (target: 95%+) |

---

## Priority Fixes

### 1. Convert Async Tasks to Sync [!]
**Problem**: Celery prefork workers + asyncio = "Event loop is closed" errors

**Files to modify**:
- `src/tasks/snapshots.py` - Replace async with sync httpx
- `src/fetchers/gamma.py` - Add sync methods
- `src/fetchers/clob.py` - Add sync methods
- `src/collectors/metrics.py` - Add sync methods
- `src/db/redis.py` - Add sync methods

**Expected outcome**: 95%+ task success rate

---

## Data Collection Targets

### Week 1 (Dec 17-24)
- [x] Infrastructure running
- [x] Discovery finding markets
- [x] All tier snapshots collecting
- [x] Dashboard showing stats
- [~] WebSocket collecting trades (working, but intermittent)

### Week 2 (Dec 24-31)
- [ ] Fix async/sync issue for 95%+ coverage
- [ ] Full feature snapshots at expected rates
- [ ] Data quality monitoring refined

### Month 1 (Jan 2025)
- [ ] Stable 24/7 collection
- [ ] 100k+ snapshots collected
- [ ] Some markets resolving (training data!)
- [ ] Ready to start ML pipeline

### Month 2 (Feb 2025)
- [ ] Sufficient resolved markets for training
- [ ] XGBoost model trained
- [ ] Basic strategy backtesting

---

## File → Component Mapping

| File | Component | Status |
|------|-----------|--------|
| `src/config/settings.py` | Configuration | ✅ |
| `src/db/models.py` | All SQLAlchemy models | ✅ |
| `src/db/database.py` | DB engine, sessions | ✅ |
| `src/db/redis.py` | Redis client | ✅ |
| `src/fetchers/gamma.py` | Gamma API client | ✅ |
| `src/fetchers/clob.py` | CLOB API client | ✅ |
| `src/collectors/websocket.py` | WebSocket manager | ✅ |
| `src/collectors/metrics.py` | Metrics computation | ✅ |
| `src/tasks/celery_app.py` | Celery configuration | ✅ |
| `src/tasks/discovery.py` | Market discovery | ✅ |
| `src/tasks/snapshots.py` | Snapshot collection | [!] NEEDS SYNC |
| `src/api/main.py` | FastAPI app | ✅ |
| `src/api/routes/*.py` | API endpoints | ✅ |
| `frontend/src/pages/*.tsx` | Dashboard pages | ✅ |
