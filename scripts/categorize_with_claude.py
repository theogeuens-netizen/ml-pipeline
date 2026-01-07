#!/usr/bin/env python3
"""
Run Claude categorization from host machine (where Claude CLI is installed).

This script:
1. Fetches uncategorized markets from the database
2. Calls Claude Code CLI (installed on host)
3. Saves results back to database

Usage:
    python scripts/categorize_with_claude.py                # Categorize 15 markets
    python scripts/categorize_with_claude.py --batch 30     # Categorize 30 markets
    python scripts/categorize_with_claude.py --validate     # Run validation instead

Can be run via cron:
    */10 * * * * cd /home/theo/polymarket-ml && python scripts/categorize_with_claude.py >> /var/log/categorize.log 2>&1
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import RealDictCursor

# Database connection (same as Docker's exposed port)
DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "polymarket_ml",
    "user": "postgres",
    "password": "postgres",
}

# Load taxonomy
from src.models.taxonomy import TAXONOMY, validate_taxonomy, find_best_match

CLAUDE_TIMEOUT = 180
DEFAULT_BATCH_SIZE = 15

CONDENSED_TAXONOMY = """L1 Categories: CRYPTO, SPORTS, ESPORTS, POLITICS, ECONOMICS, BUSINESS, ENTERTAINMENT, WEATHER, SCIENCE, TECH, LEGAL, OTHER

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

L3 types: MATCH_WINNER, SPREAD, OVER_UNDER, PROP_PLAYER, DRAW, DIRECTION_DAILY, ABOVE_THRESHOLD, BELOW_THRESHOLD, BETWEEN_RANGE, BEST_ACTRESS, BEST_ACTOR, TOURNAMENT_WINNER, VERDICT, UNCLASSIFIED"""

CATEGORIZATION_PROMPT = """Categorize these Polymarket markets into L1/L2/L3 taxonomy.

{taxonomy}

MARKETS:
{markets_json}

For each market, return L1 (domain), L2 (sub-category), L3 (market type), and confidence (high/medium/low).

Return ONLY JSON array, no explanation:
[{{"id": 123, "l1": "SPORTS", "l2": "NFL", "l3": "MATCH_WINNER", "confidence": "high"}}]
"""


def get_uncategorized_markets(limit: int = 15) -> list[dict]:
    """Fetch uncategorized markets from database."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, question, description
                FROM markets
                WHERE active = true AND category_l1 IS NULL
                ORDER BY id DESC
                LIMIT %s
            """, (limit,))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def call_claude(prompt: str, model: str = "haiku") -> str:
    """Call Claude Code CLI."""
    cmd = ["claude", "-p", prompt]
    if model == "haiku":
        cmd.extend(["--model", "haiku"])

    print(f"Calling Claude ({model})...")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
        cwd=str(Path(__file__).parent.parent),
    )

    if result.returncode != 0:
        print(f"Claude error: {result.stderr[:500]}")
        raise RuntimeError(f"Claude exit code {result.returncode}")

    return result.stdout.strip()


def parse_response(response: str, market_ids: set) -> list[dict]:
    """Parse Claude's JSON response."""
    # Find JSON array
    start = response.find("[")
    end = response.rfind("]") + 1

    if start == -1 or end == 0:
        print(f"No JSON found in response: {response[:200]}")
        return []

    json_str = response[start:end]

    try:
        items = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue

        market_id = item.get("id")
        if market_id not in market_ids:
            continue

        l1 = str(item.get("l1", "OTHER")).upper()
        l2 = str(item.get("l2", "MISCELLANEOUS")).upper()
        l3 = str(item.get("l3", "UNCLASSIFIED")).upper() if item.get("l3") else None
        confidence = item.get("confidence", "medium")

        # Validate and fix if needed
        if not validate_taxonomy(l1, l2, l3 or "UNCLASSIFIED"):
            l1, l2, l3 = find_best_match(l1, l2, l3)

        results.append({
            "id": market_id,
            "l1": l1,
            "l2": l2,
            "l3": l3 or "UNCLASSIFIED",
            "confidence": confidence,
        })

    return results


def save_results(results: list[dict]) -> int:
    """Save categorization results to database."""
    if not results:
        return 0

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            now = datetime.now(timezone.utc)
            for r in results:
                cur.execute("""
                    UPDATE markets
                    SET category_l1 = %s,
                        category_l2 = %s,
                        category_l3 = %s,
                        categorization_method = 'claude',
                        categorization_confidence = %s,
                        categorized_at = %s
                    WHERE id = %s
                """, (r["l1"], r["l2"], r["l3"], r["confidence"], now, r["id"]))
            conn.commit()
        return len(results)
    finally:
        conn.close()


def categorize(batch_size: int = DEFAULT_BATCH_SIZE, model: str = "haiku"):
    """Main categorization function."""
    print(f"\n=== CATEGORIZING MARKETS ===")
    print(f"Batch size: {batch_size}, Model: {model}\n")

    # Fetch markets
    markets = get_uncategorized_markets(batch_size)
    if not markets:
        print("No uncategorized markets found.")
        return

    print(f"Found {len(markets)} uncategorized markets")

    # Build prompt with condensed taxonomy
    markets_json = json.dumps([
        {
            "id": m["id"],
            "q": m["question"][:100],  # Shorter for token limit
        }
        for m in markets
    ])

    prompt = CATEGORIZATION_PROMPT.format(
        taxonomy=CONDENSED_TAXONOMY,
        markets_json=markets_json,
    )

    # Call Claude
    try:
        response = call_claude(prompt, model)
    except Exception as e:
        print(f"Claude call failed: {e}")
        return

    # Parse response
    market_ids = {m["id"] for m in markets}
    results = parse_response(response, market_ids)

    if not results:
        print("No results parsed from Claude response")
        return

    print(f"Parsed {len(results)} categorizations")

    # Save
    saved = save_results(results)
    print(f"Saved {saved} categorizations to database")

    # Show sample
    print("\nSample results:")
    for r in results[:5]:
        m = next((m for m in markets if m["id"] == r["id"]), None)
        if m:
            print(f"  [{r['id']}] {m['question'][:50]}...")
            print(f"       → {r['l1']}/{r['l2']}/{r['l3']} ({r['confidence']})")


def main():
    parser = argparse.ArgumentParser(description="Categorize markets with Claude")
    parser.add_argument("--batch", type=int, default=15, help="Batch size")
    parser.add_argument("--model", default="haiku", choices=["haiku", "sonnet"])
    parser.add_argument("--validate", action="store_true", help="Run validation instead")

    args = parser.parse_args()

    if args.validate:
        print("Validation not implemented in this script yet")
        return

    categorize(batch_size=args.batch, model=args.model)


if __name__ == "__main__":
    main()
