#!/usr/bin/env python3
"""
Validate rule accuracy using Codex CLI (runs from host machine).

This replaces the previous Claude-based validator while keeping the same entrypoint path for cron.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import RealDictCursor

from src.models.taxonomy import TAXONOMY

# Database connection (can be overridden via env)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "polymarket_ml"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.1-codex-mini")
CODEX_TIMEOUT = int(os.getenv("CODEX_TIMEOUT_SECONDS", "60"))
CODEX_BIN = os.getenv("CODEX_BIN", "codex")

DEFAULT_SAMPLE_SIZE = 10

VALIDATION_PROMPT = """You are verifying if a market categorization is correct.

## MARKET
ID: {market_id}
Question: {question}
Description: {description}

## CURRENT CATEGORIZATION
L1: {current_l1}
L2: {current_l2}
L3: {current_l3}

## TAXONOMY REFERENCE (JSON)
{taxonomy_json}

## TASK
Return ONLY JSON object:
{{
  "is_correct": true/false,
  "correct_l1": "...",
  "correct_l2": "...",
  "correct_l3": "...",
  "reasoning": "brief"
}}
If unsure, set is_correct=false and propose the best correction.
"""


def get_rules() -> list[dict]:
    """Fetch enabled rules from database."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, l1, l2, times_matched, times_validated, times_correct
                FROM categorization_rules
                WHERE enabled = true
                ORDER BY times_matched DESC
            """)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_sample_markets(rule_id: int, limit: int) -> list[dict]:
    """Fetch sample markets for a rule."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, question, description, category_l1, category_l2, category_l3
                FROM markets
                WHERE matched_rule_id = %s AND active = true
                ORDER BY RANDOM()
                LIMIT %s
            """, (rule_id, limit))
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def call_codex(prompt: str) -> str:
    """Call Codex CLI in non-interactive mode and return final agent message."""
    cmd = [
        CODEX_BIN,
        "exec",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        "-m",
        CODEX_MODEL,
        prompt,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=CODEX_TIMEOUT,
        cwd=str(Path(__file__).parent.parent),
    )

    if result.returncode != 0:
        raise RuntimeError(f"Codex CLI exit {result.returncode}: {result.stderr[:200]}")

    last_message = None
    for line in result.stdout.splitlines():
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "item.completed" and evt.get("item", {}).get("type") == "agent_message":
            last_message = evt["item"].get("text")
    if not last_message:
        raise RuntimeError("Codex returned no agent_message")
    return last_message.strip()


def parse_validation_response(response: str) -> dict:
    """Parse Codex JSON response."""
    start = response.find("{")
    end = response.rfind("}") + 1

    if start == -1 or end == 0:
        return {"error": "No JSON found"}

    try:
        return json.loads(response[start:end])
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}"}


def store_validation(rule_id: int, market_id: int, rule_l1: str, rule_l2: str, rule_l3: str,
                     correct_l1: str, correct_l2: str, correct_l3: str, is_correct: bool):
    """Store validation result."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rule_validations
                (rule_id, market_id, rule_l1, rule_l2, rule_l3,
                 correct_l1, correct_l2, correct_l3, is_correct, validated_at, validated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (rule_id, market_id, rule_l1, rule_l2, rule_l3,
                  correct_l1, correct_l2, correct_l3, is_correct,
                  datetime.now(timezone.utc), "codex_cli"))
            conn.commit()
    finally:
        conn.close()


def update_rule_stats(rule_id: int, validated: int, correct: int):
    """Update rule validation stats."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE categorization_rules
                SET times_validated = times_validated + %s,
                    times_correct = times_correct + %s
                WHERE id = %s
            """, (validated, correct, rule_id))
            conn.commit()
    finally:
        conn.close()


def validate_rules(sample_per_rule: int = DEFAULT_SAMPLE_SIZE):
    """Main validation function."""
    print(f"\n=== VALIDATING RULES (Codex) ===")
    print(f"Samples per rule: {sample_per_rule}\n")

    rules = get_rules()
    if not rules:
        print("No enabled rules found.")
        return

    print(f"Found {len(rules)} enabled rules\n")

    total_validated = 0
    total_correct = 0

    taxonomy_json = json.dumps(TAXONOMY)

    for rule in rules:
        markets = get_sample_markets(rule["id"], sample_per_rule)
        if not markets:
            continue

        rule_correct = 0
        rule_validated = 0

        print(f"Rule: {rule['name']} ({rule['l1']}/{rule['l2']})")

        for market in markets:
            prompt = VALIDATION_PROMPT.format(
                market_id=market["id"],
                question=(market["question"] or "")[:200],
                description=(market["description"] or "")[:200],
                current_l1=market["category_l1"],
                current_l2=market["category_l2"],
                current_l3=market["category_l3"] or "N/A",
                taxonomy_json=taxonomy_json,
            )

            try:
                response = call_codex(prompt)
                validation = parse_validation_response(response)
            except Exception as e:
                print(f"  Error validating market {market['id']}: {e}")
                continue

            if validation.get("error"):
                print(f"  Parse error for market {market['id']}: {validation['error']}")
                continue

            rule_validated += 1
            is_correct = validation.get("is_correct", False)

            if is_correct:
                rule_correct += 1

            # Store result
            store_validation(
                rule_id=rule["id"],
                market_id=market["id"],
                rule_l1=market["category_l1"],
                rule_l2=market["category_l2"],
                rule_l3=market["category_l3"],
                correct_l1=validation.get("correct_l1", market["category_l1"]),
                correct_l2=validation.get("correct_l2", market["category_l2"]),
                correct_l3=validation.get("correct_l3", market["category_l3"]),
                is_correct=is_correct,
            )

        # Update rule stats
        if rule_validated > 0:
            update_rule_stats(rule["id"], rule_validated, rule_correct)
            accuracy = (rule_correct / rule_validated) * 100
            print(f"  Validated: {rule_validated}, Correct: {rule_correct}, Accuracy: {accuracy:.1f}%")

        total_validated += rule_validated
        total_correct += rule_correct

    print(f"\n=== SUMMARY ===")
    print(f"Total validated: {total_validated}")
    print(f"Total correct: {total_correct}")
    if total_validated > 0:
        print(f"Overall accuracy: {(total_correct/total_validated)*100:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Validate rules with Codex CLI")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE_SIZE,
                        help="Samples per rule")

    args = parser.parse_args()
    validate_rules(sample_per_rule=args.sample)


if __name__ == "__main__":
    main()
