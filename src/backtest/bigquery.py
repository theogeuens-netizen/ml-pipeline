"""
BigQuery-based backtesting engine.

This module provides efficient backtesting using BigQuery for server-side
filtering and aggregation. All heavy computation happens in BigQuery,
returning only aggregated metrics to minimize data transfer.

Key Principles:
1. Aggregate in SQL - Never return raw rows, always use COUNT/SUM/AVG
2. Use ROW_NUMBER() - Deduplicate to one snapshot per market in SQL
3. Filter early - Put all WHERE conditions in the main CTE
4. Return only metrics - Dict of floats, not DataFrames or objects

Usage:
    from src.backtest.bigquery import run_bq_backtest, run_bq_robustness

    # Simple backtest
    metrics = run_bq_backtest(
        side="NO",
        yes_price_min=0.55,
        yes_price_max=0.95,
        hours_min=12,
        hours_max=168,
    )

    # With robustness checks
    result = run_bq_robustness(
        side="NO",
        yes_price_min=0.55,
        run_time_split=True,
        run_volume_split=True,
    )
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from functools import lru_cache

from google.cloud import bigquery

# =============================================================================
# CONFIGURATION
# =============================================================================

# BigQuery project and dataset
BQ_PROJECT = "elite-buttress-480609-b0"
BQ_DATASET = "longshot"
BQ_LOCATION = "EU"

# Table references
MARKETS_TABLE = f"`{BQ_PROJECT}.{BQ_DATASET}.historical_markets`"
SNAPSHOTS_TABLE = f"`{BQ_PROJECT}.{BQ_DATASET}.historical_snapshots`"

# Schema notes:
# - Timestamps are in NANOSECONDS (divide by 1e9 for seconds)
# - winner field contains "Yes"/"No" (case-sensitive)
# - liquidity column is all zeros (unusable)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BacktestMetrics:
    """Aggregated metrics from a backtest run."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    std_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "avg_pnl": self.avg_pnl,
            "profit_factor": self.profit_factor,
            "sharpe": self.sharpe,
        }


@dataclass
class SplitMetrics:
    """Metrics for one half of a split test."""
    trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    sharpe: float = 0.0


@dataclass
class RobustnessResult:
    """Results from robustness checks."""
    main_metrics: BacktestMetrics
    time_split: Optional[Dict[str, SplitMetrics]] = None
    volume_split: Optional[Dict[str, SplitMetrics]] = None
    category_split: Optional[Dict[str, SplitMetrics]] = None
    time_split_passed: bool = False
    volume_split_passed: bool = False
    category_split_passed: bool = False

    @property
    def all_passed(self) -> bool:
        """Check if all requested robustness checks passed."""
        checks = []
        if self.time_split is not None:
            checks.append(self.time_split_passed)
        if self.volume_split is not None:
            checks.append(self.volume_split_passed)
        if self.category_split is not None:
            checks.append(self.category_split_passed)
        return all(checks) if checks else True


# =============================================================================
# CLIENT
# =============================================================================

@lru_cache(maxsize=1)
def get_bq_client() -> bigquery.Client:
    """
    Get a cached BigQuery client.

    The client is cached to avoid repeated authentication overhead.
    Location is set to EU where the dataset resides.
    """
    return bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)


# =============================================================================
# QUERY BUILDERS
# =============================================================================

def _build_filters(
    side: str = "NO",
    yes_price_min: float = 0.0,
    yes_price_max: float = 1.0,
    hours_min: float = 0,
    hours_max: float = 168,
    min_volume: Optional[float] = None,
    categories: Optional[List[str]] = None,
) -> str:
    """
    Build WHERE clause filters for BigQuery.

    Args:
        side: Which side to bet ("YES" or "NO")
        yes_price_min: Minimum YES price to include
        yes_price_max: Maximum YES price to include
        hours_min: Minimum hours to close
        hours_max: Maximum hours to close
        min_volume: Minimum market volume (optional)
        categories: List of macro_category values to include (optional)

    Returns:
        SQL filter string starting with AND
    """
    filters = []

    # Price filters
    if yes_price_min > 0:
        filters.append(f"AND s.price >= {yes_price_min}")
    if yes_price_max < 1:
        filters.append(f"AND s.price <= {yes_price_max}")

    # Time filters (timestamps are in nanoseconds)
    if hours_min > 0:
        filters.append(f"AND (m.close_date - s.timestamp) / 1e9 / 3600.0 >= {hours_min}")
    if hours_max < 10000:  # Reasonable upper bound
        filters.append(f"AND (m.close_date - s.timestamp) / 1e9 / 3600.0 <= {hours_max}")

    # Volume filter (liquidity is all zeros, so we skip it)
    if min_volume:
        filters.append(f"AND m.volume >= {min_volume}")

    # Category filter
    if categories:
        cat_list = ", ".join(f'"{c}"' for c in categories)
        filters.append(f"AND m.macro_category IN ({cat_list})")

    return "\n            ".join(filters)


