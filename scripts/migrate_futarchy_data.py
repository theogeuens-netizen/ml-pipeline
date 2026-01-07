#!/usr/bin/env python3
"""
Migrate historical data from futarchy's PostgreSQL to polymarket-ml.

This is a one-time migration script that:
1. Connects to futarchy's PostgreSQL database
2. Streams markets in batches (5000 at a time)
3. Migrates them to polymarket-ml's historical_markets table
4. Migrates associated price_snapshots to historical_price_snapshots

Prerequisites:
    1. Start futarchy's PostgreSQL:
       cd /home/theo/futarchy && docker-compose up -d postgres

    2. Ensure polymarket-ml database is running:
       cd /home/theo/polymarket-ml && docker-compose up -d postgres

    3. Run the migration for historical tables:
       alembic upgrade head

Usage:
    python scripts/migrate_futarchy_data.py

    # Resume from a specific market ID (if previous run was interrupted)
    python scripts/migrate_futarchy_data.py --resume-from 50000

    # Limit number of markets (for testing)
    python scripts/migrate_futarchy_data.py --limit 1000

    # Skip snapshots (markets only)
    python scripts/migrate_futarchy_data.py --markets-only
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Futarchy database connection
FUTARCHY_DB = {
    "host": "localhost",
    "port": 5434,
    "database": "futarchy",
    "user": "futarchy",
    "password": "futarchy",
}

# Polymarket-ml database connection
POLYMARKET_DB = {
    "host": "localhost",
    "port": 5433,
    "database": "polymarket_ml",
    "user": "postgres",
    "password": "postgres",
}

BATCH_SIZE = 5000
SNAPSHOT_BATCH_SIZE = 10000


def connect_futarchy():
    """Connect to futarchy's PostgreSQL database."""
    try:
        conn = psycopg2.connect(**FUTARCHY_DB)
        logger.info("Connected to futarchy database")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Failed to connect to futarchy database: {e}")
        logger.error("Make sure futarchy's PostgreSQL is running:")
        logger.error("  cd /home/theo/futarchy && docker-compose up -d postgres")
        sys.exit(1)


def connect_polymarket():
    """Connect to polymarket-ml's PostgreSQL database."""
    try:
        conn = psycopg2.connect(**POLYMARKET_DB)
        logger.info("Connected to polymarket-ml database")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Failed to connect to polymarket-ml database: {e}")
        logger.error("Make sure polymarket-ml's PostgreSQL is running:")
        logger.error("  cd /home/theo/polymarket-ml && docker-compose up -d postgres")
        sys.exit(1)


def get_futarchy_market_count(fut_conn) -> int:
    """Get total Polymarket market count from futarchy (excludes Kalshi)."""
    with fut_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM markets WHERE platform_id = 1")
        return cur.fetchone()[0]


def get_futarchy_markets(fut_conn, offset: int = 0, limit: int = BATCH_SIZE):
    """Fetch a batch of markets from futarchy (Polymarket only, excludes Kalshi)."""
    query = """
        SELECT
            id,
            external_id,
            question,
            description,
            close_date,
            macro_category,
            micro_category,
            volume,
            liquidity,
            resolution_status,
            resolved_at,
            winner,
            resolved_early,
            platform_id
        FROM markets
        WHERE platform_id = 1  -- Polymarket only (excludes Kalshi=2, Metaculus=3)
        ORDER BY id
        OFFSET %s
        LIMIT %s
    """
    with fut_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, (offset, limit))
        return cur.fetchall()


def get_futarchy_snapshots(fut_conn, market_ids: list[int]):
    """Fetch price snapshots for the given market IDs."""
    if not market_ids:
        return []

    query = """
        SELECT
            id,
            market_id,
            timestamp,
            price,
            open_price,
            high_price,
            low_price,
            bid_price,
            ask_price,
            volume
        FROM price_snapshots
        WHERE market_id = ANY(%s)
        ORDER BY market_id, timestamp
    """
    with fut_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, (market_ids,))
        return cur.fetchall()


