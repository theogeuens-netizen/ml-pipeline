"""
CLI Debug Tool - diagnose why strategies aren't trading.

Usage:
    python -m cli.debug                     # Show leaderboard
    python -m cli.debug esports_no_1h       # Debug specific strategy
    python -m cli.debug --funnel            # Run funnel analysis on all strategies
"""

import argparse
import sys
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config.settings import get_settings
from strategies.loader import load_strategies, get_strategy_by_name
from strategies.performance import PerformanceTracker, format_metrics_table


def get_session():
    """Create a database session."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    return Session()


def show_leaderboard(session, sort_by="total_pnl", limit=25):
    """Display strategy leaderboard."""
    tracker = PerformanceTracker(session)
    metrics_list = tracker.get_leaderboard(sort_by=sort_by, limit=limit)

    print(format_metrics_table(metrics_list))


def debug_strategy(session, strategy_name: str):
    """Debug a specific strategy."""
    # Get the strategy
    strategy = get_strategy_by_name(strategy_name)
    if not strategy:
        print(f"Strategy '{strategy_name}' not found in strategies.yaml")
        return

    print(f"\n{'='*60}")
    print(f" STRATEGY: {strategy_name}")
    print(f" Type: {type(strategy).__name__}")
    print(f" Version: {strategy.version}")
    print(f" SHA: {strategy.get_sha()}")
    print(f"{'='*60}\n")

    # Show parameters
    print("PARAMETERS:")
    print("-" * 40)
    for k in dir(strategy):
        if k.startswith("_"):
            continue
        if k in ("name", "version", "logger", "scan", "filter", "get_sha",
                 "get_params", "should_exit", "on_signal_executed",
                 "on_position_closed", "get_debug_stats"):
            continue
        v = getattr(strategy, k, None)
        if callable(v):
            continue
        print(f"  {k}: {v}")
    print()

    # Get performance tracker info
    tracker = PerformanceTracker(session)
    debug_info = tracker.get_debug_info(strategy_name)

    # Show last 24h decision stats
    print("LAST 24 HOURS:")
    print("-" * 40)
    stats = debug_info.get("last_24h", {})
    print(f"  Total decisions: {stats.get('total_decisions', 0)}")
    print(f"  Executed: {stats.get('executed', 0)}")
    print(f"  Rejected: {stats.get('rejected', 0)}")
    print()

    # Show recent decisions
    print("RECENT DECISIONS:")
    print("-" * 40)
    for d in debug_info.get("recent_decisions", [])[:5]:
        status = "EXECUTED" if d["executed"] else f"REJECTED: {d.get('rejected_reason', '?')}"
        print(f"  {d['timestamp'][:19]} | market={d['market_id']} | {d['signal_side']} | {status}")

    if not debug_info.get("recent_decisions"):
        print("  (no recent decisions)")
    print()

    # Get balance info
    metrics = tracker.get_strategy_metrics(strategy_name)
    if metrics:
        print("PERFORMANCE:")
        print("-" * 40)
        print(f"  Allocated: ${metrics.allocated_usd:,.2f}")
        print(f"  Current: ${metrics.current_usd:,.2f}")
        print(f"  Total P&L: ${metrics.total_pnl:+,.2f} ({metrics.total_return_pct:+.1f}%)")
        print(f"  Trades: {metrics.trade_count} (W:{metrics.win_count} L:{metrics.loss_count})")
        print(f"  Win Rate: {metrics.win_rate*100:.0f}%")
        if metrics.sharpe_ratio is not None:
            print(f"  Sharpe: {metrics.sharpe_ratio:+.2f}")
        print(f"  Max Drawdown: {metrics.max_drawdown_pct:.1f}%")
        print(f"  Open Positions: {metrics.open_positions}")
    else:
        print("PERFORMANCE: No balance record found")
    print()


def run_funnel_analysis(session):
    """Run funnel analysis on all strategies with current market data."""
    from src.db.models import Market, Snapshot
    from strategies.base import MarketData
    from sqlalchemy import func

    print("\n" + "="*80)
    print(" FUNNEL ANALYSIS - Why strategies aren't finding opportunities")
    print("="*80 + "\n")

    # Get active markets with recent snapshots
    result = session.execute(text("""
        SELECT
            m.id,
            m.condition_id,
            m.question,
            m.yes_token_id,
            m.no_token_id,
            m.end_date,
            m.liquidity,
            m.category_l1,
            m.category_l2,
            m.category_l3,
            s.last_price as price,
            s.best_bid,
            s.best_ask,
            EXTRACT(EPOCH FROM (m.end_date - NOW())) / 3600 as hours_to_close
        FROM markets m
        JOIN LATERAL (
            SELECT last_price, best_bid, best_ask
            FROM snapshots
            WHERE market_id = m.id
            ORDER BY timestamp DESC
            LIMIT 1
        ) s ON true
        WHERE m.active = true
        AND m.end_date > NOW()
        LIMIT 5000
    """))

    markets_data = []
    for row in result:
        md = MarketData(
            id=row.id,
            condition_id=row.condition_id,
            question=row.question,
            yes_token_id=row.yes_token_id,
            no_token_id=row.no_token_id,
            price=float(row.price) if row.price else 0.5,
            best_bid=float(row.best_bid) if row.best_bid else None,
            best_ask=float(row.best_ask) if row.best_ask else None,
            hours_to_close=float(row.hours_to_close) if row.hours_to_close else None,
            liquidity=float(row.liquidity) if row.liquidity else None,
            category_l1=row.category_l1,
            category_l2=row.category_l2,
            category_l3=row.category_l3,
        )
        markets_data.append(md)

    print(f"Loaded {len(markets_data)} active markets\n")

    # Run each strategy's debug stats
    strategies = load_strategies()

    for s in strategies:
        if hasattr(s, "get_debug_stats"):
            try:
                stats = s.get_debug_stats(markets_data)
                print(f"{s.name}:")
                if "funnel" in stats:
                    print(f"  {stats['funnel']}")
                else:
                    for k, v in stats.items():
                        print(f"  {k}: {v}")
            except Exception as e:
                print(f"{s.name}: ERROR - {e}")
        else:
            # Count signals generated
            try:
                signals = list(s.scan(markets_data))
                print(f"{s.name}: {len(signals)} signals from {len(markets_data)} markets")
            except Exception as e:
                print(f"{s.name}: ERROR - {e}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Debug strategies - find out why they aren't trading"
    )
    parser.add_argument(
        "strategy",
        nargs="?",
        help="Strategy name to debug (omit for leaderboard)"
    )
    parser.add_argument(
        "--funnel",
        action="store_true",
        help="Run funnel analysis on all strategies"
    )
    parser.add_argument(
        "--sort",
        default="total_pnl",
        choices=["total_pnl", "sharpe_ratio", "win_rate", "total_return_pct"],
        help="Sort leaderboard by metric"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Limit leaderboard rows"
    )

    args = parser.parse_args()

    session = get_session()

    try:
        if args.funnel:
            run_funnel_analysis(session)
        elif args.strategy:
            debug_strategy(session, args.strategy)
        else:
            show_leaderboard(session, sort_by=args.sort, limit=args.limit)
    finally:
        session.close()


if __name__ == "__main__":
    main()
