#!/usr/bin/env python3
"""
Recategorize a market manually or with Claude.

Usage:
    python scripts/recategorize.py 12345                     # Let Claude decide
    python scripts/recategorize.py 12345 --l1 SPORTS --l2 NFL  # Manual override
    python scripts/recategorize.py 12345 -y                  # Skip confirmation
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import RealDictCursor

from src.models.taxonomy import validate_taxonomy, find_best_match

DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "polymarket_ml",
    "user": "postgres",
    "password": "postgres",
}


def get_market(market_id: int) -> dict:
    """Fetch market from database."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, question, description, category_l1, category_l2, category_l3,
                       categorization_method
                FROM markets WHERE id = %s
            """, (market_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_market(market_id: int, l1: str, l2: str, l3: str, method: str):
    """Update market category."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE markets
                SET category_l1 = %s,
                    category_l2 = %s,
                    category_l3 = %s,
                    categorization_method = %s,
                    categorized_at = %s,
                    matched_rule_id = NULL
                WHERE id = %s
            """, (l1, l2, l3, method, datetime.now(timezone.utc), market_id))
            conn.commit()
    finally:
        conn.close()


def call_claude(question: str, description: str) -> dict:
    """Call Claude for categorization."""
    prompt = f"""Categorize this Polymarket market:

Question: {question}
Description: {description or 'N/A'}

L1 Categories: CRYPTO, SPORTS, ESPORTS, POLITICS, ECONOMICS, BUSINESS, ENTERTAINMENT, WEATHER, SCIENCE, TECH, LEGAL, OTHER

L2 by L1:
- CRYPTO: BITCOIN, ETHEREUM, SOLANA, XRP, ALTCOIN, MEMECOIN
- SPORTS: NFL, NBA, MLB, NHL, EPL, CHAMPIONS_LEAGUE, GOLF, TENNIS, F1, MMA_UFC, BOXING, COLLEGE_FOOTBALL, COLLEGE_BASKETBALL, SOCCER_LALIGA, SOCCER_BUNDESLIGA, SOCCER_SERIE_A, SOCCER_MLS, SOCCER_OTHER, SOCCER_INTERNATIONAL, ICE_HOCKEY_OTHER, CRICKET, CHESS, OLYMPICS
- ESPORTS: CSGO, DOTA2, VALORANT, LOL, ROCKET_LEAGUE
- POLITICS: US_PRESIDENTIAL, US_CONGRESSIONAL, US_GUBERNATORIAL, US_POLICY, TRUMP_ACTIONS, UK_POLITICS, EU_POLITICS, LATAM_POLITICS, ASIA_POLITICS, GEOPOLITICS, INTERNATIONAL
- ECONOMICS: US_INDICES, GLOBAL_INDICES, INFLATION, UNEMPLOYMENT, GDP, FED, ECB, COMMODITIES, FOREX
- BUSINESS: EARNINGS, STOCK_PRICE, CORPORATE_ACTIONS, LEADERSHIP, ELON_MUSK, TECH_LAYOFFS, APP_RANKINGS
- ENTERTAINMENT: OSCARS, EMMYS, GRAMMYS, GOLDEN_GLOBES, MUSIC_CHARTS, YOUTUBE, TIKTOK, TV_SHOWS, MOVIES, CELEBRITY, REALITY_TV, CRITICS_CHOICE, CULTURAL
- WEATHER: TEMPERATURE, PRECIPITATION, HURRICANES, RECORDS, SEASONAL
- SCIENCE: SPACEX, NASA, SPACE_OTHER, MEDICAL, CLIMATE, NOBEL
- TECH: AI_MODELS, AI_COMPANIES, APPLE, GOOGLE, META, MICROSOFT, TESLA, REGULATION, SOCIAL_MEDIA
- LEGAL: SUPREME_COURT, CRIMINAL, CIVIL, REGULATORY
- OTHER: MISCELLANEOUS, META

Return ONLY JSON: {{"l1": "...", "l2": "...", "l3": "...", "reasoning": "..."}}"""

    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "haiku"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).parent.parent),
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude error: {result.stderr[:200]}")

    response = result.stdout.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON in response: {response[:200]}")

    return json.loads(response[start:end])


def main():
    parser = argparse.ArgumentParser(description="Recategorize a market")
    parser.add_argument("market_id", type=int, help="Market ID")
    parser.add_argument("--l1", help="Manual L1 (if not set, uses Claude)")
    parser.add_argument("--l2", help="Manual L2")
    parser.add_argument("--l3", help="Manual L3")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    # Get market
    market = get_market(args.market_id)
    if not market:
        print(f"Market {args.market_id} not found")
        sys.exit(1)

    print(f"\n=== MARKET {args.market_id} ===")
    print(f"Question: {market['question'][:100]}...")
    print(f"Current:  {market['category_l1']}/{market['category_l2']}/{market['category_l3']}")
    print(f"Method:   {market['categorization_method'] or 'none'}")

    # Determine new categories
    if args.l1:
        new_l1 = args.l1.upper()
        new_l2 = args.l2.upper() if args.l2 else "MISCELLANEOUS"
        new_l3 = args.l3.upper() if args.l3 else "UNCLASSIFIED"
        method = "manual"
        print(f"\nManual: {new_l1}/{new_l2}/{new_l3}")
    else:
        print("\nCalling Claude...")
        try:
            data = call_claude(market['question'], market['description'])
            new_l1 = data.get("l1", "OTHER").upper()
            new_l2 = data.get("l2", "MISCELLANEOUS").upper()
            new_l3 = data.get("l3", "UNCLASSIFIED").upper()
            reasoning = data.get("reasoning", "")

            if not validate_taxonomy(new_l1, new_l2, new_l3):
                new_l1, new_l2, new_l3 = find_best_match(new_l1, new_l2, new_l3)

            method = "claude"
            print(f"Claude: {new_l1}/{new_l2}/{new_l3}")
            if reasoning:
                print(f"Reason: {reasoning}")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

    # Confirm
    if not args.yes:
        confirm = input("\nApply? [y/N] ")
        if confirm.lower() != 'y':
            print("Cancelled")
            sys.exit(0)

    # Update
    update_market(args.market_id, new_l1, new_l2, new_l3, method)
    print(f"\nDone: {new_l1}/{new_l2}/{new_l3} (method: {method})")


if __name__ == "__main__":
    main()
