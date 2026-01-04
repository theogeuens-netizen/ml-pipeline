"""
Helper functions for market categorization.

Used by the /categorize slash command to interact with the database.

Usage:
    python -m cli.categorize_helpers stats
    python -m cli.categorize_helpers uncategorized [limit]
    python -m cli.categorize_helpers run-rules [limit]
"""

import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def get_stats() -> dict:
    """Get categorization statistics."""
    from sqlalchemy import select, func, and_
    from src.db.database import get_session
    from src.db.models import Market, CategorizationRule

    with get_session() as session:
        # Total markets
        total = session.execute(
            select(func.count(Market.id)).where(Market.active == True)
        ).scalar() or 0

        # Categorized (has category_l1)
        categorized = session.execute(
            select(func.count(Market.id)).where(
                and_(Market.active == True, Market.category_l1.isnot(None))
            )
        ).scalar() or 0

        # By method
        by_rule = session.execute(
            select(func.count(Market.id)).where(
                and_(Market.active == True, Market.categorization_method == "rule")
            )
        ).scalar() or 0

        by_claude = session.execute(
            select(func.count(Market.id)).where(
                and_(Market.active == True, Market.categorization_method == "claude")
            )
        ).scalar() or 0

        by_event = session.execute(
            select(func.count(Market.id)).where(
                and_(Market.active == True, Market.categorization_method == "event")
            )
        ).scalar() or 0

        # Today's activity
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        new_today = session.execute(
            select(func.count(Market.id)).where(
                Market.tracking_started_at >= today_start
            )
        ).scalar() or 0

        categorized_today = session.execute(
            select(func.count(Market.id)).where(
                Market.categorized_at >= today_start
            )
        ).scalar() or 0

        # Rule stats
        rule_count = session.execute(
            select(func.count(CategorizationRule.id)).where(
                CategorizationRule.enabled == True
            )
        ).scalar() or 0

        total_matches = session.execute(
            select(func.sum(CategorizationRule.times_matched))
        ).scalar() or 0

        uncategorized = total - categorized

        return {
            "total_markets": total,
            "categorized": categorized,
            "uncategorized": uncategorized,
            "coverage_pct": round(categorized / total * 100, 1) if total > 0 else 0,
            "by_method": {
                "rule": by_rule,
                "claude": by_claude,
                "event": by_event,
            },
            "today": {
                "new_markets": new_today,
                "categorized": categorized_today,
            },
            "rules": {
                "enabled": rule_count,
                "total_matches": total_matches,
            },
        }


def fetch_uncategorized(limit: int = 100) -> list[dict]:
    """Fetch uncategorized markets for Claude to process."""
    from sqlalchemy import select
    from src.db.database import get_session
    from src.db.models import Market

    with get_session() as session:
        result = session.execute(
            select(Market)
            .where(
                Market.active == True,
                Market.category_l1.is_(None),
            )
            .order_by(Market.tracking_started_at.desc())
            .limit(limit)
        )
        markets = result.scalars().all()

        return [
            {
                "id": m.id,
                "question": m.question,
                "description": (m.description or "")[:200],
                "event_title": m.event_title or "",
            }
            for m in markets
        ]


def save_categories(results: list[dict]) -> int:
    """
    Save Claude's categorization results to database.

    Args:
        results: List of dicts with keys: id, l1, l2, l3

    Returns:
        Number of markets updated
    """
    from sqlalchemy import update
    from src.db.database import get_session
    from src.db.models import Market
    from src.models.taxonomy import validate_taxonomy

    if not results:
        return 0

    saved = 0
    with get_session() as session:
        for r in results:
            market_id = r.get("id")
            l1 = r.get("l1")
            l2 = r.get("l2")
            l3 = r.get("l3")

            if not all([market_id, l1, l2, l3]):
                continue

            if not validate_taxonomy(l1, l2, l3):
                print(f"Invalid taxonomy for market {market_id}: {l1}/{l2}/{l3}")
                continue

            session.execute(
                update(Market)
                .where(Market.id == market_id)
                .values(
                    category_l1=l1,
                    category_l2=l2,
                    category_l3=l3,
                    categorization_method="claude",
                    matched_rule_id=None,  # Clear rule association when Claude categorizes
                    categorized_at=datetime.now(timezone.utc),
                )
            )
            saved += 1

        session.commit()

    return saved


