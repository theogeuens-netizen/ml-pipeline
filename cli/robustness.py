"""
Robustness testing CLI for experiment backtests.

Uses BigQuery by default for efficient server-side filtering and aggregation.
Use --use-postgres for legacy PostgreSQL mode (loads all data to memory).

Runs time-split, volume-split, and category-split validation
to detect overfitting and ensure edge generalizes.

Usage:
    # Simple robustness checks (BigQuery - default)
    python -m cli.robustness --side NO --yes-min 0.55 --all

    # Specific checks only
    python -m cli.robustness --side NO --time-split --volume-split

    # Category-specific with robustness
    python -m cli.robustness --side NO --category Crypto --all

    # From experiment config (BigQuery)
    python -m cli.robustness experiments/exp-001/config.yaml --all

    # Legacy PostgreSQL mode (slower, loads all data to memory)
    python -m cli.robustness --use-postgres --strategy esports_no_1h --days 30 --all
"""

import argparse
import json
import sys
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_bigquery_robustness(args):
    """Run robustness checks using BigQuery (default, efficient)."""
    try:
        from src.backtest.bigquery import (
            run_bq_robustness,
            format_bq_robustness_summary,
        )
    except ImportError as e:
        print(f"Error: BigQuery dependencies not available: {e}")
        print("Install with: pip install google-cloud-bigquery")
        sys.exit(1)

    # Determine which checks to run
    run_time = args.all or args.time_split
    run_volume = args.all or args.volume_split
    run_category = args.all or args.category_split

    # Parse categories
    categories = [args.category] if args.category else None

    print("\n" + "=" * 60)
    print("BIGQUERY ROBUSTNESS CHECKS")
    print("=" * 60)
    print(f"\nParameters:")
    print(f"  Side: {args.side}")
    print(f"  YES price range: {args.yes_min} - {args.yes_max}")
    print(f"  Time window: {args.hours_min}h - {args.hours_max}h before close")
    if args.min_volume:
        print(f"  Min volume: ${args.min_volume:,.0f}")
    if categories:
        print(f"  Categories: {categories}")
    print(f"\nChecks:")
    print(f"  Time split: {run_time}")
    print(f"  Volume split: {run_volume}")
    print(f"  Category split: {run_category}")
    print()

    # Run robustness checks
    print("Running robustness checks...")
    result = run_bq_robustness(
        side=args.side,
        yes_price_min=args.yes_min,
        yes_price_max=args.yes_max,
        hours_min=args.hours_min,
        hours_max=args.hours_max,
        min_volume=args.min_volume,
        categories=categories,
        run_time_split=run_time,
        run_volume_split=run_volume,
        run_category_split=run_category,
    )

    # Display results
    print(format_bq_robustness_summary(result))

    # Save JSON output if requested
    if args.output:
        output_data = bq_robustness_result_to_dict(result)
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nResults saved to: {args.output}")

    # Return exit code based on pass/fail
    sys.exit(0 if result.overall_passed else 1)


