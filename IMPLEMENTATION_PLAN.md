# Phase 1 Implementation Plan

> **Objective**: Build a fully automated data collection system with monitoring dashboard.
>
> **End State**: Celery tasks run 24/7, collecting ~65 features per snapshot. Frontend dashboard allows manual verification of collection health.

---

## Implementation Order & Dependencies

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        IMPLEMENTATION SEQUENCE                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  STEP 1: Foundation                                                      │
│  ├── 1.1 Python environment & dependencies                               │
│  ├── 1.2 Configuration (Pydantic settings)                               │
│  ├── 1.3 Database models (SQLAlchemy)                                    │
│  ├── 1.4 Database infrastructure (engine, sessions)                      │
│  ├── 1.5 Alembic migrations                                              │
│  └── 1.6 Docker infrastructure (postgres, redis)                         │
│          │                                                               │
│          ▼                                                               │
│  STEP 2: API Clients                                                     │
│  ├── 2.1 HTTP client base (rate limiting, retries)                       │
│  ├── 2.2 Gamma API client                                                │
│  └── 2.3 CLOB API client                                                 │
│          │                                                               │
│          ▼                                                               │
│  STEP 3: Basic Celery Tasks                                              │
│  ├── 3.1 Celery app configuration                                        │
│  ├── 3.2 Market discovery task                                           │
│  ├── 3.3 Basic snapshot task (REST only, no trade metrics)               │
│  └── 3.4 Tier update task                                                │
│          │                                                               │
│          ├──────────────────────┐                                        │
│          ▼                      ▼                                        │
│  STEP 4: WebSocket Collector    STEP 5: Redis Layer                      │
│  ├── 4.1 Connection manager     ├── 5.1 Redis client wrapper             │
│  ├── 4.2 Event handlers         ├── 5.2 Trade buffer operations          │
│  ├── 4.3 Trade processing       └── 5.3 Metrics cache operations         │
│  └── 4.4 Health monitoring               │                               │
│          │                               │                               │
│          └──────────────┬────────────────┘                               │
│                         ▼                                                │
│  STEP 6: Complete Snapshot Tasks                                         │
│  ├── 6.1 Metrics computation from Redis                                  │
│  ├── 6.2 Full snapshot builder (all 65 features)                         │
│  └── 6.3 Tiered scheduling (T0-T4)                                       │
│                         │                                                │
│                         ▼                                                │
│  STEP 7: FastAPI Backend                                                 │
│  ├── 7.1 App setup & middleware                                          │
│  ├── 7.2 Health endpoints                                                │
│  ├── 7.3 Stats endpoints                                                 │
│  ├── 7.4 Markets endpoints                                               │
│  ├── 7.5 Tasks endpoints                                                 │
│  └── 7.6 Data quality endpoints                                          │
│                         │                                                │
│                         ▼                                                │
│  STEP 8: Frontend Dashboard                                              │
│  ├── 8.1 React/Vite/Tailwind setup                                       │
│  ├── 8.2 API client & hooks                                              │
│  ├── 8.3 Dashboard page                                                  │
│  ├── 8.4 Markets page                                                    │
│  ├── 8.5 Data quality page                                               │
│  └── 8.6 Tasks page                                                      │
│                         │                                                │
│                         ▼                                                │
│  STEP 9: Deployment & Automation                                         │
│  ├── 9.1 Dockerfile for Python app                                       │
│  ├── 9.2 Dockerfile for frontend                                         │
│  ├── 9.3 Complete docker-compose.yml                                     │
│  ├── 9.4 Startup scripts & health checks                                 │
│  └── 9.5 End-to-end testing                                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## STEP 1: Foundation

### 1.1 Python Environment & Dependencies

**File**: `requirements.txt`

```
# Core
fastapi==0.109.0
uvicorn[standard]==0.27.0
pydantic==2.5.3
pydantic-settings==2.1.0

# Database
sqlalchemy==2.0.25
psycopg2-binary==2.9.9
alembic==1.13.1

# Redis
redis==5.0.1

# Celery
celery[redis]==5.3.6
flower==2.0.1

# HTTP clients
httpx==0.26.0
websockets==12.0

# Data processing
pandas==2.1.4
numpy==1.26.3

# Utilities
python-dotenv==1.0.0
structlog==24.1.0
tenacity==8.2.3

# Testing
pytest==7.4.4
pytest-asyncio==0.23.3
```

**Deliverable**: Virtual environment with all dependencies installed

---

### 1.2 Configuration

**File**: `src/config/settings.py`

```python
"""
Configuration module using Pydantic Settings.

All settings loaded from environment variables with sensible defaults.
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ===========================================
    # Database
    # ===========================================
    database_url: str = "postgresql://postgres:postgres@localhost:5433/polymarket_ml"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ===========================================
    # Redis
    # ===========================================
    redis_url: str = "redis://localhost:6380/0"
    redis_trade_buffer_ttl: int = 7200  # 2 hours
    redis_trade_buffer_max: int = 10000

    # ===========================================
    # Celery
    # ===========================================
    celery_broker_url: str = "redis://localhost:6380/0"
    celery_result_backend: str = "redis://localhost:6380/1"

    # ===========================================
    # Polymarket APIs
    # ===========================================
    gamma_api_base: str = "https://gamma-api.polymarket.com"
    clob_api_base: str = "https://clob.polymarket.com"
    websocket_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/"

    # Rate limits (requests per second, conservative)
    gamma_rate_limit: float = 10.0  # 125/10s = 12.5/s, use 10
    clob_rate_limit: float = 15.0   # 200/10s = 20/s, use 15

    # ===========================================
    # Data Collection
    # ===========================================
    # Market filtering
    ml_volume_threshold: float = 1000.0  # Min 24h volume to track
    ml_lookahead_hours: int = 72  # Track markets up to 72h out

    # Tier boundaries (hours to resolution)
    tier_0_min_hours: float = 48.0   # > 48h
    tier_1_min_hours: float = 12.0   # 12-48h
    tier_2_min_hours: float = 4.0    # 4-12h
    tier_3_min_hours: float = 1.0    # 1-4h
    # tier_4: < 1h

    # Collection intervals (seconds)
    tier_0_interval: int = 3600   # 60 min
    tier_1_interval: int = 300    # 5 min
    tier_2_interval: int = 60     # 1 min
    tier_3_interval: int = 30     # 30 sec
    tier_4_interval: int = 15     # 15 sec

    # Orderbook collection (only for T2+)
    orderbook_enabled_tiers: list[int] = [2, 3, 4]

    # ===========================================
    # Whale Detection
    # ===========================================
    whale_tier_1_threshold: float = 500.0    # Large trade
    whale_tier_2_threshold: float = 2000.0   # Whale
    whale_tier_3_threshold: float = 10000.0  # Mega whale

    # ===========================================
    # WebSocket
    # ===========================================
    websocket_enabled_tiers: list[int] = [2, 3, 4]  # Only T2+ get WS
    websocket_reconnect_delay: float = 5.0
    websocket_max_reconnect_delay: float = 60.0

    # ===========================================
    # Application
    # ===========================================
    log_level: str = "INFO"
    debug: bool = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
```

