"""
Robustness testing for backtest results.

Provides time-split, category-split, and liquidity-split validation
to detect overfitting and ensure edge generalizes.

Key functions:
- time_split_backtest: Split by resolution timestamp midpoint
- liquidity_split_backtest: Split by volume median
- category_split_backtest: Split by macro_category
- run_all_robustness_checks: Run all applicable checks
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Any
from statistics import median

from .engine import BacktestConfig, BacktestResult, HistoricalBet, run_backtest


@dataclass
class SplitMetrics:
    """Metrics for one half of a split."""
    sharpe: Optional[float] = None
    win_rate: Optional[float] = None
    trades: int = 0
    total_pnl: float = 0.0
    profit_factor: Optional[float] = None


@dataclass
class SplitResult:
    """Result of a single split check (e.g., time split)."""
    name: str
    passed: bool
    first_half: SplitMetrics = field(default_factory=SplitMetrics)
    second_half: SplitMetrics = field(default_factory=SplitMetrics)
    notes: str = ""


@dataclass
class CategorySplitResult:
    """Result of category split check."""
    name: str = "category_split"
    passed: bool = False
    by_category: Dict[str, SplitMetrics] = field(default_factory=dict)
    categories_with_edge: int = 0
    total_categories: int = 0
    notes: str = ""


@dataclass
class RobustnessResult:
    """Combined robustness check results."""
    time_split: Optional[SplitResult] = None
    liquidity_split: Optional[SplitResult] = None
    category_split: Optional[CategorySplitResult] = None
    overall_passed: bool = False
    pass_rate: float = 0.0
    summary: str = ""


def _extract_metrics(result: BacktestResult) -> SplitMetrics:
    """Extract key metrics from a backtest result."""
    m = result.metrics
    return SplitMetrics(
        sharpe=m.sharpe_ratio,
        win_rate=m.win_rate,
        trades=m.num_bets,
        total_pnl=m.total_pnl,
        profit_factor=m.profit_factor,
    )


def time_split_backtest(
    bets: Sequence[HistoricalBet],
    config: BacktestConfig,
    min_trades_per_half: int = 10,
) -> SplitResult:
    """
    Split bets by resolution timestamp into first half and second half.

    Tests whether the edge is consistent across time periods.
    A strategy that only works in one half is likely overfit.

    Args:
        bets: Sequence of historical betting opportunities
        config: Backtest configuration
        min_trades_per_half: Minimum trades required in each half

    Returns:
        SplitResult with metrics for each half and pass/fail status
    """
    if not bets:
        return SplitResult(
            name="time_split",
            passed=False,
            notes="No bets provided",
        )

    # Sort by resolution timestamp
    sorted_bets = sorted(bets, key=lambda b: b.resolution_ts)

    # Find midpoint
    midpoint_idx = len(sorted_bets) // 2
    first_half = sorted_bets[:midpoint_idx]
    second_half = sorted_bets[midpoint_idx:]

    # Check minimum trades
    if len(first_half) < min_trades_per_half or len(second_half) < min_trades_per_half:
        return SplitResult(
            name="time_split",
            passed=False,
            notes=f"Insufficient trades: {len(first_half)}/{len(second_half)} (need {min_trades_per_half} each)",
        )

    # Run backtests on each half
    first_result = run_backtest(first_half, config)
    second_result = run_backtest(second_half, config)

    first_metrics = _extract_metrics(first_result)
    second_metrics = _extract_metrics(second_result)

    # Pass criteria: positive Sharpe in both halves
    first_ok = first_metrics.sharpe is not None and first_metrics.sharpe > 0
    second_ok = second_metrics.sharpe is not None and second_metrics.sharpe > 0
    passed = first_ok and second_ok

    # Generate notes
    notes_parts = []
    if passed:
        notes_parts.append("Edge consistent across both time periods")
    else:
        if not first_ok:
            notes_parts.append(f"First half Sharpe: {first_metrics.sharpe:.2f}" if first_metrics.sharpe else "First half no Sharpe")
        if not second_ok:
            notes_parts.append(f"Second half Sharpe: {second_metrics.sharpe:.2f}" if second_metrics.sharpe else "Second half no Sharpe")

    return SplitResult(
        name="time_split",
        passed=passed,
        first_half=first_metrics,
        second_half=second_metrics,
        notes="; ".join(notes_parts) if notes_parts else "Time split analysis complete",
    )


def liquidity_split_backtest(
    bets: Sequence[HistoricalBet],
    config: BacktestConfig,
    min_trades_per_half: int = 10,
) -> SplitResult:
    """
    Split bets by volume into high liquidity and low liquidity halves.

    Tests whether the edge works in both liquid and illiquid markets.
    An edge that only works in illiquid markets may have execution risk.

    Args:
        bets: Sequence of historical betting opportunities
        config: Backtest configuration
        min_trades_per_half: Minimum trades required in each half

    Returns:
        SplitResult with metrics for each half and pass/fail status
    """
    if not bets:
        return SplitResult(
            name="liquidity_split",
            passed=False,
            notes="No bets provided",
        )

    # Get bets with volume data
    bets_with_volume = [b for b in bets if b.volume is not None and b.volume > 0]

    if len(bets_with_volume) < min_trades_per_half * 2:
        return SplitResult(
            name="liquidity_split",
            passed=False,
            notes=f"Insufficient bets with volume data: {len(bets_with_volume)}",
        )

    # Find median volume
    volumes = [b.volume for b in bets_with_volume]
    median_volume = median(volumes)

    # Split by median
    high_liquidity = [b for b in bets_with_volume if b.volume >= median_volume]
    low_liquidity = [b for b in bets_with_volume if b.volume < median_volume]

    # Check minimum trades
    if len(high_liquidity) < min_trades_per_half or len(low_liquidity) < min_trades_per_half:
        return SplitResult(
            name="liquidity_split",
            passed=False,
            notes=f"Imbalanced split: high={len(high_liquidity)}, low={len(low_liquidity)}",
        )

    # Run backtests on each half
    high_result = run_backtest(high_liquidity, config)
    low_result = run_backtest(low_liquidity, config)

    high_metrics = _extract_metrics(high_result)
    low_metrics = _extract_metrics(low_result)

    # Pass criteria: positive Sharpe in both (or at least high liquidity)
    # We're more lenient on low liquidity since it may have execution challenges
    high_ok = high_metrics.sharpe is not None and high_metrics.sharpe > 0
    low_ok = low_metrics.sharpe is not None and low_metrics.sharpe > 0

    # Pass if high liquidity works (low liquidity edge is nice but not required)
    passed = high_ok and low_ok

    # Generate notes
    notes_parts = []
    if passed:
        notes_parts.append("Edge present in both liquidity buckets")
    elif high_ok:
        notes_parts.append("Edge works in high liquidity only (acceptable with caution)")
    else:
        notes_parts.append("Edge fails in high liquidity markets (execution risk)")

    notes_parts.append(f"Median volume: ${median_volume:,.0f}")

    return SplitResult(
        name="liquidity_split",
        passed=passed,
        first_half=high_metrics,  # high = first
        second_half=low_metrics,  # low = second
        notes="; ".join(notes_parts),
    )


def category_split_backtest(
    bets: Sequence[HistoricalBet],
    config: BacktestConfig,
    min_trades_per_category: int = 10,
) -> CategorySplitResult:
    """
    Split bets by macro_category and test each separately.

    Tests whether the edge is driven by a single category or generalizes.
    Single-category dependency is not necessarily bad, but good to know.

    Args:
        bets: Sequence of historical betting opportunities
        config: Backtest configuration
        min_trades_per_category: Minimum trades required per category

    Returns:
        CategorySplitResult with metrics per category
    """
    if not bets:
        return CategorySplitResult(
            passed=False,
            notes="No bets provided",
        )

    # Group by category
    by_category: Dict[str, List[HistoricalBet]] = {}
    for bet in bets:
        cat = bet.macro_category or "Unknown"
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(bet)

    # Run backtest for each category with enough trades
    results: Dict[str, SplitMetrics] = {}
    categories_with_edge = 0

    for cat, cat_bets in by_category.items():
        if len(cat_bets) < min_trades_per_category:
            results[cat] = SplitMetrics(
                trades=len(cat_bets),
                notes=f"Insufficient trades: {len(cat_bets)}",
            )
            continue

        result = run_backtest(cat_bets, config)
        metrics = _extract_metrics(result)
        results[cat] = metrics

        if metrics.sharpe is not None and metrics.sharpe > 0:
            categories_with_edge += 1

    total_categories = len([c for c, m in results.items() if m.trades >= min_trades_per_category])

    # Pass if majority of categories show positive edge
    passed = categories_with_edge >= (total_categories / 2) if total_categories > 0 else False

    notes = f"{categories_with_edge}/{total_categories} categories show positive edge"

    return CategorySplitResult(
        passed=passed,
        by_category=results,
        categories_with_edge=categories_with_edge,
        total_categories=total_categories,
        notes=notes,
    )


def run_all_robustness_checks(
    bets: Sequence[HistoricalBet],
    config: BacktestConfig,
    run_time_split: bool = True,
    run_liquidity_split: bool = True,
    run_category_split: bool = True,
    min_trades_per_split: int = 10,
) -> RobustnessResult:
    """
    Run all applicable robustness checks on a set of bets.

    Args:
        bets: Sequence of historical betting opportunities
        config: Backtest configuration
        run_time_split: Whether to run time split check
        run_liquidity_split: Whether to run liquidity split check
        run_category_split: Whether to run category split check
        min_trades_per_split: Minimum trades required per split

    Returns:
        RobustnessResult with all check results and overall assessment
    """
    result = RobustnessResult()
    checks_run = 0
    checks_passed = 0

    if run_time_split:
        result.time_split = time_split_backtest(bets, config, min_trades_per_split)
        checks_run += 1
        if result.time_split.passed:
            checks_passed += 1

    if run_liquidity_split:
        result.liquidity_split = liquidity_split_backtest(bets, config, min_trades_per_split)
        checks_run += 1
        if result.liquidity_split.passed:
            checks_passed += 1

    if run_category_split:
        # Only run if there are multiple categories
        categories = set(b.macro_category for b in bets if b.macro_category)
        if len(categories) > 1:
            result.category_split = category_split_backtest(bets, config, min_trades_per_split)
            checks_run += 1
            if result.category_split.passed:
                checks_passed += 1

    # Calculate overall results
    result.pass_rate = checks_passed / checks_run if checks_run > 0 else 0.0
    result.overall_passed = checks_passed == checks_run and checks_run > 0

    # Generate summary
    summary_parts = []
    if result.time_split:
        status = "PASS" if result.time_split.passed else "FAIL"
        summary_parts.append(f"Time split: {status}")
    if result.liquidity_split:
        status = "PASS" if result.liquidity_split.passed else "FAIL"
        summary_parts.append(f"Liquidity split: {status}")
    if result.category_split:
        status = "PASS" if result.category_split.passed else "FAIL"
        summary_parts.append(f"Category split: {status}")

    result.summary = f"{checks_passed}/{checks_run} passed. " + "; ".join(summary_parts)

    return result


def format_robustness_results(result: RobustnessResult) -> str:
    """
    Format robustness results for display.

    Args:
        result: RobustnessResult from run_all_robustness_checks

    Returns:
        Formatted string for CLI output
    """
    lines = []
    lines.append("=" * 60)
    lines.append("ROBUSTNESS ANALYSIS")
    lines.append("=" * 60)
    lines.append("")

    # Time split
    if result.time_split:
        ts = result.time_split
        status = "PASS" if ts.passed else "FAIL"
        lines.append(f"TIME SPLIT: {status}")
        lines.append("-" * 30)

        # Format metrics safely
        first_sharpe = f"{ts.first_half.sharpe:.2f}" if ts.first_half.sharpe is not None else "N/A"
        first_wr = f"{ts.first_half.win_rate*100:.0f}" if ts.first_half.win_rate is not None else "N/A"
        second_sharpe = f"{ts.second_half.sharpe:.2f}" if ts.second_half.sharpe is not None else "N/A"
        second_wr = f"{ts.second_half.win_rate*100:.0f}" if ts.second_half.win_rate is not None else "N/A"

        lines.append(f"  First half:  Sharpe={first_sharpe:>6}, WR={first_wr:>3}%, Trades={ts.first_half.trades}")
        lines.append(f"  Second half: Sharpe={second_sharpe:>6}, WR={second_wr:>3}%, Trades={ts.second_half.trades}")
        lines.append(f"  Notes: {ts.notes}")
        lines.append("")

    # Liquidity split
    if result.liquidity_split:
        ls = result.liquidity_split
        status = "PASS" if ls.passed else "FAIL"
        lines.append(f"LIQUIDITY SPLIT: {status}")
        lines.append("-" * 30)

        # Format metrics safely
        high_sharpe = f"{ls.first_half.sharpe:.2f}" if ls.first_half.sharpe is not None else "N/A"
        high_wr = f"{ls.first_half.win_rate*100:.0f}" if ls.first_half.win_rate is not None else "N/A"
        low_sharpe = f"{ls.second_half.sharpe:.2f}" if ls.second_half.sharpe is not None else "N/A"
        low_wr = f"{ls.second_half.win_rate*100:.0f}" if ls.second_half.win_rate is not None else "N/A"

        lines.append(f"  High volume: Sharpe={high_sharpe:>6}, WR={high_wr:>3}%, Trades={ls.first_half.trades}")
        lines.append(f"  Low volume:  Sharpe={low_sharpe:>6}, WR={low_wr:>3}%, Trades={ls.second_half.trades}")
        lines.append(f"  Notes: {ls.notes}")
        lines.append("")

    # Category split
    if result.category_split:
        cs = result.category_split
        status = "PASS" if cs.passed else "FAIL"
        lines.append(f"CATEGORY SPLIT: {status}")
        lines.append("-" * 30)
        for cat, metrics in sorted(cs.by_category.items()):
            sharpe_str = f"{metrics.sharpe:.2f}" if metrics.sharpe is not None else "N/A"
            wr_str = f"{metrics.win_rate*100:.0f}%" if metrics.win_rate is not None else "N/A"
            edge_marker = "+" if (metrics.sharpe or 0) > 0 else " "
            lines.append(f"  [{edge_marker}] {cat}: Sharpe={sharpe_str:>6}, WR={wr_str:>4}, Trades={metrics.trades}")
        lines.append(f"  Notes: {cs.notes}")
        lines.append("")

    # Summary
    lines.append("=" * 60)
    overall = "PASS" if result.overall_passed else "FAIL"
    lines.append(f"OVERALL: {overall} ({result.pass_rate*100:.0f}% pass rate)")
    lines.append(result.summary)
    lines.append("=" * 60)

    return "\n".join(lines)
