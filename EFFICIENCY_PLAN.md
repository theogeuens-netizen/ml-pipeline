# Pipeline Efficiency Plan

## Problem Summary

Current state (Dec 18, 2024):
- 3,058 active markets
- 92% are T0 (>48h to resolution), getting hourly snapshots
- Only 130 markets are WebSocket-enabled (T2-T4)
- Redis shows 1,539 "connected" markets (stale data from tier changes)
- Many markets have zero volume or no price movement

## Efficiency Improvements

### 1. Clean Up Stale Redis Data (Quick Win)

**Issue**: `ws:connected` set has 1,539 entries but only 130 markets are WebSocket-enabled.

**Fix**: Add cleanup task to sync Redis with actual state.

```python
# In src/tasks/discovery.py
@shared_task(name="src.tasks.discovery.cleanup_redis_tracking")
def cleanup_redis_tracking() -> dict:
    """Clean up stale WebSocket tracking data in Redis."""
    redis = SyncRedisClient()

    # Get currently subscribed condition_ids from DB
    with get_session() as session:
        ws_markets = session.execute(
            select(Market.condition_id).where(
                Market.tier.in_([2, 3, 4]),
                Market.active == True,
                Market.resolved == False,
            )
        ).scalars().all()

    valid_ids = set(ws_markets)

    # Get current ws:connected set
    connected = redis.client.smembers("ws:connected")

    # Remove stale entries
    stale = set(connected) - valid_ids
    if stale:
        redis.client.srem("ws:connected", *stale)

    return {"valid": len(valid_ids), "removed_stale": len(stale)}
```

**Schedule**: Run every 10 minutes with cleanup_stale_markets.

---

### 2. Add `last_trade_at` Tracking (Medium)

**Issue**: No way to identify markets with recent trading activity.

**Fix**: Add `last_trade_at` column to markets table, update on trade.

```sql
ALTER TABLE markets ADD COLUMN last_trade_at TIMESTAMP WITH TIME ZONE;
```

```python
# In WebSocket collector, after inserting trade:
session.execute(
    update(Market)
    .where(Market.id == market_id)
    .values(last_trade_at=timestamp)
)
```

**Benefit**: Can filter/prioritize markets by activity.

---

### 3. Skip Snapshots for Static Markets (Medium)

**Issue**: Collecting snapshots for markets with no price change.

**Fix**: Track last price and skip if unchanged.

```python
# In snapshot_tier task, before creating snapshot:
# Check if price has changed since last snapshot
last_snapshot = session.execute(
    select(Snapshot.price)
    .where(Snapshot.market_id == market_id)
    .order_by(Snapshot.timestamp.desc())
    .limit(1)
).scalar()

if last_snapshot and abs(yes_price - last_snapshot) < 0.001:
    # Skip - no price movement
    continue
```

**Benefit**: Reduces DB writes by ~60-70% for static markets.

---

### 4. Increase Volume Threshold for T0 (Quick Win)

**Issue**: Tracking 1,190 T0 markets with zero volume.

**Fix**: Increase volume threshold or add minimum volume requirement.

```python
# In settings.py
volume_threshold: float = 500  # Increase from $100 to $500

# Or add tier-specific thresholds
volume_threshold_t0: float = 1000  # Higher bar for T0
volume_threshold_t1_plus: float = 100  # Lower bar for active tiers
```

**Benefit**: Reduces T0 market count by ~50%.

---

### 5. Activity-Based Tier Adjustment (Advanced)

**Issue**: Tier is based only on time to resolution, not activity.

**Fix**: Add activity modifier to tier calculation.

```python
def calculate_effective_tier(market, hours_to_close):
    base_tier = get_tier_from_hours(hours_to_close)

    # Downgrade tier for inactive markets
    if market.last_trade_at is None:
        return max(0, base_tier - 1)  # Never traded, reduce priority

    hours_since_trade = (now - market.last_trade_at).total_seconds() / 3600
    if hours_since_trade > 24:
        return max(0, base_tier - 1)  # No trades in 24h, reduce priority

    return base_tier
```

**Benefit**: Focuses resources on active markets.

---

### 6. Aggressive Stale Market Cleanup (Medium)

**Issue**: Current cleanup only checks T4 markets for no trades.

**Fix**: Extend cleanup to all tiers with configurable thresholds.

```python
# Cleanup thresholds by tier
STALE_THRESHOLDS = {
    0: timedelta(days=7),   # T0: no trades in 7 days
    1: timedelta(days=3),   # T1: no trades in 3 days
    2: timedelta(days=1),   # T2: no trades in 1 day
    3: timedelta(hours=6),  # T3: no trades in 6 hours
    4: timedelta(hours=1),  # T4: no trades in 1 hour
}
```

**Benefit**: Automatically cleans up dead markets across all tiers.

---

## Implementation Priority

| Priority | Task | Effort | Impact |
|----------|------|--------|--------|
| 1 | Clean up stale Redis data | Low | Medium |
| 2 | Increase volume threshold | Low | High |
| 3 | Skip static snapshots | Medium | High |
| 4 | Add last_trade_at tracking | Medium | Medium |
| 5 | Aggressive cleanup all tiers | Medium | High |
| 6 | Activity-based tier adjustment | High | Medium |

## Expected Results

After implementing priorities 1-5:
- **Active markets**: 3,058 → ~1,500 (50% reduction)
- **Snapshots/hour**: ~50,000 → ~20,000 (60% reduction)
- **DB writes**: Significantly reduced
- **System resources**: More headroom for ML training later

## Monitoring

Add metrics to track efficiency:
```sql
-- Markets by activity status
SELECT
    CASE
        WHEN last_trade_at > NOW() - INTERVAL '1 hour' THEN 'hot'
        WHEN last_trade_at > NOW() - INTERVAL '24 hours' THEN 'warm'
        WHEN last_trade_at IS NOT NULL THEN 'cold'
        ELSE 'dead'
    END as activity_status,
    COUNT(*) as count
FROM markets WHERE active = true
GROUP BY 1;
```