**Deliverable**: Configuration loads from `.env` file with all parameters

---

### 1.3 Database Models

**File**: `src/db/models.py`

Create SQLAlchemy 2.0 models for all tables:

#### Markets Table
```python
class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255))
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Event grouping
    event_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    event_slug: Mapped[Optional[str]] = mapped_column(String(255))
    event_title: Mapped[Optional[str]] = mapped_column(String(500))

    # Token IDs for CLOB API
    yes_token_id: Mapped[Optional[str]] = mapped_column(String(100))
    no_token_id: Mapped[Optional[str]] = mapped_column(String(100))

    # Timing
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Initial state (at discovery)
    initial_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    initial_spread: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    initial_volume: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    initial_liquidity: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # Resolution
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(20))  # YES/NO/INVALID
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Collection tracking
    tier: Mapped[int] = mapped_column(SmallInteger, default=0, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tracking_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    category: Mapped[Optional[str]] = mapped_column(String(100))
    tags: Mapped[Optional[dict]] = mapped_column(JSONB)
    neg_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    competitive: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    enable_order_book: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="market")
    trades: Mapped[list["Trade"]] = relationship(back_populates="market")
```

#### Snapshots Table (with all ~50 feature columns)
```python
class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    tier: Mapped[int] = mapped_column(SmallInteger)

    # === PRICE FIELDS (5) ===
    price: Mapped[float] = mapped_column(Numeric(10, 6))
    best_bid: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    best_ask: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    spread: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    last_trade_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === MOMENTUM FIELDS - FREE FROM GAMMA (3) ===
    price_change_1d: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_change_1w: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_change_1m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === VOLUME FIELDS (4) ===
    volume_total: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    volume_24h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    volume_1w: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    liquidity: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # === ORDERBOOK DEPTH (from CLOB, T2+ only) (8) ===
    bid_depth_5: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid_depth_10: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid_depth_20: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    bid_depth_50: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_5: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_10: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_20: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_depth_50: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # === ORDERBOOK DERIVED (5) ===
    bid_levels: Mapped[Optional[int]] = mapped_column(SmallInteger)
    ask_levels: Mapped[Optional[int]] = mapped_column(SmallInteger)
    book_imbalance: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    bid_wall_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    bid_wall_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ask_wall_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    ask_wall_size: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # === TRADE FLOW (from WebSocket via Redis) (9) ===
    trade_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    buy_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    sell_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    buy_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    sell_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    avg_trade_size_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    max_trade_size_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    vwap_1h: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === WHALE METRICS (from WebSocket via Redis) (8) ===
    whale_count_1h: Mapped[Optional[int]] = mapped_column(Integer)
    whale_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_buy_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_sell_volume_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_net_flow_1h: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    whale_buy_ratio_1h: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    time_since_whale: Mapped[Optional[int]] = mapped_column(Integer)  # seconds
    pct_volume_from_whales: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # === CONTEXT FIELDS (3) ===
    hours_to_close: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    day_of_week: Mapped[Optional[int]] = mapped_column(SmallInteger)
    hour_of_day: Mapped[Optional[int]] = mapped_column(SmallInteger)

    # Relationship
    market: Mapped["Market"] = relationship(back_populates="snapshots")
```

#### Trades Table
```python
class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    price: Mapped[float] = mapped_column(Numeric(10, 6))
    size: Mapped[float] = mapped_column(Numeric(20, 2))
    side: Mapped[str] = mapped_column(String(4))  # BUY/SELL

    whale_tier: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0/1/2/3

    # Market state at trade time
    best_bid: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    best_ask: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    mid_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    market: Mapped["Market"] = relationship(back_populates="trades")
```

#### Supporting Tables
```python
class OrderbookSnapshot(Base):
    """Full orderbook storage for detailed analysis"""
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    bids: Mapped[dict] = mapped_column(JSONB)  # [[price, size], ...]
    asks: Mapped[dict] = mapped_column(JSONB)

    # Summary stats
    total_bid_depth: Mapped[float] = mapped_column(Numeric(20, 2))
    total_ask_depth: Mapped[float] = mapped_column(Numeric(20, 2))
    num_bid_levels: Mapped[int] = mapped_column(SmallInteger)
    num_ask_levels: Mapped[int] = mapped_column(SmallInteger)


class WhaleEvent(Base):
    """Whale trade tracking with impact measurement"""
    __tablename__ = "whale_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    price: Mapped[float] = mapped_column(Numeric(10, 6))
    size: Mapped[float] = mapped_column(Numeric(20, 2))
    side: Mapped[str] = mapped_column(String(4))
    whale_tier: Mapped[int] = mapped_column(SmallInteger)

    # Impact tracking (filled by background task)
    price_before: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_after_1m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    price_after_5m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    impact_1m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    impact_5m: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))


class TaskRun(Base):
    """Task execution logging for monitoring"""
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str] = mapped_column(String(100), index=True)
    task_id: Mapped[str] = mapped_column(String(100))
    tier: Mapped[Optional[int]] = mapped_column(SmallInteger)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(20))  # running/success/failed
    markets_processed: Mapped[Optional[int]] = mapped_column(Integer)
    rows_inserted: Mapped[Optional[int]] = mapped_column(Integer)

    error_message: Mapped[Optional[str]] = mapped_column(Text)
    error_traceback: Mapped[Optional[str]] = mapped_column(Text)
```

**Deliverable**: All 6 SQLAlchemy models with proper types, indexes, and relationships

---

### 1.4 Database Infrastructure

**File**: `src/db/database.py`

