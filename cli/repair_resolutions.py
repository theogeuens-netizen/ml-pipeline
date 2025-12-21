"""
One-time repair script to fix historical markets with missing resolution outcomes.

This script:
1. Finds markets that are past end_date but have outcome=UNKNOWN or NULL
2. Queries Gamma API to determine actual outcome
3. Updates the outcome field

Usage:
    python -m cli.repair_resolutions          # Dry run (shows what would be fixed)
    python -m cli.repair_resolutions --apply  # Actually apply fixes
"""

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config.settings import get_settings
from src.fetchers.gamma import SyncGammaClient, GammaClient


def get_session():
    """Create a database session."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    return Session()


def determine_outcome(market_data: dict) -> str:
    """
    Determine market outcome from Gamma API response.

    Returns: YES, NO, INVALID, PENDING, or UNKNOWN
    """
    if market_data is None:
        return "UNKNOWN"

    if not (market_data.get("resolved", False) or market_data.get("closed", False)):
        return "PENDING"

    yes_price, no_price = GammaClient.parse_outcome_prices(market_data)

    if yes_price > 0.95:
        return "YES"
    elif no_price > 0.95:
        return "NO"
    elif yes_price < 0.05 and no_price < 0.05:
        return "INVALID"
    else:
        return "PENDING"


def main():
    parser = argparse.ArgumentParser(
        description="Repair historical markets with missing resolution outcomes"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply fixes (default is dry run)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum markets to process (default: 1000)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for API calls (default: 50)"
    )
    args = parser.parse_args()

    session = get_session()
    client = SyncGammaClient()

    try:
        # Find markets needing repair
        # Include both resolved with UNKNOWN and unresolved past end_date
        query = text("""
            SELECT id, condition_id, slug, resolved, outcome, end_date
            FROM markets
            WHERE end_date < NOW() - INTERVAL '1 hour'
              AND (outcome IS NULL OR outcome = 'UNKNOWN' OR (resolved = false))
            ORDER BY end_date DESC
            LIMIT :limit
        """)

        result = session.execute(query, {"limit": args.limit})
        markets = [(row.id, row.condition_id, row.slug, row.resolved, row.outcome) for row in result]

        print(f"\n{'='*60}")
        print(" RESOLUTION REPAIR")
        print(f" Mode: {'APPLY' if args.apply else 'DRY RUN'}")
        print(f" Markets to check: {len(markets)}")
        print(f"{'='*60}\n")

        if not markets:
            print("No markets need repair.")
            return

        # Track outcomes
        outcomes = {"YES": 0, "NO": 0, "INVALID": 0, "PENDING": 0, "UNKNOWN": 0, "API_ERROR": 0}
        fixed = 0

        for i, (market_id, condition_id, slug, was_resolved, old_outcome) in enumerate(markets):
            # Rate limiting
            if i > 0 and i % args.batch_size == 0:
                print(f"  Processed {i}/{len(markets)}...")
                time.sleep(1)  # Avoid rate limiting

            try:
                market_data = client.get_market(condition_id)
                outcome = determine_outcome(market_data)
            except Exception as e:
                print(f"  API error for {slug}: {e}")
                outcomes["API_ERROR"] += 1
                continue

            outcomes[outcome] += 1

            # Only fix if we have a definitive outcome
            if outcome in ("YES", "NO", "INVALID"):
                if args.apply:
                    update_query = text("""
                        UPDATE markets
                        SET resolved = true,
                            outcome = :outcome,
                            resolved_at = COALESCE(resolved_at, NOW()),
                            active = false
                        WHERE id = :market_id
                    """)
                    session.execute(update_query, {"outcome": outcome, "market_id": market_id})
                    fixed += 1

                    if fixed % 100 == 0:
                        session.commit()
                        print(f"  Committed {fixed} fixes...")

                print(f"  {slug}: {old_outcome or 'NULL'} -> {outcome}")

        if args.apply:
            session.commit()

        print(f"\n{'='*60}")
        print(" RESULTS")
        print(f"{'='*60}")
        print(f"Total checked: {len(markets)}")
        print(f"\nOutcome distribution:")
        for outcome, count in sorted(outcomes.items(), key=lambda x: -x[1]):
            pct = 100 * count / len(markets) if len(markets) > 0 else 0
            print(f"  {outcome}: {count} ({pct:.1f}%)")

        if args.apply:
            print(f"\nFixed: {fixed} markets")
        else:
            fixable = outcomes["YES"] + outcomes["NO"] + outcomes["INVALID"]
            print(f"\nFixable: {fixable} markets")
            print("\nRun with --apply to fix these markets.")

    finally:
        client.close()
        session.close()


if __name__ == "__main__":
    main()
