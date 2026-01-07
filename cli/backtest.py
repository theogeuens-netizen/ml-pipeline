"""
Backtest a strategy against historical data with full P&L simulation.

Uses BigQuery by default for efficient server-side filtering and aggregation.
Use --use-postgres for legacy PostgreSQL mode (loads all data to memory).

Usage:
    # Simple backtest (BigQuery - default)
    python -m cli.backtest --side NO --days 60

    # Filter by price range (bet NO when YES > 0.55)
    python -m cli.backtest --side NO --yes-min 0.55 --yes-max 0.95

    # Filter by category and time window
    python -m cli.backtest --side NO --category Crypto --hours-min 12 --hours-max 48

    # With robustness checks
    python -m cli.backtest --side NO --robustness

    # Show data statistics
    python -m cli.backtest --stats

    # Legacy PostgreSQL mode (slower, loads all data to memory)
    python -m cli.backtest --use-postgres --side NO --days 30
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_bigquery_backtest(args):
    """Run backtest using BigQuery (default, efficient)."""
    try:
        from src.backtest.bigquery import (
            run_bq_backtest,
            run_bq_robustness,
            get_bq_data_stats,
            format_bq_backtest_summary,
            format_bq_robustness_summary,
        )
    except ImportError as e:
        print(f"Error: BigQuery dependencies not available: {e}")
        print("Install with: pip install google-cloud-bigquery")
        sys.exit(1)

    # Show data stats if requested
    if args.stats:
        print("\n" + "=" * 60)
        print("BIGQUERY HISTORICAL DATA STATISTICS")
        print("=" * 60)

        stats = get_bq_data_stats()

        print(f"\nMarkets:")
        print(f"  Total:    {stats['total_markets']:,}")
        print(f"  Resolved: {stats['resolved_markets']:,}")
        print(f"\nResolution Stats:")
        print(f"  YES wins: {stats['yes_wins']:,}")
        print(f"  NO wins:  {stats['no_wins']:,}")
        print(f"  NO rate:  {stats['no_win_rate']:.1%}")
        print(f"\nCategories:")
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count:,}")
        print("=" * 60 + "\n")
        return

    # Parse categories
    categories = [args.category] if args.category else None

    print("\n" + "=" * 60)
    print("BIGQUERY BACKTEST")
    print("=" * 60)
    print(f"\nParameters:")
    print(f"  Side: {args.side}")
    print(f"  YES price range: {args.yes_min} - {args.yes_max}")
    print(f"  Time window: {args.hours_min}h - {args.hours_max}h before close")
    if args.min_volume:
        print(f"  Min volume: ${args.min_volume:,.0f}")
    if categories:
        print(f"  Categories: {categories}")
    print()

    if args.robustness:
        # Run with robustness checks
        print("Running backtest with robustness checks...")
        result = run_bq_robustness(
            side=args.side,
            yes_price_min=args.yes_min,
            yes_price_max=args.yes_max,
            hours_min=args.hours_min,
            hours_max=args.hours_max,
            min_volume=args.min_volume,
            categories=categories,
            run_time_split=True,
            run_volume_split=True,
            run_category_split=args.category_split,
        )
        print(format_bq_robustness_summary(result))
    else:
        # Run simple backtest
        print("Running backtest...")
        metrics = run_bq_backtest(
            side=args.side,
            yes_price_min=args.yes_min,
            yes_price_max=args.yes_max,
            hours_min=args.hours_min,
            hours_max=args.hours_max,
            min_volume=args.min_volume,
            categories=categories,
        )
        name = f"{args.side} ({args.yes_min}-{args.yes_max})"
        print(format_bq_backtest_summary(metrics, name))


def run_postgres_backtest(args):
    """Run backtest using PostgreSQL (legacy mode)."""
    try:
        from src.db.database import get_session
        from src.backtest import (
            BacktestConfig,
            run_backtest,
            run_backtest_with_lockup,
            format_backtest_summary,
            load_resolved_markets,
            load_price_snapshots,
            generate_bets_from_snapshots,
            get_historical_stats,
        )
    except ImportError as e:
        print(f"Error: PostgreSQL dependencies not available: {e}")
        sys.exit(1)

    # Show stats if requested
    if args.stats:
        with get_session() as db:
            stats = get_historical_stats(db)

        print("\n" + "=" * 60)
        print("POSTGRESQL HISTORICAL DATA STATISTICS")
        print("=" * 60)
        print(f"\nMarkets:")
        print(f"  Total:    {stats['total_markets']:,}")
        print(f"  Resolved: {stats['resolved_markets']:,}")
        print(f"\nPrice Snapshots: {stats['price_snapshots']:,}")
        print(f"\nDate Range:")
        if stats['date_range']['min']:
            print(f"  From: {stats['date_range']['min']}")
            print(f"  To:   {stats['date_range']['max']}")
        print(f"\nCategories:")
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count:,}")
        print("=" * 60 + "\n")
        return

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    print(f"\nPostgreSQL mode (legacy)")
    print(f"Backtest period: {start_date.date()} to {end_date.date()}")
    print(f"Loading historical markets...")

    categories = [args.category] if args.category else None

    # Load historical data
    with get_session() as db:
        markets = load_resolved_markets(
            db=db,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            min_volume=args.min_volume,
            limit=args.limit,
        )

        if not markets:
            print("\nNo resolved markets found in date range.")
            sys.exit(0)

        print(f"Found {len(markets):,} resolved markets")

        market_ids = [m.id for m in markets]
        snapshots = load_price_snapshots(
            db=db,
            market_ids=market_ids,
            start_date=start_date,
            end_date=end_date,
        )

        print(f"Loaded {len(snapshots):,} price snapshots")

    # Generate bets
    print(f"\nGenerating bets (side={args.side})...")
    hours_before = (args.hours_min + args.hours_max) / 2 if args.hours_min else None

    bets = list(generate_bets_from_snapshots(
        markets=markets,
        snapshots=snapshots,
        side=args.side,
        hours_before_close=hours_before,
    ))

    if not bets:
        print("No valid betting opportunities generated.")
        sys.exit(0)

    print(f"Generated {len(bets):,} betting opportunities")

    # Create config
    config = BacktestConfig(
        initial_capital=args.capital,
        stake_per_bet=args.stake,
        stake_mode=args.stake_mode,
        start_date=start_date,
        end_date=end_date,
        categories=categories,
        min_volume=args.min_volume,
        cost_per_bet=args.cost,
        max_position_pct=args.max_position,
    )

    # Run backtest
    print(f"\nRunning backtest...")
    if args.lockup:
        result = run_backtest_with_lockup(bets, config)
    else:
        result = run_backtest(bets, config)

    # Print results
    name = f"{args.category or 'All'} {args.side}"
    summary = format_backtest_summary(result, name)
    print("\n" + summary)


def main():
    parser = argparse.ArgumentParser(
        description="Backtest against historical data (BigQuery default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simple NO bias backtest
  python -m cli.backtest --side NO

  # Test YES > 0.55 hypothesis
  python -m cli.backtest --side NO --yes-min 0.55 --yes-max 0.95

  # With robustness checks
  python -m cli.backtest --side NO --yes-min 0.60 --robustness

  # Category-specific
  python -m cli.backtest --side NO --category Crypto --hours-min 12

  # Show data stats
  python -m cli.backtest --stats
        """
    )

    # Backend selection
    parser.add_argument(
        "--use-postgres",
        action="store_true",
        help="Use PostgreSQL instead of BigQuery (legacy mode, slower)"
    )

    # Common arguments
    parser.add_argument(
        "--side",
        choices=["YES", "NO"],
        default="NO",
        help="Side to bet on (default: NO)"
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Filter by macro category (e.g., Crypto, Sports)"
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        help="Minimum market volume in USD"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show historical data statistics and exit"
    )

    # BigQuery-specific arguments
    bq_group = parser.add_argument_group("BigQuery options (default)")
    bq_group.add_argument(
        "--yes-min",
        type=float,
        default=0.0,
        help="Minimum YES price to include (default: 0.0)"
    )
    bq_group.add_argument(
        "--yes-max",
        type=float,
        default=1.0,
        help="Maximum YES price to include (default: 1.0)"
    )
    bq_group.add_argument(
        "--hours-min",
        type=float,
        default=0,
        help="Minimum hours before close to enter (default: 0)"
    )
    bq_group.add_argument(
        "--hours-max",
        type=float,
        default=168,
        help="Maximum hours before close to enter (default: 168)"
    )
    bq_group.add_argument(
        "--robustness",
        action="store_true",
        help="Run robustness checks (time split, volume split)"
    )
    bq_group.add_argument(
        "--category-split",
        action="store_true",
        help="Include category split in robustness checks"
    )

    # PostgreSQL-specific arguments
    pg_group = parser.add_argument_group("PostgreSQL options (--use-postgres)")
    pg_group.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to backtest (default: 30)"
    )
    pg_group.add_argument(
        "--capital",
        type=float,
        default=1000.0,
        help="Initial capital in USD (default: 1000)"
    )
    pg_group.add_argument(
        "--stake",
        type=float,
        default=10.0,
        help="Stake per bet in USD (default: 10)"
    )
    pg_group.add_argument(
        "--stake-mode",
        choices=["fixed", "fixed_pct", "kelly", "half_kelly"],
        default="fixed",
        help="Stake sizing mode (default: fixed)"
    )
    pg_group.add_argument(
        "--lockup",
        action="store_true",
        help="Use capital lockup simulation"
    )
    pg_group.add_argument(
        "--cost",
        type=float,
        default=0.0,
        help="Cost per bet in USD"
    )
    pg_group.add_argument(
        "--max-position",
        type=float,
        default=0.25,
        help="Max position as fraction of capital"
    )
    pg_group.add_argument(
        "--limit",
        type=int,
        help="Limit number of markets"
    )

    args = parser.parse_args()

    # Route to appropriate backend
    if args.use_postgres:
        run_postgres_backtest(args)
    else:
        run_bigquery_backtest(args)


if __name__ == "__main__":
    main()