```python
"""
Database engine and session management.
"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from src.config.settings import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Session:
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency for database sessions."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

**Deliverable**: Working database connection with session management

---

### 1.5 Alembic Migrations

**Setup**:
```bash
alembic init alembic
```

**File**: `alembic/env.py` - Configure to use our models and settings

**File**: `alembic/versions/001_initial_schema.py` - Initial migration with:
- All tables created
- Indexes on frequently queried columns
- Composite indexes for common query patterns:
  - `(market_id, timestamp)` on snapshots
  - `(market_id, timestamp)` on trades
  - `(tier, active)` on markets

**Deliverable**: `alembic upgrade head` creates all tables

---

### 1.6 Docker Infrastructure

**Update**: `docker-compose.yml`

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: polymarket_ml
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    ports:
      - "6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
  redis_data:
```

**Deliverable**: `docker-compose up -d` starts postgres and redis

---

## STEP 2: API Clients

### 2.1 HTTP Client Base

**File**: `src/fetchers/base.py`

```python
"""
Base HTTP client with rate limiting and retry logic.
"""
import asyncio
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
import structlog

logger = structlog.get_logger()


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, rate: float):
        self.rate = rate  # requests per second
        self.tokens = rate
        self.last_update = asyncio.get_event_loop().time()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


class BaseClient:
    """Base HTTP client with rate limiting and retries."""

    def __init__(self, base_url: str, rate_limit: float):
        self.base_url = base_url
        self.rate_limiter = RateLimiter(rate_limit)
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            headers={"Accept": "application/json"}
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def get(self, path: str, params: dict = None) -> dict:
        await self.rate_limiter.acquire()
        response = await self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()
```

**Deliverable**: Reusable async HTTP client with rate limiting

---

### 2.2 Gamma API Client

**File**: `src/fetchers/gamma.py`

```python
"""
Gamma API client for market discovery and metadata.
"""
import json
from typing import Optional
from decimal import Decimal
from src.fetchers.base import BaseClient
from src.config.settings import settings


class GammaClient(BaseClient):
    def __init__(self):
        super().__init__(
            base_url=settings.gamma_api_base,
            rate_limit=settings.gamma_rate_limit
        )

    async def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0
    ) -> list[dict]:
        """Fetch markets with pagination."""
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }
        return await self.get("/markets", params)

    async def get_all_active_markets(self) -> list[dict]:
        """Fetch all active markets (handles pagination)."""
        all_markets = []
        offset = 0
        limit = 100

        while True:
            markets = await self.get_markets(
                active=True, closed=False, limit=limit, offset=offset
            )
            if not markets:
                break
            all_markets.extend(markets)
            offset += limit

            if len(markets) < limit:
                break

        return all_markets

    @staticmethod
    def parse_outcome_prices(market: dict) -> tuple[float, float]:
        """Extract YES and NO prices from market response."""
        prices_str = market.get("outcomePrices", '["0.5", "0.5"]')
        try:
            prices = json.loads(prices_str)
            yes_price = float(prices[0])
            no_price = float(prices[1]) if len(prices) > 1 else 1 - yes_price
            return yes_price, no_price
        except (json.JSONDecodeError, IndexError):
            return 0.5, 0.5

    @staticmethod
    def parse_token_ids(market: dict) -> tuple[Optional[str], Optional[str]]:
        """Extract YES and NO token IDs from market response."""
        tokens_str = market.get("clobTokenIds", "[]")
        try:
            tokens = json.loads(tokens_str)
            yes_token = tokens[0] if tokens else None
            no_token = tokens[1] if len(tokens) > 1 else None
            return yes_token, no_token
        except (json.JSONDecodeError, IndexError):
            return None, None
```

**Deliverable**: Working Gamma client that can fetch all active markets

---

### 2.3 CLOB API Client

**File**: `src/fetchers/clob.py`

```python
"""
CLOB API client for orderbook data.
"""
from decimal import Decimal
from src.fetchers.base import BaseClient
from src.config.settings import settings


class CLOBClient(BaseClient):
    def __init__(self):
        super().__init__(
            base_url=settings.clob_api_base,
            rate_limit=settings.clob_rate_limit
        )

    async def get_orderbook(self, token_id: str) -> dict:
        """Fetch full orderbook for a token."""
        return await self.get("/book", params={"token_id": token_id})

    async def get_midpoint(self, token_id: str) -> float:
        """Get current midpoint price."""
        result = await self.get("/midpoint", params={"token_id": token_id})
        return float(result.get("mid", 0))

    async def get_spread(self, token_id: str) -> float:
        """Get current spread."""
        result = await self.get("/spread", params={"token_id": token_id})
        return float(result.get("spread", 0))

    @staticmethod
    def calculate_depth(orderbook: dict, side: str, levels: int) -> float:
        """Calculate total depth up to N price levels."""
        orders = orderbook.get("bids" if side == "bid" else "asks", [])
        total = 0.0
        for i, order in enumerate(orders[:levels]):
            size = float(order.get("size", 0))
            total += size
        return total

    @staticmethod
    def find_wall(orderbook: dict, side: str) -> tuple[float, float]:
        """Find the largest order (wall) on a side."""
        orders = orderbook.get("bids" if side == "bid" else "asks", [])
        if not orders:
            return 0.0, 0.0

        max_order = max(orders, key=lambda x: float(x.get("size", 0)))
        return float(max_order.get("price", 0)), float(max_order.get("size", 0))

    @staticmethod
    def calculate_imbalance(orderbook: dict) -> float:
        """Calculate book imbalance: (bid_depth - ask_depth) / (bid_depth + ask_depth)."""
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        bid_depth = sum(float(o.get("size", 0)) for o in bids)
        ask_depth = sum(float(o.get("size", 0)) for o in asks)

        total = bid_depth + ask_depth
        if total == 0:
            return 0.0

        return (bid_depth - ask_depth) / total
```

**Deliverable**: Working CLOB client that can fetch orderbooks and compute depth metrics

---

## STEP 3: Basic Celery Tasks

### 3.1 Celery App Configuration

**File**: `src/tasks/celery_app.py`

