#!/bin/bash
set -e

# Configuration
GCS_BUCKET="gs://polymarket-backup/export"
BQ_DATASET="polymarket"
CHUNK_SIZE=5000000  # 5M rows per file for large tables

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Function to stream a table directly to GCS
stream_table() {
    local table=$1
    local query=$2
    local gcs_path=$3

    log_info "Streaming $table to $gcs_path..."

    docker-compose exec -T postgres psql -U postgres -d polymarket_ml \
        -c "COPY ($query) TO STDOUT WITH CSV HEADER" 2>/dev/null | \
        gsutil -q cp - "$gcs_path"

    log_info "Done: $table"
}

# Function to stream large table in chunks
stream_table_chunked() {
    local table=$1
    local total_rows=$2
    local select_cols=$3

    local num_chunks=$(( (total_rows + CHUNK_SIZE - 1) / CHUNK_SIZE ))

    log_info "Streaming $table ($total_rows rows) in $num_chunks chunks..."

    for ((i=0; i<num_chunks; i++)); do
        local offset=$((i * CHUNK_SIZE))
        local chunk_file=$(printf "%s/%s/part_%04d.csv" "$GCS_BUCKET" "$table" "$i")

        log_info "  Chunk $((i+1))/$num_chunks (offset $offset)..."

        docker-compose exec -T postgres psql -U postgres -d polymarket_ml \
            -c "COPY (SELECT $select_cols FROM $table ORDER BY id LIMIT $CHUNK_SIZE OFFSET $offset) TO STDOUT WITH CSV HEADER" 2>/dev/null | \
            gsutil -q cp - "$chunk_file"
    done

    log_info "Done: $table ($num_chunks files)"
}

# Clean up old exports
log_info "Cleaning up old exports..."
gsutil -q rm -r "${GCS_BUCKET}/snapshots/" 2>/dev/null || true
gsutil -q rm -r "${GCS_BUCKET}/orderbook_snapshots/" 2>/dev/null || true
gsutil -q rm -r "${GCS_BUCKET}/trades/" 2>/dev/null || true
gsutil -q rm "${GCS_BUCKET}/markets.csv" 2>/dev/null || true
gsutil -q rm "${GCS_BUCKET}/whale_events.csv" 2>/dev/null || true
gsutil -q rm "${GCS_BUCKET}/tier_transitions.csv" 2>/dev/null || true

# Get current row counts
log_info "Getting row counts..."
SNAPSHOTS_COUNT=$(docker-compose exec -T postgres psql -U postgres -d polymarket_ml -t -c "SELECT COUNT(*) FROM snapshots" | tr -d ' ')
ORDERBOOK_COUNT=$(docker-compose exec -T postgres psql -U postgres -d polymarket_ml -t -c "SELECT COUNT(*) FROM orderbook_snapshots" | tr -d ' ')
TRADES_COUNT=$(docker-compose exec -T postgres psql -U postgres -d polymarket_ml -t -c "SELECT COUNT(*) FROM trades" | tr -d ' ')

log_info "Row counts: snapshots=$SNAPSHOTS_COUNT, orderbook=$ORDERBOOK_COUNT, trades=$TRADES_COUNT"

# 1. Export small tables (single file each)
log_info "=== Exporting small tables ==="

# Markets - convert JSONB tags to text
stream_table "markets" "
SELECT
    id, condition_id, slug, question, description,
    event_id, event_slug, event_title,
    yes_token_id, no_token_id,
    start_date, end_date, created_at,
    initial_price::float, initial_spread::float, initial_volume::float, initial_liquidity::float,
    resolved, outcome, resolved_at,
    tier, active, tracking_started_at, last_snapshot_at, snapshot_count,
    category, tags::text,
    neg_risk, competitive::float, enable_order_book,
    first_seen, updated_at,
    category_l1, category_l2, category_l3,
    categorized_at, categorization_method, matched_rule_id,
    closed, closed_at, accepting_orders, accepting_orders_updated_at,
    uma_resolution_status, uma_status_updated_at,
    categorization_confidence, verification_status, gamma_id
FROM markets
" "${GCS_BUCKET}/markets.csv"

# Whale events
stream_table "whale_events" "
SELECT
    id, market_id, trade_id, timestamp,
    price::float, size::float, side, whale_tier,
    price_before::float, price_after_1m::float, price_after_5m::float,
    impact_1m::float, impact_5m::float
FROM whale_events
" "${GCS_BUCKET}/whale_events.csv"

# Tier transitions
stream_table "tier_transitions" "
SELECT * FROM tier_transitions
" "${GCS_BUCKET}/tier_transitions.csv"

# 2. Export large tables in chunks
log_info "=== Exporting large tables (chunked) ==="

# Snapshots - all numeric columns cast to float
SNAPSHOTS_COLS="
    id, market_id, timestamp, tier,
    price::float, best_bid::float, best_ask::float, spread::float,
    last_trade_price::float, price_change_1d::float, price_change_1w::float, price_change_1m::float,
    volume_total::float, volume_24h::float, volume_1w::float, liquidity::float,
    bid_depth_5::float, bid_depth_10::float, bid_depth_20::float, bid_depth_50::float,
    ask_depth_5::float, ask_depth_10::float, ask_depth_20::float, ask_depth_50::float,
    bid_levels, ask_levels, book_imbalance::float,
    bid_wall_price::float, bid_wall_size::float, ask_wall_price::float, ask_wall_size::float,
    trade_count_1h, buy_count_1h, sell_count_1h,
    volume_1h::float, buy_volume_1h::float, sell_volume_1h::float,
    avg_trade_size_1h::float, max_trade_size_1h::float, vwap_1h::float,
    whale_count_1h, whale_volume_1h::float, whale_buy_volume_1h::float, whale_sell_volume_1h::float,
    whale_net_flow_1h::float, whale_buy_ratio_1h::float, time_since_whale
"
stream_table_chunked "snapshots" "$SNAPSHOTS_COUNT" "$SNAPSHOTS_COLS"

# Trades
TRADES_COLS="
    id, market_id, timestamp,
    price::float, size::float, side, whale_tier,
    best_bid::float, best_ask::float, mid_price::float, token_type
"
stream_table_chunked "trades" "$TRADES_COUNT" "$TRADES_COLS"

# Orderbook snapshots - JSONB columns as text
ORDERBOOK_COLS="
    id, market_id, timestamp,
    bids::text, asks::text,
    total_bid_depth::float, total_ask_depth::float,
    num_bid_levels, num_ask_levels,
    largest_bid_price::float, largest_bid_size::float,
    largest_ask_price::float, largest_ask_size::float
"
stream_table_chunked "orderbook_snapshots" "$ORDERBOOK_COUNT" "$ORDERBOOK_COLS"

log_info "=== All tables exported to GCS ==="

# List exported files
log_info "Exported files:"
gsutil ls -l "${GCS_BUCKET}/**" 2>/dev/null | tail -20

echo ""
log_info "Export complete. Run 'bash scripts/export/create_bq_tables.sh' to load into BigQuery."
