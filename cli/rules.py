"""
CLI for managing categorization rules.

Commands:
    stats           Show rule statistics and accuracy
    validate        Run validation on rules
    mismatches      Show recent validation mismatches
    suggestions     Show pending rule suggestions
    approve         Approve a rule suggestion
    test            Test a rule against sample markets
    add-keyword     Add keyword to a rule
    disable         Disable a low-accuracy rule
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/home/theo/polymarket-ml")

from sqlalchemy import select, update, text, func
from src.db.database import get_session
from src.db.models import Market, CategorizationRule


def cmd_stats(args):
    """Show rule statistics and accuracy."""
    with get_session() as session:
        rules = session.execute(
            select(CategorizationRule)
            .where(CategorizationRule.enabled == True)
            .order_by(CategorizationRule.times_matched.desc())
        ).scalars().all()

        print("\n=== CATEGORIZATION RULES ===\n")
        print(f"{'Rule':<30} {'L1':<12} {'L2':<20} {'Matched':>8} {'Validated':>10} {'Accuracy':>10}")
        print("-" * 95)

        for rule in rules:
            accuracy = ""
            if rule.times_validated > 0:
                acc_pct = (rule.times_correct / rule.times_validated) * 100
                accuracy = f"{acc_pct:.1f}%"

            print(f"{rule.name:<30} {rule.l1:<12} {rule.l2:<20} {rule.times_matched:>8} {rule.times_validated:>10} {accuracy:>10}")

        # Summary stats
        total_matched = sum(r.times_matched for r in rules)
        total_validated = sum(r.times_validated for r in rules)
        total_correct = sum(r.times_correct for r in rules)

        print("-" * 95)
        print(f"{'TOTAL':<30} {'':<12} {'':<20} {total_matched:>8} {total_validated:>10}", end="")
        if total_validated > 0:
            print(f" {total_correct/total_validated*100:.1f}%")
        else:
            print()

        # Market coverage
        coverage = session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE category_l1 IS NULL) as uncategorized,
                COUNT(*) FILTER (WHERE categorization_method = 'rule') as by_rule,
                COUNT(*) FILTER (WHERE categorization_method = 'claude') as by_claude,
                COUNT(*) as total
            FROM markets WHERE active = true
        """)).fetchone()

        print(f"\n=== MARKET COVERAGE ===\n")
        print(f"Total active:    {coverage.total:,}")
        print(f"By rules:        {coverage.by_rule:,} ({coverage.by_rule/coverage.total*100:.1f}%)")
        print(f"By Claude:       {coverage.by_claude:,} ({coverage.by_claude/coverage.total*100:.1f}%)")
        print(f"Uncategorized:   {coverage.uncategorized:,} ({coverage.uncategorized/coverage.total*100:.1f}%)")


def cmd_validate(args):
    """Run validation on a specific rule or all rules."""
    from src.tasks.categorization import validate_rule_accuracy

    sample_size = args.sample or 10

    if args.rule:
        print(f"Validating rule '{args.rule}' with {sample_size} samples...")
        # For now, run full validation
        result = validate_rule_accuracy(sample_per_rule=sample_size)
        if args.rule in result:
            print(f"\nResult: {result[args.rule]}")
        else:
            print(f"\nRule '{args.rule}' not found or has no matches")
    else:
        print(f"Validating all rules with {sample_size} samples each...")
        result = validate_rule_accuracy(sample_per_rule=sample_size)
        print(f"\nValidated {len(result)} rules")
        for name, stats in result.items():
            print(f"  {name}: {stats['accuracy']}% ({stats['correct']}/{stats['sampled']})")


def cmd_mismatches(args):
    """Show recent validation mismatches."""
    limit = args.limit or 20

    with get_session() as session:
        mismatches = session.execute(text("""
            SELECT
                rv.id,
                rv.validated_at,
                cr.name as rule_name,
                m.question,
                rv.rule_l1, rv.rule_l2, rv.rule_l3,
                rv.claude_l1, rv.claude_l2, rv.claude_l3,
                rv.mismatch_type,
                rv.mismatch_reason
            FROM rule_validations rv
            JOIN categorization_rules cr ON rv.rule_id = cr.id
            JOIN markets m ON rv.market_id = m.id
            WHERE rv.is_match = false
            ORDER BY rv.validated_at DESC
            LIMIT :limit
        """), {"limit": limit}).fetchall()

        if not mismatches:
            print("\nNo validation mismatches found.")
            return

        print(f"\n=== RECENT MISMATCHES ({len(mismatches)}) ===\n")

        for mm in mismatches:
            print(f"Rule: {mm.rule_name}")
            print(f"Question: {mm.question[:80]}...")
            print(f"Rule said:   {mm.rule_l1} / {mm.rule_l2} / {mm.rule_l3}")
            print(f"Claude said: {mm.claude_l1} / {mm.claude_l2} / {mm.claude_l3}")
            print(f"Mismatch type: {mm.mismatch_type}")
            if mm.mismatch_reason:
                print(f"Reason: {mm.mismatch_reason}")
            print("-" * 60)


