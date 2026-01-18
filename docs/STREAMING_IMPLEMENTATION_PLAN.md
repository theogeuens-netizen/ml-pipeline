# Streaming Book Imbalance Executor - Comprehensive Implementation Plan

## Executive Summary

Build a **streaming book imbalance executor** that runs as a separate Docker service alongside the existing polling executor. It receives real-time orderbook updates via WebSocket, calculates imbalance on-the-fly, and executes trades with sub-second latency.

**Goal**: Reduce latency from 30-90s (polling) to <1s (streaming) for CRYPTO markets.

---

## 1. Problem Statement

### Current Polling Architecture Latency Breakdown

```
Celery beat schedules task (15-60s interval)
    ↓
Task queued in Redis (~1-5s)
    ↓
Worker picks up task (~1-10s queue wait)
    ↓
Worker fetches snapshot from CLOB API (~2-5s)
    ↓
Snapshot saved to PostgreSQL (~0.5s)
    ↓
Executor scans DB (30s cycle)
    ↓
Executor fetches fresh orderbook for validation (~2s)
    ↓
Signal age check: signal.created_at vs now
    ↓
If > 120s → REJECTED

TOTAL: 30-90 seconds from market event to execution attempt
```

### Why This Matters for CRYPTO
- 15-minute CRYPTO markets move fast
- By execution time, price has moved 30-40% from signal price
- 5% max deviation safety check rejects most signals
- Result: High-opportunity strategy can't execute

---

## 2. Proposed Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    STREAMING EXECUTOR SERVICE                            │
│                    (New Docker container)                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────┐                                                   │
│  │  MARKET SELECTOR │   Every 5 minutes:                                │
│  │                  │   SELECT * FROM markets                            │
│  │  • CRYPTO only   │   WHERE category_l1 = 'CRYPTO'                     │
│  │  • <4h to expiry │   AND hours_to_close < 4                          │
│  │  • active=true   │   AND active = true                               │
│  │  • resolved=false│   AND resolved = false                            │
│  └────────┬─────────┘                                                   │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │               WEBSOCKET CONNECTION (Dedicated)                    │   │
│  │                                                                   │   │
│  │  • Connect to wss://ws-subscriptions-clob.polymarket.com          │   │
│  │  • Subscribe to "book" channel for selected token IDs             │   │
│  │  • Handle reconnection with exponential backoff                   │   │
│  │  • Max 500 tokens per connection (~250 markets)                   │   │
│  └──────────────────────────┬───────────────────────────────────────┘   │
│                             │                                            │
│                             │  On each "book" event:                     │
│                             ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                 IN-MEMORY ORDERBOOK STATE                         │   │
│  │                                                                   │   │
│  │  Dict[token_id, OrderbookState]                                   │   │
│  │  • bids: list[PriceLevel]                                         │   │
│  │  • asks: list[PriceLevel]                                         │   │
│  │  • imbalance: float (calculated on update)                        │   │
│  │  • mid_price: float                                               │   │
│  │  • spread: float                                                  │   │
│  │  • last_update: datetime                                          │   │
│  └──────────────────────────┬───────────────────────────────────────┘   │
│                             │                                            │
│                             │  If |imbalance| > threshold:               │
│                             ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                  STRATEGY EVALUATOR                               │   │
│  │                                                                   │   │
│  │  Filters (all must pass):                                         │   │
│  │  1. |imbalance| >= min_imbalance (0.5)                            │   │
│  │  2. yes_price_min <= mid_price <= yes_price_max (0.30-0.70)       │   │
│  │  3. spread <= max_spread (0.02)                                   │   │
│  │  4. No existing position on this market                           │   │
│  │  5. Position count < max_positions (5)                            │   │
│  │  6. Not in cooldown for this market                               │   │
│  │  7. hours_to_close >= min_hours (0.033 = 2 min)                   │   │
│  └──────────────────────────┬───────────────────────────────────────┘   │
│                             │                                            │
│                             │  If signal generated:                      │
│                             ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                  STREAMING EXECUTOR                               │   │
│  │                                                                   │   │
│  │  Safety Checks (stricter than polling):                           │   │
│  │  1. Signal age < 5s (vs 120s for polling)                         │   │
│  │  2. Fresh orderbook fetch from CLOB API                           │   │
│  │  3. Price deviation < 3% (vs 5% for polling)                      │   │
│  │  4. Spread < 2%                                                   │   │
│  │  5. Fee rate < 200 bps                                            │   │
│  │  6. No duplicate position (DB + in-memory check)                  │   │
│  │                                                                   │   │
│  │  Execution:                                                       │   │
│  │  • REUSE: get_order_client() singleton                            │   │
│  │  • REUSE: Position model (same DB table)                          │   │
│  │  • REUSE: TradeDecision model (audit trail)                       │   │
│  │  • Paper mode: Simulate fill, update paper balance                │   │
│  │  • Live mode: Place real order via py_clob_client                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. File Structure

