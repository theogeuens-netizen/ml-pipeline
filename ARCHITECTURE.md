# Architecture Overview

> **Polymarket ML Trading System** - A production-grade data collection pipeline and trading executor for Polymarket prediction markets.

---

## System Components

```
polymarket-ml/
├── src/
│   ├── api/                    # FastAPI backend
│   │   └── routes/             # REST + WebSocket endpoints
│   ├── collectors/             # Real-time data collection
│   │   ├── websocket.py        # Multi-connection WebSocket collector
│   │   ├── healthcheck.py      # Container health checks
│   │   └── metrics.py          # Trade metrics computation
│   ├── config/                 # Configuration management
│   ├── db/                     # Database layer
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── database.py         # Connection management
│   │   └── redis.py            # Redis client
│   ├── executor/               # Trading executor system
│   │   ├── clients/            # Order client (py-clob-client)
│   │   ├── engine/             # Scanner + runner
│   │   ├── execution/          # Paper/live execution
│   │   ├── portfolio/          # Risk, sizing, positions
│   │   └── strategies/         # Strategy framework
│   ├── fetchers/               # API clients
│   │   ├── gamma.py            # Gamma API (market discovery)
│   │   ├── clob.py             # CLOB API (orderbook)
│   │   └── base.py             # Base client with circuit breaker
│   ├── tasks/                  # Celery tasks
│   │   ├── discovery.py        # Market discovery + tier management
│   │   ├── snapshots.py        # Feature collection
│   │   └── celery_app.py       # Celery configuration
│   └── ml/                     # ML pipeline (future)
├── frontend/                   # React + TypeScript dashboard
│   └── src/
│       ├── pages/              # Dashboard, Monitoring, Database, Executor
│       ├── api/                # API client + WebSocket
│       └── hooks/              # React Query hooks
├── alembic/                    # Database migrations
└── docker-compose.yml          # 10 container orchestration
```

---

## 1. Data Collection Pipeline

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATA COLLECTION SYSTEM                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────┐ │
│  │   GAMMA     │    │    CLOB     │    │         WEBSOCKET            │ │
│  │    API      │    │    API      │    │   (4 parallel connections)   │ │
│  │             │    │             │    │                              │ │
│  │ • Markets   │    │ • Orderbook │    │ • Real-time trades           │ │
│  │ • Prices    │    │ • Depth     │    │ • Price changes              │ │
│  │ • Volume    │    │ • Spread    │    │ • Book updates               │ │
│  │ • Momentum  │    │             │    │ • 2000 market capacity       │ │
│  └──────┬──────┘    └──────┬──────┘    └──────────────┬───────────────┘ │
│         │                  │                          │                 │
│         └────────────┬─────┴──────────────────────────┘                 │
│                      ▼                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    CELERY WORKERS (3 workers)                       │ │
│  │  • celery-worker: discovery, tier updates, cleanup                  │ │
│  │  • celery-snapshots: T0-T4 snapshot collection                      │ │
│  │  • celery-snapshots-2: T3-T4 batch processing                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                      │                                                   │
│         ┌────────────┼────────────┐                                     │
│         ▼            ▼            ▼                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                              │
│  │ POSTGRES │  │  REDIS   │  │ FRONTEND │                              │
│  │          │  │          │  │          │                              │
│  │ markets  │  │ cache    │  │ React    │                              │
│  │ snapshots│  │ buffers  │  │ dashboard│                              │
│  │ trades   │  │ metrics  │  │ monitor  │                              │
│  │ orderbook│  │ ws:*     │  │ executor │                              │
│  └──────────┘  └──────────┘  └──────────┘                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Tiered Collection Schedule

Markets are assigned tiers based on hours to resolution:

| Tier | Hours to Resolution | Snapshot Interval | Use Case |
|------|---------------------|-------------------|----------|
| T0 | > 48h | 60 min | Background monitoring |
| T1 | 12-48h | 5 min | Building position |
| T2 | 4-12h | 1 min | Active trading |
| T3 | 1-4h | 30 sec | High-frequency signals |
| T4 | < 1h | 15 sec | Resolution approach |

### Features Collected (~65 per snapshot)

**From Gamma API (18 features)**:
- Price: `price`, `best_bid`, `best_ask`, `spread`, `last_trade_price`
- Momentum: `price_change_1d`, `price_change_1w`, `price_change_1m`
- Volume: `volume_total`, `volume_24h`, `volume_1w`, `liquidity`
- Metadata: `category`, `neg_risk`, `competitive`

**From CLOB API (15 features, T2+ only)**:
- Depth: `bid_depth_5/10/20/50`, `ask_depth_5/10/20/50`
- Levels: `bid_levels`, `ask_levels`
- Imbalance: `book_imbalance`
- Walls: `bid_wall_price`, `bid_wall_size`, `ask_wall_price`, `ask_wall_size`

**From WebSocket (17 features)**:
- Trade flow: `trade_count_1h`, `buy_count_1h`, `sell_count_1h`, `volume_1h`, `vwap_1h`
- Whale metrics: `whale_count_1h`, `whale_volume_1h`, `whale_net_flow_1h`, `time_since_whale`

