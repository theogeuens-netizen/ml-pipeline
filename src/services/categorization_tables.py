"""
Shared DDL helpers for categorization run tracking.

These helpers are intentionally idempotent and can be called from both
scripts (psycopg2) and the API (SQLAlchemy) to ensure tables exist
without requiring a dedicated migration step yet.
"""

from sqlalchemy import text

# DDL strings kept centralized for consistency
DDL_RUNS = """
CREATE TABLE IF NOT EXISTS categorization_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    model TEXT,
    batch_size INT,
    markets_fetched INT,
    markets_sent INT,
    markets_saved INT,
    quarantined INT,
    retry_count INT,
    status TEXT,
    prompt_tokens INT,
    completion_tokens INT,
    total_tokens INT,
    error TEXT
)
"""

DDL_QUARANTINE = """
CREATE TABLE IF NOT EXISTS categorization_quarantine (
    id SERIAL PRIMARY KEY,
    run_id UUID REFERENCES categorization_runs(run_id) ON DELETE CASCADE,
    market_id INT,
    reason TEXT,
    raw_response TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
)
"""


def ensure_tables_psycopg(conn) -> None:
    """Ensure tracking tables exist using a psycopg2 connection."""
    with conn.cursor() as cur:
        cur.execute(DDL_RUNS)
        cur.execute(DDL_QUARANTINE)
    conn.commit()


def ensure_tables_sqlalchemy(session) -> None:
    """Ensure tracking tables exist using a SQLAlchemy session."""
    session.execute(text(DDL_RUNS))
    session.execute(text(DDL_QUARANTINE))
    session.commit()