```
src/streaming/
├── __init__.py              # Package exports
├── config.py                # StreamingConfig dataclass, load from strategies.yaml
├── signals.py               # StreamingSignal dataclass
├── state.py                 # OrderbookState, StreamingStateManager
├── market_selector.py       # DB queries for CRYPTO <4h market selection
├── strategy.py              # StreamingBookImbalanceStrategy (evaluate logic)
├── safety.py                # Validation and safety checks (ported from LiveExecutor)
├── executor.py              # StreamingExecutor (paper/live, reuses order client)
├── websocket.py             # WebSocket connection management (book channel)
├── runner.py                # Main entry point, orchestrates everything
└── health.py                # Health check endpoint

# Updates to existing files:
strategies.yaml              # Add streaming_book_imbalance section
docker-compose.yml           # Add streaming-executor service
src/api/routes/monitoring.py # Add /streaming/health endpoint
```

---

## 4. Detailed Component Specifications

### 4.1 StreamingConfig (`src/streaming/config.py`)

```python
@dataclass
class StreamingConfig:
    """Configuration for streaming strategy (loaded from strategies.yaml)."""
    name: str = "streaming_imbalance_crypto"
    enabled: bool = True
    live: bool = False  # Paper by default, seamless switch to live

    # Strategy parameters
    min_imbalance: float = 0.5       # |imbalance| >= 50% to trigger
    yes_price_min: float = 0.30      # Price zone minimum
    yes_price_max: float = 0.70      # Price zone maximum
    max_spread: float = 0.02         # 2% max spread

    # Market selection
    categories: list[str] = field(default_factory=lambda: ["CRYPTO"])
    max_hours_to_close: float = 4.0  # Only markets <4h to expiry
    min_minutes_to_close: float = 2  # Safety buffer before resolution

    # Position management
    max_positions: int = 5           # Max concurrent positions
    fixed_size_usd: float = 1.1      # Fixed USD size per trade
    cooldown_minutes: float = 60     # Minutes between entries on same market

    # Safety (stricter than polling)
    max_signal_age_seconds: float = 5.0     # 5s max (vs 120s polling)
    max_price_deviation: float = 0.03       # 3% max (vs 5% polling)
    max_fee_rate_bps: int = 200             # 2% max fee

    # WebSocket settings
    subscription_refresh_interval: int = 300  # Refresh subscriptions every 5 min
    reconnect_delay: float = 5.0
    max_reconnect_delay: float = 60.0


def load_streaming_config(config_path: Path = None) -> StreamingConfig:
    """Load streaming config from strategies.yaml."""
    # Implementation: Parse YAML, return StreamingConfig
```

### 4.2 OrderbookState (`src/streaming/state.py`)

```python
@dataclass
class PriceLevel:
    """Single price level in orderbook."""
    price: float
    size: float


@dataclass
class OrderbookState:
    """In-memory orderbook state for a single token."""
    token_id: str
    bids: list[PriceLevel]
    asks: list[PriceLevel]
    last_update: datetime

    @property
    def imbalance(self) -> float:
        """Calculate book imbalance: (bid_depth - ask_depth) / total."""
        bid_depth = sum(level.size for level in self.bids[:5])
        ask_depth = sum(level.size for level in self.asks[:5])
        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        return (bid_depth - ask_depth) / total

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.last_update).total_seconds()


class StreamingStateManager:
    """Manages all in-memory state for streaming executor."""

    def __init__(self):
        # Token ID -> OrderbookState
        self.orderbooks: dict[str, OrderbookState] = {}

        # Market ID -> MarketInfo (from DB)
        self.market_info: dict[int, MarketInfo] = {}

        # Token ID -> Market ID (reverse lookup)
        self.token_to_market: dict[str, int] = {}

        # Strategy:Market -> last entry timestamp (cooldowns)
        self.cooldowns: dict[str, datetime] = {}

        # Strategy -> set of market IDs with open positions (fast lookup)
        self.open_positions: dict[str, set[int]] = defaultdict(set)

    def update_orderbook(self, token_id: str, bids: list, asks: list):
        """Update orderbook from WebSocket event."""
        self.orderbooks[token_id] = OrderbookState(
            token_id=token_id,
            bids=[PriceLevel(float(b['price']), float(b['size'])) for b in bids],
            asks=[PriceLevel(float(a['price']), float(a['size'])) for a in asks],
            last_update=datetime.now(timezone.utc),
        )

    def get_imbalance(self, token_id: str) -> Optional[float]:
        """Get current imbalance for token."""
        book = self.orderbooks.get(token_id)
        return book.imbalance if book else None

    def is_in_cooldown(self, strategy_name: str, market_id: int, cooldown_minutes: float) -> bool:
        """Check if market is in cooldown."""
        key = f"{strategy_name}:{market_id}"
        last_entry = self.cooldowns.get(key)
        if last_entry is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last_entry).total_seconds() / 60
        return elapsed < cooldown_minutes

    def set_cooldown(self, strategy_name: str, market_id: int):
        """Record entry time for cooldown."""
        key = f"{strategy_name}:{market_id}"
        self.cooldowns[key] = datetime.now(timezone.utc)

    def sync_positions_from_db(self, db: Session, strategy_name: str, is_paper: bool):
        """Sync open positions from database (called on startup and periodically)."""
        from src.executor.models import Position, PositionStatus
        positions = db.query(Position).filter(
            Position.strategy_name == strategy_name,
            Position.is_paper == is_paper,
            Position.status == PositionStatus.OPEN.value,
        ).all()
        self.open_positions[strategy_name] = {p.market_id for p in positions}
```

