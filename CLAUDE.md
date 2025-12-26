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
| Tier Transition Tracking | COMPLETE | Dec 19, 2024 |
| Enhanced Monitoring Dashboard | COMPLETE | Dec 19, 2024 |
| WebSocket Trade Rate Health | COMPLETE | Dec 19, 2024 |
| Telegram Alerts | COMPLETE | Dec 19, 2024 |
| Trade Decision Audit Trail | COMPLETE | Dec 19, 2024 |
| Config-Driven Strategies | COMPLETE | Dec 20, 2024 |
| Per-Strategy Wallets | COMPLETE | Dec 20, 2024 |
| Performance Tracking (Sharpe) | COMPLETE | Dec 20, 2024 |
| Strategy Debug CLI | COMPLETE | Dec 20, 2024 |
| BigQuery Backtesting | COMPLETE | Dec 25, 2024 |
| ML Pipeline | NOT STARTED | - |

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
| `src/collectors/healthcheck.py` | WebSocket container health check module |
| `src/fetchers/gamma.py` | Gamma API client (sync + async) |
| `src/fetchers/clob.py` | CLOB API client (sync + async) |
| `src/db/redis.py` | Redis client with activity tracking |
| `src/api/routes/monitoring.py` | Monitoring API endpoints |
| `src/api/routes/database.py` | Database browser API |
| `src/api/routes/executor.py` | Strategy performance + debug endpoints |
| `frontend/src/pages/Monitoring.tsx` | System monitoring dashboard |
| `frontend/src/pages/Database.tsx` | Database browser UI |
| `strategies.yaml` | **Central config** for all 25 strategies |
| `strategies/types/` | Strategy type classes (6 types) |
| `strategies/loader.py` | Reads YAML, instantiates strategies |
| `strategies/performance.py` | Sharpe, Sortino, drawdown calculations |
| `strategies/base.py` | Strategy base class with SHA tracking |
| `cli/debug.py` | Debug CLI - diagnose why strategies aren't trading |
| `cli/deploy.py` | List/validate strategies CLI |
| `cli/status.py` | System status CLI tool |
| `cli/backtest.py` | Strategy backtesting CLI tool |
| `src/alerts/telegram.py` | Telegram alert notifications |
| `src/executor/models.py` | Executor models (incl. TradeDecision, StrategyBalance) |
| `src/executor/engine/runner.py` | Main executor loop, loads from strategies.yaml |
| `src/tasks/alerts.py` | Daily summary Celery task |
| `.claude/commands/trading.md` | Trading CLI slash command |
| `.claude/commands/categorize.md` | Categorization slash command |
| `.claude/commands/hypothesis.md` | Strategy hypothesis generation |
| `.claude/commands/test.md` | Backtest with robustness checks |
| `.claude/commands/verdict.md` | Experiment verdict and ledger update |
| `TRADING_CLI.md` | Trading CLI full reference |
| `RESEARCH_LAB.md` | Strategy Research Lab reference |
| `USER_GUIDE.md` | User-friendly guide: research → deployment |
| `src/services/rule_categorizer.py` | Rule-based market categorization |
| `src/backtest/robustness.py` | Time/liquidity/category split testing |
| `src/backtest/bigquery.py` | BigQuery backtest engine (default) |
| `cli/categorize_helpers.py` | Categorization CLI helpers |
| `cli/ledger.py` | Ledger query tool |
| `cli/robustness.py` | Robustness check CLI |
| `cli/ship.py` | Deploy experiments to strategies.yaml |
| `experiments/` | Strategy experiment files |
| `ledger/insights.jsonl` | Accumulated research learnings |

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

### Session 8 - Dec 19, 2024 (Tier Transition Tracking & Monitoring Enhancements)
**Goal**: Add visibility into market lifecycle transitions and enhance monitoring dashboard

**New Features**:

**Tier Transition Tracking** (`src/db/models.py`, `src/tasks/discovery.py`):
- **NEW**: `TierTransition` model tracks every tier change (T0→T1, T1→T2, deactivation, etc.)
- Records reason: `time`, `low_volume`, `resolved`, `expired`, `no_trades`, `delisted`
- Captures `hours_to_close` at transition for analysis
- 7-day retention with automatic cleanup (runs with `cleanup_old_task_runs`)

**Enhanced Market Cleanup** (`src/tasks/discovery.py`):
- `cleanup_stale_markets` now deactivates all market types, not just T4:
  - Resolved markets (highest priority)
  - Expired markets (end_date > 1 hour past)
  - T4 with no trades in 1 hour
  - Markets missing from Gamma API (delisted)
- All deactivations logged as tier transitions for visibility

**WebSocket Trade Rate Health** (`src/collectors/websocket.py`, `src/collectors/healthcheck.py`):
- **NEW**: Trade rate monitoring with `MIN_TRADES_PER_MINUTE=30` threshold
- Auto-reconnect if rate falls below minimum after 5-min warmup
- **NEW**: `healthcheck.py` module for Docker container health checks
- Updated `docker-compose.yml` with 10-min start_period for warmup

**Enhanced Monitoring Dashboard** (`frontend/src/pages/Monitoring.tsx`):
- **NEW**: 3-column monitoring panel:
  - **Tier Flow**: Transitions in last hour (T0→T1: 5, T3→deactivated: 2, etc.)
  - **Task Activity**: Celery task success/failure summary by task type
  - **Redis Stats**: Memory usage, key counts, ops/sec
- 3 new API endpoints: `/monitoring/tier-transitions`, `/monitoring/task-activity`, `/monitoring/redis-stats`

**Results**:
- Full visibility into market lifecycle (discovery → tier progression → deactivation)
- Proactive WebSocket health monitoring with auto-recovery
- Enhanced dashboard shows system internals at a glance

---

### Session 9 - Dec 19, 2024 (Strategy-as-Code Refactoring)
**Goal**: Move strategy configuration from YAML/frontend to Python files. Add Telegram alerts and audit logging.

**Strategy-as-Code System** (`strategies/`):
- **NEW**: Strategies are now Python files in `strategies/` directory
- Each strategy is a class inheriting from `Strategy` base class
- Class attributes = configurable parameters (no YAML needed)
- `get_sha()` method returns SHA256 of source code for versioning
- `get_params()` extracts class attributes for display
- First strategy: `longshot_yes_v1.py` - bets YES on high-probability markets

**CLI Tools** (`cli/`):
- **NEW**: `python -m cli.deploy strategies/foo.py` - Validates and deploys a strategy
- **NEW**: `python -m cli.status` - Shows system status, deployed strategies, balance, positions
- **NEW**: `python -m cli.backtest strategies/foo.py --days 30` - Backtest against historical data

**Trade Decision Audit Trail** (`src/executor/models.py`):
- **NEW**: `TradeDecision` model captures every signal with full context
- Fields: strategy_name, strategy_sha, market_snapshot, decision_inputs
- Fields: signal_side, signal_reason, signal_edge, signal_size_usd
- Fields: executed, rejected_reason, execution_price, position_id
- Enables replaying any decision with exact inputs that led to it

**Telegram Alerts** (`src/alerts/telegram.py`):
- **NEW**: `alert_trade()` - Notifies on executed trades
- **NEW**: `alert_position_closed()` - Notifies on position close with P&L
- **NEW**: `alert_error()` - Notifies on critical errors
- **NEW**: `alert_daily_summary()` - Daily balance/P&L summary at 9 AM UTC
- Configure via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars

**Frontend Simplification**:
- Removed strategy configuration UI (strategies tab, enable/disable, params)
- Trading page now shows: Balance, P&L, Positions, Signals, Trades, Wallet
- Strategy management done via CLI, not frontend

