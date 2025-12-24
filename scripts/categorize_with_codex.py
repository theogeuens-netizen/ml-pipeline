#!/usr/bin/env python3
"""
Cron-safe market categorization using OpenAI Codex.

Key features:
- Concurrency-safe via DB row locks (SKIP LOCKED) + external flock in cron wrapper
- Structured JSON logs with run_id
- Exponential backoff + jitter on Codex calls
- Strict JSON schema enforcement and Pydantic validation
- Quarantine for malformed model outputs
- Deterministic DB writes in a single transaction
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, ValidationError, field_validator
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

# Optional OpenAI import (only needed when using API mode)
try:  # pragma: no cover - import guard
    from openai import OpenAI, OpenAIError
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

    class OpenAIError(Exception):  # type: ignore
        """Fallback OpenAIError when openai package is not installed."""

# Ensure project imports resolve when run from cron
sys.path.insert(0, str(os.path.dirname(os.path.dirname(__file__))))

from src.models.taxonomy import get_taxonomy_compact, validate_taxonomy  # noqa: E402
from src.services.categorization_tables import ensure_tables_psycopg  # noqa: E402

# === Configuration ===
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "polymarket_ml"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.1-codex-mini")
CODEX_TIMEOUT = int(os.getenv("CODEX_TIMEOUT_SECONDS", "45"))
CODEX_MAX_TOKENS = int(os.getenv("CODEX_MAX_TOKENS", os.getenv("CODEX_MAX_OUTPUT_TOKENS", "800")))
CODEX_TEMPERATURE = float(os.getenv("CODEX_TEMPERATURE", "0"))
CODEX_BASE_URL = os.getenv("CODEX_BASE_URL")  # Optional override
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("CODEX_API_KEY")

LOG_PATH = os.getenv("CODEX_LOG_PATH", "/tmp/polymarket-categorize.jsonl")
CODEX_BIN = os.getenv("CODEX_BIN", "codex")

# === Prompt template ===
EXAMPLE_OUTPUT = '[{"id":123,"l1":"SPORTS","l2":"NFL","l3":"MATCH_WINNER","confidence":"high","reasoning":"Team names imply NFL match"}]'

PROMPT_TEMPLATE = """You are an expert classification service. Categorize each Polymarket market into the strict L1/L2/L3 taxonomy provided.

Rules:
- Only use values present in the taxonomy.
- If unsure, use OTHER/MISCELLANEOUS/UNCLASSIFIED.
- Return EXACTLY one JSON array, nothing else.

Taxonomy (JSON):
{taxonomy_json}

Markets (JSON):
{markets_json}

Output JSON array schema:
- id: integer market id
- l1: string L1
- l2: string L2
- l3: string L3
- confidence: "high" | "medium" | "low"
- reasoning: optional short string

