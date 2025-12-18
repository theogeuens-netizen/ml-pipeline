"""
Database browser endpoints.

Allows viewing raw table data for debugging and inspection.
Security: Only allows access to pre-defined tables.
"""
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session

from src.db.database import get_db
from src.db.models import Market, Snapshot, Trade, TaskRun, WhaleEvent, OrderbookSnapshot

router = APIRouter()

# Allowed tables for browsing (security measure)
ALLOWED_TABLES = {
    "markets": Market,
    "snapshots": Snapshot,
    "trades": Trade,
    "task_runs": TaskRun,
    "whale_events": WhaleEvent,
    "orderbook_snapshots": OrderbookSnapshot,
}


def serialize_value(value: Any) -> Any:
    """Serialize a value for JSON response."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    # For Decimal and other types
    return str(value)


@router.get("/database/tables")
async def list_tables(db: Session = Depends(get_db)):
    """
    List available tables with row counts.
    """
    tables = []
    for table_name, model in ALLOWED_TABLES.items():
        count = db.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar()
        tables.append({
            "name": table_name,
            "row_count": count,
        })

    return {
        "tables": sorted(tables, key=lambda t: t["name"]),
    }


@router.get("/database/tables/{table_name}")
async def browse_table(
    table_name: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order_by: str = Query("id", description="Column to sort by"),
    order: str = Query("desc", description="Sort order: asc or desc"),
    db: Session = Depends(get_db),
):
    """
    Browse table contents with pagination.

    Args:
        table_name: Name of the table to browse
        limit: Number of rows to return (max 500)
        offset: Starting offset for pagination
        order_by: Column to sort by (default: id)
        order: Sort order (asc or desc)

    Returns:
        Table data with columns, total count, and paginated items
    """
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(
            status_code=404,
            detail=f"Table '{table_name}' not found. Available: {list(ALLOWED_TABLES.keys())}"
        )

    model = ALLOWED_TABLES[table_name]

    # Get column info
    inspector = inspect(model)
    columns = [col.key for col in inspector.mapper.column_attrs]

    # Validate order_by column
    if order_by not in columns:
        order_by = "id"

    # Validate order
    if order.lower() not in ("asc", "desc"):
        order = "desc"

    # Get total count
    total = db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()

    # Get paginated data
    query = text(f"""
        SELECT * FROM {table_name}
        ORDER BY {order_by} {order.upper()}
        LIMIT :limit OFFSET :offset
    """)

    result = db.execute(query, {"limit": limit, "offset": offset})
    rows = result.fetchall()

    # Serialize rows
    items = []
    for row in rows:
        item = {}
        for i, col in enumerate(columns):
            item[col] = serialize_value(row[i])
        items.append(item)

    return {
        "table": table_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "columns": columns,
        "items": items,
    }


@router.get("/database/tables/{table_name}/schema")
async def get_table_schema(
    table_name: str,
    db: Session = Depends(get_db),
):
    """
    Get table schema information.
    """
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(
            status_code=404,
            detail=f"Table '{table_name}' not found."
        )

    model = ALLOWED_TABLES[table_name]
    inspector = inspect(model)

    columns = []
    for col in inspector.mapper.column_attrs:
        col_obj = col.columns[0]
        columns.append({
            "name": col.key,
            "type": str(col_obj.type),
            "nullable": col_obj.nullable,
            "primary_key": col_obj.primary_key,
        })

    return {
        "table": table_name,
        "columns": columns,
    }
