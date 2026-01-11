# Streaming Executor Architecture

> **Status**: PLANNED - Try current polling approach first, implement if latency remains a problem

## Problem

15-minute crypto markets move too fast for the current polling executor:
- Scan cycle: ~30 seconds
- By execution time, price has moved 30-40% from signal price
- 5% max deviation safety check rejects most signals

## Proposed Solution: Streaming Executor

Replace polling with real-time websocket-based detection and execution.

### Current Flow (Polling)
```
Celery snapshot task → PostgreSQL → Executor scans DB → Fresh orderbook fetch → Execute
                                    [~30s cycle]
```

### New Flow (Streaming)
```
Websocket orderbook updates → In-memory strategy eval → Execute immediately
                              [~500ms total latency]
```

## Architecture

### New Service: `websocket-executor`

Single Python process that:
1. Subscribes to Polymarket orderbook websocket channels
2. Maintains in-memory orderbook state per market
3. Evaluates strategy conditions on each update
4. Places orders immediately when signals fire

### Key Components

```python
class StreamingExecutor:
    def __init__(self):
        self.orderbooks: dict[str, Orderbook] = {}  # token_id -> orderbook
        self.positions: dict[str, Position] = {}     # In-memory position tracking
        self.cooldowns: dict[int, datetime] = {}     # market_id -> last entry time

    async def on_orderbook_update(self, data: dict):
        """Called on each websocket orderbook message."""
        token_id = data['asset_id']

        # Update in-memory orderbook
        self.orderbooks[token_id] = Orderbook(
            bids=data['bids'],
            asks=data['asks'],
            timestamp=datetime.now()
        )

        # Calculate imbalance
        imbalance = self.calculate_imbalance(data['bids'], data['asks'])

        # Check strategy conditions
        if self.should_trade(token_id, imbalance):
            await self.execute_immediately(token_id, imbalance)

    def calculate_imbalance(self, bids: list, asks: list) -> float:
        """Calculate book imbalance from raw orderbook."""
        bid_volume = sum(float(b['size']) for b in bids[:5])
        ask_volume = sum(float(a['size']) for a in asks[:5])
        total = bid_volume + ask_volume
        if total == 0:
            return 0
        return (bid_volume - ask_volume) / total

    def should_trade(self, token_id: str, imbalance: float) -> bool:
        """Evaluate all strategy conditions."""
        market = self.get_market(token_id)

        # Imbalance threshold
        if abs(imbalance) < 0.5:
            return False

        # Price zone (30-70%)
        mid_price = self.get_mid_price(token_id)
        if not (0.30 <= mid_price <= 0.70):
            return False

        # Spread check
        spread = self.get_spread(token_id)
        if spread > 0.02:
            return False

        # Time to close
        if market.minutes_to_close < 5:
            return False

        # Cooldown
        if self.in_cooldown(market.id):
            return False

        # Max positions
        if self.open_position_count() >= 3:
            return False

        return True

    async def execute_immediately(self, token_id: str, imbalance: float):
        """Place order immediately - no DB round-trip for signal."""
        side = "BUY"
        token_side = "YES" if imbalance > 0 else "NO"

        # Get current best price
        orderbook = self.orderbooks[token_id]
        price = orderbook.best_ask  # Taker price

        # Place order via API
        result = await self.order_client.place_market_order(
            token_id=token_id,
            side=side,
            size_usd=1.1,
        )

        if result.success:
            # Track position in-memory AND write to DB
            self.positions[token_id] = Position(...)
            await self.save_position_to_db(result)
            await self.log_decision(token_id, imbalance, executed=True)
        else:
            await self.log_decision(token_id, imbalance, executed=False, reason=result.message)
```

### Websocket Subscription

```python
# Polymarket orderbook channel subscription
subscribe_message = {
    "type": "subscribe",
    "channel": "book",
    "assets_ids": [token_id_1, token_id_2, ...]  # CRYPTO tokens only
}
```

### Docker Service

```yaml
# docker-compose.yml
websocket-executor:
  build: .
  command: python -m src.executor.streaming.runner
  environment:
    - STRATEGY=book_imbalance_crypto
    - MAX_POSITIONS=3
    - SIZE_USD=1.1
  depends_on:
    - postgres
    - redis
  restart: unless-stopped
```

## Implementation Plan

1. **Create `src/executor/streaming/` package**
   - `runner.py` - Main entry point
   - `orderbook.py` - In-memory orderbook management
   - `executor.py` - StreamingExecutor class

2. **Add orderbook websocket subscription**
   - Extend existing websocket client
   - Subscribe to "book" channel for CRYPTO tokens

3. **Port strategy logic**
   - Extract book_imbalance conditions into streaming-compatible format
   - No MarketData objects - work directly with orderbook data

4. **Add position management**
   - In-memory tracking for speed
   - Async writes to DB for persistence

5. **Add monitoring**
   - Latency metrics (signal → execution)
   - Orderbook update rate
   - Execution success rate

## Expected Performance

| Metric | Current (Polling) | Streaming |
|--------|-------------------|-----------|
| Signal detection | 30s cycle | <500ms |
| Execution latency | 30-120s | <1s |
| Price deviation | 30-40% | <5% |

## Risks

1. **Websocket disconnection** - Need robust reconnection logic
2. **Rate limits** - Polymarket may limit order placement rate
3. **State sync** - In-memory state must stay in sync with DB
4. **Resource usage** - More CPU/memory than polling

## Alternative: Faster Polling

Before building streaming, could try:
- Reduce scan cycle to 5-10 seconds
- Use cached orderbooks from websocket trades
- Only fetch fresh orderbook for high-imbalance markets

This is simpler but may not be fast enough for 15-min crypto.

---

*Created: 2026-01-11*
*Reference for future implementation*
