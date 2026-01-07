#!/usr/bin/env python3
"""
Sync CSGO tables from PostgreSQL to BigQuery.

Flow:
1. Export each CSGO table from PostgreSQL to GCS as CSV
2. Load CSV into BigQuery (replace mode)

Usage:
    python scripts/export/sync_csgo_to_bq.py                  # Full sync all tables
    python scripts/export/sync_csgo_to_bq.py --table csgo_matches  # Single table
    python scripts/export/sync_csgo_to_bq.py --dry-run        # Preview only
"""

import os
import sys
import gzip
import logging
from datetime import datetime
from io import BytesIO
from typing import Optional

import psycopg2
from google.cloud import storage, bigquery

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
GCS_BUCKET = "polymarket-backup"
GCS_PREFIX = "csgo"
BQ_PROJECT = "polymarket-ml"
BQ_DATASET = "polymarket"

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/polymarket_ml")
CREDENTIALS_PATH = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "/home/theomlmachine/polymarket-ml/gcp-credentials.json"
)

# CSGO table configurations with BigQuery schema hints
CSGO_TABLES = {
    "csgo_matches": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("market_id", "INTEGER"),
            bigquery.SchemaField("gamma_id", "INTEGER"),
            bigquery.SchemaField("condition_id", "STRING"),
            bigquery.SchemaField("team_yes", "STRING"),
            bigquery.SchemaField("team_no", "STRING"),
            bigquery.SchemaField("game_start_time", "TIMESTAMP"),
            bigquery.SchemaField("game_start_override", "BOOLEAN"),
            bigquery.SchemaField("end_date", "TIMESTAMP"),
            bigquery.SchemaField("tournament", "STRING"),
            bigquery.SchemaField("format", "STRING"),
            bigquery.SchemaField("market_type", "STRING"),
            bigquery.SchemaField("group_item_title", "STRING"),
            bigquery.SchemaField("game_id", "STRING"),
            bigquery.SchemaField("subscribed", "BOOLEAN"),
            bigquery.SchemaField("gamma_data", "STRING"),  # JSONB as string
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
            bigquery.SchemaField("closed", "BOOLEAN"),
            bigquery.SchemaField("resolved", "BOOLEAN"),
            bigquery.SchemaField("closed_at", "TIMESTAMP"),
            bigquery.SchemaField("accepting_orders", "BOOLEAN"),
            bigquery.SchemaField("outcome", "STRING"),
            bigquery.SchemaField("last_status_check", "TIMESTAMP"),
            bigquery.SchemaField("yes_price", "FLOAT"),
            bigquery.SchemaField("no_price", "FLOAT"),
            bigquery.SchemaField("spread", "FLOAT"),
            bigquery.SchemaField("volume_total", "FLOAT"),
            bigquery.SchemaField("volume_24h", "FLOAT"),
            bigquery.SchemaField("liquidity", "FLOAT"),
            bigquery.SchemaField("map_number", "INTEGER"),
        ],
        "select_cols": """
            id, market_id, gamma_id, condition_id, team_yes, team_no,
            game_start_time, game_start_override, end_date, tournament, format,
            market_type, group_item_title, game_id, subscribed, gamma_data::text,
            created_at, updated_at, closed, resolved, closed_at, accepting_orders,
            outcome, last_status_check, yes_price::float, no_price::float,
            spread::float, volume_total::float, volume_24h::float, liquidity::float,
            map_number
        """,
    },
    "csgo_price_ticks": {
        "timestamp_col": "timestamp",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("market_id", "INTEGER"),
            bigquery.SchemaField("timestamp", "TIMESTAMP"),
            bigquery.SchemaField("token_type", "STRING"),
            bigquery.SchemaField("event_type", "STRING"),
            bigquery.SchemaField("price", "FLOAT"),
            bigquery.SchemaField("best_bid", "FLOAT"),
            bigquery.SchemaField("best_ask", "FLOAT"),
            bigquery.SchemaField("spread", "FLOAT"),
            bigquery.SchemaField("trade_size", "FLOAT"),
            bigquery.SchemaField("trade_side", "STRING"),
            bigquery.SchemaField("price_velocity_1m", "FLOAT"),
        ],
        "select_cols": """
            id, market_id, timestamp, token_type, event_type,
            price::float, best_bid::float, best_ask::float, spread::float,
            trade_size::float, trade_side, price_velocity_1m::float
        """,
    },
    "csgo_trades": {
        "timestamp_col": "created_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("position_id", "INTEGER"),
            bigquery.SchemaField("leg_id", "INTEGER"),
            bigquery.SchemaField("token_id", "STRING"),
            bigquery.SchemaField("side", "STRING"),
            bigquery.SchemaField("shares", "FLOAT"),
            bigquery.SchemaField("price", "FLOAT"),
            bigquery.SchemaField("cost_usd", "FLOAT"),
            bigquery.SchemaField("best_bid", "FLOAT"),
            bigquery.SchemaField("best_ask", "FLOAT"),
            bigquery.SchemaField("spread", "FLOAT"),
            bigquery.SchemaField("slippage", "FLOAT"),
            bigquery.SchemaField("trigger_tick_id", "STRING"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("team_yes", "STRING"),
            bigquery.SchemaField("team_no", "STRING"),
            bigquery.SchemaField("format", "STRING"),
            bigquery.SchemaField("map_number", "INTEGER"),
            bigquery.SchemaField("game_start_time", "TIMESTAMP"),
        ],
        "select_cols": """
            id, position_id, leg_id, token_id, side,
            shares::float, price::float, cost_usd::float,
            best_bid::float, best_ask::float, spread::float, slippage::float,
            trigger_tick_id, created_at, team_yes, team_no, format, map_number,
            game_start_time
        """,
    },
    "csgo_positions": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("strategy_name", "STRING"),
            bigquery.SchemaField("market_id", "INTEGER"),
            bigquery.SchemaField("condition_id", "STRING"),
            bigquery.SchemaField("token_id", "STRING"),
            bigquery.SchemaField("token_type", "STRING"),
            bigquery.SchemaField("side", "STRING"),
            bigquery.SchemaField("initial_shares", "FLOAT"),
            bigquery.SchemaField("remaining_shares", "FLOAT"),
            bigquery.SchemaField("avg_entry_price", "FLOAT"),
            bigquery.SchemaField("cost_basis", "FLOAT"),
            bigquery.SchemaField("current_price", "FLOAT"),
            bigquery.SchemaField("unrealized_pnl", "FLOAT"),
            bigquery.SchemaField("realized_pnl", "FLOAT"),
            bigquery.SchemaField("spread_id", "INTEGER"),
            bigquery.SchemaField("team_yes", "STRING"),
            bigquery.SchemaField("team_no", "STRING"),
            bigquery.SchemaField("game_start_time", "TIMESTAMP"),
            bigquery.SchemaField("format", "STRING"),
            bigquery.SchemaField("status", "STRING"),
            bigquery.SchemaField("close_reason", "STRING"),
            bigquery.SchemaField("opened_at", "TIMESTAMP"),
            bigquery.SchemaField("closed_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, strategy_name, market_id, condition_id, token_id, token_type, side,
            initial_shares::float, remaining_shares::float, avg_entry_price::float,
            cost_basis::float, current_price::float, unrealized_pnl::float,
            realized_pnl::float, spread_id, team_yes, team_no, game_start_time,
            format, status, close_reason, opened_at, closed_at, updated_at
        """,
    },
    "csgo_position_legs": {
        "timestamp_col": "created_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("position_id", "INTEGER"),
            bigquery.SchemaField("leg_type", "STRING"),
            bigquery.SchemaField("shares_delta", "FLOAT"),
            bigquery.SchemaField("price", "FLOAT"),
            bigquery.SchemaField("cost_delta", "FLOAT"),
            bigquery.SchemaField("realized_pnl", "FLOAT"),
            bigquery.SchemaField("trigger_price", "FLOAT"),
            bigquery.SchemaField("trigger_reason", "STRING"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, position_id, leg_type, shares_delta::float, price::float,
            cost_delta::float, realized_pnl::float, trigger_price::float,
            trigger_reason, created_at
        """,
    },
    "csgo_spreads": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("strategy_name", "STRING"),
            bigquery.SchemaField("market_id", "INTEGER"),
            bigquery.SchemaField("condition_id", "STRING"),
            bigquery.SchemaField("spread_type", "STRING"),
            bigquery.SchemaField("yes_position_id", "INTEGER"),
            bigquery.SchemaField("no_position_id", "INTEGER"),
            bigquery.SchemaField("total_cost_basis", "FLOAT"),
            bigquery.SchemaField("total_realized_pnl", "FLOAT"),
            bigquery.SchemaField("total_unrealized_pnl", "FLOAT"),
            bigquery.SchemaField("team_yes", "STRING"),
            bigquery.SchemaField("team_no", "STRING"),
            bigquery.SchemaField("entry_yes_price", "FLOAT"),
            bigquery.SchemaField("status", "STRING"),
            bigquery.SchemaField("opened_at", "TIMESTAMP"),
            bigquery.SchemaField("closed_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, strategy_name, market_id, condition_id, spread_type,
            yes_position_id, no_position_id, total_cost_basis::float,
            total_realized_pnl::float, total_unrealized_pnl::float,
            team_yes, team_no, entry_yes_price::float, status,
            opened_at, closed_at, updated_at
        """,
    },
    "csgo_strategy_state": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("strategy_name", "STRING"),
            bigquery.SchemaField("allocated_usd", "FLOAT"),
            bigquery.SchemaField("available_usd", "FLOAT"),
            bigquery.SchemaField("total_realized_pnl", "FLOAT"),
            bigquery.SchemaField("total_unrealized_pnl", "FLOAT"),
            bigquery.SchemaField("trade_count", "INTEGER"),
            bigquery.SchemaField("win_count", "INTEGER"),
            bigquery.SchemaField("loss_count", "INTEGER"),
            bigquery.SchemaField("max_drawdown_usd", "FLOAT"),
            bigquery.SchemaField("high_water_mark", "FLOAT"),
            bigquery.SchemaField("is_active", "BOOLEAN"),
            bigquery.SchemaField("last_trade_at", "TIMESTAMP"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, strategy_name, allocated_usd::float, available_usd::float,
            total_realized_pnl::float, total_unrealized_pnl::float,
            trade_count, win_count, loss_count, max_drawdown_usd::float,
            high_water_mark::float, is_active, last_trade_at, created_at, updated_at
        """,
    },
    "csgo_strategy_market_state": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("strategy_name", "STRING"),
            bigquery.SchemaField("market_id", "INTEGER"),
            bigquery.SchemaField("condition_id", "STRING"),
            bigquery.SchemaField("stage", "STRING"),
            bigquery.SchemaField("entry_price", "FLOAT"),
            bigquery.SchemaField("switch_price", "FLOAT"),
            bigquery.SchemaField("exit_price", "FLOAT"),
            bigquery.SchemaField("high_water_mark", "FLOAT"),
            bigquery.SchemaField("low_water_mark", "FLOAT"),
            bigquery.SchemaField("switches_count", "INTEGER"),
            bigquery.SchemaField("reentries_count", "INTEGER"),
            bigquery.SchemaField("custom_state", "STRING"),  # JSONB as string
            bigquery.SchemaField("team_yes", "STRING"),
            bigquery.SchemaField("team_no", "STRING"),
            bigquery.SchemaField("current_side", "STRING"),
            bigquery.SchemaField("is_active", "BOOLEAN"),
            bigquery.SchemaField("stage_entered_at", "TIMESTAMP"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, strategy_name, market_id, condition_id, stage,
            entry_price::float, switch_price::float, exit_price::float,
            high_water_mark::float, low_water_mark::float,
            switches_count, reentries_count, custom_state::text,
            team_yes, team_no, current_side, is_active,
            stage_entered_at, created_at, updated_at
        """,
    },
    "csgo_teams": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("team_name", "STRING"),
            bigquery.SchemaField("wins", "INTEGER"),
            bigquery.SchemaField("losses", "INTEGER"),
            bigquery.SchemaField("total_matches", "INTEGER"),
            bigquery.SchemaField("win_rate_pct", "FLOAT"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, team_name, wins, losses, total_matches,
            win_rate_pct::float, created_at, updated_at
        """,
    },
    "csgo_h2h": {
        "timestamp_col": "updated_at",
        "order_by": "id",
        "bq_schema": [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("team1_name", "STRING"),
            bigquery.SchemaField("team2_name", "STRING"),
            bigquery.SchemaField("team1_wins", "INTEGER"),
            bigquery.SchemaField("team2_wins", "INTEGER"),
            bigquery.SchemaField("total_matches", "INTEGER"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ],
        "select_cols": """
            id, team1_name, team2_name, team1_wins, team2_wins,
            total_matches, updated_at
        """,
    },
}