def run_bigquery_from_config(args):
    """Run BigQuery robustness from experiment config.yaml."""
    try:
        from src.backtest.bigquery import (
            run_bq_robustness,
            format_bq_robustness_summary,
        )
    except ImportError as e:
        print(f"Error: BigQuery dependencies not available: {e}")
        print("Install with: pip install google-cloud-bigquery")
        sys.exit(1)

    config_file = Path(args.config)
    if not config_file.exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    with open(config_file) as f:
        config_data = yaml.safe_load(f)

    # Extract filters from config
    filters = config_data.get("filters", {})
    strategy = config_data.get("strategy", {})

    side = strategy.get("side", args.side)
    yes_min = filters.get("yes_price_min", args.yes_min)
    yes_max = filters.get("yes_price_max", args.yes_max)
    hours_min = filters.get("hours_min", args.hours_min)
    hours_max = filters.get("hours_max", args.hours_max)
    min_volume = filters.get("min_volume_24h", args.min_volume)
    categories = filters.get("categories")

    # Determine which checks to run
    run_time = args.all or args.time_split
    run_volume = args.all or args.volume_split
    run_category = args.all or args.category_split

    print("\n" + "=" * 60)
    print(f"BIGQUERY ROBUSTNESS: {config_file.parent.name}")
    print("=" * 60)
    print(f"\nParameters from config:")
    print(f"  Side: {side}")
    print(f"  YES price range: {yes_min} - {yes_max}")
    print(f"  Time window: {hours_min}h - {hours_max}h before close")
    if min_volume:
        print(f"  Min volume: ${min_volume:,.0f}")
    if categories:
        print(f"  Categories: {categories}")
    print()

    # Run robustness checks
    print("Running robustness checks...")
    result = run_bq_robustness(
        side=side,
        yes_price_min=yes_min,
        yes_price_max=yes_max,
        hours_min=hours_min,
        hours_max=hours_max,
        min_volume=min_volume,
        categories=categories if isinstance(categories, list) else [categories] if categories else None,
        run_time_split=run_time,
        run_volume_split=run_volume,
        run_category_split=run_category,
    )

    # Display results
    print(format_bq_robustness_summary(result))

    # Save JSON output if requested
    if args.output:
        output_data = bq_robustness_result_to_dict(result)
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nResults saved to: {args.output}")

    sys.exit(0 if result.overall_passed else 1)


def bq_robustness_result_to_dict(result) -> Dict[str, Any]:
    """Convert BigQuery RobustnessResult to JSON-serializable dict."""
    output = {
        "overall_passed": result.overall_passed,
        "baseline": {
            "total_trades": result.baseline.total_trades,
            "win_rate": result.baseline.win_rate,
            "sharpe": result.baseline.sharpe,
            "profit_factor": result.baseline.profit_factor,
            "total_pnl": result.baseline.total_pnl,
        },
    }

    if result.time_split:
        output["time_split"] = {
            "passed": result.time_split.passed,
            "first_half": vars(result.time_split.first_half),
            "second_half": vars(result.time_split.second_half),
            "notes": result.time_split.notes,
        }

    if result.volume_split:
        output["volume_split"] = {
            "passed": result.volume_split.passed,
            "high_volume": vars(result.volume_split.high_volume),
            "low_volume": vars(result.volume_split.low_volume),
            "notes": result.volume_split.notes,
        }

    if result.category_split:
        output["category_split"] = {
            "passed": result.category_split.passed,
            "by_category": {k: vars(v) for k, v in result.category_split.by_category.items()},
            "notes": result.category_split.notes,
        }

    return output


def run_postgres_robustness(args):
    """Run robustness checks using PostgreSQL (legacy mode)."""
    try:
        from src.db.database import get_session
        from src.backtest import (
            BacktestConfig,
            run_all_robustness_checks,
            format_robustness_results,
            load_resolved_markets,
            load_price_snapshots,
            generate_bets_from_snapshots,
        )
    except ImportError as e:
        print(f"Error: PostgreSQL dependencies not available: {e}")
        sys.exit(1)

    print(f"\nPostgreSQL mode (legacy)")

    # Load bets based on input source
    if args.config:
        bets, config = load_bets_from_config(args)
    elif args.strategy:
        bets, config = load_bets_from_strategy(args)
    else:
        print("Error: --use-postgres requires --strategy or config file")
        sys.exit(1)

    if not bets:
        print("No betting opportunities generated.")
        sys.exit(0)

    print(f"Loaded {len(bets)} betting opportunities")

    # Determine which checks to run
    run_time = args.all or args.time_split
    run_liquidity = args.all or args.volume_split  # volume_split maps to liquidity_split
    run_category = args.all or args.category_split

    # Run robustness checks
    print("\nRunning robustness checks...")
    result = run_all_robustness_checks(
        bets=bets,
        config=config,
        run_time_split=run_time,
        run_liquidity_split=run_liquidity,
        run_category_split=run_category,
        min_trades_per_split=args.min_trades,
    )

    # Display results
    print("\n" + format_robustness_results(result))

    # Save JSON output if requested
    if args.output:
        output_data = robustness_result_to_dict(result)
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nResults saved to: {args.output}")

    # Return exit code based on pass/fail
    sys.exit(0 if result.overall_passed else 1)


