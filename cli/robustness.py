"""
Robustness testing CLI for experiment backtests.

Runs time-split, category-split, and liquidity-split validation
to detect overfitting and ensure edge generalizes.

Usage:
    python -m cli.robustness experiments/exp-001/config.yaml --time-split
    python -m cli.robustness experiments/exp-001/config.yaml --liquidity-split
    python -m cli.robustness experiments/exp-001/config.yaml --all
    python -m cli.robustness --strategy esports_no_1h --days 30 --all
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


def main():
    parser = argparse.ArgumentParser(
        description="Run robustness checks on backtest results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From experiment config
  python -m cli.robustness experiments/exp-001/config.yaml --all

  # From strategy name
  python -m cli.robustness --strategy esports_no_1h --days 30 --all

  # Specific checks only
  python -m cli.robustness experiments/exp-001/config.yaml --time-split --liquidity-split
        """,
    )

    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "config",
        nargs="?",
        help="Path to experiment config.yaml",
    )
    input_group.add_argument(
        "--strategy",
        help="Strategy name (from strategies.yaml)",
    )

    # Backtest parameters (when using --strategy)
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to backtest (default: 30)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1000.0,
        help="Initial capital in USD (default: 1000)",
    )
    parser.add_argument(
        "--category",
        help="Filter by macro category",
    )
    parser.add_argument(
        "--side",
        choices=["YES", "NO"],
        default="NO",
        help="Side to bet on (default: NO)",
    )

    # Robustness check options
    parser.add_argument(
        "--time-split",
        action="store_true",
        help="Run time split check (first half vs second half)",
    )
    parser.add_argument(
        "--liquidity-split",
        action="store_true",
        help="Run liquidity split check (high vs low volume)",
    )
    parser.add_argument(
        "--category-split",
        action="store_true",
        help="Run category split check (per macro_category)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all robustness checks",
    )

    # Output options
    parser.add_argument(
        "--output", "-o",
        help="Output file for JSON results",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=10,
        help="Minimum trades required per split (default: 10)",
    )

    args = parser.parse_args()

    # Validate input
    if not args.config and not args.strategy:
        parser.error("Either config file or --strategy is required")

    if not (args.time_split or args.liquidity_split or args.category_split or args.all):
        parser.error("At least one check required: --time-split, --liquidity-split, --category-split, or --all")

    # Import dependencies
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
        print(f"Error: Dependencies not available: {e}")
        print("Run from within Docker or install dependencies")
        sys.exit(1)

    # Load bets based on input source
    if args.config:
        bets, config = load_bets_from_config(args.config, args)
    else:
        bets, config = load_bets_from_strategy(args)

    if not bets:
        print("No betting opportunities generated.")
        sys.exit(0)

    print(f"Loaded {len(bets)} betting opportunities")

    # Determine which checks to run
    run_time = args.all or args.time_split
    run_liquidity = args.all or args.liquidity_split
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


def load_bets_from_config(config_path: str, args) -> tuple:
    """Load bets from an experiment config.yaml file."""
    from src.db.database import get_session
    from src.backtest import (
        BacktestConfig,
        load_resolved_markets,
        load_price_snapshots,
        generate_bets_from_snapshots,
    )

    config_file = Path(config_path)
    if not config_file.exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    with open(config_file) as f:
        config_data = yaml.safe_load(f)

    # Extract backtest config
    bt = config_data.get("backtest", {})
    filters = config_data.get("filters", {})
    strategy = config_data.get("strategy", {})

    config = BacktestConfig(
        initial_capital=bt.get("initial_capital", args.capital),
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
        initial_capital=args.capital,
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
    """Convert RobustnessResult to JSON-serializable dict."""
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


if __name__ == "__main__":
    main()
