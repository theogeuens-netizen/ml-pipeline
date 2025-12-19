"""
Backtest a strategy against historical data.

Usage:
    python -m cli.backtest strategies/longshot_yes_v1.py --days 30

This runs the strategy's scan() method against historical snapshots
and reports how many signals would have been generated.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import load_strategy
from strategies.base import MarketData


def main():
    parser = argparse.ArgumentParser(
        description="Backtest a strategy against historical data"
    )
    parser.add_argument(
        "strategy",
        help="Path to strategy file (e.g., strategies/longshot_yes_v1.py)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to backtest (default: 30)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just validate strategy, don't run backtest"
    )
    args = parser.parse_args()

    # Load strategy
    strategy = load_strategy(args.strategy)
    if not strategy:
        print(f"Error: Failed to load strategy from {args.strategy}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Strategy: {strategy.name} v{strategy.version}")
    print(f"SHA: {strategy.get_sha()}")
    print(f"Parameters:")
    for key, value in strategy.get_params().items():
        print(f"  {key}: {value}")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("Dry run mode - strategy validated successfully")
        sys.exit(0)

    # Import database dependencies (only if not dry run)
    try:
        from sqlalchemy import select, and_, desc
        from src.db.database import get_session
        from src.db.models import Market, Snapshot
    except ImportError as e:
        print(f"Error: Database dependencies not available: {e}")
        print("Run from within Docker or install dependencies")
        sys.exit(1)

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    print(f"Backtest period: {start_date.date()} to {end_date.date()}")
    print(f"Loading snapshots...")

    # Query snapshots
    with get_session() as db:
        # Get unique snapshot timestamps (sample every hour to reduce volume)
        from sqlalchemy import func

        # Get count of snapshots in range
        count_query = select(func.count(Snapshot.id)).where(
            Snapshot.timestamp >= start_date
        )
        total_snapshots = db.execute(count_query).scalar()
        print(f"Total snapshots in range: {total_snapshots:,}")

        # Get markets that have snapshots in range
        market_ids_query = (
            select(Snapshot.market_id)
            .where(Snapshot.timestamp >= start_date)
            .distinct()
        )
        market_ids = [row[0] for row in db.execute(market_ids_query)]
        print(f"Markets with data: {len(market_ids):,}")

        # Load markets
        markets_query = select(Market).where(Market.id.in_(market_ids))
        markets = {m.id: m for m in db.execute(markets_query).scalars()}

        # Sample snapshots (one per hour per market)
        signals_generated = 0
        signals_by_day = {}
        markets_scanned = 0

        # Process in daily chunks to manage memory
        current_date = start_date
        while current_date < end_date:
            next_date = current_date + timedelta(days=1)

            # Get snapshots for this day (sample one per market)
            snapshots_query = (
                select(Snapshot)
                .where(
                    and_(
                        Snapshot.timestamp >= current_date,
                        Snapshot.timestamp < next_date,
                    )
                )
                .order_by(Snapshot.market_id, desc(Snapshot.timestamp))
                .distinct(Snapshot.market_id)
            )
            snapshots = list(db.execute(snapshots_query).scalars())

            # Convert to MarketData
            market_data_list = []
            for snap in snapshots:
                market = markets.get(snap.market_id)
                if not market:
                    continue

                # Calculate hours to close at snapshot time
                hours_to_close = None
                if market.end_date:
                    time_diff = market.end_date - snap.timestamp
                    hours_to_close = time_diff.total_seconds() / 3600

                md = MarketData(
                    id=market.id,
                    condition_id=market.condition_id,
                    question=market.question or "",
                    yes_token_id=market.yes_token_id,
                    no_token_id=market.no_token_id,
                    price=float(snap.price) if snap.price else 0.5,
                    best_bid=float(snap.best_bid) if snap.best_bid else None,
                    best_ask=float(snap.best_ask) if snap.best_ask else None,
                    spread=float(snap.spread) if snap.spread else None,
                    hours_to_close=hours_to_close,
                    end_date=market.end_date,
                    volume_24h=float(snap.volume_24h) if snap.volume_24h else None,
                    liquidity=float(snap.liquidity) if snap.liquidity else None,
                    category=market.category,
                    event_id=market.event_id,
                    snapshot={"timestamp": snap.timestamp.isoformat()},
                )
                market_data_list.append(md)

            markets_scanned += len(market_data_list)

            # Run strategy
            day_signals = 0
            for signal in strategy.scan(market_data_list):
                signals_generated += 1
                day_signals += 1

            day_key = current_date.strftime("%Y-%m-%d")
            signals_by_day[day_key] = day_signals

            print(f"  {day_key}: {len(market_data_list)} markets, {day_signals} signals")

            current_date = next_date

    # Summary
    print(f"\n{'='*60}")
    print("BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"Period: {args.days} days")
    print(f"Markets scanned: {markets_scanned:,}")
    print(f"Signals generated: {signals_generated:,}")
    print(f"Avg signals/day: {signals_generated / args.days:.1f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