Example output:
{example_output}
"""


# === Models & helpers ===
class CategorizationItem(BaseModel):
    """Validated model for a single market categorization."""

    id: int
    l1: str
    l2: str
    l3: str
    confidence: str
    reasoning: str | None = None

    @field_validator("l1", "l2", "l3", mode="before")
    @classmethod
    def normalize_upper(cls, value: Any) -> str:
        return str(value).upper() if value is not None else value

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> str:
        allowed = {"high", "medium", "low"}
        val = str(value).lower()
        if val not in allowed:
            raise ValueError(f"confidence must be one of {allowed}")
        return val


@dataclass
class RunMetrics:
    run_id: str
    started_at: datetime
    model: str
    batch_size: int
    markets_fetched: int = 0
    markets_sent: int = 0
    markets_saved: int = 0
    quarantined: int = 0
    retry_count: int = 0
    status: str = "running"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None


def log_event(run_id: str, event: str, **kwargs: Any) -> None:
    """Structured JSON logging to stdout/file."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "event": event,
        **kwargs,
    }
    line = json.dumps(entry, ensure_ascii=True)
    # Always write to stdout for cron capture
    print(line)
    # Also append to log file for auditing
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_uncategorized_markets(conn, limit: int) -> List[dict]:
    """
    Fetch uncategorized markets with row locks to avoid double-processing.
    Uses SKIP LOCKED to stay idempotent under overlap.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, question, COALESCE(description, '') AS description
            FROM markets
            WHERE active = true AND category_l1 IS NULL
            ORDER BY id DESC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def build_prompt(markets: Iterable[dict]) -> str:
    """Build prompt payload with compact taxonomy."""
    markets_payload = [
        {
            "id": m["id"],
            "question": (m["question"] or "")[:220],
            "description": (m.get("description") or "")[:220],
        }
        for m in markets
    ]
    return PROMPT_TEMPLATE.format(
        taxonomy_json=get_taxonomy_compact(),
        markets_json=json.dumps(markets_payload, ensure_ascii=True),
        example_output=EXAMPLE_OUTPUT,
    )


class CodexAPIClient:
    """Codex via OpenAI API (requires API key)."""

    def __init__(self, model: str, max_tokens: int, temperature: float, timeout: int, run_id: str):
        if OpenAI is None:
            raise RuntimeError("openai package not installed; install or use Codex CLI (no API key).")
        self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=CODEX_BASE_URL)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.run_id = run_id

    @retry(
        wait=wait_random_exponential(multiplier=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(OpenAIError),
        reraise=True,
    )
    def complete(self, prompt: str) -> Tuple[str, dict]:
        """Call Codex API with retries. Returns content string and usage dict."""
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            messages=[
                {
                    "role": "system",
                    "content": "Return strict JSON only. Do not include markdown or prose.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "market_categories",
                    "schema": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "l1": {"type": "string"},
                                "l2": {"type": "string"},
                                "l3": {"type": "string"},
                                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["id", "l1", "l2", "l3", "confidence"],
                            "additionalProperties": False,
                        },
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            user=self.run_id,
        )

        content = response.choices[0].message.content or "[]"
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }
        return content, usage


class CodexCLIClient:
    """
    Codex via CLI (no API key required, uses local Codex subscription).
    Uses `codex exec --json` and reads the final agent_message.
    """

    def __init__(self, model: str, timeout: int):
        if not shutil.which(CODEX_BIN):
            raise RuntimeError(f"Codex CLI '{CODEX_BIN}' not found in PATH")
        self.model = model
        self.timeout = timeout

    @retry(
        wait=wait_random_exponential(multiplier=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def complete(self, prompt: str) -> Tuple[str, dict]:
        """Call Codex CLI, parse JSONL, and return final agent message + usage."""
        import subprocess
        import json

        cmd = [
            CODEX_BIN,
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "-m",
            self.model,
            prompt,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Codex CLI failed (exit {result.returncode}): {result.stderr[:200]}")

        last_message = None
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for line in result.stdout.splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "item.completed" and evt.get("item", {}).get("type") == "agent_message":
                last_message = evt["item"].get("text")
            if evt.get("type") == "turn.completed" and "usage" in evt:
                usage.update(evt["usage"])

        if not last_message:
            raise RuntimeError("Codex CLI returned no agent_message")

        return last_message.strip(), usage


def parse_response(raw: str, market_ids: set[int]) -> Tuple[list[CategorizationItem], list[dict]]:
    """Parse and validate Codex response. Returns (valid, quarantined_raw)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from Codex: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("Codex response must be a JSON array")

    valid: list[CategorizationItem] = []
    quarantined: list[dict] = []

    for item in data:
        try:
            parsed = CategorizationItem.model_validate(item)
        except ValidationError as exc:
            quarantined.append({"item": item, "reason": f"schema_error: {exc}"})
            continue

        if parsed.id not in market_ids:
            quarantined.append({"item": item, "reason": "unknown_market_id"})
            continue

        if not validate_taxonomy(parsed.l1, parsed.l2, parsed.l3):
            quarantined.append({"item": item, "reason": "invalid_taxonomy"})
            continue

        valid.append(parsed)

    return valid, quarantined


def save_categorizations(conn, results: list[CategorizationItem]) -> int:
    """Persist categorizations atomically."""
    if not results:
        return 0

    now = datetime.now(timezone.utc)
    updated = 0

    with conn.cursor() as cur:
        for res in results:
            cur.execute(
                """
                UPDATE markets
                SET category_l1 = %s,
                    category_l2 = %s,
                    category_l3 = %s,
                    categorization_method = 'codex',
                    categorization_confidence = %s,
                    categorized_at = %s,
                    matched_rule_id = NULL
                WHERE id = %s AND category_l1 IS NULL
                """,
                (res.l1, res.l2, res.l3, res.confidence, now, res.id),
            )
            updated += cur.rowcount
    conn.commit()
    return updated


