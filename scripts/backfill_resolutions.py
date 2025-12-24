#!/usr/bin/env python3
"""
One-time backfill to capture resolutions for existing markets.

This script fetches resolution data for all historical markets that
haven't been resolved yet. Run after deploying the gamma_id migration.

Usage:
    docker-compose exec api python scripts/backfill_resolutions.py
"""

import sys
import time
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, "/home/theo/polymarket-ml")

from sqlalchemy import select

from src.db.database import get_session
from src.db.models import Market
from src.fetchers.gamma import SyncGammaClient
from src.tasks.discovery import derive_outcome


def backfill_resolutions():
    """Backfill resolution data for all historical markets."""
    client = SyncGammaClient()
    now = datetime.now(timezone.utc)
    total_checked = 0
    total_resolved = 0
    total_gamma_ids_updated = 0

    try:
        with get_session() as session:
            # Get ALL unresolved markets past end_date (no time limit for backfill)
            markets = session.execute(
                select(Market).where(
                    Market.resolved == False,
                    Market.outcome == None,
                    Market.end_date < now,
                ).order_by(Market.end_date.desc())
            ).scalars().all()

            print(f"Found {len(markets)} markets to check")

            for i, market in enumerate(markets):
                # Use gamma_id if available, else slug
                market_data = None
                if market.gamma_id:
                    market_data = client.get_market_by_id(market.gamma_id)
                if market_data is None and market.slug:
                    market_data = client.get_market_by_slug(market.slug)

                total_checked += 1

                if market_data is None:
                    # Market removed from API
                    market.outcome = "UNKNOWN"
                    market.resolved = True
                    market.resolved_at = now
                    market.active = False
                    total_resolved += 1
                else:
                    uma_status = market_data.get("umaResolutionStatus")
                    if uma_status == "resolved":
                        outcome = derive_outcome(market_data.get("outcomePrices"))
                        if outcome:
                            market.outcome = outcome
                            market.resolved = True
                            market.resolved_at = now
                            market.active = False
                            total_resolved += 1
                        else:
                            # UMA resolved but prices indeterminate (0.5/0.5)
                            market.outcome = "UNKNOWN"
                            market.resolved = True
                            market.resolved_at = now
                            market.active = False
                            total_resolved += 1

                    # Update gamma_id if missing
                    if market.gamma_id is None and market_data.get("id"):
                        market.gamma_id = int(market_data.get("id"))
                        total_gamma_ids_updated += 1

                    # Update lifecycle fields
                    market.closed = market_data.get("closed", False)
                    market.accepting_orders = market_data.get("acceptingOrders", True)
                    if uma_status != market.uma_resolution_status:
                        market.uma_resolution_status = uma_status
                        market.uma_status_updated_at = now

                # Progress + commit every 50 with longer delay
                if (i + 1) % 50 == 0:
                    session.commit()
                    print(f"Progress: {i + 1}/{len(markets)}, resolved: {total_resolved}, gamma_ids: {total_gamma_ids_updated}")
                    # Longer delay to avoid rate limiting
                    time.sleep(2.0)
                else:
                    # Small delay between each request
                    time.sleep(0.1)

            session.commit()

        print(f"\nBackfill complete:")
        print(f"  Markets checked: {total_checked}")
        print(f"  Markets resolved: {total_resolved}")
        print(f"  Gamma IDs updated: {total_gamma_ids_updated}")

    finally:
        client.close()


if __name__ == "__main__":
    backfill_resolutions()
