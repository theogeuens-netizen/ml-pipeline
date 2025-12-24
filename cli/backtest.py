"""
Backtest a strategy against historical data with full P&L simulation.

Usage:
    # By strategy name (from strategies.yaml)
    python -m cli.backtest esports_no_1h --days 30 --capital 1000

    # With stake mode
    python -m cli.backtest longshot_yes_1h --stake-mode kelly --capital 5000

    # Filter by category
    python -m cli.backtest --category Crypto --side NO --days 60

    # With capital lockup (realistic simulation)
    python -m cli.backtest politics_no_24h --lockup

This runs the backtest engine against historical market data and reports
full P&L metrics including Sharpe ratio, max drawdown, and win rate.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(
        description="Backtest against historical data with P&L simulation"
    )
    parser.add_argument(
        "strategy",
        nargs="?",
        help="Strategy name (from strategies.yaml) or 'all' for category-based backtest"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to backtest (default: 30)"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1000.0,
        help="Initial capital in USD (default: 1000)"
    )
    parser.add_argument(
        "--stake",
        type=float,
        default=10.0,
        help="Stake per bet in USD for fixed mode (default: 10)"
    )
    parser.add_argument(
        "--stake-mode",
        choices=["fixed", "fixed_pct", "kelly", "half_kelly"],
        default="fixed",
        help="Stake sizing mode (default: fixed)"
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Filter by macro category (e.g., Crypto, Sports, Politics)"
    )
    parser.add_argument(
        "--side",
        choices=["YES", "NO"],
        default="NO",
        help="Side to bet on (default: NO)"
    )
    parser.add_argument(
        "--hours-before",
        type=float,
        help="Hours before market close to enter (default: last available price)"
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        help="Minimum market volume filter"
    )
    parser.add_argument(
        "--lockup",
        action="store_true",
        help="Use capital lockup simulation (realistic mode)"
    )
    parser.add_argument(
        "--cost",
        type=float,
        default=0.0,
        help="Cost per bet in USD (trading fee)"
    )
    parser.add_argument(
        "--max-position",
        type=float,
        default=0.25,
        help="Max position as fraction of capital (default: 0.25)"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show historical data statistics and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load strategy/data but don't run backtest"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of markets to process"
    )
    args = parser.parse_args()

    # Import database dependencies
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
        print(f"Error: Dependencies not available: {e}")
        print("Run from within Docker or install dependencies")
        sys.exit(1)

    # Show historical data stats if requested
    if args.stats:
        with get_session() as db:
            stats = get_historical_stats(db)

        print("\n" + "=" * 60)
        print("HISTORICAL DATA STATISTICS")
        print("=" * 60)
        print(f"\nMarkets:")
        print(f"  Total:    {stats['total_markets']:,}")
        print(f"  Resolved: {stats['resolved_markets']:,}")
        print(f"\nPrice Snapshots: {stats['price_snapshots']:,}")
        print(f"\nDate Range:")
        if stats['date_range']['min']:
            print(f"  From: {stats['date_range']['min']}")
            print(f"  To:   {stats['date_range']['max']}")
        else:
            print("  No data available")
        print(f"\nCategories:")
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count:,}")
        print("=" * 60 + "\n")
        return

    # Determine backtest parameters
    strategy_name = None
    category = args.category
    side = args.side
    hours_before = args.hours_before

    # If strategy name provided, try to load it for parameters
    if args.strategy and args.strategy != "all":
        try:
            from strategies.loader import get_strategy_by_name, get_strategy_config

            strategy = get_strategy_by_name(args.strategy)
            if strategy:
                strategy_name = strategy.name

                # Extract parameters from strategy
                params = strategy.get_params()

                # Get category from strategy if not overridden
                if not category and "category" in params:
                    category = params["category"]

                # Get side from strategy type (NO strategies bet NO, etc.)
                strategy_config = get_strategy_config(args.strategy)
                if strategy_config:
                    stype = strategy_config.get("type", "")
                    if "no" in stype.lower() or "no" in args.strategy.lower():
                        side = "NO"
                    elif "yes" in stype.lower() or "yes" in args.strategy.lower():
                        side = "YES"

                # Get hours from strategy params
                if not hours_before:
                    min_hours = params.get("min_hours", 1)
                    max_hours = params.get("max_hours", 24)
                    # Use midpoint for entry timing
                    hours_before = (min_hours + max_hours) / 2

                print(f"\n{'='*60}")
                print(f"Strategy: {strategy.name} v{strategy.version}")
                print(f"SHA: {strategy.get_sha()}")
                print(f"Type: {strategy_config.get('type', 'unknown') if strategy_config else 'unknown'}")
                print(f"\nParameters:")
                for key, value in params.items():
                    print(f"  {key}: {value}")
                print(f"\nBacktest Settings:")
                print(f"  Category: {category or 'all'}")
                print(f"  Side: {side}")
                print(f"  Entry: {hours_before:.1f}h before close" if hours_before else "  Entry: last available price")
                print(f"{'='*60}\n")
            else:
                print(f"Warning: Strategy '{args.strategy}' not found in strategies.yaml")
                print("Running category-based backtest instead\n")
        except Exception as e:
            print(f"Warning: Could not load strategy: {e}")
            print("Running category-based backtest instead\n")

    if args.dry_run:
        print("Dry run mode - configuration validated")
        return

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    print(f"Backtest period: {start_date.date()} to {end_date.date()}")
    print(f"Loading historical markets...")

    # Load historical data
    with get_session() as db:
        # Load resolved markets
        categories = [category] if category else None

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
            print("Run 'python -m cli.backtest --stats' to see available data.")
            sys.exit(0)

        print(f"Found {len(markets):,} resolved markets")

        # Load price snapshots
        market_ids = [m.id for m in markets]
        snapshots = load_price_snapshots(
            db=db,
            market_ids=market_ids,
            start_date=start_date,
            end_date=end_date,
        )

        print(f"Loaded {len(snapshots):,} price snapshots")

    # Generate betting opportunities
    print(f"\nGenerating bets (side={side})...")
    bets = list(generate_bets_from_snapshots(
        markets=markets,
        snapshots=snapshots,
        side=side,
        hours_before_close=hours_before,
    ))

    if not bets:
        print("No valid betting opportunities generated.")
        print("Markets may lack price snapshots or have invalid prices.")
        sys.exit(0)

    print(f"Generated {len(bets):,} betting opportunities")

    # Create backtest config
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
    print(f"\nRunning backtest ({'with lockup' if args.lockup else 'simple'} mode)...")
    if args.lockup:
        result = run_backtest_with_lockup(bets, config)
    else:
        result = run_backtest(bets, config)

    # Format and print results
    name_for_display = strategy_name or f"{category or 'All'} {side}"
    summary = format_backtest_summary(result, name_for_display)
    print("\n" + summary)

    # Additional category breakdown if no specific category
    if not category and result.trades:
        print("\nPERFORMANCE BY CATEGORY")
        print("-" * 40)

        # Group trades by category
        by_category: dict[str, list] = {}
        for trade in result.trades:
            cat = trade.macro_category or "Unknown"
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(trade)

        # Calculate per-category metrics
        for cat, trades in sorted(by_category.items(), key=lambda x: -sum(t.pnl for t in x[1])):
            total_pnl = sum(t.pnl for t in trades)
            wins = sum(1 for t in trades if t.won)
            win_rate = wins / len(trades) * 100 if trades else 0
            sign = "+" if total_pnl >= 0 else ""
            print(f"  {cat}: {len(trades)} trades, {win_rate:.0f}% win, {sign}${total_pnl:.2f}")

        print()

    # Print example trades (first 5)
    if result.trades and len(result.trades) > 0:
        print("SAMPLE TRADES (first 5)")
        print("-" * 40)
        for trade in result.trades[:5]:
            outcome = "WIN" if trade.won else "LOSS"
            print(f"  [{trade.resolution_ts.strftime('%Y-%m-%d')}] "
                  f"{trade.side} @ {trade.entry_price:.3f} -> {outcome} "
                  f"(${trade.pnl:+.2f})")
        if len(result.trades) > 5:
            print(f"  ... and {len(result.trades) - 5} more trades")
        print()


if __name__ == "__main__":
    main()
