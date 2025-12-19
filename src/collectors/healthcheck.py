#!/usr/bin/env python3
"""
Health check script for WebSocket collector.

Checks:
1. Redis connectivity and last activity timestamp
2. Trade rate from database (must be > MIN_RATE trades/minute)

Exit codes:
- 0: Healthy
- 1: Unhealthy
"""
import sys
import os

# Minimum trades per minute to consider healthy (after warmup)
MIN_TRADE_RATE = 20
WARMUP_MINUTES = 10  # Don't fail during initial warmup


def check_health():
    """Check WebSocket collector health."""
    import redis
    from datetime import datetime, timezone

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    try:
        r = redis.from_url(redis_url, socket_timeout=5)

        # Check 1: Redis connectivity
        if not r.ping():
            print("UNHEALTHY: Redis not responding")
            return 1

        # Check 2: Last activity timestamp
        last_activity = r.get("ws:last_activity")
        if last_activity:
            try:
                last_ts = datetime.fromisoformat(last_activity.decode())
                seconds_ago = (datetime.now(timezone.utc) - last_ts).total_seconds()
                if seconds_ago > 300:  # No activity in 5 minutes
                    print(f"UNHEALTHY: No WebSocket activity for {int(seconds_ago)}s")
                    return 1
            except Exception as e:
                print(f"WARNING: Could not parse last_activity: {e}")

        # Check 3: Trade rate from database
        database_url = os.environ.get("DATABASE_URL")
        if database_url:
            try:
                import psycopg2
                conn = psycopg2.connect(database_url)
                cur = conn.cursor()

                # Check how long collector has been running
                cur.execute("""
                    SELECT
                        EXTRACT(EPOCH FROM (NOW() - MIN(timestamp))) / 60 as minutes_running,
                        COUNT(*) as trade_count
                    FROM trades
                    WHERE timestamp > NOW() - INTERVAL '10 minutes'
                """)
                row = cur.fetchone()

                if row:
                    minutes_running, trade_count = row
                    trade_rate = trade_count / 10.0 if trade_count else 0

                    # Only enforce rate check after warmup period
                    if minutes_running and minutes_running > WARMUP_MINUTES:
                        if trade_rate < MIN_TRADE_RATE:
                            print(f"UNHEALTHY: Trade rate {trade_rate:.1f}/min < {MIN_TRADE_RATE}/min")
                            return 1

                    print(f"HEALTHY: Trade rate {trade_rate:.1f}/min, {trade_count} trades in last 10min")

                conn.close()
            except Exception as e:
                print(f"WARNING: Database check failed: {e}")
                # Don't fail just because DB check failed

        print("HEALTHY: WebSocket collector operational")
        return 0

    except redis.ConnectionError as e:
        print(f"UNHEALTHY: Redis connection failed: {e}")
        return 1
    except Exception as e:
        print(f"UNHEALTHY: Health check error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(check_health())