**Executor Runner Updates** (`src/executor/engine/runner.py`):
- Loads strategies from `deployed_strategies.yaml` instead of config.yaml
- Hot-reload: Detects strategy file changes and reloads
- Logs every decision to `trade_decisions` table
- Sends Telegram alerts on trade execution

**Results**:
- Strategies are now versioned Python files (git-tracked, reviewable)
- Every trade decision has full audit trail for replay/analysis
- CLI-based deployment workflow: write → backtest → deploy
- Telegram notifications for real-time monitoring

---

### Session 10 - Dec 20, 2024 (Config-Driven Strategy System)
**Goal**: Replace 25 individual strategy files with a config-driven architecture. Add per-strategy wallets and performance tracking.

**Architecture Refactoring**:
- **OLD**: 25 separate Python files, one per strategy variant
- **NEW**: 1 YAML config + 6 strategy type classes

**New Files Created**:
- `strategies.yaml` - Central config with all 25 strategy instances
- `strategies/types/` - 6 strategy type classes:
  - `no_bias.py` - NoBiasStrategy (11 instances)
  - `longshot.py` - LongshotStrategy (3 instances)
  - `mean_reversion.py` - MeanReversionStrategy (4 instances)
  - `whale_fade.py` - WhaleFadeStrategy (3 instances)
  - `flow.py` - FlowStrategy (3 instances)
  - `new_market.py` - NewMarketStrategy (1 instance)
- `strategies/loader.py` - Reads YAML, instantiates strategies
- `strategies/performance.py` - Sharpe, Sortino, drawdown calculations
- `cli/debug.py` - CLI to diagnose "why isn't it trading?"

**Per-Strategy Wallets** (`src/executor/models.py`):
- `StrategyBalance` model with $400 allocation per strategy
- Tracks: current_usd, realized_pnl, unrealized_pnl, trade_count, win/loss
- High/low water marks, max drawdown tracking

**Performance Metrics**:
- Sharpe ratio (annualized)
- Sortino ratio (downside deviation)
- Max drawdown (USD and %)
- Win rate, profit factor, expectancy

**New API Endpoints** (`src/api/routes/executor.py`):
- `GET /executor/strategies` - List all loaded strategies
- `GET /executor/strategies/leaderboard` - Ranked by P&L, Sharpe, etc.
- `GET /executor/strategies/balances` - Per-strategy wallet info
- `GET /executor/strategies/{name}/metrics` - Detailed performance
- `GET /executor/strategies/{name}/debug` - Funnel diagnostics

**Executor Updates**:
- Runner now loads from `strategies.yaml` (not `deployed_strategies.yaml`)
- Auto-reloads when YAML changes
- Each strategy type has `get_debug_stats()` for funnel analysis

**Files Deleted**: 25 individual strategy Python files (2,763 → 1,900 lines)

**Results**:
- Add new strategy variants by editing YAML (no code changes)
- Debug CLI: `python3 -m cli.debug esports_no_1h`
- Leaderboard: `python3 -m cli.debug` (no args)
- Full performance tracking with Sharpe/drawdown

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
| `GET /api/monitoring/tier-transitions?hours=1` | Tier flow visualization (T0→T1, deactivations) |
| `GET /api/monitoring/task-activity?limit=50` | Celery task success/failure summary |
| `GET /api/monitoring/redis-stats` | Redis memory, key counts, ops/sec |
| `GET /api/database/tables` | List all tables with row counts |
| `GET /api/database/tables/{name}` | Browse table data with pagination |
| `GET /api/executor/decisions` | Recent trade decisions with audit info |
| `GET /api/executor/strategies` | List all loaded strategies |
| `GET /api/executor/strategies/leaderboard` | Performance ranking (sort by P&L, Sharpe) |
| `GET /api/executor/strategies/balances` | Per-strategy wallet balances |
| `GET /api/executor/strategies/{name}/metrics` | Detailed metrics for one strategy |
| `GET /api/executor/strategies/{name}/debug` | Debug info (params, funnel, decisions) |