def run_rules(limit: int = 500) -> dict:
    """
    Run rule-based categorization on uncategorized markets.

    Returns:
        Dict with matched count and details
    """
    from sqlalchemy import select
    from src.db.database import get_session
    from src.db.models import Market
    from src.services.rule_categorizer import get_rule_categorizer

    # Fetch uncategorized markets
    with get_session() as session:
        result = session.execute(
            select(Market)
            .where(
                Market.active == True,
                Market.category_l1.is_(None),
            )
            .limit(limit)
        )
        markets = result.scalars().all()

        market_dicts = [
            {
                "id": m.id,
                "question": m.question,
                "description": m.description or "",
                "event_title": m.event_title or "",
            }
            for m in markets
        ]

    if not market_dicts:
        return {"checked": 0, "matched": 0, "remaining": 0}

    # Run rule categorizer
    categorizer = get_rule_categorizer()
    matched, unmatched = categorizer.categorize_batch(market_dicts)

    # Save results
    if matched:
        categorizer.save_results(matched)

    return {
        "checked": len(market_dicts),
        "matched": len(matched),
        "remaining": len(unmatched),
        "matches": [
            {"id": r.market_id, "l1": r.l1, "l2": r.l2, "l3": r.l3, "rule": r.rule_name}
            for r in matched[:10]  # First 10 for display
        ],
    }


def fetch_for_validation(limit: int = 50) -> list[dict]:
    """
    Fetch random rule-categorized markets from today for validation.

    Returns:
        List of market dicts with their rule-assigned categories
    """
    from sqlalchemy import select, func
    from src.db.database import get_session
    from src.db.models import Market

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    with get_session() as session:
        result = session.execute(
            select(Market)
            .where(
                Market.categorization_method == "rule",
                Market.categorized_at >= today_start,
            )
            .order_by(func.random())
            .limit(limit)
        )
        markets = result.scalars().all()

        return [
            {
                "id": m.id,
                "question": m.question,
                "description": (m.description or "")[:200],
                "event_title": m.event_title or "",
                "rule_l1": m.category_l1,
                "rule_l2": m.category_l2,
                "rule_l3": m.category_l3,
                "matched_rule_id": m.matched_rule_id,
            }
            for m in markets
        ]


def save_validation_results(results: list[dict]) -> dict:
    """
    Save validation results and update rule accuracy.

    Args:
        results: List of dicts with keys: id, is_correct, correct_l1, correct_l2, correct_l3

    Returns:
        Summary of validation results
    """
    from sqlalchemy import select, update
    from src.db.database import get_session
    from src.db.models import Market, CategorizationRule, RuleValidation

    if not results:
        return {"validated": 0, "correct": 0, "incorrect": 0, "skipped": 0}

    correct_count = 0
    incorrect_count = 0
    skipped_count = 0

    with get_session() as session:
        for r in results:
            market_id = r.get("id")
            is_correct = r.get("is_correct", True)

            # Get market to find rule_id
            market = session.execute(
                select(Market).where(Market.id == market_id)
            ).scalar()

            if not market:
                continue

            # Check for duplicate validation - skip if already validated
            existing = session.execute(
                select(RuleValidation).where(RuleValidation.market_id == market_id)
            ).scalar()
            if existing:
                print(f"  Skipping market {market_id}: already validated")
                skipped_count += 1
                continue

            # Determine correct categories (use provided or fall back to current)
            correct_l1 = r.get("correct_l1") or market.category_l1
            correct_l2 = r.get("correct_l2") or market.category_l2
            correct_l3 = r.get("correct_l3") or market.category_l3

            # SANITY CHECK: Auto-correct is_correct based on category comparison
            # This prevents corrupt data where is_correct=false but categories match
            categories_match = (
                correct_l1 == market.category_l1 and
                correct_l2 == market.category_l2
            )
            if categories_match and not is_correct:
                print(f"  Auto-correcting market {market_id}: categories match but marked incorrect")
                is_correct = True
            elif not categories_match and is_correct:
                print(f"  Auto-correcting market {market_id}: categories differ but marked correct")
                is_correct = False

            # Create validation record
            validation = RuleValidation(
                market_id=market_id,
                rule_id=market.matched_rule_id,
                rule_l1=market.category_l1,
                rule_l2=market.category_l2,
                rule_l3=market.category_l3,
                correct_l1=correct_l1,
                correct_l2=correct_l2,
                correct_l3=correct_l3,
                is_correct=is_correct,
                validated_by="claude",
            )
            session.add(validation)

            # Update rule stats
            if market.matched_rule_id:
                session.execute(
                    update(CategorizationRule)
                    .where(CategorizationRule.id == market.matched_rule_id)
                    .values(
                        times_validated=CategorizationRule.times_validated + 1,
                        times_correct=CategorizationRule.times_correct + (1 if is_correct else 0),
                    )
                )

            # If incorrect, update market with correct category
            if not is_correct and all([r.get("correct_l1"), r.get("correct_l2"), r.get("correct_l3")]):
                session.execute(
                    update(Market)
                    .where(Market.id == market_id)
                    .values(
                        category_l1=r.get("correct_l1"),
                        category_l2=r.get("correct_l2"),
                        category_l3=r.get("correct_l3"),
                        categorization_method="claude",
                        categorized_at=datetime.now(timezone.utc),
                    )
                )
                incorrect_count += 1
            else:
                correct_count += 1

        session.commit()

    return {
        "validated": correct_count + incorrect_count,
        "correct": correct_count,
        "incorrect": incorrect_count,
        "skipped": skipped_count,
    }


