"""
Backfill lifecycle fields for all existing markets.

This script fetches market data from the Gamma API and populates:
- closed
- closed_at
- accepting_orders
- accepting_orders_updated_at
- uma_resolution_status
- uma_status_updated_at

Usage:
    python -m cli.backfill_lifecycle          # Dry run (shows what would be updated)
    python -m cli.backfill_lifecycle --apply  # Actually apply updates
    python -m cli.backfill_lifecycle --apply --batch-size 100  # Custom batch size
"""

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.config.settings import get_settings
from src.db.models import Market
from src.fetchers.gamma import SyncGammaClient, GammaClient


def get_session():
    """Create a database session."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    return Session()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill lifecycle fields for all markets from Gamma API"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply updates (default is dry run)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of markets to fetch per API call (default: 100)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between API batches in seconds (default: 0.5)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum markets to process (default: all)"
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only backfill active markets"
    )
    parser.add_argument(
        "--closed-only",
        action="store_true",
        help="Only backfill closed/resolved markets"
    )
    args = parser.parse_args()

    session = get_session()
    client = SyncGammaClient()

    try:
        # Get all markets from database
        print(f"\n{'='*60}")
        print(" LIFECYCLE BACKFILL")
        print(f" Mode: {'APPLY' if args.apply else 'DRY RUN'}")
        print(f"{'='*60}\n")

        # Query markets based on filters
        query = select(Market)
        if args.active_only:
            query = query.where(Market.active == True, Market.resolved == False)
        elif args.closed_only:
            query = query.where((Market.active == False) | (Market.resolved == True))

        if args.limit:
            query = query.limit(args.limit)

        result = session.execute(query)
        markets = result.scalars().all()
        print(f"Found {len(markets)} markets to process\n")

        if not markets:
            print("No markets to process.")
            return

        # Build lookup map by condition_id
        market_map = {m.condition_id: m for m in markets}
        condition_ids = list(market_map.keys())

        # Fetch from Gamma API in batches
        # First, get all active markets
        print("Fetching active markets from Gamma API...")
        api_markets = []
        offset = 0

        while True:
            batch = client.get_markets(active=True, closed=False, limit=100, offset=offset)
            if not batch:
                break
            api_markets.extend(batch)
            offset += 100
            if len(batch) < 100:
                break
            time.sleep(args.delay)
            print(f"  Fetched {len(api_markets)} active markets...")

        print(f"  Total active markets: {len(api_markets)}")

        # Also fetch closed markets
        print("\nFetching closed markets from Gamma API...")
        closed_markets = client.get_closed_markets(limit=500, days_back=30)
        api_markets.extend(closed_markets)
        print(f"  Total closed markets: {len(closed_markets)}")

        # Build API lookup
        api_map = {m.get("conditionId"): m for m in api_markets}
        print(f"\nTotal API markets fetched: {len(api_map)}")

        # Track stats
        updated = 0
        not_in_api = 0
        unchanged = 0
        status_counts = Counter()
        uma_status_counts = Counter()

        # Process each market
        print("\nProcessing markets...\n")
        for i, condition_id in enumerate(condition_ids):
            if i > 0 and i % 1000 == 0:
                print(f"  Processed {i}/{len(condition_ids)}...")
                if args.apply:
                    session.commit()

            market = market_map[condition_id]
            api_data = api_map.get(condition_id)

            if api_data is None:
                not_in_api += 1
                continue

            # Extract lifecycle fields
            new_closed = api_data.get("closed", False)
            new_accepting = api_data.get("acceptingOrders", True)
            new_uma_status = api_data.get("umaResolutionStatus")
            closed_time = GammaClient.parse_datetime(api_data.get("closedTime"))
            accepting_time = GammaClient.parse_datetime(api_data.get("acceptingOrdersTimestamp"))

            # Track stats
            if new_closed:
                status_counts["closed"] += 1
            elif new_accepting:
                status_counts["trading"] += 1
            else:
                status_counts["suspended"] += 1

            if new_uma_status:
                uma_status_counts[new_uma_status] += 1
            else:
                uma_status_counts["none"] += 1

            # Check if update needed
            needs_update = False
            changes = []

            if market.closed != new_closed:
                changes.append(f"closed: {market.closed} -> {new_closed}")
                needs_update = True
            if market.accepting_orders != new_accepting:
                changes.append(f"accepting_orders: {market.accepting_orders} -> {new_accepting}")
                needs_update = True
            if market.uma_resolution_status != new_uma_status:
                changes.append(f"uma_status: {market.uma_resolution_status} -> {new_uma_status}")
                needs_update = True

            if not needs_update:
                unchanged += 1
                continue

            updated += 1

            if args.apply:
                now = datetime.now(timezone.utc)

                # Update closed
                if new_closed and not market.closed:
                    market.closed_at = closed_time or now
                market.closed = new_closed

                # Update accepting_orders
                if new_accepting != market.accepting_orders:
                    market.accepting_orders_updated_at = accepting_time or now
                market.accepting_orders = new_accepting

                # Update UMA status
                if new_uma_status != market.uma_resolution_status:
                    market.uma_status_updated_at = now
                market.uma_resolution_status = new_uma_status

            # Print sample updates
            if updated <= 20:
                print(f"  {market.slug[:50]}:")
                for change in changes:
                    print(f"    - {change}")

        if args.apply:
            session.commit()

        # Print summary
        print(f"\n{'='*60}")
        print(" RESULTS")
        print(f"{'='*60}")
        print(f"\nProcessed: {len(condition_ids)}")
        print(f"Not in API: {not_in_api}")
        print(f"Unchanged: {unchanged}")
        print(f"Updated: {updated}")

        print(f"\nTrading Status Distribution:")
        for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
            pct = 100 * count / len(api_map) if api_map else 0
            print(f"  {status}: {count} ({pct:.1f}%)")

        print(f"\nUMA Resolution Status Distribution:")
        for status, count in sorted(uma_status_counts.items(), key=lambda x: -x[1]):
            pct = 100 * count / len(api_map) if api_map else 0
            print(f"  {status}: {count} ({pct:.1f}%)")

        if not args.apply:
            print(f"\nThis was a DRY RUN. Run with --apply to update the database.")

    finally:
        client.close()
        session.close()


if __name__ == "__main__":
    main()