def insert_historical_markets(poly_conn, markets: list[dict]) -> dict[int, int]:
    """
    Insert markets into historical_markets table.

    Returns:
        Mapping of old market IDs to new market IDs
    """
    if not markets:
        return {}

    id_mapping = {}

    insert_query = """
        INSERT INTO historical_markets (
            external_id,
            question,
            description,
            close_date,
            macro_category,
            micro_category,
            volume,
            liquidity,
            resolution_status,
            resolved_at,
            winner,
            resolved_early,
            platform
        ) VALUES (
            %(external_id)s,
            %(question)s,
            %(description)s,
            %(close_date)s,
            %(macro_category)s,
            %(micro_category)s,
            %(volume)s,
            %(liquidity)s,
            %(resolution_status)s,
            %(resolved_at)s,
            %(winner)s,
            %(resolved_early)s,
            %(platform)s
        )
        ON CONFLICT (external_id) DO UPDATE SET
            question = EXCLUDED.question,
            resolution_status = EXCLUDED.resolution_status,
            winner = EXCLUDED.winner,
            resolved_at = EXCLUDED.resolved_at
        RETURNING id
    """

    with poly_conn.cursor() as cur:
        for market in markets:
            # Map platform_id to platform name
            platform_map = {
                1: "polymarket",
                2: "kalshi",
                3: "metaculus",
            }
            platform = platform_map.get(market.get("platform_id"), "polymarket")

            params = {
                "external_id": market["external_id"],
                "question": market.get("question"),
                "description": market.get("description"),
                "close_date": market.get("close_date"),
                "macro_category": market.get("macro_category"),
                "micro_category": market.get("micro_category"),
                "volume": market.get("volume"),
                "liquidity": market.get("liquidity"),
                "resolution_status": market.get("resolution_status"),
                "resolved_at": market.get("resolved_at"),
                "winner": market.get("winner"),
                "resolved_early": market.get("resolved_early"),
                "platform": platform,
            }

            try:
                cur.execute(insert_query, params)
                new_id = cur.fetchone()[0]
                id_mapping[market["id"]] = new_id
            except Exception as e:
                logger.warning(f"Failed to insert market {market['id']}: {e}")
                continue

    poly_conn.commit()
    return id_mapping


def insert_historical_snapshots(poly_conn, snapshots: list[dict], id_mapping: dict[int, int]):
    """Insert price snapshots into historical_price_snapshots table."""
    if not snapshots:
        return 0

    insert_query = """
        INSERT INTO historical_price_snapshots (
            market_id,
            timestamp,
            price,
            open_price,
            high_price,
            low_price,
            bid_price,
            ask_price,
            volume
        ) VALUES (
            %(market_id)s,
            %(timestamp)s,
            %(price)s,
            %(open_price)s,
            %(high_price)s,
            %(low_price)s,
            %(bid_price)s,
            %(ask_price)s,
            %(volume)s
        )
        ON CONFLICT DO NOTHING
    """

    inserted = 0
    with poly_conn.cursor() as cur:
        for snap in snapshots:
            old_market_id = snap["market_id"]
            new_market_id = id_mapping.get(old_market_id)

            if not new_market_id:
                continue

            params = {
                "market_id": new_market_id,
                "timestamp": snap["timestamp"],
                "price": snap.get("price"),
                "open_price": snap.get("open_price"),
                "high_price": snap.get("high_price"),
                "low_price": snap.get("low_price"),
                "bid_price": snap.get("bid_price"),
                "ask_price": snap.get("ask_price"),
                "volume": snap.get("volume"),
            }

            try:
                cur.execute(insert_query, params)
                inserted += 1
            except Exception as e:
                logger.debug(f"Failed to insert snapshot: {e}")
                continue

    poly_conn.commit()
    return inserted