def load_bets_from_config(args) -> tuple:
    """Load bets from an experiment config.yaml file."""
    from src.db.database import get_session
    from src.backtest import (
        BacktestConfig,
        load_resolved_markets,
        load_price_snapshots,
        generate_bets_from_snapshots,
    )

    config_file = Path(args.config)
    if not config_file.exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    with open(config_file) as f:
        config_data = yaml.safe_load(f)

    # Extract backtest config
    bt = config_data.get("backtest", {})
    filters = config_data.get("filters", {})
    strategy = config_data.get("strategy", {})

    config = BacktestConfig(
        initial_capital=bt.get("initial_capital", 1000.0),
        stake_per_bet=bt.get("stake_per_bet", 10.0),
        stake_mode=bt.get("stake_mode", "fixed"),
        cost_per_bet=bt.get("cost_per_bet", 0.0),
        max_position_pct=bt.get("max_position_pct", 0.25),
        categories=filters.get("categories"),
        min_volume=filters.get("min_volume_24h"),
    )

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    # Load historical data
    with get_session() as db:
        markets = load_resolved_markets(
            db=db,
            start_date=start_date,
            end_date=end_date,
            categories=config.categories,
            min_volume=config.min_volume,
        )

        if not markets:
            print("No resolved markets found.")
            return [], config

        market_ids = [m.id for m in markets]
        snapshots = load_price_snapshots(
            db=db,
            market_ids=market_ids,
            start_date=start_date,
            end_date=end_date,
        )

    # Generate bets
    side = strategy.get("side", args.side)
    bets = list(generate_bets_from_snapshots(
        markets=markets,
        snapshots=snapshots,
        side=side,
    ))

    return bets, config


def load_bets_from_strategy(args) -> tuple:
    """Load bets from a strategy name."""
    from src.db.database import get_session
    from src.backtest import (
        BacktestConfig,
        load_resolved_markets,
        load_price_snapshots,
        generate_bets_from_snapshots,
    )

    # Try to load strategy config
    strategy_name = args.strategy
    category = args.category
    side = args.side

    try:
        from strategies.loader import get_strategy_by_name, get_strategy_config

        strategy = get_strategy_by_name(strategy_name)
        if strategy:
            params = strategy.get_params()

            # Get category from strategy if not overridden
            if not category and "category" in params:
                category = params["category"]

            # Get side from strategy type
            strategy_config = get_strategy_config(strategy_name)
            if strategy_config:
                stype = strategy_config.get("type", "")
                if "no" in stype.lower() or "no" in strategy_name.lower():
                    side = "NO"
                elif "yes" in stype.lower() or "yes" in strategy_name.lower():
                    side = "YES"

            print(f"Strategy: {strategy.name}")
            print(f"Category: {category or 'all'}")
            print(f"Side: {side}")
    except Exception as e:
        print(f"Warning: Could not load strategy: {e}")

    config = BacktestConfig(
        initial_capital=1000.0,
        stake_per_bet=10.0,
        stake_mode="fixed",
        categories=[category] if category else None,
    )

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    # Load historical data
    with get_session() as db:
        markets = load_resolved_markets(
            db=db,
            start_date=start_date,
            end_date=end_date,
            categories=config.categories,
        )

        if not markets:
            print("No resolved markets found.")
            return [], config

        print(f"Found {len(markets)} resolved markets")

        market_ids = [m.id for m in markets]
        snapshots = load_price_snapshots(
            db=db,
            market_ids=market_ids,
            start_date=start_date,
            end_date=end_date,
        )

    # Generate bets
    bets = list(generate_bets_from_snapshots(
        markets=markets,
        snapshots=snapshots,
        side=side,
    ))

    return bets, config