def _build_pnl_case(side: str = "NO") -> str:
    """
    Build the PnL calculation CASE statement.

    For NO bets:
    - Win (market resolves NO): profit = yes_price (we bought at no_price = 1 - yes_price)
    - Loss (market resolves YES): loss = -no_price = -(1 - yes_price)

    For YES bets:
    - Win (market resolves YES): profit = no_price = 1 - yes_price
    - Loss (market resolves NO): loss = -yes_price
    """
    if side == "NO":
        return """
            CASE
                WHEN m.winner = "No" THEN s.price  -- profit = yes_price
                ELSE -(1 - s.price)                -- loss = -no_price
            END"""
    else:  # YES
        return """
            CASE
                WHEN m.winner = "Yes" THEN (1 - s.price)  -- profit = no_price
                ELSE -s.price                              -- loss = -yes_price
            END"""


def _build_won_case(side: str = "NO") -> str:
    """Build the won/lost indicator CASE statement."""
    winner = "No" if side == "NO" else "Yes"
    return f'CASE WHEN m.winner = "{winner}" THEN 1 ELSE 0 END'


# =============================================================================
# MAIN BACKTEST QUERY
# =============================================================================

def run_bq_backtest(
    side: str = "NO",
    yes_price_min: float = 0.0,
    yes_price_max: float = 1.0,
    hours_min: float = 0,
    hours_max: float = 168,
    min_volume: Optional[float] = None,
    categories: Optional[List[str]] = None,
) -> BacktestMetrics:
    """
    Run a backtest using BigQuery.

    All filtering and aggregation happens in BigQuery. Returns only
    aggregated metrics, never raw data.

    Args:
        side: Which side to bet ("YES" or "NO")
        yes_price_min: Minimum YES price to include (0-1)
        yes_price_max: Maximum YES price to include (0-1)
        hours_min: Minimum hours before close to enter
        hours_max: Maximum hours before close to enter
        min_volume: Minimum market volume in USD (optional)
        categories: List of macro_category to include (optional)

    Returns:
        BacktestMetrics with aggregated results
    """
    client = get_bq_client()

    filters = _build_filters(
        side, yes_price_min, yes_price_max,
        hours_min, hours_max, min_volume, categories
    )
    pnl_case = _build_pnl_case(side)
    won_case = _build_won_case(side)
    target_hours = (hours_min + hours_max) / 2

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.winner,
            m.macro_category,
            m.volume as market_volume,
            s.price as yes_price,
            (m.close_date - s.timestamp) / 1e9 / 3600.0 as hours_to_close,
            {won_case} as won,
            {pnl_case} as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {target_hours})
            ) as rn
        FROM {MARKETS_TABLE} m
        JOIN {SNAPSHOTS_TABLE} s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price > 0
            AND s.price < 1
            {filters}
    ),
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    ),
    metrics AS (
        SELECT
            COUNT(*) as total_trades,
            SUM(won) as wins,
            SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) as losses,
            AVG(won) as win_rate,
            SUM(pnl_per_dollar) as total_pnl,
            AVG(pnl_per_dollar) as avg_pnl,
            STDDEV(pnl_per_dollar) as std_pnl,
            SUM(CASE WHEN pnl_per_dollar > 0 THEN pnl_per_dollar ELSE 0 END) as gross_profit,
            SUM(CASE WHEN pnl_per_dollar < 0 THEN ABS(pnl_per_dollar) ELSE 0 END) as gross_loss
        FROM unique_bets
    )
    SELECT
        total_trades,
        wins,
        losses,
        win_rate,
        total_pnl,
        avg_pnl,
        std_pnl,
        gross_profit,
        gross_loss,
        CASE WHEN gross_loss > 0 THEN gross_profit / gross_loss ELSE 0 END as profit_factor,
        CASE WHEN std_pnl > 0 THEN avg_pnl / std_pnl * SQRT(252) ELSE 0 END as sharpe
    FROM metrics
    """

    result = client.query(query).result()
    row = list(result)[0]

    return BacktestMetrics(
        total_trades=row.total_trades or 0,
        wins=row.wins or 0,
        losses=row.losses or 0,
        win_rate=float(row.win_rate) if row.win_rate else 0.0,
        total_pnl=float(row.total_pnl) if row.total_pnl else 0.0,
        avg_pnl=float(row.avg_pnl) if row.avg_pnl else 0.0,
        std_pnl=float(row.std_pnl) if row.std_pnl else 0.0,
        gross_profit=float(row.gross_profit) if row.gross_profit else 0.0,
        gross_loss=float(row.gross_loss) if row.gross_loss else 0.0,
        profit_factor=float(row.profit_factor) if row.profit_factor else 0.0,
        sharpe=float(row.sharpe) if row.sharpe else 0.0,
    )


# =============================================================================
# ROBUSTNESS QUERIES
# =============================================================================

def _run_time_split_query(
    side: str,
    filters: str,
    pnl_case: str,
    won_case: str,
    target_hours: float,
) -> Dict[str, SplitMetrics]:
    """Run time-split robustness check (first half vs second half by close_date)."""
    client = get_bq_client()

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.close_date,
            {won_case} as won,
            {pnl_case} as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {target_hours})
            ) as rn
        FROM {MARKETS_TABLE} m
        JOIN {SNAPSHOTS_TABLE} s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price > 0 AND s.price < 1
            {filters}
    ),
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    ),
    with_median AS (
        SELECT
            *,
            PERCENTILE_CONT(close_date, 0.5) OVER () as median_close
        FROM unique_bets
    )
    SELECT
        CASE WHEN close_date <= median_close THEN 'first_half' ELSE 'second_half' END as split,
        COUNT(*) as trades,
        AVG(won) as win_rate,
        SUM(pnl_per_dollar) as total_pnl,
        AVG(pnl_per_dollar) as avg_pnl,
        STDDEV(pnl_per_dollar) as std_pnl
    FROM with_median
    GROUP BY split
    """

    result = client.query(query).result()

    splits = {}
    for row in result:
        sharpe = 0.0
        if row.std_pnl and row.std_pnl > 0 and row.avg_pnl:
            sharpe = float(row.avg_pnl / row.std_pnl * math.sqrt(252))

        splits[row.split] = SplitMetrics(
            trades=row.trades or 0,
            win_rate=float(row.win_rate) if row.win_rate else 0.0,
            total_pnl=float(row.total_pnl) if row.total_pnl else 0.0,
            sharpe=sharpe,
        )

    # Ensure both halves exist
    if "first_half" not in splits:
        splits["first_half"] = SplitMetrics()
    if "second_half" not in splits:
        splits["second_half"] = SplitMetrics()

    return splits