### 4.3 MarketSelector (`src/streaming/market_selector.py`)

```python
@dataclass
class MarketInfo:
    """Minimal market info for streaming executor."""
    id: int
    condition_id: str
    yes_token_id: str
    no_token_id: str
    question: str
    hours_to_close: float
    category_l1: str


def get_streaming_markets(db: Session, config: StreamingConfig) -> list[MarketInfo]:
    """
    Query markets eligible for streaming execution.

    Selection criteria:
    - category_l1 in config.categories (default: CRYPTO)
    - hours_to_close < config.max_hours_to_close (default: 4h)
    - active = true
    - resolved = false
    - has both yes_token_id and no_token_id
    """
    from src.db.models import Market

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=config.max_hours_to_close)

    markets = db.query(Market).filter(
        Market.category_l1.in_(config.categories),
        Market.active == True,
        Market.resolved == False,
        Market.yes_token_id.isnot(None),
        Market.no_token_id.isnot(None),
        Market.end_date.isnot(None),
        Market.end_date <= cutoff,
        Market.end_date > now + timedelta(minutes=config.min_minutes_to_close),
    ).all()

    result = []
    for m in markets:
        hours_to_close = (m.end_date - now).total_seconds() / 3600 if m.end_date else None
        if hours_to_close and hours_to_close > 0:
            result.append(MarketInfo(
                id=m.id,
                condition_id=m.condition_id,
                yes_token_id=m.yes_token_id,
                no_token_id=m.no_token_id,
                question=m.question[:100] if m.question else "",
                hours_to_close=hours_to_close,
                category_l1=m.category_l1,
            ))

    return result
```

### 4.4 StreamingSignal (`src/streaming/signals.py`)

```python
@dataclass
class StreamingSignal:
    """Signal generated by streaming strategy."""
    # Core identifiers
    strategy_name: str
    market_id: int
    token_id: str
    condition_id: str

    # Direction
    side: str  # "BUY"
    token_side: str  # "YES" or "NO"

    # Pricing at signal time
    price_at_signal: float
    best_bid: float
    best_ask: float
    imbalance: float
    spread: float

    # Timing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hours_to_close: float = 0.0

    # Sizing
    size_usd: float = 1.1

    # Metadata
    reason: str = ""
    edge: float = 0.0
    confidence: float = 0.6

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()
```

### 4.5 StreamingStrategy (`src/streaming/strategy.py`)

```python
class StreamingBookImbalanceStrategy:
    """
    Evaluate book imbalance signals on orderbook updates.

    MOMENTUM strategy: Follow the imbalance direction
    - Bid-heavy (imbalance > 0): Buy YES expecting price to rise
    - Ask-heavy (imbalance < 0): Buy NO expecting price to fall
    """

    def __init__(self, config: StreamingConfig):
        self.config = config
        self.name = config.name

    def evaluate(
        self,
        book: OrderbookState,
        market: MarketInfo,
        state: StreamingStateManager,
    ) -> Optional[StreamingSignal]:
        """
        Evaluate orderbook update for potential signal.

        Returns StreamingSignal if all conditions met, None otherwise.
        """
        # 1. Check imbalance threshold
        imbalance = book.imbalance
        if abs(imbalance) < self.config.min_imbalance:
            return None

        # 2. Check price zone
        mid_price = book.mid_price
        if mid_price is None:
            return None
        if mid_price < self.config.yes_price_min or mid_price > self.config.yes_price_max:
            return None

        # 3. Check spread
        spread = book.spread
        if spread is None or spread > self.config.max_spread:
            return None

        # 4. Check position limit
        current_positions = len(state.open_positions.get(self.name, set()))
        if current_positions >= self.config.max_positions:
            return None

        # 5. Check existing position on this market
        if market.id in state.open_positions.get(self.name, set()):
            return None

        # 6. Check cooldown
        if state.is_in_cooldown(self.name, market.id, self.config.cooldown_minutes):
            return None

        # 7. Check time to close
        if market.hours_to_close < (self.config.min_minutes_to_close / 60):
            return None

        # All checks passed - generate signal
        if imbalance > 0:
            # Bid-heavy: buy YES
            token_id = market.yes_token_id
            token_side = "YES"
            execution_price = book.best_ask
        else:
            # Ask-heavy: buy NO
            token_id = market.no_token_id
            token_side = "NO"
            execution_price = 1 - book.best_bid

        return StreamingSignal(
            strategy_name=self.name,
            market_id=market.id,
            token_id=token_id,
            condition_id=market.condition_id,
            side="BUY",
            token_side=token_side,
            price_at_signal=execution_price,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            imbalance=imbalance,
            spread=spread,
            hours_to_close=market.hours_to_close,
            size_usd=self.config.fixed_size_usd,
            reason=f"Streaming imbalance {imbalance:+.0%} → {token_side}",
            edge=abs(imbalance) * 0.1,
            confidence=0.6,
        )
```

### 4.6 Safety Checks (`src/streaming/safety.py`)