def cmd_suggestions(args):
    """Show pending rule suggestions."""
    with get_session() as session:
        suggestions = session.execute(text("""
            SELECT
                id,
                suggested_l1,
                suggested_l2,
                suggested_keywords,
                example_markets,
                occurrence_count,
                status,
                created_at
            FROM rule_suggestions
            WHERE status = 'pending'
            ORDER BY occurrence_count DESC
        """)).fetchall()

        if not suggestions:
            print("\nNo pending rule suggestions.")
            return

        print(f"\n=== PENDING RULE SUGGESTIONS ({len(suggestions)}) ===\n")

        for s in suggestions:
            keywords = json.loads(s.suggested_keywords) if s.suggested_keywords else []
            examples = json.loads(s.example_markets) if s.example_markets else []

            print(f"ID: {s.id}")
            print(f"Category: {s.suggested_l1} / {s.suggested_l2}")
            print(f"Occurrences: {s.occurrence_count}")
            print(f"Suggested keywords: {', '.join(keywords[:5])}")
            print("Example questions:")
            for ex in examples[:3]:
                print(f"  - {ex['question'][:70]}...")
            print()
            print(f"To approve: python -m cli.rules approve {s.id}")
            print("-" * 60)


def cmd_approve(args):
    """Approve a rule suggestion and create the rule."""
    suggestion_id = args.id

    with get_session() as session:
        # Get suggestion
        suggestion = session.execute(text("""
            SELECT * FROM rule_suggestions WHERE id = :id
        """), {"id": suggestion_id}).fetchone()

        if not suggestion:
            print(f"Suggestion {suggestion_id} not found")
            return

        if suggestion.status != "pending":
            print(f"Suggestion is already {suggestion.status}")
            return

        keywords = json.loads(suggestion.suggested_keywords) if suggestion.suggested_keywords else []

        # Create the rule
        rule_name = f"{suggestion.suggested_l1.lower()}_{suggestion.suggested_l2.lower()}"

        # Check if rule already exists
        existing = session.execute(
            select(CategorizationRule).where(CategorizationRule.name == rule_name)
        ).scalar_one_or_none()

        if existing:
            print(f"Rule '{rule_name}' already exists")
            return

        new_rule = CategorizationRule(
            name=rule_name,
            l1=suggestion.suggested_l1,
            l2=suggestion.suggested_l2,
            keywords=keywords,
            negative_keywords=[],
            l3_patterns={},
            l3_default=None,
            enabled=True,
            notes=f"Created from suggestion {suggestion_id}",
        )
        session.add(new_rule)

        # Update suggestion status
        session.execute(text("""
            UPDATE rule_suggestions
            SET status = 'approved', reviewed_at = :now
            WHERE id = :id
        """), {"id": suggestion_id, "now": datetime.now(timezone.utc)})

        session.commit()

        print(f"Created rule: {rule_name}")
        print(f"Keywords: {', '.join(keywords)}")
        print(f"\nRule will be used in next categorization run.")


def cmd_test(args):
    """Test a rule against sample markets."""
    rule_name = args.rule
    limit = args.limit or 20

    with get_session() as session:
        # Get the rule
        rule = session.execute(
            select(CategorizationRule).where(CategorizationRule.name == rule_name)
        ).scalar_one_or_none()

        if not rule:
            print(f"Rule '{rule_name}' not found")
            return

        print(f"\n=== TESTING RULE: {rule_name} ===")
        print(f"L1: {rule.l1}, L2: {rule.l2}")
        print(f"Keywords: {rule.keywords}")
        print(f"Negative: {rule.negative_keywords}")

        # Get uncategorized markets
        markets = session.execute(
            select(Market)
            .where(Market.active == True, Market.category_l1 == None)
            .limit(limit * 5)  # Get more to find matches
        ).scalars().all()

        # Test rule
        from src.services.rule_categorizer import RuleCategorizer

        categorizer = RuleCategorizer()
        categorizer.rules = [rule]
        categorizer._compiled_l3_patterns = {}

        # Compile L3 patterns
        import re
        if rule.l3_patterns:
            categorizer._compiled_l3_patterns[rule.id] = {}
            for l3, patterns in rule.l3_patterns.items():
                categorizer._compiled_l3_patterns[rule.id][l3] = [
                    re.compile(p, re.IGNORECASE) for p in patterns
                ]

        matches = []
        for m in markets:
            result = categorizer.categorize({
                "id": m.id,
                "question": m.question,
                "description": m.description or "",
                "event_title": m.event_title or "",
            })
            if result:
                matches.append((m, result))
                if len(matches) >= limit:
                    break

        print(f"\n=== MATCHES ({len(matches)}) ===\n")

        for m, result in matches:
            print(f"[{m.id}] {m.question[:70]}...")
            print(f"  → {result.l1} / {result.l2} / {result.l3}")
            print()


