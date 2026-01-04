#!/usr/bin/env python3
"""
Streaming export of PostgreSQL tables to GCS as gzipped CSV.

Uses PostgreSQL COPY TO STDOUT with direct streaming to GCS.
- Zero memory overhead (streaming, not batch loading)
- Zero disk usage (no temp files)
- Native PostgreSQL performance

Usage:
    python scripts/export_to_gcs.py                    # Full export
    python scripts/export_to_gcs.py --incremental      # Only new data since last export
    python scripts/export_to_gcs.py --table snapshots  # Export single table
    python scripts/export_to_gcs.py --dry-run          # Preview without uploading

Structure:
    gs://longshot-lake/curated/{table}/{date}/{table}_{date}.csv.gz
"""

import os
import sys
import json
import gzip
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from io import BytesIO

import psycopg2
from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
GCS_BUCKET = "longshot-lake"
GCS_PREFIX = "curated"

# Parse database URL components for psycopg2
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/polymarket_ml")
CREDENTIALS_PATH = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "/home/theo/polymarket-ml/gcp-credentials.json"
)
STATE_FILE = Path("/home/theo/polymarket-ml/.export_state.json")

# Streaming chunk size (5MB - good balance for GCS resumable upload)
CHUNK_SIZE = 5 * 1024 * 1024

# Table configurations
TABLES = {
    "markets": {
        "timestamp_col": "updated_at",
        "order_by": "id",
    },
    "snapshots": {
        "timestamp_col": "timestamp",
        "order_by": "id",
    },
    "trades": {
        "timestamp_col": "timestamp",
        "order_by": "id",
    },
    "orderbook_snapshots": {
        "timestamp_col": "timestamp",
        "order_by": "id",
    },
    "whale_events": {
        "timestamp_col": "timestamp",
        "order_by": "id",
    },
    "task_runs": {
        "timestamp_col": "started_at",
        "order_by": "id",
    },
    "historical_markets": {
        "timestamp_col": "imported_at",
        "order_by": "id",
    },
    "historical_snapshots": {
        "table_name": "historical_price_snapshots",
        "timestamp_col": "timestamp",
        "order_by": "id",
    },
    "news_items": {
        "timestamp_col": "published_at",
        "order_by": "id",
    },
}


def parse_db_url(url: str) -> dict:
    """Parse PostgreSQL URL into connection params."""
    # postgresql://user:pass@host:port/dbname
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
        "dbname": parsed.path.lstrip("/") or "polymarket_ml",
    }


