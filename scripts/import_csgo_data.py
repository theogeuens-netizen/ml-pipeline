#!/usr/bin/env python3
"""
Import CS:GO team data from futarchy CSVs into polymarket-ml database.

This script imports:
1. Team leaderboard (win rates) from csgo_team_leaderboard.csv
2. Head-to-head records from csgo_h2h_matrix.csv

Prerequisites:
    1. Ensure polymarket-ml database is running:
       cd /home/theo/polymarket-ml && docker-compose up -d postgres

    2. Run the migration for CS:GO tables:
       alembic upgrade head

Usage:
    python scripts/import_csgo_data.py

    # Import only teams (skip H2H)
    python scripts/import_csgo_data.py --teams-only

    # Import only H2H (skip teams)
    python scripts/import_csgo_data.py --h2h-only

    # Verify data after import
    python scripts/import_csgo_data.py --verify
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# File paths
FUTARCHY_DIR = Path("/home/theo/futarchy")
TEAM_LEADERBOARD_CSV = FUTARCHY_DIR / "csgo_team_leaderboard.csv"
H2H_MATRIX_CSV = FUTARCHY_DIR / "csgo_h2h_matrix.csv"

# Polymarket-ml database connection
POLYMARKET_DB = {
    "host": "localhost",
    "port": 5433,
    "database": "polymarket_ml",
    "user": "postgres",
    "password": "postgres",
}


def connect_db():
    """Connect to polymarket-ml's PostgreSQL database."""
    try:
        conn = psycopg2.connect(**POLYMARKET_DB)
        logger.info("Connected to polymarket-ml database")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Failed to connect to database: {e}")
        logger.error("Make sure PostgreSQL is running:")
        logger.error("  cd /home/theo/polymarket-ml && docker-compose up -d postgres")
        sys.exit(1)


def import_team_leaderboard(conn) -> int:
    """
    Import team leaderboard from CSV.

    Upserts by team_name - existing teams are updated, new teams are inserted.

    Returns:
        Number of teams imported
    """
    if not TEAM_LEADERBOARD_CSV.exists():
        logger.error(f"Team leaderboard CSV not found: {TEAM_LEADERBOARD_CSV}")
        return 0

    logger.info(f"Reading team leaderboard from {TEAM_LEADERBOARD_CSV}")

    teams = []
    with open(TEAM_LEADERBOARD_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            teams.append((
                row["team"],
                int(row["wins"]),
                int(row["losses"]),
                int(row["total_matches"]),
                float(row["win_rate_pct"]),
            ))

    logger.info(f"Parsed {len(teams)} teams from CSV")

    # Upsert using ON CONFLICT
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO csgo_teams (team_name, wins, losses, total_matches, win_rate_pct, updated_at)
            VALUES %s
            ON CONFLICT (team_name) DO UPDATE SET
                wins = EXCLUDED.wins,
                losses = EXCLUDED.losses,
                total_matches = EXCLUDED.total_matches,
                win_rate_pct = EXCLUDED.win_rate_pct,
                updated_at = NOW()
            """,
            [(t[0], t[1], t[2], t[3], t[4], datetime.utcnow()) for t in teams],
            template="(%s, %s, %s, %s, %s, %s)",
        )
        conn.commit()

    logger.info(f"Upserted {len(teams)} teams into csgo_teams table")
    return len(teams)


def import_h2h_matrix(conn) -> int:
    """
    Import head-to-head records from CSV.

    Normalizes team pairs so team1 < team2 alphabetically.
    Upserts by (team1_name, team2_name) pair.

    Returns:
        Number of H2H records imported
    """
    if not H2H_MATRIX_CSV.exists():
        logger.error(f"H2H matrix CSV not found: {H2H_MATRIX_CSV}")
        return 0

    logger.info(f"Reading H2H matrix from {H2H_MATRIX_CSV}")

    h2h_records = []
    with open(H2H_MATRIX_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team1 = row["team1"]
            team2 = row["team2"]
            team1_wins = int(row["team1_wins"])
            team2_wins = int(row["team2_wins"])
            total = int(row["total_matches"])

            # Normalize: team1 < team2 alphabetically
            if team1 > team2:
                team1, team2 = team2, team1
                team1_wins, team2_wins = team2_wins, team1_wins

            h2h_records.append((team1, team2, team1_wins, team2_wins, total))

    logger.info(f"Parsed {len(h2h_records)} H2H records from CSV")

    # Upsert using ON CONFLICT
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO csgo_h2h (team1_name, team2_name, team1_wins, team2_wins, total_matches, updated_at)
            VALUES %s
            ON CONFLICT ON CONSTRAINT uq_csgo_h2h_teams DO UPDATE SET
                team1_wins = EXCLUDED.team1_wins,
                team2_wins = EXCLUDED.team2_wins,
                total_matches = EXCLUDED.total_matches,
                updated_at = NOW()
            """,
            [(r[0], r[1], r[2], r[3], r[4], datetime.utcnow()) for r in h2h_records],
            template="(%s, %s, %s, %s, %s, %s)",
        )
        conn.commit()

    logger.info(f"Upserted {len(h2h_records)} H2H records into csgo_h2h table")
    return len(h2h_records)


def verify_data(conn):
    """Print summary statistics after import."""
    with conn.cursor() as cur:
        # Team stats
        cur.execute("SELECT COUNT(*) FROM csgo_teams")
        team_count = cur.fetchone()[0]

        cur.execute("SELECT team_name, win_rate_pct FROM csgo_teams ORDER BY win_rate_pct DESC LIMIT 5")
        top_teams = cur.fetchall()

        cur.execute("SELECT AVG(win_rate_pct), MIN(win_rate_pct), MAX(win_rate_pct) FROM csgo_teams")
        avg_wr, min_wr, max_wr = cur.fetchone()

        # H2H stats
        cur.execute("SELECT COUNT(*) FROM csgo_h2h")
        h2h_count = cur.fetchone()[0]

        cur.execute("SELECT SUM(total_matches) FROM csgo_h2h")
        total_matches = cur.fetchone()[0] or 0

    print("\n" + "=" * 50)
    print("CS:GO DATA IMPORT SUMMARY")
    print("=" * 50)
    print(f"\nTeams: {team_count}")
    print(f"  Average win rate: {avg_wr:.1f}%")
    print(f"  Range: {min_wr:.1f}% - {max_wr:.1f}%")
    print("\n  Top 5 Teams by Win Rate:")
    for name, wr in top_teams:
        print(f"    {name}: {wr:.1f}%")

    print(f"\nH2H Records: {h2h_count}")
    print(f"  Total matches tracked: {total_matches}")
    print("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Import CS:GO team data from futarchy CSVs")
    parser.add_argument("--teams-only", action="store_true", help="Import only team leaderboard")
    parser.add_argument("--h2h-only", action="store_true", help="Import only H2H matrix")
    parser.add_argument("--verify", action="store_true", help="Only verify existing data, don't import")
    args = parser.parse_args()

    conn = connect_db()

    try:
        if args.verify:
            verify_data(conn)
            return

        if not args.h2h_only:
            import_team_leaderboard(conn)

        if not args.teams_only:
            import_h2h_matrix(conn)

        verify_data(conn)

    finally:
        conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    main()