**Context (15 features)**:
- Time: `hours_to_close`, `day_of_week`, `hour_of_day`
- Computed: derived metrics at snapshot time

### WebSocket Architecture

Multi-connection collector for high throughput:

```python
# 4 parallel WebSocket connections
# Each handles up to 500 markets (Polymarket limit)
# Total capacity: 2,000 markets

class MultiConnectionCollector:
    - Round-robin market distribution
    - Staggered reconnection (prevents data gaps)
    - Trade rate monitoring (MIN_TRADES_PER_MINUTE = 30)
    - Auto-reconnect on degraded connection
    - Per-connection health tracking
```

### Market Lifecycle Tracking

Tier transitions are tracked for monitoring:

```
Discovery → T0 → T1 → T2 → T3 → T4 → Deactivation
                                        ↓
                              (resolved | expired | no_trades | delisted)
```

All transitions logged with reason and `hours_to_close` at transition time.

---

## 2. Trading Executor System

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TRADING EXECUTOR                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │   Scanner   │────▶│  Strategies │────▶│   Signals   │                │
│  │             │     │   .scan()   │     │             │                │
│  │ Get markets │     │ Filter,     │     │ BUY/SELL    │                │
│  │ from DB     │     │ analyze,    │     │ with edge,  │                │
│  │             │     │ yield       │     │ confidence  │                │
│  └─────────────┘     └─────────────┘     └──────┬──────┘                │
│                                                  │                       │
│                                                  ▼                       │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │ Risk Mgr    │◀────│   Sizing    │◀────│  Approved   │                │
│  │             │     │             │     │  Signals    │                │
│  │ Max pos     │     │ Fixed       │     │             │                │
│  │ Max exposure│     │ Kelly       │     │ Pass risk   │                │
│  │ Drawdown    │     │ Vol-scaled  │     │ checks      │                │
│  └──────┬──────┘     └─────────────┘     └─────────────┘                │
│         │                                                                │
│         ▼                                                                │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         EXECUTOR                                 │   │
│  │  ┌─────────────────┐          ┌─────────────────┐               │   │
│  │  │  Paper Mode     │          │   Live Mode     │               │   │
│  │  │                 │          │                 │               │   │
│  │  │ Simulated fills │          │ py-clob-client  │               │   │
│  │  │ Virtual balance │          │ Real orders     │               │   │
│  │  │ Slippage model  │          │ Wallet signing  │               │   │
│  │  └─────────────────┘          └─────────────────┘               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Strategy Interface

```python
class Strategy(ABC):
    name: str
    description: str
    version: str

    @abstractmethod
    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """Yield signals for opportunities."""
        pass

    def filter(self, market: MarketData) -> bool:
        """Pre-filter markets."""
        return True

    def should_exit(self, position, market) -> Optional[Signal]:
        """Custom exit logic."""
        return None
```

### Built-in Strategies

| Strategy | Description | Signal |
|----------|-------------|--------|
| `longshot_yes` | Buy YES on high-probability events (92-99%) near expiry | Near-certain outcomes are slightly underpriced |
| `longshot_no` | Buy NO against overpriced longshots (YES < 8%) | Tail risks are systematically overestimated |
| `mean_reversion` | Fade large price moves that look like overreactions | Enter when move exceeds threshold |
| `term_structure` | Exploit probability violations in multi-deadline markets | Later deadlines should have >= probability |

### Execution Modes

```yaml
execution:
  default_order_type: limit  # market | limit | spread

  # Market: Cross spread immediately, pay taker fee
  # Limit: Post at offset from mid, wait for fill
  # Spread: Post to capture spread, fall back to market after timeout
```

### Position Sizing

```yaml
sizing:
  method: fixed      # fixed | kelly | volatility_scaled
  fixed_amount_usd: 25
  kelly_fraction: 0.25
```

### Risk Management

```yaml
risk:
  max_position_usd: 100        # Per-position limit
  max_total_exposure_usd: 1000 # Portfolio limit
  max_positions: 20            # Open position count
  max_drawdown_pct: 0.15       # Stop trading if exceeded
```

---

## 3. Database Schema

### Core Tables