```python
"""
Celery application configuration.
"""
from celery import Celery
from celery.schedules import crontab
from src.config.settings import settings

app = Celery(
    "polymarket_ml",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 min max
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
)

# Beat schedule - all automated tasks
app.conf.beat_schedule = {
    # Discovery - hourly
    "discover-markets": {
        "task": "src.tasks.discovery.discover_markets",
        "schedule": crontab(minute=0),  # Every hour at :00
    },

    # Tier updates - every 5 minutes
    "update-tiers": {
        "task": "src.tasks.discovery.update_market_tiers",
        "schedule": crontab(minute="*/5"),
    },

    # Snapshots - tiered intervals
    "snapshot-tier-0": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": crontab(minute=0),  # Hourly
        "args": [0],
    },
    "snapshot-tier-1": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": crontab(minute="*/5"),  # Every 5 min
        "args": [1],
    },
    "snapshot-tier-2": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": 60.0,  # Every 1 min
        "args": [2],
    },
    "snapshot-tier-3": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": 30.0,  # Every 30 sec
        "args": [3],
    },
    "snapshot-tier-4": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": 15.0,  # Every 15 sec
        "args": [4],
    },
}

# Import tasks to register them
app.autodiscover_tasks(["src.tasks"])
```

**Deliverable**: Celery app with beat schedule for all tasks

---

### 3.2 Market Discovery Task

**File**: `src/tasks/discovery.py`

```python
"""
Market discovery and tier management tasks.
"""
import asyncio
from datetime import datetime, timezone
from celery import shared_task
from sqlalchemy import select, update
from src.db.database import get_session
from src.db.models import Market, TaskRun
from src.fetchers.gamma import GammaClient
from src.config.settings import settings
import structlog

logger = structlog.get_logger()


def calculate_tier(end_date: datetime) -> int:
    """Calculate collection tier based on hours to resolution."""
    if not end_date:
        return 0

    now = datetime.now(timezone.utc)
    hours_to_close = (end_date - now).total_seconds() / 3600

    if hours_to_close < settings.tier_3_min_hours:
        return 4
    elif hours_to_close < settings.tier_2_min_hours:
        return 3
    elif hours_to_close < settings.tier_1_min_hours:
        return 2
    elif hours_to_close < settings.tier_0_min_hours:
        return 1
    else:
        return 0


@shared_task(name="src.tasks.discovery.discover_markets")
def discover_markets():
    """Discover new markets and update existing ones."""
    return asyncio.get_event_loop().run_until_complete(_discover_markets())


async def _discover_markets():
    task_run = _start_task_run("discover_markets")

    try:
        client = GammaClient()
        markets = await client.get_all_active_markets()
        await client.close()

        markets_processed = 0
        rows_inserted = 0

        with get_session() as session:
            for market_data in markets:
                # Parse market data
                yes_price, _ = GammaClient.parse_outcome_prices(market_data)
                yes_token, no_token = GammaClient.parse_token_ids(market_data)

                # Filter: must have volume and orderbook
                volume_24h = market_data.get("volume24hr", 0) or 0
                if volume_24h < settings.ml_volume_threshold:
                    continue
                if not market_data.get("enableOrderBook", False):
                    continue

                # Parse end date
                end_date_str = market_data.get("endDate")
                end_date = None
                if end_date_str:
                    try:
                        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                    except:
                        pass

                # Check lookahead window
                if end_date:
                    hours_to_close = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_to_close > settings.ml_lookahead_hours or hours_to_close < 0:
                        continue

                # Calculate tier
                tier = calculate_tier(end_date)

                # Upsert market
                existing = session.execute(
                    select(Market).where(Market.condition_id == market_data.get("conditionId"))
                ).scalar_one_or_none()

                if existing:
                    # Update existing
                    existing.tier = tier
                    existing.active = market_data.get("active", True)
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    # Insert new
                    new_market = Market(
                        condition_id=market_data.get("conditionId"),
                        slug=market_data.get("slug", ""),
                        question=market_data.get("question", ""),
                        description=market_data.get("description"),
                        yes_token_id=yes_token,
                        no_token_id=no_token,
                        start_date=_parse_datetime(market_data.get("startDate")),
                        end_date=end_date,
                        created_at=_parse_datetime(market_data.get("createdAt")),
                        initial_price=yes_price,
                        initial_spread=market_data.get("spread"),
                        initial_volume=volume_24h,
                        initial_liquidity=market_data.get("liquidityNum"),
                        tier=tier,
                        active=market_data.get("active", True),
                        category=market_data.get("category"),
                        neg_risk=market_data.get("negRisk", False),
                        competitive=market_data.get("competitive"),
                        enable_order_book=market_data.get("enableOrderBook", True),
                    )
                    session.add(new_market)
                    rows_inserted += 1

                markets_processed += 1

            session.commit()

        _complete_task_run(task_run, "success", markets_processed, rows_inserted)
        logger.info("Discovery complete", markets=markets_processed, new=rows_inserted)
        return {"markets_processed": markets_processed, "rows_inserted": rows_inserted}

    except Exception as e:
        _fail_task_run(task_run, e)
        raise


@shared_task(name="src.tasks.discovery.update_market_tiers")
def update_market_tiers():
    """Reassign tiers for all active markets."""
    with get_session() as session:
        markets = session.execute(
            select(Market).where(Market.active == True, Market.resolved == False)
        ).scalars().all()

        updated = 0
        for market in markets:
            new_tier = calculate_tier(market.end_date)
            if market.tier != new_tier:
                market.tier = new_tier
                updated += 1

        session.commit()
        logger.info("Tiers updated", count=updated)
        return {"updated": updated}


def _parse_datetime(dt_str: str) -> datetime:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except:
        return None


def _start_task_run(task_name: str, tier: int = None) -> TaskRun:
    with get_session() as session:
        run = TaskRun(
            task_name=task_name,
            task_id="",  # Could use celery task id
            tier=tier,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        session.add(run)
        session.commit()
        return run.id


def _complete_task_run(run_id: int, status: str, markets: int, rows: int):
    with get_session() as session:
        run = session.get(TaskRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
            run.status = status
            run.markets_processed = markets
            run.rows_inserted = rows
            session.commit()


def _fail_task_run(run_id: int, error: Exception):
    import traceback
    with get_session() as session:
        run = session.get(TaskRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
            run.status = "failed"
            run.error_message = str(error)
            run.error_traceback = traceback.format_exc()
            session.commit()
```

**Deliverable**: Discovery task that finds markets and assigns tiers

---

### 3.3 Basic Snapshot Task

**File**: `src/tasks/snapshots.py`