```python
@dataclass
class SafetyCheckResult:
    """Result of safety validation."""
    passed: bool
    reason: str = ""


class StreamingSafetyChecker:
    """
    Safety checks for streaming signals.

    Stricter than polling executor because:
    - We're reacting to real-time events
    - Higher risk of duplicates if not careful
    - Need fresh price validation
    """

    def __init__(self, config: StreamingConfig):
        self.config = config

    def check_all(
        self,
        signal: StreamingSignal,
        order_client: PolymarketOrderClient,
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """Run all safety checks. Returns first failure or success."""

        checks = [
            self._check_signal_age,
            self._check_price_deviation,
            self._check_spread,
            self._check_fee_rate,
            self._check_duplicate_position,
            self._check_recent_orders,
        ]

        for check in checks:
            result = check(signal, order_client, db, is_paper)
            if not result.passed:
                return result

        return SafetyCheckResult(passed=True)

    def _check_signal_age(self, signal, order_client, db, is_paper) -> SafetyCheckResult:
        """Signal must be fresh (< 5 seconds for streaming)."""
        age = signal.age_seconds
        if age > self.config.max_signal_age_seconds:
            return SafetyCheckResult(
                passed=False,
                reason=f"Signal too old: {age:.1f}s > {self.config.max_signal_age_seconds}s",
            )
        return SafetyCheckResult(passed=True)

    def _check_price_deviation(self, signal, order_client, db, is_paper) -> SafetyCheckResult:
        """Fresh orderbook price must match signal price within tolerance."""
        try:
            orderbook = order_client.get_orderbook(signal.token_id)
            best_bid, best_ask = order_client.get_best_bid_ask(orderbook)

            if best_bid is None or best_ask is None:
                return SafetyCheckResult(passed=False, reason="No liquidity in orderbook")

            live_mid = (best_bid + best_ask) / 2
            signal_mid = (signal.best_bid + signal.best_ask) / 2

            deviation = abs(live_mid - signal_mid) / signal_mid if signal_mid > 0 else 0
            if deviation > self.config.max_price_deviation:
                return SafetyCheckResult(
                    passed=False,
                    reason=f"Price moved: signal={signal_mid:.2%}, live={live_mid:.2%}, dev={deviation:.1%}",
                )

            return SafetyCheckResult(passed=True)

        except Exception as e:
            return SafetyCheckResult(passed=False, reason=f"Orderbook fetch failed: {e}")

    def _check_spread(self, signal, order_client, db, is_paper) -> SafetyCheckResult:
        """Spread must be within tolerance."""
        if signal.spread > self.config.max_spread:
            return SafetyCheckResult(
                passed=False,
                reason=f"Spread too high: {signal.spread:.1%} > {self.config.max_spread:.0%}",
            )
        return SafetyCheckResult(passed=True)

    def _check_fee_rate(self, signal, order_client, db, is_paper) -> SafetyCheckResult:
        """Fee rate must be acceptable."""
        try:
            fee_bps = order_client.get_fee_rate_bps(signal.token_id)
            if fee_bps > self.config.max_fee_rate_bps:
                return SafetyCheckResult(
                    passed=False,
                    reason=f"Fee too high: {fee_bps} bps > {self.config.max_fee_rate_bps} bps",
                )
            return SafetyCheckResult(passed=True)
        except Exception as e:
            # Log warning but allow trade (fees are usually 0)
            return SafetyCheckResult(passed=True)

    def _check_duplicate_position(self, signal, order_client, db, is_paper) -> SafetyCheckResult:
        """Check for existing position on this token."""
        from src.executor.models import Position, PositionStatus
        existing = db.query(Position).filter(
            Position.token_id == signal.token_id,
            Position.is_paper == is_paper,
            Position.status == PositionStatus.OPEN.value,
        ).first()

        if existing:
            return SafetyCheckResult(
                passed=False,
                reason=f"Position already exists: ID {existing.id}",
            )
        return SafetyCheckResult(passed=True)

    def _check_recent_orders(self, signal, order_client, db, is_paper) -> SafetyCheckResult:
        """Check for recent orders on this token (catch untracked fills)."""
        from src.executor.models import ExecutorOrder, OrderStatus
        from datetime import timedelta

        ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
        recent = db.query(ExecutorOrder).filter(
            ExecutorOrder.token_id == signal.token_id,
            ExecutorOrder.is_paper == is_paper,
            ExecutorOrder.submitted_at >= ten_mins_ago,
        ).first()

        if recent:
            return SafetyCheckResult(
                passed=False,
                reason=f"Recent order exists: ID {recent.id}, status {recent.status}",
            )
        return SafetyCheckResult(passed=True)
```

### 4.7 StreamingExecutor (`src/streaming/executor.py`)

