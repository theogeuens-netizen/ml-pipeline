# Polymarket Executor

Build a Polymarket trading system with a clean React frontend dashboard, modular strategy architecture, and flexible execution options. Focus on usability, observability, and easy configuration.

## Architecture Overview

```
polymarket-trader/
├── backend/                 # Python FastAPI
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app entry
│   │   ├── config/
│   │   │   ├── schema.py    # Pydantic config models
│   │   │   ├── loader.py    # YAML/env loading
│   │   │   └── defaults.py
│   │   ├── models/
│   │   │   ├── market.py
│   │   │   ├── signal.py
│   │   │   ├── order.py
│   │   │   └── position.py
│   │   ├── clients/
│   │   │   ├── gamma.py     # Market discovery
│   │   │   └── clob.py      # py-clob-client wrapper
│   │   ├── strategies/
│   │   │   ├── base.py      # Abstract Strategy interface
│   │   │   ├── registry.py  # Auto-discovery + factory
│   │   │   └── builtin/
│   │   │       ├── longshot_yes.py
│   │   │       ├── longshot_no.py
│   │   │       ├── mean_reversion.py
│   │   │       ├── term_structure.py
│   │   │       └── volatility_hedge.py
│   │   ├── execution/
│   │   │   ├── executor.py      # Main executor
│   │   │   ├── order_types.py   # Market, Limit, Spread modes
│   │   │   ├── paper.py         # Paper trading simulator
│   │   │   └── live.py          # Real execution
│   │   ├── portfolio/
│   │   │   ├── positions.py     # Position tracking
│   │   │   ├── risk.py          # Risk limits
│   │   │   └── sizing.py        # Position sizing
│   │   ├── engine/
│   │   │   ├── scanner.py       # Market scanner
│   │   │   └── runner.py        # Main loop
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── status.py
│   │   │   │   ├── positions.py
│   │   │   │   ├── trades.py
│   │   │   │   ├── strategies.py
│   │   │   │   ├── signals.py
│   │   │   │   └── config.py
│   │   │   └── websocket.py     # Real-time updates
│   │   └── persistence/
│   │       ├── database.py      # SQLite
│   │       └── models.py
│   ├── config.yaml
│   └── requirements.txt
│
├── frontend/                # React + TypeScript + Tailwind
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   └── websocket.ts
│   │   ├── components/
│   │   │   ├── Layout/
│   │   │   ├── Dashboard/
│   │   │   ├── Positions/
│   │   │   ├── SignalFeed/
│   │   │   ├── Strategies/
│   │   │   ├── Config/
│   │   │   └── Trades/
│   │   ├── pages/
│   │   ├── hooks/
│   │   ├── types/
│   │   └── stores/          # Zustand for state
│   ├── package.json
│   └── vite.config.ts
│
└── docker-compose.yml
```

## Design Principles

1. **Configuration-first**: Everything adjustable via UI or config — no code changes needed
2. **Strategy as plugin**: Adding a strategy = one file implementing one interface
3. **Execution flexibility**: Market orders, limit orders, spread capture, configurable per-strategy
4. **Observable**: Real-time signal feed showing what's happening and why
5. **Paper trading by default**: Safe testing before going live

---

## Configuration System

Everything controllable via `config.yaml` or the React UI.

### Example `config.yaml`

```yaml
credentials:
  private_key: ${POLYMARKET_PRIVATE_KEY}
  funder_address: ${POLYMARKET_FUNDER_ADDRESS}

mode: paper  # paper | live

settings:
  scan_interval_seconds: 30
  log_level: INFO

risk:
  max_position_usd: 100
  max_total_exposure_usd: 1000
  max_positions: 20
  max_drawdown_pct: 0.15

execution:
  default_order_type: limit        # market | limit | spread
  limit_offset_bps: 50             # Place 0.5% better than mid
  spread_timeout_seconds: 30       # For spread mode: wait then cross
  
sizing:
  method: fixed                    # fixed | kelly | volatility_scaled
  fixed_amount_usd: 25
  kelly_fraction: 0.25

strategies:
  longshot_yes:
    enabled: true
    params:
      min_probability: 0.92
      max_probability: 0.99
      max_hours_to_expiry: 72
      min_liquidity_usd: 5000
    execution:
      order_type: limit
      limit_offset_bps: 30
    sizing:
      method: kelly
      
  longshot_no:
    enabled: true
    params:
      max_probability: 0.08
      min_hours_to_expiry: 24
      
  mean_reversion:
    enabled: false
    params:
      spike_threshold: 0.15
      lookback_hours: 24
      
  term_structure:
    enabled: false
    params:
      min_violation: 0.03

  volatility_hedge:
    enabled: true
    params:
      # Entry conditions (buy favorite)
      entry_min_probability: 0.50
      entry_max_probability: 0.75
      min_hours_to_resolution: 1
      max_hours_to_resolution: 48
      # Hedge trigger conditions
      hedge_trigger_price_increase: 0.15    # Favorite must rise 15%+ from entry
      hedge_underdog_max_price: 0.15        # Underdog must be <15 cents
      hedge_allocation_pct: 0.40            # Hedge with 40% of initial position
      # Categories to monitor (high-volatility events)
      categories: ["sports", "esports", "crypto"]

filters:
  min_liquidity_usd: 1000
  excluded_keywords: ["celebrity", "influencer"]
```

