"""
Claude-based market categorization using Claude Code CLI.

This service:
1. Calls Claude Code CLI with market data
2. Parses the JSON response
3. Validates against the taxonomy schema
4. Returns structured category assignments

Models:
- haiku: Fast, cheap, good for bulk categorization
- sonnet: Better quality, used for validation

NOTE: This must run on the HOST machine where Claude CLI is installed,
not inside Docker containers. Use the cron script or run manually.
"""

import json
import subprocess
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
import structlog

from src.models.taxonomy import TAXONOMY, validate_taxonomy, find_best_match

logger = structlog.get_logger()

# CLI settings
CLAUDE_TIMEOUT = 180  # 3 minutes max
DEFAULT_BATCH_SIZE = 15


@dataclass
class CategoryResult:
    """Result of categorizing a single market."""
    market_id: int
    l1: str
    l2: str
    l3: str
    confidence: str  # high, medium, low
    reasoning: Optional[str] = None
    is_valid: bool = True  # Passed taxonomy validation


@dataclass
class BatchResult:
    """Result of categorizing a batch of markets."""
    results: list[CategoryResult]
    total: int
    valid_count: int
    invalid_count: int
    errors: list[str]


CATEGORIZATION_PROMPT = """You are categorizing Polymarket prediction markets into a strict L1/L2/L3 taxonomy.

## TAXONOMY SCHEMA (you MUST use these exact values)

{taxonomy_json}

## MARKETS TO CATEGORIZE

{markets_json}

## INSTRUCTIONS

For each market:
1. Determine L1 (domain) from the question
2. Determine L2 (sub-domain) that MUST be a valid child of L1
3. Determine L3 (market type) that MUST be a valid child of L1+L2
4. Rate confidence: "high" (obvious), "medium" (reasonable inference), "low" (uncertain)

IMPORTANT:
- L2 MUST exist in the taxonomy under the chosen L1
- L3 MUST exist in the taxonomy under the chosen L1+L2
- If unsure, use OTHER/MISCELLANEOUS/UNCLASSIFIED
- Do NOT invent new L2 or L3 values

## OUTPUT FORMAT

Return ONLY a JSON array, no markdown, no explanation:
[
  {{"id": 123, "l1": "CRYPTO", "l2": "BITCOIN", "l3": "DIRECTION_DAILY", "confidence": "high"}},
  {{"id": 456, "l1": "SPORTS", "l2": "NFL", "l3": "MATCH_WINNER", "confidence": "medium", "reasoning": "Team names suggest NFL"}}
]
"""


VALIDATION_PROMPT = """You are verifying if a market categorization is correct.

## MARKET
ID: {market_id}
Question: {question}
Description: {description}

## CURRENT CATEGORIZATION
L1: {current_l1}
L2: {current_l2}
L3: {current_l3}

## TAXONOMY REFERENCE
{taxonomy_json}

## TASK

Is this categorization CORRECT? Consider:
1. Is L1 the right domain?
2. Is L2 the right sub-domain within L1?
3. Is L3 the right market type?

Return ONLY JSON:
{{
  "is_correct": true/false,
  "correct_l1": "...",
  "correct_l2": "...",
  "correct_l3": "...",
  "reasoning": "brief explanation"
}}
"""