def robustness_result_to_dict(result) -> Dict[str, Any]:
    """Convert PostgreSQL RobustnessResult to JSON-serializable dict."""
    output = {
        "overall_passed": result.overall_passed,
        "pass_rate": result.pass_rate,
        "summary": result.summary,
    }

    if result.time_split:
        ts = result.time_split
        output["time_split"] = {
            "passed": ts.passed,
            "first_half": {
                "sharpe": ts.first_half.sharpe,
                "win_rate": ts.first_half.win_rate,
                "trades": ts.first_half.trades,
                "total_pnl": ts.first_half.total_pnl,
            },
            "second_half": {
                "sharpe": ts.second_half.sharpe,
                "win_rate": ts.second_half.win_rate,
                "trades": ts.second_half.trades,
                "total_pnl": ts.second_half.total_pnl,
            },
            "notes": ts.notes,
        }

    if result.liquidity_split:
        ls = result.liquidity_split
        output["liquidity_split"] = {
            "passed": ls.passed,
            "high_liquidity": {
                "sharpe": ls.first_half.sharpe,
                "win_rate": ls.first_half.win_rate,
                "trades": ls.first_half.trades,
                "total_pnl": ls.first_half.total_pnl,
            },
            "low_liquidity": {
                "sharpe": ls.second_half.sharpe,
                "win_rate": ls.second_half.win_rate,
                "trades": ls.second_half.trades,
                "total_pnl": ls.second_half.total_pnl,
            },
            "notes": ls.notes,
        }

    if result.category_split:
        cs = result.category_split
        by_cat = {}
        for cat, metrics in cs.by_category.items():
            by_cat[cat] = {
                "sharpe": metrics.sharpe,
                "win_rate": metrics.win_rate,
                "trades": metrics.trades,
                "total_pnl": metrics.total_pnl,
            }
        output["category_split"] = {
            "passed": cs.passed,
            "by_category": by_cat,
            "categories_with_edge": cs.categories_with_edge,
            "total_categories": cs.total_categories,
            "notes": cs.notes,
        }

    return output


def main():
    parser = argparse.ArgumentParser(
        description="Run robustness checks (BigQuery default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simple robustness checks (BigQuery)
  python -m cli.robustness --side NO --yes-min 0.55 --all

  # From experiment config
  python -m cli.robustness experiments/exp-001/config.yaml --all

  # Category-specific
  python -m cli.robustness --side NO --category Crypto --all

  # Legacy PostgreSQL mode
  python -m cli.robustness --use-postgres --strategy esports_no_1h --days 30 --all
        """,
    )

    # Backend selection
    parser.add_argument(
        "--use-postgres",
        action="store_true",
        help="Use PostgreSQL instead of BigQuery (legacy mode, slower)"
    )

    # Input source
    parser.add_argument(
        "config",
        nargs="?",
        help="Path to experiment config.yaml",
    )
    parser.add_argument(
        "--strategy",
        help="Strategy name - for PostgreSQL mode (from strategies.yaml)",
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

    # Robustness check options
    check_group = parser.add_argument_group("Robustness checks")
    check_group.add_argument(
        "--time-split",
        action="store_true",
        help="Run time split check (first half vs second half)"
    )
    check_group.add_argument(
        "--volume-split",
        action="store_true",
        help="Run volume split check (high vs low volume markets)"
    )
    check_group.add_argument(
        "--category-split",
        action="store_true",
        help="Run category split check (per macro_category)"
    )
    check_group.add_argument(
        "--all",
        action="store_true",
        help="Run all robustness checks"
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
        "--min-trades",
        type=int,
        default=10,
        help="Minimum trades required per split (default: 10)"
    )

    # Output options
    parser.add_argument(
        "--output", "-o",
        help="Output file for JSON results"
    )

    args = parser.parse_args()

    # Validate: at least one check must be specified
    if not (args.time_split or args.volume_split or args.category_split or args.all):
        parser.error("At least one check required: --time-split, --volume-split, --category-split, or --all")

    # Route to appropriate backend
    if args.use_postgres:
        if not args.config and not args.strategy:
            parser.error("--use-postgres requires --strategy or config file")
        run_postgres_robustness(args)
    elif args.config:
        # Config file provided, use BigQuery from config
        run_bigquery_from_config(args)
    else:
        # Direct BigQuery mode
        run_bigquery_robustness(args)


if __name__ == "__main__":
    main()