def migrate_data(
    resume_from: int = 0,
    limit: int | None = None,
    markets_only: bool = False,
):
    """Main migration function."""
    logger.info("Starting futarchy -> polymarket-ml data migration")

    # Connect to both databases
    fut_conn = connect_futarchy()
    poly_conn = connect_polymarket()

    try:
        # Get total count
        total_markets = get_futarchy_market_count(fut_conn)
        logger.info(f"Total markets in futarchy: {total_markets:,}")

        if limit:
            total_markets = min(total_markets, limit + resume_from)
            logger.info(f"Limiting to {limit:,} markets")

        # Track progress
        markets_migrated = 0
        snapshots_migrated = 0
        offset = resume_from

        while offset < total_markets:
            batch_limit = min(BATCH_SIZE, total_markets - offset)
            if limit:
                batch_limit = min(batch_limit, limit - markets_migrated)

            if batch_limit <= 0:
                break

            logger.info(f"Processing markets {offset:,} - {offset + batch_limit:,}")

            # Fetch markets
            markets = get_futarchy_markets(fut_conn, offset, batch_limit)

            if not markets:
                break

            # Insert markets
            id_mapping = insert_historical_markets(poly_conn, markets)
            markets_migrated += len(id_mapping)

            # Fetch and insert snapshots
            if not markets_only and id_mapping:
                old_market_ids = list(id_mapping.keys())
                snapshots = get_futarchy_snapshots(fut_conn, old_market_ids)
                inserted = insert_historical_snapshots(poly_conn, snapshots, id_mapping)
                snapshots_migrated += inserted
                logger.info(f"  Migrated {len(id_mapping)} markets, {inserted:,} snapshots")
            else:
                logger.info(f"  Migrated {len(id_mapping)} markets")

            offset += len(markets)

            # Progress update
            pct = (offset / total_markets) * 100
            logger.info(f"Progress: {pct:.1f}% ({offset:,}/{total_markets:,})")

        # Final summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("MIGRATION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Markets migrated: {markets_migrated:,}")
        if not markets_only:
            logger.info(f"Snapshots migrated: {snapshots_migrated:,}")
        logger.info("=" * 60)

    finally:
        fut_conn.close()
        poly_conn.close()


def verify_migration():
    """Verify the migration by comparing counts."""
    logger.info("Verifying migration...")

    fut_conn = connect_futarchy()
    poly_conn = connect_polymarket()

    try:
        # Futarchy counts
        with fut_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM markets")
            fut_markets = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM markets WHERE resolution_status = 'resolved'")
            fut_resolved = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM price_snapshots")
            fut_snapshots = cur.fetchone()[0]

        # Polymarket-ml counts
        with poly_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM historical_markets")
            poly_markets = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM historical_markets WHERE resolution_status = 'resolved'")
            poly_resolved = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM historical_price_snapshots")
            poly_snapshots = cur.fetchone()[0]

        logger.info("")
        logger.info("=" * 60)
        logger.info("VERIFICATION RESULTS")
        logger.info("=" * 60)
        logger.info(f"Markets:          {fut_markets:>10,} (futarchy) -> {poly_markets:>10,} (polymarket-ml)")
        logger.info(f"Resolved:         {fut_resolved:>10,} (futarchy) -> {poly_resolved:>10,} (polymarket-ml)")
        logger.info(f"Price Snapshots:  {fut_snapshots:>10,} (futarchy) -> {poly_snapshots:>10,} (polymarket-ml)")
        logger.info("=" * 60)

        if poly_markets == fut_markets:
            logger.info("All markets migrated successfully!")
        else:
            diff = fut_markets - poly_markets
            logger.warning(f"Missing {diff:,} markets - some may have been skipped due to conflicts")

    finally:
        fut_conn.close()
        poly_conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate historical data from futarchy to polymarket-ml"
    )
    parser.add_argument(
        "--resume-from",
        type=int,
        default=0,
        help="Resume from this market offset (default: 0)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of markets to migrate"
    )
    parser.add_argument(
        "--markets-only",
        action="store_true",
        help="Only migrate markets, skip price snapshots"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify migration counts instead of migrating"
    )
    args = parser.parse_args()

    if args.verify:
        verify_migration()
    else:
        migrate_data(
            resume_from=args.resume_from,
            limit=args.limit,
            markets_only=args.markets_only,
        )


if __name__ == "__main__":
    main()