---

## Config-Driven Strategy System

### Architecture

Strategies use a **config-driven** approach:
- `strategies.yaml` - Central config with all strategy instances
- `strategies/types/` - Python classes for each strategy type (6 types)
- Add new variants by editing YAML - no code changes needed

### Strategy Types

| Type | Class | Count | Purpose |
|------|-------|-------|---------|
| `no_bias` | `NoBiasStrategy` | 11 | Exploit NO resolution bias by category |
| `longshot` | `LongshotStrategy` | 3 | Buy high-probability outcomes near expiry |
| `mean_reversion` | `MeanReversionStrategy` | 4 | Fade price deviations from mean |
| `whale_fade` | `WhaleFadeStrategy` | 3 | Fade large trades expecting reversion |
| `flow` | `FlowStrategy` | 3 | Fade volume spikes and order flow |
| `new_market` | `NewMarketStrategy` | 1 | Buy NO on new markets |

### Adding a New Strategy Variant

Edit `strategies.yaml` and add an entry under the appropriate type:

```yaml
no_bias:
  - name: politics_no_24h
    category: POLITICS
    historical_no_rate: 0.55
    min_hours: 1
    max_hours: 24
    min_liquidity: 1000
```

The executor auto-reloads when `strategies.yaml` changes.

### CLI Commands

```bash
# Check system status
python -m cli.status

# List all strategies
python -m cli.deploy --list

# Validate config
python -m cli.deploy --validate

# Debug a strategy (why isn't it trading?)
python -m cli.debug esports_no_1h

# Show leaderboard
python -m cli.debug

# Run funnel analysis on all strategies
python -m cli.debug --funnel
```

### Telegram Alerts Setup

1. Create a Telegram bot via @BotFather
2. Get your chat ID by messaging @userinfobot
3. Set environment variables:
   ```bash
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_CHAT_ID=123456789
   ```
4. Alerts sent automatically on:
   - Trade execution
   - Position close (with P&L)
   - Critical errors
   - Daily summary (9 AM UTC)

---

## Slash Commands Reference

Claude Code slash commands for interacting with the system. Type `/command` to invoke.

### `/trading` - Trading CLI Mode

Interactive trading interface for managing strategies, positions, and system status.

**Startup**: Displays command menu, enters CLI mode until you say "exit"

| Command | Description |
|---------|-------------|
| `status` | Show balance, positions, P&L, recent decisions |
| `strategies` | List deployed strategies with parameters |
| `create` | Create new strategy from natural language description |
| `adjust` | Change strategy parameters or risk settings |
| `deploy` | Deploy/undeploy a strategy |
| `backtest` | Test strategy against historical data |
| `logs` | Show recent errors or activity |
| `advise` | Switch to proactive advisor mode (recommendations) |

**Example session:**
```
> /trading
> status
> strategies
> adjust longshot_yes_v1 size_usd=50
> backtest strategies/my_new_strategy.py --days 14
> exit
```

**Key files referenced:**
- `TRADING_CLI.md` - Full command reference and queries
- `config.yaml` - Risk, sizing, execution settings
- `deployed_strategies.yaml` - Active strategies
- `strategies/*.py` - Strategy code files

---

### `/categorize` - Market Categorization

Categorize Polymarket markets into L1/L2/L3 taxonomy.

| Command | Description |
|---------|-------------|
| `stats` | Show categorization statistics (total, by rules, uncategorized) |
| `batch [n]` | Categorize n uncategorized markets (default: 100) |
| `validate [n]` | Validate n random rule-categorized markets |
| `rules` | Show rule performance stats |
| `run-rules` | Run rule engine on uncategorized markets |

**Example session:**
```
> /categorize stats

=== CATEGORIZATION STATS ===
Total markets: 14,124
By rules: 1,150 (8.1%)
By Claude: 858 (6.1%)
Uncategorized: 0

> /categorize batch 50
[Categories 50 markets, outputs JSON, saves to DB]

> /categorize validate 30
[Validates 30 rule-categorized markets for accuracy]
```