```python
class StreamingExecutor:
    """
    Execute streaming signals.

    Supports both paper and live modes with seamless switching.
    Reuses existing order client and position models.
    """

    def __init__(self, config: StreamingConfig):
        self.config = config
        self.is_paper = not config.live
        self._order_client = None
        self.safety_checker = StreamingSafetyChecker(config)

    @property
    def order_client(self) -> PolymarketOrderClient:
        """Lazy initialization of order client."""
        if self._order_client is None:
            from src.executor.clients.order_client import get_order_client
            self._order_client = get_order_client()
        return self._order_client

    async def execute(
        self,
        signal: StreamingSignal,
        state: StreamingStateManager,
    ) -> ExecutionResult:
        """
        Execute a streaming signal.

        1. Run safety checks
        2. Create audit records
        3. Execute order (paper or live)
        4. Update state
        5. Send alerts
        """
        with get_session() as db:
            # Safety checks
            check = self.safety_checker.check_all(
                signal,
                self.order_client,
                db,
                self.is_paper,
            )

            if not check.passed:
                self._log_rejection(signal, check.reason, db)
                return ExecutionResult(success=False, reason=check.reason)

            try:
                if self.is_paper:
                    result = self._execute_paper(signal, db)
                else:
                    result = self._execute_live(signal, db)

                if result.success:
                    # Update state
                    state.set_cooldown(signal.strategy_name, signal.market_id)
                    state.open_positions[signal.strategy_name].add(signal.market_id)

                    # Send Telegram alert
                    self._send_alert(signal, result)

                return result

            except Exception as e:
                logger.error(f"Execution failed: {e}", exc_info=True)
                self._log_rejection(signal, str(e), db)
                return ExecutionResult(success=False, reason=str(e))

    def _execute_paper(self, signal: StreamingSignal, db: Session) -> ExecutionResult:
        """Simulate paper execution."""
        from src.executor.execution.paper import PaperExecutor
        from src.executor.models import Signal as SignalModel, SignalStatus

        # Create signal model
        signal_model = SignalModel(
            strategy_name=signal.strategy_name,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            reason=signal.reason,
            edge=signal.edge,
            confidence=signal.confidence,
            price_at_signal=signal.price_at_signal,
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            suggested_size_usd=signal.size_usd,
            status=SignalStatus.APPROVED.value,
            created_at=signal.created_at,
        )
        db.add(signal_model)
        db.flush()

        # Execute via paper executor
        paper_executor = PaperExecutor()
        orderbook = OrderbookState(
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            mid_price=(signal.best_bid + signal.best_ask) / 2,
            bid_depth_10=1000.0,
            ask_depth_10=1000.0,
            spread=signal.spread,
        )

        result = paper_executor.execute_signal(signal_model, orderbook, db=db)

        # Log decision
        self._log_decision(signal, result, db)

        return ExecutionResult(
            success=result.success,
            position_id=result.position_id if hasattr(result, 'position_id') else None,
            executed_price=result.executed_price,
            executed_shares=result.executed_shares,
            reason=result.message,
        )

    def _execute_live(self, signal: StreamingSignal, db: Session) -> ExecutionResult:
        """Execute live order."""
        from src.executor.execution.live import LiveExecutor
        from src.executor.models import Signal as SignalModel, SignalStatus

        # Create signal model
        signal_model = SignalModel(
            strategy_name=signal.strategy_name,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            reason=signal.reason,
            edge=signal.edge,
            confidence=signal.confidence,
            price_at_signal=signal.price_at_signal,
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            suggested_size_usd=signal.size_usd,
            status=SignalStatus.APPROVED.value,
            created_at=signal.created_at,
        )
        db.add(signal_model)
        db.flush()

        # Execute via live executor (fetches fresh orderbook for safety)
        live_executor = LiveExecutor(self.order_client)
        result = live_executor.execute_signal(signal_model, db=db)

        # Log decision
        self._log_decision(signal, result, db)

        return ExecutionResult(
            success=result.success,
            position_id=result.position_id if hasattr(result, 'position_id') else None,
            executed_price=result.executed_price,
            executed_shares=result.executed_shares,
            reason=result.message,
        )

    def _log_decision(self, signal: StreamingSignal, result, db: Session):
        """Log trade decision for audit trail."""
        from src.executor.models import TradeDecision
        decision = TradeDecision(
            strategy_name=signal.strategy_name,
            strategy_sha="streaming",
            market_id=signal.market_id,
            condition_id=signal.condition_id,
            market_snapshot={"imbalance": signal.imbalance, "spread": signal.spread},
            decision_inputs={
                "imbalance": signal.imbalance,
                "hours_to_close": signal.hours_to_close,
                "token_side": signal.token_side,
            },
            signal_side=signal.side,
            signal_reason=signal.reason,
            signal_edge=signal.edge,
            signal_size_usd=signal.size_usd,
            executed=result.success,
            rejected_reason=result.message if not result.success else None,
            execution_price=result.executed_price if result.success else None,
            position_id=result.position_id if result.success and hasattr(result, 'position_id') else None,
        )
        db.add(decision)
        db.commit()

    def _log_rejection(self, signal: StreamingSignal, reason: str, db: Session):
        """Log rejected signal."""
        self._log_decision(signal, ExecutionResult(success=False, reason=reason), db)

    def _send_alert(self, signal: StreamingSignal, result):
        """Send Telegram alert for executed trade."""
        from src.alerts.telegram import alert_trade
        alert_trade(
            strategy=signal.strategy_name,
            side=signal.side,
            market_title=f"CRYPTO market {signal.market_id}",
            market_id=signal.market_id,
            token_side=signal.token_side,
            price=result.executed_price,
            size=signal.size_usd,
            edge=signal.edge,
            expected_win_rate=0.9,  # Based on paper backtest
            order_type="market",
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            hours_to_close=signal.hours_to_close,
            is_live=not self.is_paper,
        )
```

### 4.8 WebSocket Handler (`src/streaming/websocket.py`)