---

## Strategy Interface

Minimal interface — strategies just yield signals, framework handles the rest.

```python
from abc import ABC, abstractmethod
from typing import Iterator

class Strategy(ABC):
    """Base class for trading strategies."""
    
    name: str
    description: str
    version: str = "1.0.0"
    
    # Injected by framework
    config: dict      # Strategy params from config
    logger: Logger
    
    @abstractmethod
    def scan(self, markets: list[Market]) -> Iterator[Signal]:
        """
        Scan markets and yield signals for opportunities.
        Framework handles sizing, risk checks, execution.
        """
        pass
    
    def should_exit(self, position: Position, market: Market) -> Signal | None:
        """Optional custom exit logic. Default: hold to expiry."""
        return None
    
    def filter(self, market: Market) -> bool:
        """Optional pre-filter. Default: True."""
        return True
```

### Built-in Strategies

Implement these standard prediction market strategies:

1. **LongshotYes** — Buy YES on high-probability events (92-99%) near expiry. Edge comes from slight underpricing of near-certain outcomes.

2. **LongshotNo** — Buy NO against overpriced longshots (YES < 8%). Tail risks are systematically overestimated.

3. **MeanReversion** — Fade large price moves that look like overreactions. Track rolling price history, enter when move exceeds threshold.

4. **TermStructure** — Exploit probability violations in multi-deadline markets. Later deadlines should have ≥ probability of earlier ones.

5. **VolatilityHedge** — Two-phase strategy exploiting live event volatility:
   - **Phase 1 (Entry)**: Before/early in event, buy YES on the favorite at reasonable odds (50-75%)
   - **Phase 2 (Hedge)**: When favorite performs well and market overreacts (favorite price spikes, underdog crashes to <15%), buy the underdog to lock in profit regardless of outcome
   - **Payoff structure**: Small guaranteed profit if favorite wins, massive profit if underdog stages comeback
   - Works best on high-volatility events: sports, esports, crypto markets with short resolution times

Each strategy exposes configurable parameters via the UI.

---

## Execution Modes

### Order Types

```yaml
execution:
  order_type: spread  # Options:
  
  # market: Cross spread immediately, pay taker fee
  # limit: Post at offset from mid, wait for fill
  # spread: Post to capture spread, fall back to market after timeout
```

**Spread Capture Mode**:
- Post limit order on the book to earn spread instead of paying it
- If not filled within `spread_timeout_seconds`, cross to market
- Good for low-edge strategies where spread matters

### Paper Trading

Paper mode simulates execution using real market data:

```python
class PaperExecutor:
    """
    Simulates trading without real money.
    
    - Tracks virtual balance and positions
    - Estimates fills based on real orderbook
    - Models slippage
    - Logs everything as if live
    """
    
    def __init__(self, starting_balance: float = 10000):
        self.balance = starting_balance
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
    
    def execute(self, signal: Signal, orderbook: OrderBook) -> Trade:
        """Simulate fill with realistic slippage estimate."""
        ...
    
    def mark_to_market(self) -> float:
        """Current P&L based on live prices."""
        ...
```

---

## React Frontend

Clean, modern dashboard for monitoring and configuration.

### Pages

1. **Dashboard** — Overview: mode, balance, P&L, active positions summary, recent signals
2. **Positions** — All open positions with live P&L, entry price, time held, hedge status
3. **Trades** — Trade history with filters (strategy, date range, outcome)
4. **Signals** — Real-time feed of all signals (approved, executed, skipped)
5. **Strategies** — Enable/disable strategies, configure parameters, view per-strategy stats
6. **Config** — Edit execution settings, risk limits, sizing; switch paper/live mode

### Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  Polymarket Trader                              [Paper Mode] [⚙️]   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ Balance     │  │ Total P&L   │  │ Positions   │  │ Win Rate   │ │
│  │ $9,847.32   │  │ +$127.45    │  │ 4 open      │  │ 73%        │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─ Active Positions ───────────────────────────────────────────┐  │
│  │ Market                      Side   Entry   Current   P&L     │  │
│  │ BTC > 100k by Dec 31        YES    $0.94   $0.96    +$8.50   │  │
│  │ Vitality vs Spirit [HEDGE]  YES+NO $0.60   hedged   +$12.00  │  │
│  │ Trump wins popular vote     NO     $0.12   $0.10    +$5.00   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─ Signal Feed ────────────────────────────────────────────────┐  │
│  │ 14:32:01  vol_hedge     HEDGE "Spirit" @ 0.12      ✓ FILLED  │  │
│  │ 14:30:45  vol_hedge     ENTRY "Vitality" @ 0.60    ✓ FILLED  │  │
│  │ 14:28:12  longshot_yes  BUY YES "BTC > 99k" @ 0.94 ✓ FILLED  │  │
│  │ 14:27:30  longshot_no   BUY NO "Celeb X" @ 0.92    ⏳ PENDING │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌─ Strategy Performance ───────────────────────────────────────┐  │
│  │ Strategy        Status    Signals   Trades   P&L      Win%   │  │
│  │ longshot_yes    ✓ ON      24        18       +$89.20  78%    │  │
│  │ longshot_no     ✓ ON      8         5        +$32.10  80%    │  │
│  │ volatility_hedge✓ ON      6         4        +$45.00  100%   │  │
│  │ mean_reversion  ○ OFF     —         —        —        —      │  │
│  │ term_structure  ○ OFF     —         —        —        —      │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Strategy Configuration UI

```
┌─ Strategy: volatility_hedge ────────────────────────────────────────┐
│                                                                     │
│  [✓] Enabled                                                        │
│                                                                     │
│  Entry Conditions (Phase 1: Buy Favorite)                           │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Min Probability      [0.50    ] ───●───────── 0.30 ──── 0.80   ││
│  │ Max Probability      [0.75    ] ─────────●─── 0.30 ──── 0.80   ││
│  │ Min Hours to Event   [1       ]                                ││
│  │ Max Hours to Event   [48      ]                                ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Hedge Conditions (Phase 2: Buy Underdog)                           │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Trigger: Favorite +  [15      ]% from entry                    ││
│  │ Underdog Max Price   [0.15    ] ─●─────────── 0.05 ──── 0.25   ││
│  │ Hedge Allocation     [40      ]% of initial position           ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Market Categories                                                  │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ [✓] Sports   [✓] Esports   [✓] Crypto   [ ] Politics          ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Execution                                                          │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Order Type    (•) Market  ( ) Limit  ( ) Spread                ││
│  │ (Market recommended for time-sensitive hedge execution)        ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Sizing                                                             │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Method        (•) Fixed  ( ) Kelly  ( ) Vol-Scaled             ││
│  │ Fixed Amount  $[50      ]                                      ││
│  │ Max Size ($)   [200     ]                                      ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Stats (Last 7 Days)                                                │
│  Entries: 6  |  Hedged: 4  |  Win Rate: 100%  |  P&L: +$45.00      │
│                                                                     │
│                                        [Cancel]  [Save Changes]     │
└─────────────────────────────────────────────────────────────────────┘
```

### Global Config UI

```
┌─ Configuration ─────────────────────────────────────────────────────┐
│                                                                     │
│  Mode                                                               │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ (•) Paper Trading    ( ) Live Trading                          ││
│  │                                                                 ││
│  │ ⚠️  Live mode will execute real trades with real money          ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Risk Limits                                                        │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Max Position Size      $[100    ]                              ││
│  │ Max Total Exposure     $[1000   ]                              ││
│  │ Max Open Positions      [20     ]                              ││
│  │ Max Drawdown            [15     ]%                             ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Default Execution                                                  │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Order Type    ( ) Market  (•) Limit  ( ) Spread                ││
│  │ Limit Offset  [50 bps]                                         ││
│  │ Spread Timeout [30 sec] (for spread mode)                      ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Default Sizing                                                     │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Method    (•) Fixed  ( ) Kelly  ( ) Vol-Scaled                 ││
│  │ Fixed Amount  $[25]                                            ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Scanner                                                            │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │ Scan Interval    [30    ] seconds                              ││
│  │ Min Liquidity    $[1000 ]                                      ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                     │
│                                        [Reset Defaults]  [Save]     │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Frontend Features

- **Real-time updates** via WebSocket — positions, signals, P&L update live
- **Hedge status tracking** — clearly show which positions are hedged vs open
- **Responsive** — works on desktop and tablet
- **Dark mode** — easy on the eyes for monitoring
- **Toast notifications** — for fills, hedge triggers, errors
- **Confirmation dialogs** — when switching to live mode
- **Charts** — P&L over time, per-strategy performance

---

## Backend API

### REST Endpoints

```
GET  /api/status              # Mode, balance, summary stats
GET  /api/positions           # All positions (with hedge status)
GET  /api/positions/{id}      # Single position detail
GET  /api/trades              # Trade history (paginated, filterable)
GET  /api/signals             # Recent signals (paginated)
GET  /api/strategies          # All strategies with status
GET  /api/strategies/{name}   # Strategy detail + stats
POST /api/strategies/{name}/config   # Update strategy config
POST /api/strategies/{name}/enable
POST /api/strategies/{name}/disable
GET  /api/config              # Current config
POST /api/config              # Update config
POST /api/mode                # Switch paper/live
GET  /api/markets             # Available markets (from Gamma)
```

### WebSocket

```
WS /api/ws