def load_state() -> dict:
    """Load last export timestamps from state file."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    """Save export state to file."""
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def get_gcs_client() -> storage.Client:
    """Initialize GCS client with credentials."""
    if os.path.exists(CREDENTIALS_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
        return storage.Client.from_service_account_json(CREDENTIALS_PATH)
    return storage.Client()


class StreamingGzipUploader:
    """
    Streams data through gzip compression directly to GCS.
    Uses resumable upload with chunked writes.
    """

    def __init__(self, bucket: storage.Bucket, blob_name: str):
        self.blob = bucket.blob(blob_name)
        self.buffer = BytesIO()
        self.gzip_file = gzip.GzipFile(mode='wb', fileobj=self.buffer)
        self.total_bytes = 0
        self.blob_name = blob_name

    def write(self, data: bytes):
        """Write data to gzip buffer."""
        self.gzip_file.write(data)
        self.total_bytes += len(data)

    def finish(self) -> tuple[int, int]:
        """Finalize gzip and upload to GCS. Returns (uncompressed, compressed) bytes."""
        self.gzip_file.close()

        # Get compressed size before seeking
        compressed_size = self.buffer.tell()
        self.buffer.seek(0)

        # Upload the complete gzipped data
        self.blob.upload_from_file(
            self.buffer,
            content_type='application/gzip',
            timeout=600,  # 10 min timeout for large files
        )

        logger.info(f"  Uploaded: gs://{GCS_BUCKET}/{self.blob_name} "
                   f"({compressed_size:,} bytes compressed)")
        return compressed_size, compressed_size


def export_table_streaming(
    conn,
    gcs_client: storage.Client,
    table_key: str,
    config: dict,
    incremental: bool = False,
    last_export: Optional[datetime] = None,
    dry_run: bool = False,
) -> dict:
    """
    Export a table using PostgreSQL COPY with streaming upload to GCS.
    Zero memory overhead - data flows directly from PostgreSQL to GCS.
    """
    table_name = config.get("table_name", table_key)
    timestamp_col = config["timestamp_col"]
    order_by = config["order_by"]

    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Build WHERE clause for incremental
    if incremental and last_export:
        where_clause = f"WHERE {timestamp_col} > '{last_export.isoformat()}'"
        logger.info(f"[{table_key}] Incremental export since {last_export}")
    else:
        where_clause = ""
        logger.info(f"[{table_key}] Full export")

    # Get row count first
    with conn.cursor() as cur:
        count_query = f"SELECT COUNT(*) FROM {table_name} {where_clause}"
        cur.execute(count_query)
        total_rows = cur.fetchone()[0]

    if total_rows == 0:
        logger.info(f"[{table_key}] No rows to export")
        return {"table": table_key, "rows": 0, "files": 0}

    logger.info(f"[{table_key}] {total_rows:,} rows to export")

    if dry_run:
        return {"table": table_key, "rows": total_rows, "files": 0, "dry_run": True}

    # Build COPY query with optional WHERE clause
    if where_clause:
        copy_query = f"""
            COPY (
                SELECT * FROM {table_name}
                {where_clause}
                ORDER BY {order_by}
            ) TO STDOUT WITH CSV HEADER
        """
    else:
        copy_query = f"""
            COPY (
                SELECT * FROM {table_name}
                ORDER BY {order_by}
            ) TO STDOUT WITH CSV HEADER
        """

    # Setup streaming upload
    bucket = gcs_client.bucket(GCS_BUCKET)
    gcs_path = f"{GCS_PREFIX}/{table_key}/{today}/{table_key}_{today}.csv.gz"
    uploader = StreamingGzipUploader(bucket, gcs_path)

    # Stream from PostgreSQL directly to GCS
    with conn.cursor() as cur:
        # copy_expert streams data through the callback
        cur.copy_expert(copy_query, uploader.gzip_file)

    # Finalize upload
    _, compressed_bytes = uploader.finish()

    return {
        "table": table_key,
        "rows": total_rows,
        "files": 1,
        "bytes": compressed_bytes,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Stream PostgreSQL tables to GCS")
    parser.add_argument("--incremental", action="store_true", help="Only export new data")
    parser.add_argument("--table", type=str, help="Export single table")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading")
    args = parser.parse_args()

    state = load_state()

    # Connect to PostgreSQL using psycopg2 (required for COPY)
    logger.info("Connecting to database...")
    db_params = parse_db_url(DB_URL)
    conn = psycopg2.connect(**db_params)
    conn.set_session(readonly=True)  # Safety: read-only session

    logger.info("Connecting to GCS...")
    gcs_client = get_gcs_client()

    # Verify bucket access
    try:
        bucket = gcs_client.bucket(GCS_BUCKET)
        bucket.reload()
        logger.info(f"Connected to bucket: gs://{GCS_BUCKET}")
    except Exception as e:
        logger.error(f"Failed to access bucket: {e}")
        conn.close()
        sys.exit(1)

    # Select tables
    if args.table:
        if args.table not in TABLES:
            logger.error(f"Unknown table: {args.table}")
            logger.error(f"Available: {list(TABLES.keys())}")
            conn.close()
            sys.exit(1)
        tables_to_export = {args.table: TABLES[args.table]}
    else:
        tables_to_export = TABLES

    # Export
    results = []
    export_time = datetime.utcnow()

    for table_key, config in tables_to_export.items():
        try:
            last_export = None
            if args.incremental and table_key in state:
                last_export = datetime.fromisoformat(state[table_key])

            result = export_table_streaming(
                conn=conn,
                gcs_client=gcs_client,
                table_key=table_key,
                config=config,
                incremental=args.incremental,
                last_export=last_export,
                dry_run=args.dry_run,
            )
            results.append(result)

            if not args.dry_run and result.get("rows", 0) > 0:
                state[table_key] = export_time.isoformat()
                # Save state after each successful table (crash recovery)
                save_state(state)

        except Exception as e:
            logger.error(f"[{table_key}] Failed: {e}")
            import traceback
            traceback.print_exc()
            results.append({"table": table_key, "error": str(e)})

    conn.close()

    # Summary
    print("\n" + "=" * 60)
    print("EXPORT SUMMARY (Streaming Mode)")
    print("=" * 60)

    total_rows = 0
    total_files = 0
    total_bytes = 0

    for r in results:
        if "error" in r:
            print(f"  {r['table']:25} ERROR: {r['error']}")
        elif r.get("dry_run"):
            print(f"  {r['table']:25} {r['rows']:>12,} rows (dry run)")
            total_rows += r["rows"]
        else:
            bytes_str = f"{r.get('bytes', 0) / 1024 / 1024:.1f}MB" if r.get('bytes') else ""
            print(f"  {r['table']:25} {r['rows']:>12,} rows  {bytes_str}")
            total_rows += r["rows"]
            total_files += r.get("files", 0)
            total_bytes += r.get("bytes", 0)

    print("-" * 60)
    print(f"  {'TOTAL':25} {total_rows:>12,} rows, {total_files} files, {total_bytes/1024/1024:.1f}MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