**Taxonomy (L1 categories):**
CRYPTO, SPORTS, ESPORTS, POLITICS, ECONOMICS, BUSINESS, ENTERTAINMENT, WEATHER, SCIENCE, TECH, LEGAL, OTHER

**Key files:**
- `.claude/commands/categorize.md` - Full command reference
- `src/services/rule_categorizer.py` - Rule engine
- `cli/categorize_helpers.py` - CLI helper functions

---

### Strategy Research Lab

Systematic strategy research through friction analysis and rigorous backtesting.

**Slash Commands:**

| Command | Description |
|---------|-------------|
| `/hypothesis <bucket>` | Generate testable hypothesis for friction bucket |
| `/test <exp_id>` | Run backtest with robustness checks |
| `/verdict <exp_id>` | Evaluate results, update ledger |

**Friction Buckets:**
- `timing` - Information arrives gradually; markets update slowly
- `liquidity` - Thin markets, volume clustering, overreactions
- `behavioral` - Anchoring, favorite/underdog bias, narrative preference
- `mechanism` - Resolution rules traders misunderstand
- `cross-market` - Same outcome priced in multiple markets

**Experiment Workflow:**
```
/hypothesis timing     → Creates experiments/exp-001/spec.md
/test exp-001          → Creates config.yaml, results.json
/verdict exp-001       → Creates verdict.md, updates ledger
```

**Kill Criteria (strategy is dead if ANY fail):**
- Sharpe < 0.5
- Win rate < 51%
- Sample size < 50 trades
- Profit factor < 1.1
- Time-split inconsistent

**CLI Tools:**
```bash
python3 -m cli.ledger stats              # Ledger statistics
python3 -m cli.ledger search timing      # Search by friction bucket
python3 -m cli.robustness exp-001 --all  # Run robustness checks
python3 -m cli.ship exp-001              # Preview deployment
python3 -m cli.ship exp-001 --apply      # Deploy to strategies.yaml
```

**Trading Integration:**
From `/trading` mode, use:
- `research` - View ledger stats and recent experiments
- `ship <exp_id>` - Deploy shipped experiment to strategies.yaml

**Key files:**
- `RESEARCH_LAB.md` - Full reference documentation
- `experiments/` - Experiment specs and results
- `ledger/insights.jsonl` - Accumulated learnings
- `.claude/commands/hypothesis.md`, `test.md`, `verdict.md`
- `cli/ledger.py`, `cli/robustness.py`, `cli/ship.py`
- `src/backtest/robustness.py`

---

## BigQuery Backtesting

The backtesting engine uses BigQuery by default for efficient server-side filtering and aggregation. This avoids loading large datasets into memory (170K markets + 11M snapshots crashed the PostgreSQL approach).

### Configuration

```
Project: elite-buttress-480609-b0
Dataset: longshot
Tables: historical_markets, historical_snapshots
Location: EU
```

### CLI Usage

```bash
# Simple backtest (BigQuery - default)
python -m cli.backtest --side NO --yes-min 0.55 --yes-max 0.95

# With robustness checks
python -m cli.backtest --side NO --robustness

# Category-specific
python -m cli.backtest --side NO --category Crypto --hours-min 12 --hours-max 48

# Show data statistics
python -m cli.backtest --stats

# Robustness checks
python -m cli.robustness --side NO --yes-min 0.55 --all

# From experiment config
python -m cli.robustness experiments/exp-001/config.yaml --all

# Legacy PostgreSQL mode (fallback, slower)
python -m cli.backtest --use-postgres --side NO --days 30
```

### Query Efficiency Guidelines