def _run_volume_split_query(
    side: str,
    filters: str,
    pnl_case: str,
    won_case: str,
    target_hours: float,
) -> Dict[str, SplitMetrics]:
    """Run volume-split robustness check (high volume vs low volume)."""
    client = get_bq_client()

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.volume as market_volume,
            {won_case} as won,
            {pnl_case} as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {target_hours})
            ) as rn
        FROM {MARKETS_TABLE} m
        JOIN {SNAPSHOTS_TABLE} s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price > 0 AND s.price < 1
            {filters}
    ),
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    ),
    with_median AS (
        SELECT
            *,
            PERCENTILE_CONT(market_volume, 0.5) OVER () as median_volume
        FROM unique_bets
    )
    SELECT
        CASE WHEN market_volume >= median_volume THEN 'high_volume' ELSE 'low_volume' END as split,
        COUNT(*) as trades,
        AVG(won) as win_rate,
        SUM(pnl_per_dollar) as total_pnl,
        AVG(pnl_per_dollar) as avg_pnl,
        STDDEV(pnl_per_dollar) as std_pnl
    FROM with_median
    GROUP BY split
    """

    result = client.query(query).result()

    splits = {}
    for row in result:
        sharpe = 0.0
        if row.std_pnl and row.std_pnl > 0 and row.avg_pnl:
            sharpe = float(row.avg_pnl / row.std_pnl * math.sqrt(252))

        splits[row.split] = SplitMetrics(
            trades=row.trades or 0,
            win_rate=float(row.win_rate) if row.win_rate else 0.0,
            total_pnl=float(row.total_pnl) if row.total_pnl else 0.0,
            sharpe=sharpe,
        )

    # Ensure both halves exist
    if "high_volume" not in splits:
        splits["high_volume"] = SplitMetrics()
    if "low_volume" not in splits:
        splits["low_volume"] = SplitMetrics()

    return splits


def _run_category_split_query(
    side: str,
    filters: str,
    pnl_case: str,
    won_case: str,
    target_hours: float,
) -> Dict[str, SplitMetrics]:
    """Run category-split robustness check (per macro_category)."""
    client = get_bq_client()

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.macro_category,
            {won_case} as won,
            {pnl_case} as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {target_hours})
            ) as rn
        FROM {MARKETS_TABLE} m
        JOIN {SNAPSHOTS_TABLE} s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price > 0 AND s.price < 1
            {filters}
    ),
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    )
    SELECT
        macro_category as category,
        COUNT(*) as trades,
        AVG(won) as win_rate,
        SUM(pnl_per_dollar) as total_pnl,
        AVG(pnl_per_dollar) as avg_pnl,
        STDDEV(pnl_per_dollar) as std_pnl
    FROM unique_bets
    GROUP BY macro_category
    HAVING COUNT(*) >= 10
    """

    result = client.query(query).result()

    splits = {}
    for row in result:
        if not row.category:
            continue

        sharpe = 0.0
        if row.std_pnl and row.std_pnl > 0 and row.avg_pnl:
            sharpe = float(row.avg_pnl / row.std_pnl * math.sqrt(252))

        splits[row.category] = SplitMetrics(
            trades=row.trades or 0,
            win_rate=float(row.win_rate) if row.win_rate else 0.0,
            total_pnl=float(row.total_pnl) if row.total_pnl else 0.0,
            sharpe=sharpe,
        )

    return splits