```sql
-- Market metadata and tier tracking
markets (
    id, condition_id, slug, question,
    yes_token_id, no_token_id,
    end_date, tier, active, resolved, outcome,
    initial_price, initial_volume, initial_liquidity,
    category, tracking_started_at
)

-- ~65 feature snapshots at tiered intervals
snapshots (
    id, market_id, timestamp, tier,
    price, best_bid, best_ask, spread,
    volume_24h, liquidity,
    bid_depth_5/10/20/50, ask_depth_5/10/20/50,
    trade_count_1h, whale_count_1h, vwap_1h,
    hours_to_close, day_of_week, hour_of_day
)

-- Real-time trades from WebSocket
trades (
    id, market_id, timestamp,
    price, size, side, whale_tier
)

-- Full orderbook storage (JSONB)
orderbook_snapshots (
    id, market_id, timestamp,
    bids, asks,  -- JSONB arrays
    total_bid_depth, total_ask_depth
)

-- Market tier transition tracking
tier_transitions (
    id, market_id, condition_id, market_slug,
    from_tier, to_tier, transitioned_at,
    hours_to_close, reason
)

-- Celery task execution logging
task_runs (
    id, task_name, task_id, tier,
    started_at, completed_at, duration_ms,
    status, markets_processed, rows_inserted,
    error_message, error_traceback
)

-- Trading executor tables
positions (
    id, market_id, token_id, strategy_name,
    side, entry_price, size_shares, cost_basis,
    current_price, current_value, unrealized_pnl,
    status, is_paper
)

signals (
    id, strategy_name, market_id, token_id,
    side, reason, edge, confidence,
    price_at_signal, suggested_size_usd,
    status, processed_at
)
```

---

## 4. Docker Services

```yaml
# 10 containers total
services:
  postgres:       # PostgreSQL 16, port 5433
  redis:          # Redis 7, port 6380
  api:            # FastAPI backend, port 8000
  celery-worker:  # Discovery + default tasks
  celery-snapshots:    # Snapshot worker 1
  celery-snapshots-2:  # Snapshot worker 2
  celery-beat:    # Task scheduler
  websocket-collector: # Real-time trade collection
  frontend:       # React dashboard, port 80
  flower:         # Celery monitoring, port 5555
```

All services have:
- Health checks
- Log rotation (50MB max, 3 files)
- Auto-restart on failure

---

## 5. API Endpoints

### Monitoring

| Endpoint | Purpose |
|----------|---------|
| `GET /api/monitoring/health` | System health overview |
| `GET /api/monitoring/critical` | Returns 503 on failures (for external monitoring) |
| `GET /api/monitoring/websocket-coverage` | Compare subscribed vs should-subscribe |
| `GET /api/monitoring/tier-transitions` | Tier flow visualization |
| `GET /api/monitoring/task-activity` | Celery task summary |
| `GET /api/monitoring/redis-stats` | Redis memory, keys, ops/sec |

### Database Browser

| Endpoint | Purpose |
|----------|---------|
| `GET /api/database/tables` | List all tables with row counts |
| `GET /api/database/tables/{name}` | Browse table data with pagination |

### Executor

| Endpoint | Purpose |
|----------|---------|
| `GET /api/executor/status` | Mode, balance, positions |
| `GET /api/executor/positions` | All open positions |
| `GET /api/executor/trades` | Trade history |
| `GET /api/executor/signals` | Signal feed |
| `POST /api/executor/config` | Update configuration |
| `WS /api/executor/ws` | Real-time updates |

### Strategies

| Endpoint | Purpose |
|----------|---------|
| `GET /api/strategies` | All strategies with status |
| `POST /api/strategies/{name}/enable` | Enable strategy |
| `POST /api/strategies/{name}/disable` | Disable strategy |
| `POST /api/strategies/{name}/config` | Update strategy params |

---

## 6. Frontend Dashboard

### Pages

1. **Dashboard** (`/`) - System overview, stats, tier distribution
2. **Monitoring** (`/monitoring`) - WebSocket health, task activity, tier flow
3. **Database** (`/database`) - Table browser with pagination
4. **Executor** (`/executor`) - Trading positions, signals, P&L

### Key Features

- Real-time updates via React Query (polling) and WebSocket
- Dark mode UI
- Responsive design
- Toast notifications for events

---

## 7. Configuration

### Environment Variables

```bash
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/polymarket_ml
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
```

### Settings (`src/config/settings.py`)

```python
# Data collection
ml_volume_threshold: float = 100.0      # Min 24h volume
ml_lookahead_hours: int = 336           # 2 weeks

# Tier boundaries
tier_0_min_hours: float = 48.0
tier_1_min_hours: float = 12.0
tier_2_min_hours: float = 4.0
tier_3_min_hours: float = 1.0

# WebSocket
websocket_num_connections: int = 4      # 2000 market capacity
websocket_enabled_tiers: list = [2,3,4]
```

### Executor Config (`executor_config.yaml`)

```yaml
mode: paper  # paper | live

risk:
  max_position_usd: 100
  max_total_exposure_usd: 1000
  max_positions: 20

strategies:
  longshot_yes:
    enabled: true
    params:
      min_probability: 0.92
      max_probability: 0.99
```

---

## 8. Quick Start

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f websocket-collector
docker-compose logs -f celery-snapshots

# Access dashboards
open http://localhost        # Frontend
open http://localhost:5555   # Flower (Celery)
open http://localhost:8000/docs  # API docs

# Check health
curl http://localhost:8000/api/monitoring/health
```

---

## 9. Future Work

- **ML Pipeline**: XGBoost model training once sufficient resolved markets exist
- **Backtesting**: Historical strategy testing framework
- **Alerting**: External notifications for system degradation
- **Multi-strategy execution**: Parallel live strategy execution