```python
"""
Snapshot collection tasks.
"""
import asyncio
from datetime import datetime, timezone
from celery import shared_task
from sqlalchemy import select
from src.db.database import get_session
from src.db.models import Market, Snapshot
from src.fetchers.gamma import GammaClient
from src.fetchers.clob import CLOBClient
from src.config.settings import settings
import structlog

logger = structlog.get_logger()


@shared_task(name="src.tasks.snapshots.snapshot_tier")
def snapshot_tier(tier: int):
    """Collect snapshots for all markets in a tier."""
    return asyncio.get_event_loop().run_until_complete(_snapshot_tier(tier))


async def _snapshot_tier(tier: int):
    # Get markets in this tier
    with get_session() as session:
        markets = session.execute(
            select(Market).where(
                Market.tier == tier,
                Market.active == True,
                Market.resolved == False
            )
        ).scalars().all()

        if not markets:
            logger.debug("No markets in tier", tier=tier)
            return {"tier": tier, "markets": 0, "snapshots": 0}

        market_ids = {m.condition_id: m.id for m in markets}
        yes_tokens = {m.condition_id: m.yes_token_id for m in markets}

    # Fetch fresh data from Gamma
    gamma = GammaClient()
    clob = CLOBClient()

    try:
        all_markets = await gamma.get_all_active_markets()

        # Build snapshots
        snapshots = []
        now = datetime.now(timezone.utc)

        for market_data in all_markets:
            condition_id = market_data.get("conditionId")
            if condition_id not in market_ids:
                continue

            market_id = market_ids[condition_id]
            yes_price, _ = GammaClient.parse_outcome_prices(market_data)

            # Calculate hours to close
            end_date_str = market_data.get("endDate")
            hours_to_close = None
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                    hours_to_close = (end_date - now).total_seconds() / 3600
                except:
                    pass

            snapshot = Snapshot(
                market_id=market_id,
                timestamp=now,
                tier=tier,

                # Price fields
                price=yes_price,
                best_bid=market_data.get("bestBid"),
                best_ask=market_data.get("bestAsk"),
                spread=market_data.get("spread"),
                last_trade_price=market_data.get("lastTradePrice"),

                # Momentum (FREE from Gamma!)
                price_change_1d=market_data.get("oneDayPriceChange"),
                price_change_1w=market_data.get("oneWeekPriceChange"),
                price_change_1m=market_data.get("oneMonthPriceChange"),

                # Volume
                volume_total=market_data.get("volumeNum"),
                volume_24h=market_data.get("volume24hr"),
                volume_1w=market_data.get("volume1wk"),
                liquidity=market_data.get("liquidityNum"),

                # Context
                hours_to_close=hours_to_close,
                day_of_week=now.weekday(),
                hour_of_day=now.hour,
            )

            # Fetch orderbook for T2+ tiers
            if tier in settings.orderbook_enabled_tiers:
                token_id = yes_tokens.get(condition_id)
                if token_id:
                    try:
                        orderbook = await clob.get_orderbook(token_id)

                        # Depth calculations
                        snapshot.bid_depth_5 = CLOBClient.calculate_depth(orderbook, "bid", 5)
                        snapshot.bid_depth_10 = CLOBClient.calculate_depth(orderbook, "bid", 10)
                        snapshot.bid_depth_20 = CLOBClient.calculate_depth(orderbook, "bid", 20)
                        snapshot.bid_depth_50 = CLOBClient.calculate_depth(orderbook, "bid", 50)
                        snapshot.ask_depth_5 = CLOBClient.calculate_depth(orderbook, "ask", 5)
                        snapshot.ask_depth_10 = CLOBClient.calculate_depth(orderbook, "ask", 10)
                        snapshot.ask_depth_20 = CLOBClient.calculate_depth(orderbook, "ask", 20)
                        snapshot.ask_depth_50 = CLOBClient.calculate_depth(orderbook, "ask", 50)

                        # Level counts
                        snapshot.bid_levels = len(orderbook.get("bids", []))
                        snapshot.ask_levels = len(orderbook.get("asks", []))

                        # Imbalance
                        snapshot.book_imbalance = CLOBClient.calculate_imbalance(orderbook)

                        # Walls
                        bid_wall_price, bid_wall_size = CLOBClient.find_wall(orderbook, "bid")
                        ask_wall_price, ask_wall_size = CLOBClient.find_wall(orderbook, "ask")
                        snapshot.bid_wall_price = bid_wall_price
                        snapshot.bid_wall_size = bid_wall_size
                        snapshot.ask_wall_price = ask_wall_price
                        snapshot.ask_wall_size = ask_wall_size

                    except Exception as e:
                        logger.warning("Orderbook fetch failed", error=str(e), market=condition_id)

            snapshots.append(snapshot)

        # Bulk insert snapshots
        with get_session() as session:
            session.add_all(snapshots)

            # Update market last_snapshot_at and snapshot_count
            for snapshot in snapshots:
                session.execute(
                    update(Market)
                    .where(Market.id == snapshot.market_id)
                    .values(
                        last_snapshot_at=now,
                        snapshot_count=Market.snapshot_count + 1
                    )
                )

            session.commit()

        logger.info("Snapshots collected", tier=tier, count=len(snapshots))
        return {"tier": tier, "markets": len(market_ids), "snapshots": len(snapshots)}

    finally:
        await gamma.close()
        await clob.close()
```

**Deliverable**: Snapshot task that collects price, momentum, volume, and orderbook data

---

## STEP 4: WebSocket Collector

### 4.1-4.4 WebSocket Manager

**File**: `src/collectors/websocket.py`

