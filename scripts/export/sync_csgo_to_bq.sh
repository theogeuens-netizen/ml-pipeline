#!/bin/bash
# Sync CSGO tables from PostgreSQL to BigQuery
#
# Flow:
# 1. Export each CSGO table from PostgreSQL to GCS as CSV
# 2. Load CSV into BigQuery (replace mode)
#
# Usage:
#   ./sync_csgo_to_bq.sh              # Full sync all tables
#   ./sync_csgo_to_bq.sh csgo_matches # Sync single table
#   ./sync_csgo_to_bq.sh --dry-run    # Preview only

set -e

# Configuration
GCS_BUCKET="gs://polymarket-backup/csgo"
BQ_DATASET="polymarket"
BQ_PROJECT="polymarket-ml"
TODAY=$(date +%Y-%m-%d)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

DRY_RUN=false
SINGLE_TABLE=""

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            SINGLE_TABLE="$1"
            shift
            ;;
    esac
done

# Function to export a table to GCS and load to BigQuery
sync_table() {
    local table=$1
    local select_cols=$2
    local bq_schema=$3

    log_info "=== Syncing $table ==="

    # Get row count
    local row_count
    row_count=$(docker-compose exec -T postgres psql -U postgres -d polymarket_ml -t -c \
        "SELECT COUNT(*) FROM $table" 2>/dev/null | tr -d ' \n')

    if [ "$row_count" -eq 0 ] 2>/dev/null; then
        log_warn "[$table] No rows to export, skipping"
        return 0
    fi

    log_info "[$table] $row_count rows to export"

    if [ "$DRY_RUN" = true ]; then
        log_info "[$table] DRY RUN - would export to ${GCS_BUCKET}/${table}/${TODAY}/"
        return 0
    fi

    # Export to GCS
    local gcs_path="${GCS_BUCKET}/${table}/${TODAY}/${table}.csv"
    log_info "[$table] Exporting to $gcs_path..."

    docker-compose exec -T postgres psql -U postgres -d polymarket_ml \
        -c "COPY (SELECT $select_cols FROM $table ORDER BY id) TO STDOUT WITH CSV HEADER" 2>/dev/null | \
        gsutil -q cp - "$gcs_path"

    if [ $? -ne 0 ]; then
        log_error "[$table] Failed to export to GCS"
        return 1
    fi

    log_info "[$table] Exported to GCS"

    # Load to BigQuery (replace table)
    local bq_table="${BQ_PROJECT}:${BQ_DATASET}.${table}"
    log_info "[$table] Loading to BigQuery: $bq_table"

    # Drop existing table if it exists
    bq rm -f "${BQ_DATASET}.${table}" 2>/dev/null || true

    # Load from GCS with schema autodetect
    bq load \
        --source_format=CSV \
        --skip_leading_rows=1 \
        --allow_quoted_newlines \
        --autodetect \
        "${BQ_DATASET}.${table}" \
        "$gcs_path"

    if [ $? -ne 0 ]; then
        log_error "[$table] Failed to load to BigQuery"
        return 1
    fi

    # Verify row count
    local bq_rows
    bq_rows=$(bq query --use_legacy_sql=false --format=csv \
        "SELECT COUNT(*) as cnt FROM \`${BQ_PROJECT}.${BQ_DATASET}.${table}\`" 2>/dev/null | tail -1)

    log_info "[$table] Loaded $bq_rows rows to BigQuery"
    echo ""
}

# CSGO table definitions with column selections
# Format: table_name|select_cols

declare -A CSGO_TABLES

CSGO_TABLES["csgo_matches"]="id, market_id, gamma_id, condition_id, team_yes, team_no, game_start_time, game_start_override, end_date, tournament, format, market_type, group_item_title, game_id, subscribed, gamma_data::text, created_at, updated_at, closed, resolved, closed_at, accepting_orders, outcome, last_status_check, yes_price::float, no_price::float, spread::float, volume_total::float, volume_24h::float, liquidity::float, map_number"

CSGO_TABLES["csgo_price_ticks"]="id, market_id, timestamp, token_type, event_type, price::float, best_bid::float, best_ask::float, spread::float, trade_size::float, trade_side, price_velocity_1m::float"

CSGO_TABLES["csgo_trades"]="id, position_id, leg_id, token_id, side, shares::float, price::float, cost_usd::float, best_bid::float, best_ask::float, spread::float, slippage::float, trigger_tick_id, created_at, team_yes, team_no, format, map_number, game_start_time"

CSGO_TABLES["csgo_positions"]="id, strategy_name, market_id, condition_id, token_id, token_type, side, initial_shares::float, remaining_shares::float, avg_entry_price::float, cost_basis::float, current_price::float, unrealized_pnl::float, realized_pnl::float, spread_id, team_yes, team_no, game_start_time, format, status, close_reason, opened_at, closed_at, updated_at"

CSGO_TABLES["csgo_position_legs"]="id, position_id, leg_type, shares_delta::float, price::float, cost_delta::float, realized_pnl::float, trigger_price::float, trigger_reason, created_at"

CSGO_TABLES["csgo_spreads"]="id, strategy_name, market_id, condition_id, spread_type, yes_position_id, no_position_id, total_cost_basis::float, total_realized_pnl::float, total_unrealized_pnl::float, team_yes, team_no, entry_yes_price::float, status, opened_at, closed_at, updated_at"

CSGO_TABLES["csgo_strategy_state"]="id, strategy_name, allocated_usd::float, available_usd::float, total_realized_pnl::float, total_unrealized_pnl::float, trade_count, win_count, loss_count, max_drawdown_usd::float, high_water_mark::float, is_active, last_trade_at, created_at, updated_at"

CSGO_TABLES["csgo_strategy_market_state"]="id, strategy_name, market_id, condition_id, stage, entry_price::float, switch_price::float, exit_price::float, high_water_mark::float, low_water_mark::float, switches_count, reentries_count, custom_state::text, team_yes, team_no, current_side, is_active, stage_entered_at, created_at, updated_at"

CSGO_TABLES["csgo_teams"]="id, team_name, wins, losses, total_matches, win_rate_pct::float, created_at, updated_at"

CSGO_TABLES["csgo_h2h"]="id, team1_name, team2_name, team1_wins, team2_wins, total_matches, updated_at"

# Main execution
log_info "Starting CSGO BigQuery sync ($TODAY)"
echo ""

if [ -n "$SINGLE_TABLE" ]; then
    if [ -z "${CSGO_TABLES[$SINGLE_TABLE]}" ]; then
        log_error "Unknown table: $SINGLE_TABLE"
        log_error "Available tables: ${!CSGO_TABLES[*]}"
        exit 1
    fi
    sync_table "$SINGLE_TABLE" "${CSGO_TABLES[$SINGLE_TABLE]}"
else
    for table in "${!CSGO_TABLES[@]}"; do
        sync_table "$table" "${CSGO_TABLES[$table]}" || true
    done
fi

echo ""
log_info "=== CSGO sync complete ==="

# Show BigQuery row counts
echo ""
log_info "BigQuery row counts:"
bq query --use_legacy_sql=false --format=pretty "
SELECT table_id, row_count
FROM \`${BQ_PROJECT}.${BQ_DATASET}.__TABLES__\`
WHERE table_id LIKE 'csgo_%'
ORDER BY table_id
" 2>/dev/null