def parse_db_url(url: str) -> dict:
    """Parse PostgreSQL URL into connection params."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
        "dbname": parsed.path.lstrip("/") or "polymarket_ml",
    }


def get_gcs_client() -> storage.Client:
    """Initialize GCS client with credentials."""
    if os.path.exists(CREDENTIALS_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
        return storage.Client.from_service_account_json(CREDENTIALS_PATH)
    return storage.Client()


def get_bq_client() -> bigquery.Client:
    """Initialize BigQuery client with credentials."""
    if os.path.exists(CREDENTIALS_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
        return bigquery.Client.from_service_account_json(CREDENTIALS_PATH, project=BQ_PROJECT)
    return bigquery.Client(project=BQ_PROJECT)


def export_table_to_gcs(
    conn,
    gcs_client: storage.Client,
    table_name: str,
    config: dict,
    dry_run: bool = False,
) -> dict:
    """Export a PostgreSQL table to GCS as gzipped CSV."""
    select_cols = config.get("select_cols", "*")
    order_by = config["order_by"]

    # Get row count
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        total_rows = cur.fetchone()[0]

    if total_rows == 0:
        logger.info(f"[{table_name}] No rows to export")
        return {"table": table_name, "rows": 0}

    logger.info(f"[{table_name}] {total_rows:,} rows to export")

    if dry_run:
        return {"table": table_name, "rows": total_rows, "dry_run": True}

    # Build COPY query
    copy_query = f"""
        COPY (
            SELECT {select_cols}
            FROM {table_name}
            ORDER BY {order_by}
        ) TO STDOUT WITH CSV HEADER
    """

    # Export to GCS
    today = datetime.utcnow().strftime("%Y-%m-%d")
    gcs_path = f"{GCS_PREFIX}/{table_name}/{today}/{table_name}.csv.gz"

    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)

    # Stream with gzip compression
    buffer = BytesIO()
    with gzip.GzipFile(mode='wb', fileobj=buffer) as gz:
        with conn.cursor() as cur:
            cur.copy_expert(copy_query, gz)

    buffer.seek(0)
    blob.upload_from_file(buffer, content_type='application/gzip')

    compressed_size = buffer.tell()
    logger.info(f"[{table_name}] Uploaded to gs://{GCS_BUCKET}/{gcs_path} ({compressed_size:,} bytes)")

    return {
        "table": table_name,
        "rows": total_rows,
        "gcs_path": f"gs://{GCS_BUCKET}/{gcs_path}",
        "bytes": compressed_size,
    }


def load_gcs_to_bigquery(
    bq_client: bigquery.Client,
    table_name: str,
    config: dict,
    gcs_path: str,
    dry_run: bool = False,
) -> dict:
    """Load CSV from GCS into BigQuery."""
    bq_table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{table_name}"

    if dry_run:
        logger.info(f"[{table_name}] Would load {gcs_path} -> {bq_table_id}")
        return {"table": table_name, "dry_run": True}

    # Configure load job
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        schema=config.get("bq_schema"),
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # Replace table
        allow_quoted_newlines=True,
    )

    # If no schema provided, use autodetect
    if not config.get("bq_schema"):
        job_config.autodetect = True

    logger.info(f"[{table_name}] Loading {gcs_path} -> {bq_table_id}")

    load_job = bq_client.load_table_from_uri(
        gcs_path,
        bq_table_id,
        job_config=job_config,
    )

    # Wait for job to complete
    load_job.result()

    # Get final row count
    table = bq_client.get_table(bq_table_id)
    logger.info(f"[{table_name}] Loaded {table.num_rows:,} rows to BigQuery")

    return {
        "table": table_name,
        "bq_table": bq_table_id,
        "rows": table.num_rows,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync CSGO tables to BigQuery")
    parser.add_argument("--table", type=str, help="Sync single table")
    parser.add_argument("--dry-run", action="store_true", help="Preview without syncing")
    parser.add_argument("--gcs-only", action="store_true", help="Export to GCS only, skip BigQuery")
    parser.add_argument("--bq-only", action="store_true", help="Load to BigQuery only (assumes GCS files exist)")
    args = parser.parse_args()

    # Select tables
    if args.table:
        if args.table not in CSGO_TABLES:
            logger.error(f"Unknown table: {args.table}")
            logger.error(f"Available: {list(CSGO_TABLES.keys())}")
            sys.exit(1)
        tables_to_sync = {args.table: CSGO_TABLES[args.table]}
    else:
        tables_to_sync = CSGO_TABLES

    # Initialize clients
    gcs_client = None
    bq_client = None
    conn = None

    if not args.bq_only:
        logger.info("Connecting to PostgreSQL...")
        db_params = parse_db_url(DB_URL)
        conn = psycopg2.connect(**db_params)
        conn.set_session(readonly=True)

        logger.info("Connecting to GCS...")
        gcs_client = get_gcs_client()

    if not args.gcs_only:
        logger.info("Connecting to BigQuery...")
        bq_client = get_bq_client()

    results = []
    today = datetime.utcnow().strftime("%Y-%m-%d")

    for table_name, config in tables_to_sync.items():
        try:
            gcs_path = f"gs://{GCS_BUCKET}/{GCS_PREFIX}/{table_name}/{today}/{table_name}.csv.gz"

            # Step 1: Export to GCS
            if not args.bq_only:
                gcs_result = export_table_to_gcs(
                    conn=conn,
                    gcs_client=gcs_client,
                    table_name=table_name,
                    config=config,
                    dry_run=args.dry_run,
                )
                results.append(gcs_result)

                if gcs_result.get("rows", 0) == 0:
                    continue

                gcs_path = gcs_result.get("gcs_path", gcs_path)

            # Step 2: Load to BigQuery
            if not args.gcs_only:
                bq_result = load_gcs_to_bigquery(
                    bq_client=bq_client,
                    table_name=table_name,
                    config=config,
                    gcs_path=gcs_path,
                    dry_run=args.dry_run,
                )
                results.append(bq_result)

        except Exception as e:
            logger.error(f"[{table_name}] Failed: {e}")
            import traceback
            traceback.print_exc()
            results.append({"table": table_name, "error": str(e)})

    if conn:
        conn.close()

    # Summary
    print("\n" + "=" * 60)
    print("CSGO SYNC SUMMARY")
    print("=" * 60)

    for r in results:
        if "error" in r:
            print(f"  {r['table']:30} ERROR: {r['error']}")
        elif r.get("dry_run"):
            print(f"  {r['table']:30} {r.get('rows', 0):>10,} rows (dry run)")
        else:
            print(f"  {r['table']:30} {r.get('rows', 0):>10,} rows")

    print("=" * 60)


if __name__ == "__main__":
    main()