def run_bq_robustness(
    side: str = "NO",
    yes_price_min: float = 0.0,
    yes_price_max: float = 1.0,
    hours_min: float = 0,
    hours_max: float = 168,
    min_volume: Optional[float] = None,
    categories: Optional[List[str]] = None,
    run_time_split: bool = True,
    run_volume_split: bool = True,
    run_category_split: bool = False,
    min_trades_per_split: int = 10,
) -> RobustnessResult:
    """
    Run backtest with robustness checks using BigQuery.

    Args:
        side: Which side to bet ("YES" or "NO")
        yes_price_min: Minimum YES price to include (0-1)
        yes_price_max: Maximum YES price to include (0-1)
        hours_min: Minimum hours before close to enter
        hours_max: Maximum hours before close to enter
        min_volume: Minimum market volume in USD (optional)
        categories: List of macro_category to include (optional)
        run_time_split: Whether to run time split check
        run_volume_split: Whether to run volume split check
        run_category_split: Whether to run category split check
        min_trades_per_split: Minimum trades required per split

    Returns:
        RobustnessResult with main metrics and split results
    """
    # Build common query components
    filters = _build_filters(
        side, yes_price_min, yes_price_max,
        hours_min, hours_max, min_volume, categories
    )
    pnl_case = _build_pnl_case(side)
    won_case = _build_won_case(side)
    target_hours = (hours_min + hours_max) / 2

    # Run main backtest
    main_metrics = run_bq_backtest(
        side, yes_price_min, yes_price_max,
        hours_min, hours_max, min_volume, categories
    )

    result = RobustnessResult(main_metrics=main_metrics)

    # Time split
    if run_time_split:
        result.time_split = _run_time_split_query(
            side, filters, pnl_case, won_case, target_hours
        )
        first = result.time_split.get("first_half", SplitMetrics())
        second = result.time_split.get("second_half", SplitMetrics())
        result.time_split_passed = (
            first.total_pnl > 0 and second.total_pnl > 0 and
            first.trades >= min_trades_per_split and
            second.trades >= min_trades_per_split
        )

    # Volume split
    if run_volume_split:
        result.volume_split = _run_volume_split_query(
            side, filters, pnl_case, won_case, target_hours
        )
        high = result.volume_split.get("high_volume", SplitMetrics())
        low = result.volume_split.get("low_volume", SplitMetrics())
        result.volume_split_passed = (
            high.total_pnl > 0 and low.total_pnl > 0 and
            high.trades >= min_trades_per_split and
            low.trades >= min_trades_per_split
        )

    # Category split
    if run_category_split:
        result.category_split = _run_category_split_query(
            side, filters, pnl_case, won_case, target_hours
        )
        # Pass if >50% of categories have positive PnL
        if result.category_split:
            positive = sum(1 for s in result.category_split.values() if s.total_pnl > 0)
            total = len(result.category_split)
            result.category_split_passed = positive > total / 2

    return result


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_bq_data_stats() -> Dict[str, Any]:
    """
    Get summary statistics about the BigQuery historical data.

    Returns:
        Dict with market counts, date ranges, and category breakdown.
    """
    client = get_bq_client()

    query = f"""
    SELECT
        COUNT(*) as total_markets,
        COUNTIF(resolution_status = "resolved") as resolved_markets,
        COUNTIF(winner = "Yes") as yes_wins,
        COUNTIF(winner = "No") as no_wins,
        MIN(close_date) / 1e9 as min_close_epoch,
        MAX(close_date) / 1e9 as max_close_epoch
    FROM {MARKETS_TABLE}
    """

    result = client.query(query).result()
    row = list(result)[0]

    # Get category breakdown
    cat_query = f"""
    SELECT macro_category, COUNT(*) as cnt
    FROM {MARKETS_TABLE}
    WHERE resolution_status = "resolved"
    GROUP BY macro_category
    ORDER BY cnt DESC
    """

    cat_result = client.query(cat_query).result()
    categories = {r.macro_category: r.cnt for r in cat_result if r.macro_category}

    return {
        "total_markets": row.total_markets,
        "resolved_markets": row.resolved_markets,
        "yes_wins": row.yes_wins,
        "no_wins": row.no_wins,
        "no_win_rate": row.no_wins / (row.yes_wins + row.no_wins) if (row.yes_wins + row.no_wins) > 0 else 0,
        "categories": categories,
    }