**DO:**
1. **Aggregate in SQL** - Never return raw rows, always use COUNT/SUM/AVG
2. **Use ROW_NUMBER()** - Deduplicate to one snapshot per market in SQL
3. **Filter early** - Put all WHERE conditions in the main CTE
4. **Return only metrics** - Dict of floats, not DataFrames or objects
5. **Use SAFE_DIVIDE** - Avoid division by zero errors

**DON'T:**
1. **Don't SELECT \*** - Always specify exact columns needed
2. **Don't load to DataFrame** - Use `client.query().result()` directly
3. **Don't iterate rows in Python** - Aggregate in SQL
4. **Don't use multiple queries** - Combine with CTEs where possible

### Schema Notes

- **Timestamps are in nanoseconds** - Divide by 1e9 for seconds
- **Winner field** - Contains "Yes"/"No" (not "YES"/"NO")
- **Liquidity column** - All zeros (unusable, don't filter by it)
- **Volume** - In USD, ranges from 0 to 400M

### Key Functions

```python
from src.backtest.bigquery import (
    run_bq_backtest,      # Run simple backtest
    run_bq_robustness,    # Run with time/volume/category splits
    get_bq_data_stats,    # Get dataset statistics
    format_bq_backtest_summary,    # Format results
    format_bq_robustness_summary,  # Format robustness results
)

# Example usage
metrics = run_bq_backtest(
    side="NO",
    yes_price_min=0.55,
    yes_price_max=0.95,
    hours_min=12,
    hours_max=48,
    min_volume=1000,
    categories=["Crypto", "Sports"],
)
print(f"Win rate: {metrics.win_rate:.1%}, Sharpe: {metrics.sharpe:.2f}")
```

### Robustness Checks

All strategies must pass these checks to avoid overfitting:

| Check | Purpose | Pass Criteria |
|-------|---------|---------------|
| Time Split | First half vs second half | Both halves profitable |
| Volume Split | High vs low volume markets | Edge consistent across volume |
| Category Split | Per macro_category | 50%+ categories have edge |

---

## Database Tables

| Table | Purpose | Row Count |
|-------|---------|-----------|
| markets | Market metadata, tier, token IDs | ~9,300 |
| snapshots | Price/volume/orderbook features | ~1,148,000 |
| trades | Individual trades from WebSocket | ~76,000 |
| orderbook_snapshots | Full orderbook storage (JSONB) | ~495,000 |
| task_runs | Task execution history (7-day retention) | varies |
| tier_transitions | Market tier change tracking (7-day retention) | varies |
| whale_events | Large trade tracking | - |
| trade_decisions | Strategy decision audit trail | varies |
| signals | Trading signals from strategies | varies |
| positions | Open/closed trading positions | varies |
| executor_trades | Executed trades | varies |
| paper_balances | Overall paper trading balance | 1 |
| strategy_balances | Per-strategy wallet allocation and P&L | 25 |

---

## Notes for Next Session

- Data collection is stable and running at **100% task success rate**
- WebSocket capturing **~380 trades/minute** across 4 connections
- System hardened for autonomous operation (log rotation, health checks, staggered reconnects)
- Use `/api/monitoring/critical` for external uptime monitoring (returns 503 on failure)
- **Config-driven strategies**: 25 strategies in `strategies.yaml`
- **Per-strategy wallets**: Each strategy has $400 allocation in `strategy_balances` table
- **Performance tracking**: Sharpe, Sortino, drawdown in `strategies/performance.py`
- **Debug CLI**: `python -m cli.debug <strategy_name>` to diagnose issues
- **Telegram alerts**: Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to enable
- **BigQuery backtesting**: Default engine for backtests (`python -m cli.backtest`), use `--use-postgres` for legacy mode
- **Next priorities**:
  1. Run `/verdict exp-004` to evaluate behavioral NO bias experiment results
  2. Monitor strategy performance via leaderboard (`python -m cli.debug`)
  3. Add new strategy variants by editing `strategies.yaml` (no code changes)
  4. Begin ML pipeline design once sufficient resolved markets exist

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