# Server pushes:
{ "type": "signal", "data": { ... } }
{ "type": "trade", "data": { ... } }
{ "type": "position_update", "data": { ... } }
{ "type": "hedge_triggered", "data": { ... } }
{ "type": "status", "data": { "balance": ..., "pnl": ... } }
```

---

## Signal Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Scanner   │────▶│  Strategy   │────▶│   Sizing    │────▶│  Executor   │
│             │     │   .scan()   │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
      │                   │                   │                   │
      ▼                   ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Signal Feed (UI + Logs)                          │
│  Shows: market scanned → signal generated → sized → executed/skipped    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Volatility Hedge Flow (Special Case)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Volatility Hedge Lifecycle                        │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Phase 1: Entry                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │ Scan for    │────▶│ Buy YES on  │────▶│ Track       │                │
│  │ favorites   │     │ favorite    │     │ position    │                │
│  │ 50-75%      │     │ @ entry     │     │             │                │
│  └─────────────┘     └─────────────┘     └─────────────┘                │
│                                                │                         │
│                                                ▼                         │
│  Phase 2: Monitor & Hedge                                                │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │ Monitor     │────▶│ Favorite    │────▶│ Buy YES on  │                │
│  │ price       │     │ +15% AND    │     │ underdog    │                │
│  │ movement    │     │ underdog    │     │ (40% of     │                │
│  │             │     │ <15¢        │     │ initial)    │                │
│  └─────────────┘     └─────────────┘     └─────────────┘                │
│                            │                    │                        │
│                            ▼                    ▼                        │
│  Phase 3: Resolution                                                     │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │ Outcome A: Favorite wins                                      │      │
│  │ → Favorite shares pay $1, underdog worth $0                   │      │
│  │ → Net profit (smaller, guaranteed)                            │      │
│  │                                                               │      │
│  │ Outcome B: Underdog wins                                      │      │
│  │ → Favorite shares worth $0, underdog pays $1                  │      │
│  │ → Net profit (larger, from cheap underdog shares)             │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Usage

### Start the system

```bash
# Development
cd backend && uvicorn app.main:app --reload
cd frontend && npm run dev

# Production
docker-compose up
```

### Programmatic usage

```python
from polymarket_trader import Runner

runner = Runner.from_config("config.yaml")
runner.run()  # Starts scanner loop + API server
```

### Adding a custom strategy

```python
# backend/app/strategies/builtin/my_strategy.py
from ..base import Strategy, Signal

class MyStrategy(Strategy):
    name = "my_strategy"
    description = "My custom logic"
    
    def scan(self, markets):
        for m in markets:
            if self.detect_opportunity(m):
                yield Signal(
                    token_id=m.yes_token_id,
                    side=Side.BUY,
                    reason="Custom condition met",
                    edge=0.02,
                    confidence=0.8,
                )
```

Then add to config:
```yaml
strategies:
  my_strategy:
    enabled: true
    params:
      my_param: value
```

Strategy auto-discovered and appears in UI.

---

## Tech Stack

**Backend:**
- Python 3.11+
- FastAPI
- py-clob-client (Polymarket SDK)
- SQLite (positions, trades, signals)
- Pydantic (config validation)

**Frontend:**
- React 18 + TypeScript
- Vite
- Tailwind CSS
- Zustand (state management)
- Recharts (charts)
- React Query (API calls)

---

## Key Behaviors

1. **Paper mode by default** — Must explicitly enable live trading
2. **Config hot-reload** — Changes in UI apply immediately without restart
3. **Persistent state** — Positions and trades survive restarts
4. **Hedge tracking** — System tracks entry/hedge pairs for volatility strategy
5. **Graceful shutdown** — Saves state on SIGINT
6. **Strategy isolation** — One strategy error doesn't crash others
7. **Comprehensive logging** — Every decision traceable