class ClaudeCategorizer:
    """Categorize markets using Claude Code CLI."""

    def __init__(self, model: str = "haiku"):
        """
        Initialize categorizer.

        Args:
            model: "haiku" (fast/cheap) or "sonnet" (quality)
        """
        self.model = model
        self.batch_size = DEFAULT_BATCH_SIZE if model == "haiku" else 10

    def categorize_batch(self, markets: list[dict[str, Any]]) -> BatchResult:
        """
        Categorize a batch of markets.

        Args:
            markets: List of dicts with 'id', 'question', 'description'

        Returns:
            BatchResult with categorizations
        """
        if not markets:
            return BatchResult(results=[], total=0, valid_count=0, invalid_count=0, errors=[])

        # Build prompt
        taxonomy_json = json.dumps(TAXONOMY, indent=2)
        markets_json = json.dumps([
            {
                "id": m["id"],
                "question": m["question"],
                "description": (m.get("description") or "")[:300],  # Truncate for tokens
            }
            for m in markets
        ], indent=2)

        prompt = CATEGORIZATION_PROMPT.format(
            taxonomy_json=taxonomy_json,
            markets_json=markets_json,
        )

        # Call Claude
        try:
            response = self._call_claude(prompt)
            results = self._parse_response(response, markets)
        except Exception as e:
            logger.error("Claude categorization failed", error=str(e))
            return BatchResult(
                results=[],
                total=len(markets),
                valid_count=0,
                invalid_count=0,
                errors=[str(e)],
            )

        # Validate and count
        valid_count = sum(1 for r in results if r.is_valid)
        invalid_count = len(results) - valid_count

        return BatchResult(
            results=results,
            total=len(markets),
            valid_count=valid_count,
            invalid_count=invalid_count,
            errors=[],
        )

    def validate_categorization(
        self,
        market_id: int,
        question: str,
        description: str,
        current_l1: str,
        current_l2: str,
        current_l3: str,
    ) -> dict[str, Any]:
        """
        Validate a single categorization using Claude.

        Args:
            market_id: Market ID
            question: Market question
            description: Market description
            current_l1/l2/l3: Current category assignment

        Returns:
            Dict with is_correct, correct_l1/l2/l3, reasoning
        """
        taxonomy_json = json.dumps(TAXONOMY, indent=2)
        prompt = VALIDATION_PROMPT.format(
            market_id=market_id,
            question=question,
            description=description or "",
            current_l1=current_l1,
            current_l2=current_l2,
            current_l3=current_l3 or "N/A",
            taxonomy_json=taxonomy_json,
        )

        try:
            response = self._call_claude(prompt, timeout=60)
            return self._parse_validation_response(response)
        except Exception as e:
            logger.error("Validation failed", market_id=market_id, error=str(e))
            return {
                "is_correct": None,
                "error": str(e),
            }

    def _call_claude(self, prompt: str, timeout: int = CLAUDE_TIMEOUT) -> str:
        """Call Claude Code CLI and return response."""
        cmd = ["claude", "-p", prompt]
        if self.model == "haiku":
            cmd.extend(["--model", "haiku"])
        # sonnet is default

        logger.debug("Calling Claude Code", model=self.model, prompt_len=len(prompt))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                logger.error("Claude CLI failed", stderr=result.stderr[:500])
                raise RuntimeError(f"Claude exit code {result.returncode}: {result.stderr[:200]}")

            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            logger.error("Claude timed out", timeout=timeout)
            raise

    def _parse_response(self, response: str, markets: list[dict]) -> list[CategoryResult]:
        """Parse Claude's JSON response into CategoryResults."""
        # Extract JSON array from response
        response = response.strip()

        # Find JSON array bounds
        start = response.find("[")
        end = response.rfind("]") + 1

        if start == -1 or end == 0:
            logger.error("No JSON array in response", response=response[:300])
            raise ValueError("No JSON array found in response")

        json_str = response[start:end]

        try:
            items = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("JSON parse error", error=str(e), json_str=json_str[:300])
            raise

        # Build results with validation
        results = []
        market_ids = {m["id"] for m in markets}

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
            reasoning = item.get("reasoning")

            # Validate against taxonomy
            is_valid = validate_taxonomy(l1, l2, l3) if l3 else l1 in TAXONOMY

            if not is_valid:
                # Try to find best match
                l1, l2, l3 = find_best_match(l1, l2, l3)
                is_valid = False  # Mark as originally invalid

            results.append(CategoryResult(
                market_id=market_id,
                l1=l1,
                l2=l2,
                l3=l3 or "UNCLASSIFIED",
                confidence=confidence,
                reasoning=reasoning,
                is_valid=is_valid,
            ))

        return results

    def _parse_validation_response(self, response: str) -> dict[str, Any]:
        """Parse validation response."""
        response = response.strip()

        # Find JSON object
        start = response.find("{")
        end = response.rfind("}") + 1

        if start == -1 or end == 0:
            raise ValueError("No JSON object in response")

        json_str = response[start:end]

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Validation JSON parse error", error=str(e))
            raise


def get_claude_categorizer(model: str = "haiku") -> ClaudeCategorizer:
    """Factory function for ClaudeCategorizer."""
    return ClaudeCategorizer(model=model)