```python
class StreamingWebSocket:
    """
    Dedicated WebSocket connection for streaming executor.

    Connects to Polymarket WebSocket and subscribes to "book" channel
    for selected CRYPTO <4h markets.
    """

    def __init__(
        self,
        config: StreamingConfig,
        state: StreamingStateManager,
        on_book_update: Callable[[str, list, list], Awaitable[None]],
    ):
        self.config = config
        self.state = state
        self.on_book_update = on_book_update
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.subscribed_tokens: set[str] = set()
        self.reconnect_delay = config.reconnect_delay

    async def start(self):
        """Start WebSocket connection with auto-reconnect."""
        self.running = True
        while self.running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self.running:
                logger.info(f"Reconnecting in {self.reconnect_delay}s")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(
                    self.reconnect_delay * 1.5,
                    self.config.max_reconnect_delay,
                )

    async def _connect_and_run(self):
        """Connect and process messages."""
        url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

        async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
            self.ws = ws
            self.reconnect_delay = self.config.reconnect_delay
            logger.info("Streaming WebSocket connected")

            # Subscribe to current tokens
            if self.subscribed_tokens:
                await self._subscribe(list(self.subscribed_tokens))

            # Process messages
            async for message in ws:
                await self._handle_message(message)

    async def _handle_message(self, message: str | bytes):
        """Process incoming WebSocket message."""
        try:
            if isinstance(message, bytes):
                import msgpack
                data = msgpack.unpackb(message, raw=False)
            else:
                if message in ("PING", "PONG"):
                    return
                data = json.loads(message)

            if isinstance(data, list):
                for event in data:
                    await self._process_event(event)
            else:
                await self._process_event(data)

        except Exception as e:
            logger.warning(f"Failed to handle message: {e}")

    async def _process_event(self, data: dict):
        """Process single event."""
        if data.get("event_type") == "book":
            token_id = data.get("asset_id")
            if token_id and token_id in self.subscribed_tokens:
                bids = data.get("buys", [])
                asks = data.get("sells", [])
                await self.on_book_update(token_id, bids, asks)

    async def update_subscriptions(self, token_ids: list[str]):
        """Update subscribed tokens."""
        new_tokens = set(token_ids)

        # Unsubscribe removed
        to_remove = self.subscribed_tokens - new_tokens
        # Subscribe added
        to_add = new_tokens - self.subscribed_tokens

        self.subscribed_tokens = new_tokens

        if self.ws and to_add:
            await self._subscribe(list(to_add))

    async def _subscribe(self, token_ids: list[str]):
        """Send subscription message."""
        if self.ws and token_ids:
            message = {
                "type": "market",
                "assets_ids": token_ids[:500],  # Max 500 per connection
            }
            await self.ws.send(json.dumps(message))
            logger.info(f"Subscribed to {len(token_ids)} tokens")

    def stop(self):
        """Stop WebSocket connection."""
        self.running = False
```

### 4.9 Runner (`src/streaming/runner.py`)