def get_rule_performance() -> list[dict]:
    """Get performance stats for each rule."""
    from sqlalchemy import select
    from src.db.database import get_session
    from src.db.models import CategorizationRule

    with get_session() as session:
        result = session.execute(
            select(CategorizationRule)
            .where(CategorizationRule.enabled == True)
            .order_by(CategorizationRule.times_matched.desc())
        )
        rules = result.scalars().all()

        return [
            {
                "id": r.id,
                "name": r.name,
                "l1": r.l1,
                "l2": r.l2,
                "times_matched": r.times_matched,
                "times_validated": r.times_validated,
                "times_correct": r.times_correct,
                "accuracy": round(r.times_correct / r.times_validated * 100, 1)
                    if r.times_validated > 0 else None,
                "keywords_count": len(r.keywords) if r.keywords else 0,
            }
            for r in rules
        ]


def print_stats():
    """Print categorization statistics."""
    stats = get_stats()

    print(f"\n{'='*50}")
    print("CATEGORIZATION STATS")
    print(f"{'='*50}\n")

    print(f"Total markets:    {stats['total_markets']:,}")
    print(f"Categorized:      {stats['categorized']:,} ({stats['coverage_pct']}%)")
    print(f"Uncategorized:    {stats['uncategorized']:,}")
    print()

    print("By method:")
    for method, count in stats['by_method'].items():
        print(f"  {method:10s}: {count:,}")
    print()

    print("Today:")
    print(f"  New markets:    {stats['today']['new_markets']}")
    print(f"  Categorized:    {stats['today']['categorized']}")
    print()

    print("Rules:")
    print(f"  Enabled:        {stats['rules']['enabled']}")
    print(f"  Total matches:  {stats['rules']['total_matches']:,}")
    print()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Categorization helper commands")
    parser.add_argument("command", choices=["stats", "uncategorized", "run-rules", "rules"])
    parser.add_argument("--limit", type=int, default=100, help="Limit for queries")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.command == "stats":
        if args.json:
            print(json.dumps(get_stats(), indent=2))
        else:
            print_stats()

    elif args.command == "uncategorized":
        markets = fetch_uncategorized(args.limit)
        if args.json:
            print(json.dumps(markets, indent=2))
        else:
            print(f"\nUncategorized markets ({len(markets)}):\n")
            for m in markets[:20]:
                print(f"  [{m['id']}] {m['question'][:70]}...")
            if len(markets) > 20:
                print(f"\n  ... and {len(markets) - 20} more")

    elif args.command == "run-rules":
        result = run_rules(args.limit)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\nRule categorization complete:")
            print(f"  Checked:   {result['checked']}")
            print(f"  Matched:   {result['matched']}")
            print(f"  Remaining: {result['remaining']}")
            if result.get('matches'):
                print("\nSample matches:")
                for m in result['matches'][:5]:
                    print(f"  [{m['id']}] {m['l1']}/{m['l2']}/{m['l3']} (rule: {m['rule']})")

    elif args.command == "rules":
        rules = get_rule_performance()
        if args.json:
            print(json.dumps(rules, indent=2))
        else:
            print(f"\nRule performance ({len(rules)} rules):\n")
            print(f"{'Name':<25} {'L1':<12} {'Matches':>8} {'Validated':>10} {'Accuracy':>10}")
            print("-" * 70)
            for r in rules:
                acc = f"{r['accuracy']}%" if r['accuracy'] is not None else "N/A"
                print(f"{r['name']:<25} {r['l1']:<12} {r['times_matched']:>8} {r['times_validated']:>10} {acc:>10}")


if __name__ == "__main__":
    main()