def cmd_add_keyword(args):
    """Add a keyword to a rule."""
    rule_name = args.rule
    keyword = args.keyword

    with get_session() as session:
        rule = session.execute(
            select(CategorizationRule).where(CategorizationRule.name == rule_name)
        ).scalar_one_or_none()

        if not rule:
            print(f"Rule '{rule_name}' not found")
            return

        keywords = list(rule.keywords or [])
        if keyword in keywords:
            print(f"Keyword '{keyword}' already in rule")
            return

        keywords.append(keyword)

        session.execute(
            update(CategorizationRule)
            .where(CategorizationRule.id == rule.id)
            .values(keywords=keywords)
        )
        session.commit()

        print(f"Added '{keyword}' to rule '{rule_name}'")
        print(f"Keywords now: {keywords}")


def cmd_disable(args):
    """Disable a rule."""
    rule_name = args.rule

    with get_session() as session:
        rule = session.execute(
            select(CategorizationRule).where(CategorizationRule.name == rule_name)
        ).scalar_one_or_none()

        if not rule:
            print(f"Rule '{rule_name}' not found")
            return

        session.execute(
            update(CategorizationRule)
            .where(CategorizationRule.id == rule.id)
            .values(enabled=False)
        )
        session.commit()

        print(f"Disabled rule '{rule_name}'")


def cmd_pending(args):
    """Show pending rule improvements from learning loop."""
    with get_session() as session:
        improvements = session.execute(text("""
            SELECT i.id, i.rule_id, r.name as rule_name,
                   i.improvement_type, i.suggestion, i.confidence,
                   i.example_market_ids, i.created_at
            FROM rule_improvements i
            JOIN categorization_rules r ON i.rule_id = r.id
            WHERE i.status = 'pending'
            ORDER BY i.created_at DESC
        """)).fetchall()

        if not improvements:
            print("\nNo pending improvements from learning loop.")
            return

        print(f"\n=== PENDING IMPROVEMENTS ({len(improvements)}) ===\n")

        for i in improvements:
            suggestion = i.suggestion if isinstance(i.suggestion, dict) else json.loads(i.suggestion)
            examples = i.example_market_ids if isinstance(i.example_market_ids, list) else json.loads(i.example_market_ids or '[]')

            print(f"ID: {i.id}")
            print(f"  Rule: {i.rule_name} (ID: {i.rule_id})")
            print(f"  Type: {i.improvement_type}")
            print(f"  Action: {suggestion.get('action')}")
            print(f"  Keyword: {suggestion.get('keyword', 'N/A')}")
            print(f"  Reason: {suggestion.get('reason', 'N/A')}")
            print(f"  Confidence: {i.confidence}")
            print(f"  Example markets: {examples[:3]}")
            print(f"  Created: {i.created_at}")
            print()

        print("Commands:")
        print("  python -m cli.rules apply-improvement <id>   # Apply")
        print("  python -m cli.rules reject-improvement <id>  # Reject")