```python
class StreamingRunner:
    """
    Main entry point for streaming executor.

    Orchestrates:
    1. Market selection (refresh every 5 min)
    2. WebSocket management
    3. Strategy evaluation on book updates
    4. Signal execution
    5. Position sync
    """

    def __init__(self):
        self.config = load_streaming_config()
        self.state = StreamingStateManager()
        self.strategy = StreamingBookImbalanceStrategy(self.config)
        self.executor = StreamingExecutor(self.config)

        self.websocket = StreamingWebSocket(
            config=self.config,
            state=self.state,
            on_book_update=self._on_book_update,
        )

        self.running = False

    async def run(self):
        """Main run loop."""
        self.running = True
        logger.info(f"Starting streaming executor: {self.config.name}")
        logger.info(f"Mode: {'PAPER' if not self.config.live else 'LIVE'}")

        # Initial setup
        await self._refresh_markets()
        await self._sync_positions()

        # Start background tasks
        tasks = [
            asyncio.create_task(self.websocket.start()),
            asyncio.create_task(self._market_refresh_loop()),
            asyncio.create_task(self._position_sync_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self.websocket.stop()

    async def _on_book_update(self, token_id: str, bids: list, asks: list):
        """
        Handle orderbook update from WebSocket.

        Called for every book event. Must be fast.
        """
        # Update in-memory state
        self.state.update_orderbook(token_id, bids, asks)

        # Get market info
        market_id = self.state.token_to_market.get(token_id)
        if market_id is None:
            return

        market = self.state.market_info.get(market_id)
        if market is None:
            return

        # Get orderbook state
        book = self.state.orderbooks.get(token_id)
        if book is None:
            return

        # Quick imbalance check (avoid expensive evaluation if no signal)
        if abs(book.imbalance) < self.config.min_imbalance:
            return

        # Evaluate strategy
        signal = self.strategy.evaluate(book, market, self.state)

        if signal:
            logger.info(
                f"Signal generated: {signal.token_side} on market {market_id}, "
                f"imbalance={signal.imbalance:+.0%}"
            )

            # Execute (async to not block book processing)
            asyncio.create_task(self._execute_signal(signal))

    async def _execute_signal(self, signal: StreamingSignal):
        """Execute signal in background."""
        try:
            result = await self.executor.execute(signal, self.state)
            if result.success:
                logger.info(
                    f"Executed: {signal.token_side} @ ${result.executed_price:.4f}, "
                    f"position={result.position_id}"
                )
            else:
                logger.warning(f"Execution failed: {result.reason}")
        except Exception as e:
            logger.error(f"Execution error: {e}", exc_info=True)

    async def _refresh_markets(self):
        """Refresh market selection and update subscriptions."""
        with get_session() as db:
            markets = get_streaming_markets(db, self.config)

        # Update state
        self.state.market_info = {m.id: m for m in markets}
        self.state.token_to_market = {}

        token_ids = []
        for m in markets:
            self.state.token_to_market[m.yes_token_id] = m.id
            self.state.token_to_market[m.no_token_id] = m.id
            token_ids.extend([m.yes_token_id, m.no_token_id])

        logger.info(f"Selected {len(markets)} CRYPTO markets <4h")

        # Update WebSocket subscriptions
        await self.websocket.update_subscriptions(token_ids)

    async def _market_refresh_loop(self):
        """Periodically refresh market selection."""
        while self.running:
            await asyncio.sleep(self.config.subscription_refresh_interval)
            try:
                await self._refresh_markets()
            except Exception as e:
                logger.error(f"Market refresh failed: {e}")

    async def _sync_positions(self):
        """Sync open positions from database."""
        with get_session() as db:
            self.state.sync_positions_from_db(
                db,
                self.config.name,
                is_paper=not self.config.live,
            )
        logger.info(
            f"Synced positions: {len(self.state.open_positions.get(self.config.name, set()))} open"
        )

    async def _position_sync_loop(self):
        """Periodically sync positions from database."""
        while self.running:
            await asyncio.sleep(60)  # Every minute
            try:
                await self._sync_positions()
            except Exception as e:
                logger.error(f"Position sync failed: {e}")

    def stop(self):
        """Stop the runner."""
        self.running = False
        self.websocket.stop()


async def main():
    """Entry point."""
    import signal as sig

    runner = StreamingRunner()

    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received")
        runner.stop()

    sig.signal(sig.SIGTERM, shutdown_handler)
    sig.signal(sig.SIGINT, shutdown_handler)

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 5. Configuration Updates

### 5.1 strategies.yaml Addition

```yaml
# =============================================================================
# STREAMING BOOK IMBALANCE (Real-time execution)
# Dedicated WebSocket → In-memory evaluation → Sub-second execution
# Paper by default, set live: true for real money
# =============================================================================
streaming_book_imbalance:
  - name: streaming_imbalance_crypto
    enabled: true
    live: false                    # PAPER first - set to true for real money

    # Strategy parameters
    min_imbalance: 0.5             # |imbalance| >= 50% required
    yes_price_min: 0.30            # Price zone 30-70%
    yes_price_max: 0.70
    max_spread: 0.02               # 2% max spread

    # Market selection
    categories:
      - CRYPTO
    max_hours_to_close: 4.0        # Only <4h markets
    min_minutes_to_close: 2        # 2 min buffer before resolution

    # Position management
    max_positions: 5
    fixed_size_usd: 1.1            # Ultra-conservative sizing
    cooldown_minutes: 60

    # Safety (stricter than polling)
    max_signal_age_seconds: 5.0    # 5s max (vs 120s polling)
    max_price_deviation: 0.03      # 3% max (vs 5% polling)
    max_fee_rate_bps: 200

    # WebSocket
    subscription_refresh_interval: 300  # 5 min
```

### 5.2 docker-compose.yml Addition

```yaml
  # Streaming executor for real-time book imbalance trading
  streaming-executor:
    build: .
    command: python -m src.streaming.runner
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/polymarket_ml
      - REDIS_URL=redis://redis:6379/0
      - POLYMARKET_PRIVATE_KEY=${POLYMARKET_PRIVATE_KEY:-}
      - POLYMARKET_FUNDER_ADDRESS=${POLYMARKET_FUNDER_ADDRESS:-}
      - TRADING_PROXY_URL=${TRADING_PROXY_URL:-}
      - TRADING_PROXY_FALLBACKS=${TRADING_PROXY_FALLBACKS:-}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - .:/app
    restart: unless-stopped
    logging: *default-logging
```

---

## 6. Integration Points

### 6.1 What's Reused (No Changes Needed)

| Component | Location | How Reused |
|-----------|----------|------------|
| PolymarketOrderClient | `src/executor/clients/order_client.py` | `get_order_client()` singleton |
| Position model | `src/executor/models.py` | Same DB table, `is_paper` flag |
| TradeDecision model | `src/executor/models.py` | Same audit trail |
| ExecutorOrder model | `src/executor/models.py` | Order tracking |
| Signal model | `src/executor/models.py` | Signal records |
| PaperExecutor | `src/executor/execution/paper.py` | Paper trade simulation |
| LiveExecutor safety | `src/executor/execution/live.py` | Safety check patterns |
| Telegram alerts | `src/alerts/telegram.py` | `alert_trade()` |
| Database session | `src/db/database.py` | `get_session()` |
| Market model | `src/db/models.py` | Market info queries |

### 6.2 What's New

| Component | Purpose |
|-----------|---------|
| `src/streaming/` | New package for streaming executor |
| `streaming-executor` service | New Docker container |
| `streaming_book_imbalance` config | New section in strategies.yaml |
| Dedicated WebSocket | Separate from data collection WS |

### 6.3 Coexistence with Polling Executor

The streaming executor runs **alongside** the polling executor:
- Polling executor: Handles all non-CRYPTO strategies, longer-term trades
- Streaming executor: Handles only CRYPTO <4h with real-time book imbalance

**No conflicts** because:
1. Different strategy names (`streaming_imbalance_crypto` vs `book_imbalance_crypto`)
2. Duplicate position checks query same Position table
3. TradeDecision audit trail shared (useful for comparison)

---

## 7. Paper/Live Mode Switching

### Seamless Toggle

```yaml
# Paper mode (default - safe for testing)
streaming_book_imbalance:
  - name: streaming_imbalance_crypto
    live: false  # Paper mode

