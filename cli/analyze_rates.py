"""
CLI Tool - Analyze actual resolution rates vs strategy assumptions.

Usage:
    python -m cli.analyze_rates                # Show resolution rates by category
    python -m cli.analyze_rates --detailed     # Include time bucket breakdown
    python -m cli.analyze_rates --category CRYPTO  # Filter to specific category

Validates strategy edge assumptions against actual market data.
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import get_settings


def get_session():
    """Create a database session."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    return Session()


def load_assumed_rates() -> dict[str, float]:
    """
    Load assumed NO rates from strategies.yaml.

    Returns:
        Dict mapping category to assumed NO rate
    """
    strategies_path = Path(__file__).parent.parent / "strategies.yaml"

    if not strategies_path.exists():
        print(f"Warning: strategies.yaml not found at {strategies_path}")
        return {}

    with open(strategies_path) as f:
        config = yaml.safe_load(f)

    assumed_rates = {}

    # Extract rates from no_bias strategies
    no_bias_strategies = config.get("no_bias", [])
    for strategy in no_bias_strategies:
        category = strategy.get("category")
        rate = strategy.get("historical_no_rate")
        if category and rate:
            # Use first occurrence (they should be the same per category)
            if category not in assumed_rates:
                assumed_rates[category] = rate

    # Also check new_market strategy for base rate
    new_market = config.get("new_market", [])
    for strategy in new_market:
        rate = strategy.get("assumed_no_rate")
        if rate:
            assumed_rates["_NEW_MARKET_BASE"] = rate

    return assumed_rates


def get_resolution_rates(session, category: Optional[str] = None, days: int = 90) -> list[dict]:
    """
    Query actual resolution rates by category.

    Args:
        session: Database session
        category: Optional category filter
        days: Lookback period in days

    Returns:
        List of dicts with category, total, no_count, yes_count, no_rate
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    category_filter = ""
    if category:
        category_filter = f"AND category_l1 = '{category}'"

    query = text(f"""
        SELECT
            category_l1 as category,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome = 'NO') as no_count,
            COUNT(*) FILTER (WHERE outcome = 'YES') as yes_count,
            COUNT(*) FILTER (WHERE outcome = 'INVALID' OR outcome = 'UNKNOWN') as other_count,
            CASE
                WHEN COUNT(*) > 0
                THEN ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'NO') / COUNT(*), 1)
                ELSE 0
            END as no_rate_pct
        FROM markets
        WHERE resolved = true
          AND category_l1 IS NOT NULL
          AND resolved_at > :cutoff
          {category_filter}
        GROUP BY category_l1
        ORDER BY total DESC
    """)

    result = session.execute(query, {"cutoff": cutoff})

    rows = []
    for row in result:
        rows.append({
            "category": row.category,
            "total": int(row.total),
            "no_count": int(row.no_count),
            "yes_count": int(row.yes_count),
            "other_count": int(row.other_count),
            "no_rate": float(row.no_rate_pct) / 100.0,  # Convert to decimal
        })

    return rows


def get_resolution_rates_by_time(session, category: Optional[str] = None, days: int = 90) -> list[dict]:
    """
    Query resolution rates by category AND time bucket.

    Time buckets:
    - under_1h: Markets with < 1 hour duration
    - 1h_to_1d: 1 hour to 1 day
    - 1d_to_7d: 1 day to 7 days
    - over_7d: More than 7 days
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    category_filter = ""
    if category:
        category_filter = f"AND category_l1 = '{category}'"

    query = text(f"""
        SELECT
            category_l1 as category,
            CASE
                WHEN (end_date - created_at) < interval '1 hour' THEN 'under_1h'
                WHEN (end_date - created_at) < interval '24 hours' THEN '1h_to_1d'
                WHEN (end_date - created_at) < interval '168 hours' THEN '1d_to_7d'
                ELSE 'over_7d'
            END as time_bucket,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE outcome = 'NO') as no_count,
            CASE
                WHEN COUNT(*) > 0
                THEN ROUND(100.0 * COUNT(*) FILTER (WHERE outcome = 'NO') / COUNT(*), 1)
                ELSE 0
            END as no_rate_pct
        FROM markets
        WHERE resolved = true
          AND category_l1 IS NOT NULL
          AND resolved_at > :cutoff
          {category_filter}
        GROUP BY category_l1, time_bucket
        ORDER BY category_l1, no_rate_pct DESC
    """)

    result = session.execute(query, {"cutoff": cutoff})

    rows = []
    for row in result:
        rows.append({
            "category": row.category,
            "time_bucket": row.time_bucket,
            "total": int(row.total),
            "no_count": int(row.no_count),
            "no_rate": float(row.no_rate_pct) / 100.0,
        })

    return rows