def format_bq_backtest_summary(metrics: BacktestMetrics, name: str = "Backtest") -> str:
    """
    Format backtest metrics as a readable summary string.

    Args:
        metrics: BacktestMetrics from run_bq_backtest
        name: Display name for the backtest

    Returns:
        Formatted multi-line string
    """
    lines = [
        "=" * 60,
        f"BACKTEST RESULTS: {name}",
        "=" * 60,
        "",
        f"Trades: {metrics.total_trades:,}",
        f"Wins: {metrics.wins:,} | Losses: {metrics.losses:,}",
        f"Win Rate: {metrics.win_rate:.1%}",
        "",
        f"Total P&L: ${metrics.total_pnl:,.2f}",
        f"Avg P&L per trade: ${metrics.avg_pnl:.4f}",
        f"Profit Factor: {metrics.profit_factor:.2f}",
        f"Sharpe Ratio: {metrics.sharpe:.2f}",
        "",
        "=" * 60,
    ]
    return "\n".join(lines)


def format_bq_robustness_summary(result: RobustnessResult) -> str:
    """
    Format robustness results as a readable summary string.

    Args:
        result: RobustnessResult from run_bq_robustness

    Returns:
        Formatted multi-line string
    """
    lines = [
        "=" * 60,
        "ROBUSTNESS CHECKS",
        "=" * 60,
    ]

    # Main metrics
    m = result.main_metrics
    lines.extend([
        "",
        f"Main: {m.total_trades} trades, {m.win_rate:.1%} WR, Sharpe={m.sharpe:.2f}",
    ])

    # Time split
    if result.time_split:
        first = result.time_split.get("first_half", SplitMetrics())
        second = result.time_split.get("second_half", SplitMetrics())
        status = "PASS" if result.time_split_passed else "FAIL"
        lines.extend([
            "",
            f"Time Split: {status}",
            f"  First half:  {first.trades} trades, {first.win_rate:.1%} WR, Sharpe={first.sharpe:.2f}",
            f"  Second half: {second.trades} trades, {second.win_rate:.1%} WR, Sharpe={second.sharpe:.2f}",
        ])

    # Volume split
    if result.volume_split:
        high = result.volume_split.get("high_volume", SplitMetrics())
        low = result.volume_split.get("low_volume", SplitMetrics())
        status = "PASS" if result.volume_split_passed else "FAIL"
        lines.extend([
            "",
            f"Volume Split: {status}",
            f"  High volume: {high.trades} trades, {high.win_rate:.1%} WR, Sharpe={high.sharpe:.2f}",
            f"  Low volume:  {low.trades} trades, {low.win_rate:.1%} WR, Sharpe={low.sharpe:.2f}",
        ])

    # Category split
    if result.category_split:
        status = "PASS" if result.category_split_passed else "FAIL"
        lines.extend([
            "",
            f"Category Split: {status}",
        ])
        for cat, metrics in sorted(result.category_split.items(), key=lambda x: -x[1].total_pnl):
            sign = "+" if metrics.total_pnl >= 0 else ""
            lines.append(
                f"  {cat}: {metrics.trades} trades, {metrics.win_rate:.1%} WR, {sign}${metrics.total_pnl:.2f}"
            )

    # Overall
    lines.extend([
        "",
        "=" * 60,
        f"OVERALL: {'PASS' if result.all_passed else 'FAIL'}",
        "=" * 60,
    ])

    return "\n".join(lines)
