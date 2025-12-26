"""
Rule-based market categorization using database-stored patterns.

This service loads rules from the categorization_rules table and applies
pattern matching to categorize markets. Rules are matched by keywords
and L3 is detected via regex patterns.

Performance: ~0.1ms per market (10,000x faster than API calls)
"""

import re
from dataclasses import dataclass
from typing import Optional
import structlog

from sqlalchemy import select, update

from src.db.database import get_session
from src.db.models import CategorizationRule, Market
from src.models.taxonomy import validate_taxonomy, TAXONOMY

logger = structlog.get_logger()


@dataclass
class RuleResult:
    """Result of rule-based categorization."""
    market_id: int
    l1: str
    l2: str
    l3: str
    rule_id: int
    rule_name: str


class RuleCategorizer:
    """
    Rule-based categorization engine using database-stored patterns.

    Pattern matching strategy:
    1. Keywords for L1/L2 identification (fast string matching)
    2. Regex patterns for L3 detection (spread, O/U, direction, etc.)
    3. Negative keywords to exclude false positives
    """

    def __init__(self):
        """Initialize and load rules from database."""
        self.rules: list[CategorizationRule] = []
        self._compiled_l3_patterns: dict[int, dict[str, list[re.Pattern]]] = {}
        self._load_rules()

    def _load_rules(self) -> None:
        """Load enabled rules from database and compile patterns."""
        with get_session() as session:
            result = session.execute(
                select(CategorizationRule)
                .where(CategorizationRule.enabled == True)
                .order_by(CategorizationRule.id)
            )
            self.rules = list(result.scalars().all())

            # Detach from session
            for rule in self.rules:
                session.expunge(rule)

        # Compile L3 patterns for each rule
        for rule in self.rules:
            self._compiled_l3_patterns[rule.id] = {}
            if rule.l3_patterns:
                for l3, patterns in rule.l3_patterns.items():
                    self._compiled_l3_patterns[rule.id][l3] = [
                        re.compile(p, re.IGNORECASE) for p in patterns
                    ]

        logger.info("Loaded categorization rules", count=len(self.rules))

    def reload_rules(self) -> None:
        """Reload rules from database (call after rule changes)."""
        self._load_rules()

    def categorize(self, market: dict) -> Optional[RuleResult]:
        """
        Attempt to categorize a single market using rules.

        Args:
            market: Dict with 'id', 'question', 'description', 'event_title'

        Returns:
            RuleResult if a rule matched, None otherwise
        """
        text = self._build_search_text(market)
        text_lower = text.lower()

        for rule in self.rules:
            # Check negative keywords first (exclusions)
            if self._has_negative_keywords(text_lower, rule):
                continue

            # Check for primary keywords
            if not self._has_keywords(text_lower, rule):
                continue

            # Rule matched - now detect L3
            l3 = self._detect_l3(text, rule)

            # Validate the taxonomy combination
            if validate_taxonomy(rule.l1, rule.l2, l3):
                logger.debug(
                    "Rule match",
                    market_id=market["id"],
                    rule=rule.name,
                    l1=rule.l1,
                    l2=rule.l2,
                    l3=l3,
                )
                return RuleResult(
                    market_id=market["id"],
                    l1=rule.l1,
                    l2=rule.l2,
                    l3=l3,
                    rule_id=rule.id,
                    rule_name=rule.name,
                )

        return None

    def categorize_batch(
        self,
        markets: list[dict],
    ) -> tuple[list[RuleResult], list[dict]]:
        """
        Categorize multiple markets.

        Args:
            markets: List of market dicts

        Returns:
            Tuple of (matched_results, unmatched_markets)
        """
        matched = []
        unmatched = []

        for market in markets:
            result = self.categorize(market)
            if result:
                matched.append(result)
            else:
                unmatched.append(market)

        logger.info(
            "Rule categorization batch complete",
            total=len(markets),
            matched=len(matched),
            unmatched=len(unmatched),
        )

        return matched, unmatched

    def save_results(self, results: list[RuleResult]) -> int:
        """
        Save categorization results to database.

        Args:
            results: List of RuleResult to save

        Returns:
            Number of markets updated
        """
        from datetime import datetime, timezone

        if not results:
            return 0

        with get_session() as session:
            for result in results:
                session.execute(
                    update(Market)
                    .where(Market.id == result.market_id)
                    .values(
                        category_l1=result.l1,
                        category_l2=result.l2,
                        category_l3=result.l3,
                        categorization_method="rule",
                        matched_rule_id=result.rule_id,
                        categorized_at=datetime.now(timezone.utc),
                    )
                )

                # Update rule match count
                session.execute(
                    update(CategorizationRule)
                    .where(CategorizationRule.id == result.rule_id)
                    .values(times_matched=CategorizationRule.times_matched + 1)
                )

            session.commit()

        logger.info("Saved rule categorization results", count=len(results))
        return len(results)

    def _build_search_text(self, market: dict) -> str:
        """Combine market fields into searchable text."""
        parts = [
            market.get("question", ""),
            market.get("description", "") or "",
            market.get("event_title", "") or "",
        ]
        return " ".join(parts)

    def _has_keywords(self, text_lower: str, rule: CategorizationRule) -> bool:
        """Check if text contains any of the rule's keywords (word boundary matching)."""
        keywords = rule.keywords or []
        for kw in keywords:
            # Use word boundary matching to avoid partial matches
            # e.g., "SOL" should not match "resolve"
            pattern = r'\b' + re.escape(kw.lower()) + r'\b'
            if re.search(pattern, text_lower):
                return True
        return False

    def _has_negative_keywords(self, text_lower: str, rule: CategorizationRule) -> bool:
        """Check if text contains any negative keywords (word boundary matching)."""
        negative = rule.negative_keywords or []
        for kw in negative:
            pattern = r'\b' + re.escape(kw.lower()) + r'\b'
            if re.search(pattern, text_lower):
                return True
        return False

    # L3 priority order - more specific patterns first
    L3_PRIORITY = [
        "DIRECTION_15MIN",
        "DIRECTION_HOURLY",
        "DIRECTION_DAILY",
        "ABOVE_THRESHOLD",
        "BELOW_THRESHOLD",
        "PRICE_RANGE",
        "REACH_TARGET",
        "PRICE_MOVEMENT",
    ]

    def _detect_l3(self, text: str, rule: CategorizationRule) -> str:
        """
        Detect L3 category based on text patterns.

        Uses rule-specific L3 patterns in priority order, then falls back to default.
        Priority order ensures time-based patterns are checked before price patterns.
        """
        # Check rule-specific L3 patterns in priority order
        if rule.id in self._compiled_l3_patterns:
            patterns_dict = self._compiled_l3_patterns[rule.id]
            # Check in priority order
            for l3 in self.L3_PRIORITY:
                if l3 in patterns_dict:
                    for pattern in patterns_dict[l3]:
                        if pattern.search(text):
                            return l3
            # Check any remaining L3s not in priority list
            for l3, patterns in patterns_dict.items():
                if l3 not in self.L3_PRIORITY:
                    for pattern in patterns:
                        if pattern.search(text):
                            return l3

        # Use default L3 if available
        if rule.l3_default:
            return rule.l3_default

        # Fall back to first valid L3 for this L1/L2
        valid_l3s = TAXONOMY.get(rule.l1, {}).get(rule.l2, [])
        if valid_l3s:
            return valid_l3s[0]

        return "UNCLASSIFIED"

    def get_stats(self) -> dict:
        """Get statistics about loaded rules."""
        by_l1 = {}
        for rule in self.rules:
            by_l1[rule.l1] = by_l1.get(rule.l1, 0) + 1

        return {
            "total_rules": len(self.rules),
            "rules_by_l1": by_l1,
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "l1": r.l1,
                    "l2": r.l2,
                    "keywords_count": len(r.keywords or []),
                    "times_matched": r.times_matched,
                    "accuracy": r.accuracy,
                }
                for r in self.rules
            ],
        }


# Singleton instance for reuse
_categorizer: Optional[RuleCategorizer] = None


def get_rule_categorizer() -> RuleCategorizer:
    """Get or create the singleton rule categorizer."""
    global _categorizer
    if _categorizer is None:
        _categorizer = RuleCategorizer()
    return _categorizer


def reload_rules() -> None:
    """Reload rules from database."""
    global _categorizer
    if _categorizer is not None:
        _categorizer.reload_rules()