def get_overall_stats(session, days: int = 90) -> dict:
    """Get overall resolution statistics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = text("""
        SELECT
            COUNT(*) as total_resolved,
            COUNT(*) FILTER (WHERE outcome = 'NO') as no_count,
            COUNT(*) FILTER (WHERE outcome = 'YES') as yes_count,
            COUNT(*) FILTER (WHERE category_l1 IS NOT NULL) as categorized,
            COUNT(*) FILTER (WHERE category_l1 IS NULL) as uncategorized
        FROM markets
        WHERE resolved = true
          AND resolved_at > :cutoff
    """)

    result = session.execute(query, {"cutoff": cutoff}).fetchone()

    return {
        "total_resolved": result.total_resolved,
        "no_count": result.no_count,
        "yes_count": result.yes_count,
        "categorized": result.categorized,
        "uncategorized": result.uncategorized,
        "overall_no_rate": result.no_count / result.total_resolved if result.total_resolved > 0 else 0,
    }


def format_table(rates: list[dict], assumed_rates: dict[str, float]) -> str:
    """Format rates as a table with comparison to assumed rates."""
    if not rates:
        return "No resolved markets found with category_l1 set."

    # Header
    lines = []
    lines.append("")
    lines.append("=" * 85)
    lines.append(f"{'Category':<15} {'Total':>7} {'NO':>6} {'YES':>6} {'NO Rate':>9} {'Assumed':>9} {'Gap':>8} {'Status':<10}")
    lines.append("=" * 85)

    # Data rows
    for row in rates:
        category = row["category"]
        total = row["total"]
        no_count = row["no_count"]
        yes_count = row["yes_count"]
        no_rate = row["no_rate"]

        assumed = assumed_rates.get(category)

        if assumed is not None:
            gap = no_rate - assumed
            gap_str = f"{gap:+.1%}"

            # Status based on gap magnitude and sample size
            if total < 30:
                status = "LOW_N"
            elif abs(gap) > 0.10:
                status = "CRITICAL"
            elif abs(gap) > 0.05:
                status = "WARNING"
            else:
                status = "OK"
            assumed_str = f"{assumed:.1%}"
        else:
            gap_str = "N/A"
            status = "NO_STRAT"
            assumed_str = "-"

        lines.append(
            f"{category:<15} {total:>7} {no_count:>6} {yes_count:>6} "
            f"{no_rate:>8.1%} {assumed_str:>9} {gap_str:>8} {status:<10}"
        )

    lines.append("=" * 85)

    # Legend
    lines.append("")
    lines.append("Status Legend:")
    lines.append("  OK       - Actual rate within 5% of assumed")
    lines.append("  WARNING  - Gap between 5-10% (review strategy)")
    lines.append("  CRITICAL - Gap > 10% (disable strategy!)")
    lines.append("  LOW_N    - Insufficient data (<30 markets)")
    lines.append("  NO_STRAT - No strategy uses this category")
    lines.append("")

    return "\n".join(lines)


def format_detailed_table(rates: list[dict]) -> str:
    """Format rates by category and time bucket."""
    if not rates:
        return "No resolved markets found."

    lines = []
    lines.append("")
    lines.append("=" * 65)
    lines.append(f"{'Category':<15} {'Time Bucket':<12} {'Total':>7} {'NO':>6} {'NO Rate':>10}")
    lines.append("=" * 65)

    current_category = None
    for row in rates:
        if row["category"] != current_category:
            if current_category is not None:
                lines.append("-" * 65)
            current_category = row["category"]

        lines.append(
            f"{row['category']:<15} {row['time_bucket']:<12} "
            f"{row['total']:>7} {row['no_count']:>6} {row['no_rate']:>9.1%}"
        )

    lines.append("=" * 65)
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze actual resolution rates vs strategy assumptions"
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show breakdown by time bucket"
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Filter to specific category (e.g., CRYPTO, SPORTS)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback period in days (default: 90)"
    )
    args = parser.parse_args()

    session = get_session()

    try:
        # Load assumed rates from strategies.yaml
        assumed_rates = load_assumed_rates()

        # Get overall stats
        overall = get_overall_stats(session, args.days)

        print("\n" + "=" * 60)
        print(" RESOLUTION RATE ANALYSIS")
        print(f" Period: Last {args.days} days")
        print("=" * 60)

        print(f"\nOverall Statistics:")
        print(f"  Total resolved markets: {overall['total_resolved']:,}")
        print(f"  - Resolved NO:  {overall['no_count']:,} ({overall['overall_no_rate']:.1%})")
        print(f"  - Resolved YES: {overall['yes_count']:,} ({1 - overall['overall_no_rate']:.1%})")
        print(f"  - With category: {overall['categorized']:,}")
        print(f"  - Uncategorized: {overall['uncategorized']:,}")

        if overall['total_resolved'] == 0:
            print("\nNo resolved markets found. Data collection may have just started.")
            print("Check back after markets begin resolving (typically Jan-Feb 2025).")
            return

        # Check if outcomes are actually populated
        if overall['no_count'] == 0 and overall['yes_count'] == 0:
            print("\n" + "!" * 60)
            print(" WARNING: All resolved markets have outcome=UNKNOWN!")
            print("!" * 60)
            print("\nThis indicates CRITICAL-1 bug: check_resolutions() is marking")
            print("markets as resolved but NOT determining the actual outcome.")
            print("\nTo fix: Update src/tasks/discovery.py:check_resolutions()")
            print("to properly determine YES/NO outcomes from Gamma API.")
            print("\nQuerying outcome distribution...")

            # Show actual outcome values
            outcome_query = text("""
                SELECT outcome, COUNT(*) as count
                FROM markets
                WHERE resolved = true
                GROUP BY outcome
                ORDER BY count DESC
            """)
            outcome_result = session.execute(outcome_query)
            print("\nOutcome distribution:")
            for row in outcome_result:
                print(f"  {row.outcome or 'NULL'}: {row.count:,}")

            return

        # Get rates by category
        rates = get_resolution_rates(session, args.category, args.days)

        # Show main comparison table
        print(format_table(rates, assumed_rates))

        # Show strategies.yaml assumed rates for reference
        if assumed_rates:
            print("Assumed rates from strategies.yaml:")
            for cat, rate in sorted(assumed_rates.items()):
                if cat != "_NEW_MARKET_BASE":
                    print(f"  {cat}: {rate:.1%}")
            if "_NEW_MARKET_BASE" in assumed_rates:
                print(f"  (new_market base rate: {assumed_rates['_NEW_MARKET_BASE']:.1%})")
            print()

        # Show detailed breakdown if requested
        if args.detailed:
            print("\n" + "=" * 60)
            print(" BREAKDOWN BY TIME BUCKET")
            print("=" * 60)

            detailed = get_resolution_rates_by_time(session, args.category, args.days)
            print(format_detailed_table(detailed))

        # Warnings for critical gaps
        critical_gaps = []
        for row in rates:
            assumed = assumed_rates.get(row["category"])
            if assumed is not None and row["total"] >= 30:
                gap = row["no_rate"] - assumed
                if abs(gap) > 0.05:
                    critical_gaps.append((row["category"], gap, row["total"]))

        if critical_gaps:
            print("=" * 60)
            print(" ACTION REQUIRED")
            print("=" * 60)
            for cat, gap, n in critical_gaps:
                direction = "HIGHER" if gap > 0 else "LOWER"
                print(f"  {cat}: Actual NO rate is {abs(gap):.1%} {direction} than assumed (n={n})")
            print("\nRecommendation: Update strategies.yaml or disable affected strategies.")
            print()

    finally:
        session.close()


if __name__ == "__main__":
    main()