def cmd_apply_improvement(args):
    """Apply a pending improvement from learning loop."""
    improvement_id = args.id

    with get_session() as session:
        # Fetch improvement
        imp = session.execute(text("""
            SELECT i.*, r.keywords, r.negative_keywords, r.name as rule_name
            FROM rule_improvements i
            JOIN categorization_rules r ON i.rule_id = r.id
            WHERE i.id = :id AND i.status = 'pending'
        """), {"id": improvement_id}).fetchone()

        if not imp:
            print(f"Improvement {improvement_id} not found or already processed.")
            return

        suggestion = imp.suggestion if isinstance(imp.suggestion, dict) else json.loads(imp.suggestion)
        action = suggestion.get('action')
        keyword = suggestion.get('keyword', '').strip()

        if not keyword:
            print("No keyword specified in suggestion.")
            return

        print(f"\nApplying improvement {improvement_id}:")
        print(f"  Rule: {imp.rule_name}")
        print(f"  Action: {action}")
        print(f"  Keyword: {keyword}")
        print(f"  Reason: {suggestion.get('reason', 'N/A')}")

        # Apply based on action type
        if action == 'ADD_NEGATIVE_KEYWORD':
            neg_keywords = list(imp.negative_keywords or [])
            if keyword.lower() not in [k.lower() for k in neg_keywords]:
                neg_keywords.append(keyword)
                session.execute(text("""
                    UPDATE categorization_rules
                    SET negative_keywords = :keywords
                    WHERE id = :rule_id
                """), {"keywords": json.dumps(neg_keywords), "rule_id": imp.rule_id})
                print(f"\nAdded negative keyword '{keyword}' to rule '{imp.rule_name}'")
            else:
                print(f"\nKeyword '{keyword}' already in negative keywords.")

        elif action == 'ADD_KEYWORD':
            keywords = list(imp.keywords or [])
            if keyword.lower() not in [k.lower() for k in keywords]:
                keywords.append(keyword)
                session.execute(text("""
                    UPDATE categorization_rules
                    SET keywords = :keywords
                    WHERE id = :rule_id
                """), {"keywords": json.dumps(keywords), "rule_id": imp.rule_id})
                print(f"\nAdded keyword '{keyword}' to rule '{imp.rule_name}'")
            else:
                print(f"\nKeyword '{keyword}' already exists.")

        elif action == 'NEW_RULE':
            print("\nNEW_RULE action requires manual creation.")
            print(f"Suggested keyword: {keyword}")
            print(f"Reason: {suggestion.get('reason')}")
            return

        else:
            print(f"\nUnknown action: {action}")
            return

        # Mark as applied
        session.execute(text("""
            UPDATE rule_improvements
            SET status = 'applied', applied_at = :now
            WHERE id = :id
        """), {"now": datetime.now(timezone.utc), "id": improvement_id})

        session.commit()
        print(f"\nImprovement {improvement_id} applied successfully.")


def cmd_reject_improvement(args):
    """Reject a pending improvement."""
    improvement_id = args.id

    with get_session() as session:
        result = session.execute(text("""
            UPDATE rule_improvements
            SET status = 'rejected', reviewed_at = :now
            WHERE id = :id AND status = 'pending'
        """), {"now": datetime.now(timezone.utc), "id": improvement_id})

        if result.rowcount > 0:
            session.commit()
            print(f"Improvement {improvement_id} rejected.")
        else:
            print(f"Improvement {improvement_id} not found or already processed.")