# Live mode (real money)
streaming_book_imbalance:
  - name: streaming_imbalance_crypto
    live: true   # LIVE mode - trades with real money!
```

The executor reads this config on startup and sets:
- `is_paper = not config.live`
- Uses same Position table with `is_paper` flag
- Uses same order client for both (paper simulates, live executes)

### Safety for Live Mode

1. **Config protection**: `live: false` by default in YAML
2. **Startup logging**: Clear "LIVE MODE" warning on startup
3. **Telegram alerts**: Include "🔴 LIVE" indicator
4. **Stricter checks**: 5s signal age, 3% price deviation (vs 120s/5% for polling)
5. **Small size**: `fixed_size_usd: 1.1` by default

---

## 8. Monitoring

### Health Endpoint

Add to `src/api/routes/monitoring.py`:

```python
@router.get("/streaming/health")
async def streaming_health():
    """Check streaming executor health."""
    # Read stats from Redis (set by streaming executor)
    redis = RedisClient()
    stats = await redis.client.hgetall("streaming:stats")

    return {
        "status": "healthy" if stats else "unknown",
        "markets_subscribed": int(stats.get("markets", 0)),
        "signals_generated": int(stats.get("signals", 0)),
        "signals_executed": int(stats.get("executed", 0)),
        "last_book_update": stats.get("last_update"),
        "websocket_connected": stats.get("ws_connected") == "1",
    }
```

### Logging

Structured logging at key points:
- Market selection: "Selected X CRYPTO markets <4h"
- Book update with signal: "Signal: YES/NO on market X, imbalance Y%"
- Execution: "Executed: YES/NO @ $X.XX, position=Y"
- Rejection: "Rejected: reason"

---

## 9. Testing Plan

### Phase 1: Unit Tests
- [ ] `test_orderbook_state.py` - Imbalance calculation
- [ ] `test_strategy.py` - Signal generation logic
- [ ] `test_safety.py` - Safety check edge cases

### Phase 2: Integration Tests
- [ ] Market selector queries correct markets
- [ ] WebSocket receives and parses book events
- [ ] Paper executor creates positions correctly
- [ ] Audit trail records all decisions

### Phase 3: Paper Trading (24-48h)
- [ ] Deploy with `live: false`
- [ ] Monitor signals generated
- [ ] Verify position tracking
- [ ] Check latency (signal creation → execution)
- [ ] Validate against polling executor signals

### Phase 4: Live Trading (small size)
- [ ] Switch to `live: true`
- [ ] `fixed_size_usd: 1.1` (ultra-conservative)
- [ ] `max_positions: 3` (start small)
- [ ] Monitor for 24h
- [ ] Gradually increase if successful

---

## 10. Implementation Order

### Step 1: Core Infrastructure
1. Create `src/streaming/__init__.py`
2. Implement `config.py` - Load from strategies.yaml
3. Implement `signals.py` - StreamingSignal dataclass
4. Implement `state.py` - OrderbookState, StateManager

### Step 2: Market Selection
5. Implement `market_selector.py` - DB queries

### Step 3: Strategy Logic
6. Implement `strategy.py` - StreamingBookImbalanceStrategy

### Step 4: Safety & Execution
7. Implement `safety.py` - Safety checks
8. Implement `executor.py` - Paper/live execution

### Step 5: WebSocket & Runner
9. Implement `websocket.py` - WebSocket handler
10. Implement `runner.py` - Main orchestration

### Step 6: Integration
11. Add config to `strategies.yaml`
12. Add health endpoint to monitoring routes
13. Add Docker service to `docker-compose.yml`

### Step 7: Testing & Deployment
14. Run paper mode for 24-48h
15. Review results, fix issues
16. Enable live with small size

---

## 11. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Duplicate positions | In-memory + DB check before execution |
| Stale signals | 5s max age (vs 120s polling) |
| Price slippage | Fresh orderbook fetch + 3% deviation check |
| WebSocket disconnect | Auto-reconnect with backoff |
| High fees | 200 bps max fee check |
| Market resolution | 2 min buffer before close |
| Runaway positions | max_positions: 5 limit |
| Bug in live mode | Paper first, gradual rollout |

---

## 12. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Latency | <1s | `signal.created_at` to `position.entry_time` |
| Signal-to-execution rate | >80% | Approved signals that execute successfully |
| Win rate | >85% | Compare to polling's 89.9% paper performance |
| Price deviation | <3% | Signal price vs execution price |
| Uptime | >99% | WebSocket connected time |

---

*Plan created: 2026-01-12*
*Ready for implementation*