```python
"""
WebSocket collector for real-time trade data.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
import websockets
from sqlalchemy import select
from src.db.database import get_session
from src.db.models import Market, Trade, WhaleEvent
from src.db.redis import RedisClient
from src.config.settings import settings
import structlog

logger = structlog.get_logger()


class WebSocketCollector:
    """Manages WebSocket connections for trade data collection."""

    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.redis = RedisClient()
        self.subscribed_markets: dict[str, dict] = {}  # condition_id -> {yes_token_id, market_id}
        self.running = False
        self.reconnect_delay = settings.websocket_reconnect_delay

    async def start(self):
        """Start the WebSocket collector."""
        self.running = True
        while self.running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error("WebSocket error", error=str(e))
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(
                    self.reconnect_delay * 2,
                    settings.websocket_max_reconnect_delay
                )

    async def _connect_and_run(self):
        """Connect to WebSocket and process messages."""
        async with websockets.connect(settings.websocket_url) as ws:
            self.ws = ws
            self.reconnect_delay = settings.websocket_reconnect_delay
            logger.info("WebSocket connected")

            # Update subscriptions
            await self._update_subscriptions()

            # Process messages
            async for message in ws:
                await self._handle_message(message)

    async def _update_subscriptions(self):
        """Subscribe to markets in T2+ tiers."""
        with get_session() as session:
            markets = session.execute(
                select(Market).where(
                    Market.tier.in_(settings.websocket_enabled_tiers),
                    Market.active == True,
                    Market.resolved == False,
                    Market.yes_token_id.isnot(None)
                )
            ).scalars().all()

            new_subscriptions = {
                m.condition_id: {
                    "yes_token_id": m.yes_token_id,
                    "market_id": m.id
                }
                for m in markets
            }

        # Unsubscribe from removed markets
        for cid in self.subscribed_markets.keys() - new_subscriptions.keys():
            await self._unsubscribe(cid)

        # Subscribe to new markets
        for cid, data in new_subscriptions.items():
            if cid not in self.subscribed_markets:
                await self._subscribe(cid, data["yes_token_id"])

        self.subscribed_markets = new_subscriptions
        logger.info("Subscriptions updated", count=len(self.subscribed_markets))

    async def _subscribe(self, condition_id: str, token_id: str):
        """Subscribe to a market's trade feed."""
        if self.ws:
            message = {
                "type": "market",
                "asset_ids": [token_id],
            }
            await self.ws.send(json.dumps(message))
            await self.redis.set_ws_connected(condition_id, True)

    async def _unsubscribe(self, condition_id: str):
        """Unsubscribe from a market."""
        await self.redis.set_ws_connected(condition_id, False)

    async def _handle_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            event_type = data.get("event_type")

            if event_type == "last_trade_price":
                await self._handle_trade(data)
            elif event_type == "book":
                await self._handle_book(data)
            elif event_type == "price_change":
                await self._handle_price_change(data)

        except Exception as e:
            logger.warning("Failed to handle message", error=str(e))

    async def _handle_trade(self, data: dict):
        """Process trade event."""
        condition_id = data.get("market")
        if condition_id not in self.subscribed_markets:
            return

        market_id = self.subscribed_markets[condition_id]["market_id"]

        timestamp = datetime.fromtimestamp(
            int(data.get("timestamp", 0)) / 1000,
            tz=timezone.utc
        )
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))
        side = data.get("side", "BUY")

        # Classify whale tier
        whale_tier = self._classify_whale(size)

        # Insert trade to database
        with get_session() as session:
            trade = Trade(
                market_id=market_id,
                timestamp=timestamp,
                price=price,
                size=size,
                side=side,
                whale_tier=whale_tier,
            )
            session.add(trade)

            # Create whale event if whale
            if whale_tier >= 2:
                whale_event = WhaleEvent(
                    market_id=market_id,
                    trade_id=trade.id,
                    timestamp=timestamp,
                    price=price,
                    size=size,
                    side=side,
                    whale_tier=whale_tier,
                )
                session.add(whale_event)

            session.commit()

        # Push to Redis buffer
        trade_data = {
            "timestamp": timestamp.isoformat(),
            "price": price,
            "size": size,
            "side": side,
            "whale_tier": whale_tier,
        }
        await self.redis.push_trade(condition_id, trade_data)
        await self.redis.set_ws_last_event(condition_id)

        logger.debug("Trade recorded", market=condition_id, size=size, whale=whale_tier)

    async def _handle_book(self, data: dict):
        """Process orderbook update."""
        condition_id = data.get("market")
        if condition_id not in self.subscribed_markets:
            return

        # Update Redis with latest orderbook state
        await self.redis.set_orderbook(condition_id, data)

    async def _handle_price_change(self, data: dict):
        """Process price change event."""
        # Could update real-time price cache
        pass

    def _classify_whale(self, size: float) -> int:
        """Classify trade size into whale tiers."""
        if size >= settings.whale_tier_3_threshold:
            return 3  # Mega whale
        elif size >= settings.whale_tier_2_threshold:
            return 2  # Whale
        elif size >= settings.whale_tier_1_threshold:
            return 1  # Large trade
        return 0  # Normal

    def stop(self):
        """Stop the collector."""
        self.running = False


async def run_collector():
    """Entry point for WebSocket collector service."""
    collector = WebSocketCollector()

    # Periodic subscription updates
    async def update_loop():
        while collector.running:
            await asyncio.sleep(60)  # Update every minute
            try:
                await collector._update_subscriptions()
            except Exception as e:
                logger.error("Subscription update failed", error=str(e))

    asyncio.create_task(update_loop())
    await collector.start()


if __name__ == "__main__":
    asyncio.run(run_collector())
```

**Deliverable**: WebSocket service that streams trades and stores them in DB + Redis

---

## STEP 5: Redis Layer

**File**: `src/db/redis.py`