def cmd_recategorize(args):
    """Recategorize a market manually or with Claude."""
    market_id = args.market_id

    with get_session() as session:
        # Get the market
        market = session.execute(
            select(Market).where(Market.id == market_id)
        ).scalar_one_or_none()

        if not market:
            print(f"Market {market_id} not found")
            return

        print(f"\n=== MARKET {market_id} ===")
        print(f"Question: {market.question[:100]}...")
        print(f"Current:  {market.category_l1}/{market.category_l2}/{market.category_l3}")
        print(f"Method:   {market.categorization_method or 'none'}")

        # Determine new categories
        if args.l1:
            # Manual override
            new_l1 = args.l1.upper()
            new_l2 = args.l2.upper() if args.l2 else "MISCELLANEOUS"
            new_l3 = args.l3.upper() if args.l3 else "UNCLASSIFIED"
            method = "manual"
            print(f"\nManual override: {new_l1}/{new_l2}/{new_l3}")
        else:
            # Call Claude
            print("\nCalling Claude for recategorization...")
            try:
                import subprocess
                from src.models.taxonomy import validate_taxonomy, find_best_match

                prompt = f"""Categorize this Polymarket market:

Question: {market.question}
Description: {market.description or 'N/A'}

L1 Categories: CRYPTO, SPORTS, ESPORTS, POLITICS, ECONOMICS, BUSINESS, ENTERTAINMENT, WEATHER, SCIENCE, TECH, LEGAL, OTHER

Return ONLY JSON: {{"l1": "...", "l2": "...", "l3": "...", "reasoning": "..."}}"""

                result = subprocess.run(
                    ["claude", "-p", prompt, "--model", "haiku"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode != 0:
                    print(f"Claude error: {result.stderr[:200]}")
                    return

                # Parse response
                import json
                response = result.stdout.strip()
                start = response.find("{")
                end = response.rfind("}") + 1
                if start == -1 or end == 0:
                    print(f"No JSON in response: {response[:200]}")
                    return

                data = json.loads(response[start:end])
                new_l1 = data.get("l1", "OTHER").upper()
                new_l2 = data.get("l2", "MISCELLANEOUS").upper()
                new_l3 = data.get("l3", "UNCLASSIFIED").upper()
                reasoning = data.get("reasoning", "")

                # Validate
                if not validate_taxonomy(new_l1, new_l2, new_l3):
                    new_l1, new_l2, new_l3 = find_best_match(new_l1, new_l2, new_l3)

                method = "claude"
                print(f"Claude says: {new_l1}/{new_l2}/{new_l3}")
                if reasoning:
                    print(f"Reasoning: {reasoning}")

            except Exception as e:
                print(f"Error calling Claude: {e}")
                return

        # Confirm if not --yes
        if not args.yes:
            confirm = input("\nApply this change? [y/N] ")
            if confirm.lower() != 'y':
                print("Cancelled")
                return

        # Update
        from datetime import datetime, timezone
        session.execute(
            update(Market)
            .where(Market.id == market_id)
            .values(
                category_l1=new_l1,
                category_l2=new_l2,
                category_l3=new_l3,
                categorization_method=method,
                categorized_at=datetime.now(timezone.utc),
                matched_rule_id=None,  # Clear rule association
            )
        )
        session.commit()

        print(f"\nUpdated market {market_id}: {new_l1}/{new_l2}/{new_l3} (method: {method})")


def main():
    parser = argparse.ArgumentParser(description="Categorization rule management")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # stats
    subparsers.add_parser("stats", help="Show rule statistics")

    # validate
    validate_parser = subparsers.add_parser("validate", help="Run validation")
    validate_parser.add_argument("rule", nargs="?", help="Specific rule to validate")
    validate_parser.add_argument("--sample", type=int, help="Sample size per rule")

    # mismatches
    mismatches_parser = subparsers.add_parser("mismatches", help="Show validation mismatches")
    mismatches_parser.add_argument("--limit", type=int, default=20, help="Number to show")

    # suggestions
    subparsers.add_parser("suggestions", help="Show pending rule suggestions")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve a suggestion")
    approve_parser.add_argument("id", type=int, help="Suggestion ID to approve")

    # test
    test_parser = subparsers.add_parser("test", help="Test a rule")
    test_parser.add_argument("rule", help="Rule name to test")
    test_parser.add_argument("--limit", type=int, default=20, help="Markets to test")

    # add-keyword
    keyword_parser = subparsers.add_parser("add-keyword", help="Add keyword to rule")
    keyword_parser.add_argument("rule", help="Rule name")
    keyword_parser.add_argument("keyword", help="Keyword to add")

    # disable
    disable_parser = subparsers.add_parser("disable", help="Disable a rule")
    disable_parser.add_argument("rule", help="Rule name to disable")

    # pending improvements (from learning loop)
    subparsers.add_parser("pending", help="Show pending improvements from learning loop")

    # apply-improvement
    apply_imp_parser = subparsers.add_parser("apply-improvement", help="Apply a pending improvement")
    apply_imp_parser.add_argument("id", type=int, help="Improvement ID to apply")

    # reject-improvement
    reject_imp_parser = subparsers.add_parser("reject-improvement", help="Reject a pending improvement")
    reject_imp_parser.add_argument("id", type=int, help="Improvement ID to reject")

    # recategorize
    recat_parser = subparsers.add_parser("recategorize", help="Recategorize a market")
    recat_parser.add_argument("market_id", type=int, help="Market ID to recategorize")
    recat_parser.add_argument("--l1", help="Manual L1 category (if not set, uses Claude)")
    recat_parser.add_argument("--l2", help="Manual L2 category")
    recat_parser.add_argument("--l3", help="Manual L3 category")
    recat_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "mismatches":
        cmd_mismatches(args)
    elif args.command == "suggestions":
        cmd_suggestions(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "add-keyword":
        cmd_add_keyword(args)
    elif args.command == "disable":
        cmd_disable(args)
    elif args.command == "pending":
        cmd_pending(args)
    elif args.command == "apply-improvement":
        cmd_apply_improvement(args)
    elif args.command == "reject-improvement":
        cmd_reject_improvement(args)
    elif args.command == "recategorize":
        cmd_recategorize(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
