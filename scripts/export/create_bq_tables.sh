#!/bin/bash
set -e

# Configuration
GCS_BUCKET="gs://polymarket-backup/export"
BQ_DATASET="polymarket"
PROJECT="polymarket-ml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Drop existing tables if they exist
log_info "Dropping existing tables..."
for table in markets_v2 snapshots_v2 trades_v2 whale_events_v2 orderbook_snapshots_v2 tier_transitions_v2; do
    bq rm -f "${BQ_DATASET}.${table}" 2>/dev/null || true
done

log_info "=== Creating BigQuery tables ==="

# 1. Markets table
log_info "Loading markets..."
bq load \
    --source_format=CSV \
    --skip_leading_rows=1 \
    --allow_quoted_newlines \
    --autodetect \
    "${BQ_DATASET}.markets_v2" \
    "${GCS_BUCKET}/markets.csv"

# 2. Whale events table
log_info "Loading whale_events..."
bq load \
    --source_format=CSV \
    --skip_leading_rows=1 \
    --autodetect \
    "${BQ_DATASET}.whale_events_v2" \
    "${GCS_BUCKET}/whale_events.csv"

# 3. Tier transitions table
log_info "Loading tier_transitions..."
bq load \
    --source_format=CSV \
    --skip_leading_rows=1 \
    --autodetect \
    "${BQ_DATASET}.tier_transitions_v2" \
    "${GCS_BUCKET}/tier_transitions.csv"

# 4. Snapshots table (multiple files)
log_info "Loading snapshots (multiple files)..."
bq load \
    --source_format=CSV \
    --skip_leading_rows=1 \
    --autodetect \
    "${BQ_DATASET}.snapshots_v2" \
    "${GCS_BUCKET}/snapshots/part_*.csv"

# 5. Trades table (multiple files)
log_info "Loading trades (multiple files)..."
bq load \
    --source_format=CSV \
    --skip_leading_rows=1 \
    --autodetect \
    "${BQ_DATASET}.trades_v2" \
    "${GCS_BUCKET}/trades/part_*.csv"

# 6. Orderbook snapshots table (multiple files)
log_info "Loading orderbook_snapshots (multiple files)..."
bq load \
    --source_format=CSV \
    --skip_leading_rows=1 \
    --allow_quoted_newlines \
    --autodetect \
    "${BQ_DATASET}.orderbook_snapshots_v2" \
    "${GCS_BUCKET}/orderbook_snapshots/part_*.csv"

log_info "=== Verifying row counts ==="

echo ""
echo "BigQuery row counts:"
bq query --use_legacy_sql=false "
SELECT 'markets_v2' as table_name, COUNT(*) as rows FROM \`${PROJECT}.${BQ_DATASET}.markets_v2\`
UNION ALL
SELECT 'snapshots_v2', COUNT(*) FROM \`${PROJECT}.${BQ_DATASET}.snapshots_v2\`
UNION ALL
SELECT 'trades_v2', COUNT(*) FROM \`${PROJECT}.${BQ_DATASET}.trades_v2\`
UNION ALL
SELECT 'whale_events_v2', COUNT(*) FROM \`${PROJECT}.${BQ_DATASET}.whale_events_v2\`
UNION ALL
SELECT 'orderbook_snapshots_v2', COUNT(*) FROM \`${PROJECT}.${BQ_DATASET}.orderbook_snapshots_v2\`
UNION ALL
SELECT 'tier_transitions_v2', COUNT(*) FROM \`${PROJECT}.${BQ_DATASET}.tier_transitions_v2\`
ORDER BY table_name
"

echo ""
log_info "=== Creating unified views for feature engineering ==="

# Create a view that joins snapshots with market metadata
bq query --use_legacy_sql=false "
CREATE OR REPLACE VIEW \`${PROJECT}.${BQ_DATASET}.snapshots_enriched\` AS
SELECT
    s.*,
    m.question,
    m.slug,
    m.category,
    m.category_l1,
    m.category_l2,
    m.category_l3,
    m.resolved,
    m.outcome,
    m.end_date,
    m.start_date,
    m.neg_risk,
    TIMESTAMP_DIFF(m.end_date, s.timestamp, HOUR) as hours_to_resolution
FROM \`${PROJECT}.${BQ_DATASET}.snapshots_v2\` s
JOIN \`${PROJECT}.${BQ_DATASET}.markets_v2\` m ON s.market_id = m.id
"

log_info "Created view: snapshots_enriched (snapshots + market metadata)"

# Create resolved markets view for ML training
bq query --use_legacy_sql=false "
CREATE OR REPLACE VIEW \`${PROJECT}.${BQ_DATASET}.resolved_snapshots\` AS
SELECT
    s.*,
    m.question,
    m.slug,
    m.category_l1,
    m.category_l2,
    m.outcome,
    m.end_date,
    m.resolved_at,
    CASE WHEN m.outcome = 'Yes' THEN 1.0 ELSE 0.0 END as target,
    TIMESTAMP_DIFF(m.end_date, s.timestamp, HOUR) as hours_to_resolution
FROM \`${PROJECT}.${BQ_DATASET}.snapshots_v2\` s
JOIN \`${PROJECT}.${BQ_DATASET}.markets_v2\` m ON s.market_id = m.id
WHERE m.resolved = true
  AND m.outcome IN ('Yes', 'No')
"

log_info "Created view: resolved_snapshots (for ML training with target variable)"

echo ""
log_info "=== Export complete! ==="
echo ""
echo "Tables created:"
echo "  - ${BQ_DATASET}.markets_v2"
echo "  - ${BQ_DATASET}.snapshots_v2"
echo "  - ${BQ_DATASET}.trades_v2"
echo "  - ${BQ_DATASET}.whale_events_v2"
echo "  - ${BQ_DATASET}.orderbook_snapshots_v2"
echo "  - ${BQ_DATASET}.tier_transitions_v2"
echo ""
echo "Views for feature engineering:"
echo "  - ${BQ_DATASET}.snapshots_enriched (snapshots + market metadata)"
echo "  - ${BQ_DATASET}.resolved_snapshots (training data with target)"