def save_quarantine(conn, run_id: str, quarantined: list[dict]) -> None:
    """Persist quarantined outputs for follow-up triage."""
    if not quarantined:
        return

    with conn.cursor() as cur:
        for item in quarantined:
            cur.execute(
                """
                INSERT INTO categorization_quarantine (run_id, market_id, reason, raw_response)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    run_id,
                    item.get("item", {}).get("id"),
                    item.get("reason"),
                    json.dumps(item)[:2000],
                ),
            )
    conn.commit()


def record_run(conn, metrics: RunMetrics) -> None:
    """Upsert run metrics."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO categorization_runs (
                run_id, started_at, completed_at, model, batch_size, markets_fetched,
                markets_sent, markets_saved, quarantined, retry_count, status,
                prompt_tokens, completion_tokens, total_tokens, error
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                completed_at = EXCLUDED.completed_at,
                markets_fetched = EXCLUDED.markets_fetched,
                markets_sent = EXCLUDED.markets_sent,
                markets_saved = EXCLUDED.markets_saved,
                quarantined = EXCLUDED.quarantined,
                retry_count = EXCLUDED.retry_count,
                status = EXCLUDED.status,
                prompt_tokens = EXCLUDED.prompt_tokens,
                completion_tokens = EXCLUDED.completion_tokens,
                total_tokens = EXCLUDED.total_tokens,
                error = EXCLUDED.error
            """,
            (
                metrics.run_id,
                metrics.started_at,
                datetime.now(timezone.utc),
                metrics.model,
                metrics.batch_size,
                metrics.markets_fetched,
                metrics.markets_sent,
                metrics.markets_saved,
                metrics.quarantined,
                metrics.retry_count,
                metrics.status,
                metrics.prompt_tokens,
                metrics.completion_tokens,
                metrics.total_tokens,
                metrics.error,
            ),
        )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Categorize markets with OpenAI Codex (cron-safe).")
    parser.add_argument("--batch", type=int, default=30, help="Batch size for Codex")
    parser.add_argument("--max-tokens", type=int, default=CODEX_MAX_TOKENS, help="Max output tokens (API mode)")
    parser.add_argument("--run-id", type=str, help="Optional run id (uuid).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = args.run_id or str(uuid.uuid4())
    metrics = RunMetrics(
        run_id=run_id,
        started_at=datetime.now(timezone.utc),
        model=CODEX_MODEL,
        batch_size=args.batch,
    )

    log_event(run_id, "start", model=CODEX_MODEL, batch_size=args.batch)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except Exception as exc:  # noqa: BLE001
        log_event(run_id, "db_connect_failed", error=str(exc))
        return 1

    try:
        ensure_tables_psycopg(conn)

        markets = fetch_uncategorized_markets(conn, args.batch)
        metrics.markets_fetched = len(markets)
        metrics.markets_sent = len(markets)

        if not markets:
            metrics.status = "no_work"
            record_run(conn, metrics)
            log_event(run_id, "no_markets")
            return 0

        prompt = build_prompt(markets)

        # Prefer API when key is available, otherwise fallback to CLI subscription.
        if OPENAI_API_KEY:
            client = CodexAPIClient(
                model=CODEX_MODEL,
                max_tokens=args.max_tokens,
                temperature=CODEX_TEMPERATURE,
                timeout=CODEX_TIMEOUT,
                run_id=run_id,
            )
        else:
            client = CodexCLIClient(
                model=CODEX_MODEL,
                timeout=CODEX_TIMEOUT,
            )

        try:
            content, usage = client.complete(prompt)
            if hasattr(client.complete, "retry"):
                attempt_num = getattr(client.complete.retry, "statistics", {}).get("attempt_number", 1)
                metrics.retry_count = max(0, attempt_num - 1)
            metrics.prompt_tokens = usage.get("prompt_tokens", 0)
            metrics.completion_tokens = usage.get("completion_tokens", 0)
            metrics.total_tokens = usage.get("total_tokens", 0)
        except OpenAIError as exc:
            metrics.status = "failed"
            metrics.error = f"codex_error: {exc}"
            record_run(conn, metrics)
            log_event(run_id, "codex_failed", error=str(exc))
            return 2
        except Exception as exc:  # noqa: BLE001
            metrics.status = "failed"
            metrics.error = f"codex_cli_error: {exc}"
            record_run(conn, metrics)
            log_event(run_id, "codex_failed", error=str(exc))
            return 2

        try:
            valid, quarantined = parse_response(content, {m["id"] for m in markets})
        except Exception as exc:  # noqa: BLE001
            metrics.status = "failed"
            metrics.error = f"parse_error: {exc}"
            record_run(conn, metrics)
            save_quarantine(conn, run_id, [{"item": {}, "reason": f"parse_error: {exc}", "raw": content[:500]}])
            log_event(run_id, "parse_failed", error=str(exc))
            return 3

        metrics.quarantined = len(quarantined)

        saved = save_categorizations(conn, valid)
        metrics.markets_saved = saved
        metrics.status = "success"

        save_quarantine(conn, run_id, quarantined)
        record_run(conn, metrics)

        log_event(
            run_id,
            "completed",
            saved=saved,
            quarantined=len(quarantined),
            fetched=len(markets),
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