```python
"""
Redis client for trade buffers and metrics caching.
"""
import json
from datetime import datetime, timezone
from typing import Optional
import redis.asyncio as redis
from src.config.settings import settings


class RedisClient:
    def __init__(self):
        self.client = redis.from_url(settings.redis_url, decode_responses=True)

    # === Trade Buffer Operations ===

    async def push_trade(self, condition_id: str, trade_data: dict):
        """Push trade to buffer (FIFO, max size limited)."""
        key = f"trades:{condition_id}"
        await self.client.lpush(key, json.dumps(trade_data))
        await self.client.ltrim(key, 0, settings.redis_trade_buffer_max - 1)
        await self.client.expire(key, settings.redis_trade_buffer_ttl)

    async def get_trades_1h(self, condition_id: str) -> list[dict]:
        """Get trades from the last hour."""
        key = f"trades:{condition_id}"
        trades_raw = await self.client.lrange(key, 0, -1)

        one_hour_ago = datetime.now(timezone.utc).timestamp() - 3600
        trades = []

        for raw in trades_raw:
            trade = json.loads(raw)
            ts = datetime.fromisoformat(trade["timestamp"]).timestamp()
            if ts >= one_hour_ago:
                trades.append(trade)

        return trades

    # === Metrics Operations ===

    async def set_metrics(self, condition_id: str, metrics: dict):
        """Cache computed metrics for a market."""
        key = f"metrics:{condition_id}"
        await self.client.hset(key, mapping={k: json.dumps(v) for k, v in metrics.items()})
        await self.client.expire(key, 300)  # 5 min TTL

    async def get_metrics(self, condition_id: str) -> Optional[dict]:
        """Get cached metrics for a market."""
        key = f"metrics:{condition_id}"
        raw = await self.client.hgetall(key)
        if not raw:
            return None
        return {k: json.loads(v) for k, v in raw.items()}

    # === Tier Operations ===

    async def set_market_tier(self, condition_id: str, tier: int):
        """Track market tier membership."""
        # Remove from all tiers
        for t in range(5):
            await self.client.srem(f"tier:{t}", condition_id)
        # Add to current tier
        await self.client.sadd(f"tier:{tier}", condition_id)

    async def get_markets_in_tier(self, tier: int) -> set[str]:
        """Get all markets in a tier."""
        return await self.client.smembers(f"tier:{tier}")

    # === WebSocket Health ===

    async def set_ws_connected(self, condition_id: str, connected: bool):
        """Track WebSocket connection status."""
        if connected:
            await self.client.sadd("ws:connected", condition_id)
        else:
            await self.client.srem("ws:connected", condition_id)

    async def set_ws_last_event(self, condition_id: str):
        """Update last event timestamp."""
        await self.client.hset("ws:last_event", condition_id, datetime.now(timezone.utc).isoformat())

    async def get_ws_status(self) -> dict:
        """Get WebSocket health status."""
        connected = await self.client.smembers("ws:connected")
        last_events = await self.client.hgetall("ws:last_event")
        return {
            "connected_count": len(connected),
            "connected_markets": list(connected),
            "last_events": last_events,
        }

    # === Orderbook Cache ===

    async def set_orderbook(self, condition_id: str, orderbook: dict):
        """Cache latest orderbook."""
        key = f"orderbook:{condition_id}"
        await self.client.set(key, json.dumps(orderbook), ex=60)

    async def get_orderbook(self, condition_id: str) -> Optional[dict]:
        """Get cached orderbook."""
        key = f"orderbook:{condition_id}"
        raw = await self.client.get(key)
        return json.loads(raw) if raw else None
```

**Deliverable**: Redis client with all buffer and cache operations

---

## STEP 6: Complete Snapshot Tasks

**File**: `src/collectors/metrics.py`

```python
"""
Compute trade metrics from Redis buffer.
"""
from src.db.redis import RedisClient


async def compute_trade_metrics(condition_id: str) -> dict:
    """Compute all trade flow metrics from 1h buffer."""
    redis = RedisClient()
    trades = await redis.get_trades_1h(condition_id)

    if not trades:
        return {
            "trade_count_1h": 0,
            "buy_count_1h": 0,
            "sell_count_1h": 0,
            "volume_1h": 0,
            "buy_volume_1h": 0,
            "sell_volume_1h": 0,
            "avg_trade_size_1h": None,
            "max_trade_size_1h": None,
            "vwap_1h": None,
        }

    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    total_volume = sum(t["size"] for t in trades)
    buy_volume = sum(t["size"] for t in buys)
    sell_volume = sum(t["size"] for t in sells)

    # VWAP calculation
    vwap = None
    if total_volume > 0:
        vwap = sum(t["price"] * t["size"] for t in trades) / total_volume

    return {
        "trade_count_1h": len(trades),
        "buy_count_1h": len(buys),
        "sell_count_1h": len(sells),
        "volume_1h": total_volume,
        "buy_volume_1h": buy_volume,
        "sell_volume_1h": sell_volume,
        "avg_trade_size_1h": total_volume / len(trades) if trades else None,
        "max_trade_size_1h": max(t["size"] for t in trades) if trades else None,
        "vwap_1h": vwap,
    }


async def compute_whale_metrics(condition_id: str) -> dict:
    """Compute whale metrics from 1h buffer."""
    redis = RedisClient()
    trades = await redis.get_trades_1h(condition_id)

    # Filter to whales (tier >= 2)
    whales = [t for t in trades if t.get("whale_tier", 0) >= 2]

    if not whales:
        return {
            "whale_count_1h": 0,
            "whale_volume_1h": 0,
            "whale_buy_volume_1h": 0,
            "whale_sell_volume_1h": 0,
            "whale_net_flow_1h": 0,
            "whale_buy_ratio_1h": None,
            "time_since_whale": None,
            "pct_volume_from_whales": 0,
        }

    whale_buys = [t for t in whales if t["side"] == "BUY"]
    whale_sells = [t for t in whales if t["side"] == "SELL"]

    whale_volume = sum(t["size"] for t in whales)
    whale_buy_volume = sum(t["size"] for t in whale_buys)
    whale_sell_volume = sum(t["size"] for t in whale_sells)
    total_volume = sum(t["size"] for t in trades) if trades else 0

    # Time since last whale
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    last_whale_time = max(
        datetime.fromisoformat(t["timestamp"]) for t in whales
    )
    time_since = int((now - last_whale_time).total_seconds())

    return {
        "whale_count_1h": len(whales),
        "whale_volume_1h": whale_volume,
        "whale_buy_volume_1h": whale_buy_volume,
        "whale_sell_volume_1h": whale_sell_volume,
        "whale_net_flow_1h": whale_buy_volume - whale_sell_volume,
        "whale_buy_ratio_1h": whale_buy_volume / whale_volume if whale_volume > 0 else None,
        "time_since_whale": time_since,
        "pct_volume_from_whales": whale_volume / total_volume if total_volume > 0 else 0,
    }
```

**Update snapshot task** to include trade metrics:
- Call `compute_trade_metrics()` and `compute_whale_metrics()`
- Populate all trade flow and whale fields in snapshot

**Deliverable**: Snapshots now include all ~50 features from all sources

---

## STEP 7: FastAPI Backend

### 7.1-7.6 API Implementation

**File**: `src/api/main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routes import health, stats, markets, tasks, data_quality

app = FastAPI(
    title="Polymarket ML Data Collector",
    description="Data collection and monitoring API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])
app.include_router(stats.router, prefix="/api", tags=["Stats"])
app.include_router(markets.router, prefix="/api", tags=["Markets"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(data_quality.router, prefix="/api", tags=["Data Quality"])
```

**Key endpoints**:
- `GET /health` - Basic health check
- `GET /health/detailed` - DB, Redis, Celery, WebSocket status
- `GET /api/stats` - Markets by tier, snapshot counts, trade counts, DB size
- `GET /api/markets` - Paginated market list with latest snapshot
- `GET /api/markets/{id}` - Single market with history
- `GET /api/tasks/status` - All Celery tasks status
- `GET /api/tasks/runs` - Task run history
- `GET /api/data-quality/coverage` - Expected vs actual snapshots by tier
- `GET /api/data-quality/gaps` - Markets with missing data

**Deliverable**: Full REST API for monitoring dashboard

---

## STEP 8: Frontend Dashboard

### 8.1-8.6 React Implementation

**Tech stack**:
- Vite + React 18 + TypeScript
- Tailwind CSS for styling
- TanStack Query (React Query) for data fetching
- Recharts for charts

**Pages**:

#### Dashboard (`/`)
```
┌─────────────────────────────────────────────────────────────┐
│  Polymarket ML Data Collector                        [Live] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ Markets │ │Snapshots│ │ Trades  │ │   DB    │           │
│  │   127   │ │  24,532 │ │  8,234  │ │ 1.2 GB  │           │
│  │ tracked │ │  today  │ │  today  │ │  size   │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│                                                             │
│  ┌────────────────────────┐ ┌─────────────────────────────┐│
│  │   Markets by Tier      │ │    WebSocket Status         ││
│  │   [bar chart]          │ │    Connected: 35 markets    ││
│  │   T0: 45  T1: 32       │ │    Events/sec: 12.5         ││
│  │   T2: 28  T3: 15       │ │    Last event: 2s ago       ││
│  │   T4: 7                │ │                             ││
│  └────────────────────────┘ └─────────────────────────────┘│
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │   Task Health                                         │  │
│  │   ┌──────────────┬──────────┬──────────┬──────────┐  │  │
│  │   │ Task         │ Last Run │ Status   │ Next     │  │  │
│  │   ├──────────────┼──────────┼──────────┼──────────┤  │  │
│  │   │ discover     │ 10:00:02 │ ✓        │ 11:00:00 │  │  │
│  │   │ snapshot_t0  │ 10:00:15 │ ✓        │ 11:00:00 │  │  │
│  │   │ snapshot_t4  │ 10:05:45 │ ✓        │ 10:06:00 │  │  │
│  │   └──────────────┴──────────┴──────────┴──────────┘  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Markets (`/markets`)
```
┌─────────────────────────────────────────────────────────────┐
│  Markets                                                    │
├─────────────────────────────────────────────────────────────┤
│  Tier: [All ▼]  Category: [All ▼]  [Search...         ]    │
├─────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────┐│
│  │ Question                │ Price │Tier│ Snaps │ Updated ││
│  ├────────────────────────────────────────────────────────┤│
│  │ Will BTC hit $100k...   │ 72.3% │ T1 │ 1,432 │ 2m ago  ││
│  │ Fed cuts rates in Jan?  │ 94.1% │ T4 │   856 │ 15s ago ││
│  │ Lakers win championsh...│ 45.2% │ T2 │   234 │ 1m ago  ││
│  └────────────────────────────────────────────────────────┘│
│                                      [< 1 2 3 ... 10 >]    │
└─────────────────────────────────────────────────────────────┘
```

#### Data Quality (`/data-quality`)
```
┌─────────────────────────────────────────────────────────────┐
│  Data Quality                                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Coverage by Tier                                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Tier │ Markets │ Expected/hr │ Actual/hr │ Coverage │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │ T0   │   45    │      45     │     45    │  100.0%  │  │
│  │ T1   │   32    │     384     │    382    │   99.5%  │  │
│  │ T2   │   28    │   1,680     │  1,675    │   99.7%  │  │
│  │ T3   │   15    │   1,800     │  1,798    │   99.9%  │  │
│  │ T4   │    7    │   1,680     │  1,679    │   99.9%  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  Gaps Detected: 0                                           │
│  Stale Markets: 0                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Deliverable**: Fully functional React dashboard with auto-refresh

---

## STEP 9: Deployment

### 9.1-9.5 Docker Compose & Automation

**File**: `docker-compose.yml` (complete)

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: polymarket_ml
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    ports:
      - "6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  api:
    build: .
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/polymarket_ml
      - REDIS_URL=redis://redis:6379/0
      - CELERY_BROKER_URL=redis://redis:6379/0
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  celery-worker:
    build: .
    command: celery -A src.tasks.celery_app worker -l info -c 4
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/polymarket_ml
      - REDIS_URL=redis://redis:6379/0
      - CELERY_BROKER_URL=redis://redis:6379/0
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  celery-beat:
    build: .
    command: celery -A src.tasks.celery_app beat -l info
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/polymarket_ml
      - REDIS_URL=redis://redis:6379/0
      - CELERY_BROKER_URL=redis://redis:6379/0
    depends_on:
      - celery-worker
    restart: unless-stopped

  websocket-collector:
    build: .
    command: python -m src.collectors.websocket
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/polymarket_ml
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - api
    restart: unless-stopped

  flower:
    build: .
    command: celery -A src.tasks.celery_app flower --port=5555
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
    ports:
      - "5555:5555"
    depends_on:
      - redis
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

**Startup sequence**:
1. `docker-compose up -d postgres redis` - Start infrastructure
2. `docker-compose run --rm api alembic upgrade head` - Run migrations
3. `docker-compose up -d` - Start all services

**Deliverable**: Single command deployment with automatic recovery

---

## Summary: What Gets Built

| Component | Features Collected | Automation |
|-----------|-------------------|------------|
| Gamma REST | 18 features (price, momentum, volume) | Celery tasks |
| CLOB REST | 15 features (orderbook depth, walls) | Celery tasks (T2+) |
| WebSocket | 17 features (trade flow, whale metrics) | Dedicated service |
| Computed | 15 features (context, derived) | At snapshot time |
| **Total** | **~65 features** | **Fully automated** |

| Service | Purpose | Auto-restart |
|---------|---------|--------------|
| postgres | Data storage | Yes |
| redis | Trade buffers, cache | Yes |
| api | Monitoring endpoints | Yes |
| celery-worker | Task execution | Yes |
| celery-beat | Task scheduling | Yes |
| websocket-collector | Real-time trades | Yes |
| frontend | Dashboard UI | Yes |

**End state**: `docker-compose up -d` starts everything. Data collection runs 24/7 with no manual intervention. Dashboard at `http://localhost` shows collection health.
