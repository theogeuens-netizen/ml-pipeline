"""
Comprehensive performance metrics calculation for backtesting.

Ported from futarchy's backtesting infrastructure.

Metrics Categories:
- Return Metrics
- Risk Metrics
- Risk-Adjusted Metrics
- Betting Statistics
- Robustness Metrics
- Composite Scores
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
from scipy import stats


@dataclass
class TradeRecord:
    """Single trade/bet record for metrics calculation."""

    entry_ts: datetime
    resolution_ts: datetime
    stake: float
    pnl: float
    roi: float  # roi_per_stake_net
    won: bool
    side: str  # YES or NO
    entry_price: float
    condition_id: Optional[str] = None
    market_id: Optional[int] = None
    category: Optional[str] = None
    macro_category: Optional[str] = None
    micro_category: Optional[str] = None
    volume: Optional[float] = None


@dataclass
class EquityPoint:
    """Point in equity curve."""

    timestamp: datetime
    capital: float
    drawdown: float = 0.0
    drawdown_pct: float = 0.0


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics."""

    # === Return Metrics ===
    total_return: float = 0.0
    total_return_pct: float = 0.0
    annualized_return: float = 0.0
    annualized_return_pct: float = 0.0
    total_pnl: float = 0.0

    # === Capital Metrics ===
    initial_capital: float = 0.0
    final_capital: float = 0.0
    peak_capital: float = 0.0
    min_capital: float = 0.0

    # === Risk Metrics ===
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_drawdown: float = 0.0
    avg_drawdown_pct: float = 0.0
    max_drawdown_duration_days: float = 0.0
    volatility: float = 0.0
    downside_deviation: float = 0.0
    ulcer_index: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0
    max_consecutive_losses: int = 0
    max_consecutive_wins: int = 0

    # === Risk-Adjusted Metrics ===
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    omega_ratio: Optional[float] = None
    gain_to_pain_ratio: Optional[float] = None
    sterling_ratio: Optional[float] = None
    burke_ratio: Optional[float] = None
    tail_ratio: Optional[float] = None

    # === Betting Statistics ===
    num_bets: int = 0
    num_wins: int = 0
    num_losses: int = 0
    win_rate: Optional[float] = None
    loss_rate: Optional[float] = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_loss_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    expected_value: float = 0.0
    avg_roi: Optional[float] = None
    median_roi: Optional[float] = None
    roi_std: Optional[float] = None
    kelly_edge: Optional[float] = None
    bets_per_day: float = 0.0
    capital_turnover: float = 0.0
    total_staked: float = 0.0
    avg_stake: float = 0.0

    # === Time Metrics ===
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_days: float = 0.0
    total_years: float = 0.0

    # === Robustness Metrics ===
    robustness_score: Optional[float] = None
    worst_case_return: Optional[float] = None
    sample_efficiency: Optional[float] = None
    bootstrap_p_value: Optional[float] = None

    # === Composite Scores ===
    composite_score: float = 0.0
    quality_score: Optional[float] = None
    tradability_score: Optional[float] = None

    # === Distribution Summary ===
    distribution_summary: Optional[str] = None
    distribution_percentiles: Dict[str, float] = field(default_factory=dict)


def calculate_drawdowns(
    equity_curve: List[EquityPoint],
) -> Tuple[List[float], List[float]]:
    """
    Calculate drawdown series from equity curve.
    Returns (drawdowns_absolute, drawdowns_pct)
    """
    if not equity_curve:
        return [], []

    capitals = [p.capital for p in equity_curve]
    peak = capitals[0]
    drawdowns = []
    drawdowns_pct = []

    for capital in capitals:
        peak = max(peak, capital)
        dd = peak - capital
        dd_pct = dd / peak if peak > 0 else 0
        drawdowns.append(dd)
        drawdowns_pct.append(dd_pct)

    return drawdowns, drawdowns_pct


def calculate_drawdown_durations(
    equity_curve: List[EquityPoint], drawdowns_pct: List[float]
) -> Tuple[float, List[Tuple[datetime, datetime, float]]]:
    """
    Calculate max drawdown duration and all drawdown periods.
    Returns (max_duration_days, [(start, end, duration_days), ...])
    """
    if not equity_curve or not drawdowns_pct:
        return 0.0, []

    periods = []
    in_drawdown = False
    dd_start = None
    max_duration = 0.0

    for i, (point, dd_pct) in enumerate(zip(equity_curve, drawdowns_pct)):
        if dd_pct > 0.001:  # In drawdown (> 0.1%)
            if not in_drawdown:
                in_drawdown = True
                dd_start = point.timestamp
        else:
            if in_drawdown:
                in_drawdown = False
                duration = (point.timestamp - dd_start).total_seconds() / 86400
                periods.append((dd_start, point.timestamp, duration))
                max_duration = max(max_duration, duration)
                dd_start = None

    # Handle case where we end in a drawdown
    if in_drawdown and dd_start:
        duration = (equity_curve[-1].timestamp - dd_start).total_seconds() / 86400
        periods.append((dd_start, equity_curve[-1].timestamp, duration))
        max_duration = max(max_duration, duration)

    return max_duration, periods


def calculate_consecutive_streaks(trades: List[TradeRecord]) -> Tuple[int, int]:
    """Calculate max consecutive wins and losses."""
    if not trades:
        return 0, 0

    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0

    for trade in trades:
        if trade.won:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)

    return max_wins, max_losses


def calculate_sharpe_ratio(
    returns: np.ndarray,
    num_bets: int,
    total_days: float,
    risk_free_rate: float = 0.0,
) -> Optional[float]:
    """
    Calculate Sharpe ratio annualized by calendar time.

    Note: We annualize by TIME (calendar days), not by bet frequency.
    Using bet frequency would artificially inflate Sharpe for high-frequency strategies.
    """
    if len(returns) < 2 or np.std(returns, ddof=1) == 0 or total_days <= 0:
        return None

    days_per_year = 365.25
    annualization_factor = np.sqrt(days_per_year / total_days) if total_days > 0 else 1.0

    excess_return = np.mean(returns) - risk_free_rate
    sharpe = (excess_return / np.std(returns, ddof=1)) * annualization_factor

    return float(sharpe)


def calculate_sortino_ratio(
    returns: np.ndarray,
    num_bets: int,
    total_days: float,
    target_return: float = 0.0,
) -> Optional[float]:
    """
    Calculate Sortino ratio (penalizes only downside volatility).

    Note: We annualize by TIME (calendar days), not by bet frequency.
    """
    if len(returns) < 2 or total_days <= 0:
        return None

    below_target = returns[returns < target_return]

    if len(below_target) == 0:
        return None

    deviations = below_target - target_return
    downside_deviation = float(np.sqrt(np.mean(deviations**2)))

    if downside_deviation == 0:
        return None

    days_per_year = 365.25
    annualization_factor = np.sqrt(days_per_year / total_days) if total_days > 0 else 1.0

    excess_return = np.mean(returns) - target_return
    sortino = (excess_return / downside_deviation) * annualization_factor

    return float(sortino)


def calculate_omega_ratio(returns: np.ndarray, threshold: float = 0.0) -> Optional[float]:
    """
    Calculate Omega ratio for a given threshold.
    Gains are returns above the threshold; losses are returns below it.
    """
    if returns.size == 0:
        return None

    gains = np.clip(returns - threshold, 0, None)
    losses = np.clip(threshold - returns, 0, None)

    losses_sum = losses.sum()
    if losses_sum == 0:
        return None

    return float(gains.sum() / losses_sum)


def calculate_var_cvar(
    returns: np.ndarray, confidence: float = 0.95
) -> Tuple[float, float]:
    """
    Calculate Value at Risk and Conditional VaR (Expected Shortfall).

    VaR = quantile at (1 - confidence)
    CVaR = mean of returns below VaR
    """
    if len(returns) < 10:
        return 0.0, 0.0

    var = np.percentile(returns, (1 - confidence) * 100)
    cvar_returns = returns[returns <= var]
    cvar = np.mean(cvar_returns) if len(cvar_returns) > 0 else var

    return float(var), float(cvar)


def calculate_ulcer_index(drawdowns_pct: List[float]) -> float:
    """
    Calculate Ulcer Index.

    UI = sqrt(mean(drawdown_pct^2))
    """
    if not drawdowns_pct:
        return 0.0

    dd_squared = [d**2 for d in drawdowns_pct]
    return float(np.sqrt(np.mean(dd_squared)))


def calculate_tail_ratio(returns: np.ndarray) -> Optional[float]:
    """
    Calculate Tail Ratio.

    Tail Ratio = |95th percentile| / |5th percentile|
    """
    if len(returns) < 20:
        return None

    p95 = np.percentile(returns, 95)
    p5 = np.percentile(returns, 5)

    if p5 == 0:
        return None

    return float(abs(p95) / abs(p5))


def calculate_kelly_edge(
    win_rate: float, avg_win: float, avg_loss: float
) -> Optional[float]:
    """
    Calculate Kelly edge.

    Kelly = win_rate - (loss_rate / odds)
    where odds = avg_win / |avg_loss|
    """
    if avg_loss == 0 or avg_win == 0:
        return None

    odds = abs(avg_win / avg_loss)
    loss_rate = 1 - win_rate
    kelly = win_rate - (loss_rate / odds)

    return float(kelly)


def calculate_profit_factor(wins: List[float], losses: List[float]) -> Optional[float]:
    """
    Calculate Profit Factor.

    PF = sum(wins) / sum(|losses|)
    """
    total_wins = sum(wins)
    total_losses = sum(abs(l) for l in losses)

    if total_losses == 0:
        return None

    return float(total_wins / total_losses)


def bootstrap_sharpe_pvalue(
    returns: np.ndarray, num_samples: int = 10000, seed: int = 42
) -> float:
    """
    Calculate bootstrap p-value for Sharpe ratio > 0.

    Returns percentage of bootstrap samples with positive Sharpe.
    """
    if len(returns) < 10:
        return 0.5  # Not enough data

    rng = np.random.default_rng(seed)
    positive_count = 0

    for _ in range(num_samples):
        sample = rng.choice(returns, size=len(returns), replace=True)
        if np.std(sample) > 0:
            sample_sharpe = np.mean(sample) / np.std(sample)
            if sample_sharpe > 0:
                positive_count += 1

    return positive_count / num_samples


def calculate_metrics(
    trades: List[TradeRecord],
    equity_curve: List[EquityPoint],
    initial_capital: float,
) -> PerformanceMetrics:
    """
    Calculate ALL performance metrics from trades and equity curve.

    This is the main entry point for metrics calculation.
    """
    metrics = PerformanceMetrics(initial_capital=initial_capital)

    if not trades or not equity_curve:
        return metrics

    # === Basic stats ===
    metrics.num_bets = len(trades)
    metrics.num_wins = sum(1 for t in trades if t.won)
    metrics.num_losses = metrics.num_bets - metrics.num_wins

    # === Time metrics ===
    entry_timestamps = [t.entry_ts for t in trades]
    resolution_timestamps = [t.resolution_ts for t in trades]
    metrics.start_date = min(entry_timestamps)
    metrics.end_date = max(resolution_timestamps)
    metrics.total_days = (metrics.end_date - metrics.start_date).total_seconds() / 86400
    metrics.total_years = metrics.total_days / 365.25 if metrics.total_days > 0 else 0

    # === Capital metrics ===
    capitals = [p.capital for p in equity_curve]
    metrics.final_capital = capitals[-1]
    metrics.peak_capital = max(capitals)
    metrics.min_capital = min(capitals)

    # === Return metrics ===
    metrics.total_pnl = metrics.final_capital - initial_capital
    metrics.total_return = metrics.total_pnl / initial_capital if initial_capital > 0 else 0
    metrics.total_return_pct = metrics.total_return * 100

    if metrics.total_years > 0 and metrics.final_capital > 0 and initial_capital > 0:
        metrics.annualized_return = (
            (metrics.final_capital / initial_capital) ** (1 / metrics.total_years) - 1
        )
        metrics.annualized_return_pct = metrics.annualized_return * 100

    # === Staking metrics ===
    stakes = [t.stake for t in trades]
    metrics.total_staked = sum(stakes)
    metrics.avg_stake = np.mean(stakes) if stakes else 0
    avg_capital = np.mean(capitals) if capitals else initial_capital
    metrics.capital_turnover = metrics.total_staked / avg_capital if avg_capital > 0 else 0
    metrics.bets_per_day = metrics.num_bets / metrics.total_days if metrics.total_days > 0 else 0

    # === Win/Loss metrics ===
    if metrics.num_bets > 0:
        metrics.win_rate = metrics.num_wins / metrics.num_bets
        metrics.loss_rate = metrics.num_losses / metrics.num_bets

    pnls = [t.pnl for t in trades]
    wins_pnl = [p for p in pnls if p > 0]
    losses_pnl = [p for p in pnls if p < 0]

    metrics.avg_win = np.mean(wins_pnl) if wins_pnl else 0
    metrics.avg_loss = np.mean(losses_pnl) if losses_pnl else 0  # negative

    if metrics.avg_loss != 0 and metrics.avg_win != 0:
        metrics.win_loss_ratio = abs(metrics.avg_win / metrics.avg_loss)

    metrics.profit_factor = calculate_profit_factor(wins_pnl, losses_pnl)

    # === ROI metrics ===
    rois = np.array([t.roi for t in trades])
    if len(rois) > 0:
        metrics.avg_roi = float(np.mean(rois))
        metrics.median_roi = float(np.median(rois))
        metrics.roi_std = float(np.std(rois)) if len(rois) > 1 else None

    # === Expected value ===
    if metrics.win_rate is not None:
        metrics.expected_value = (metrics.win_rate * metrics.avg_win) + (
            metrics.loss_rate * metrics.avg_loss
        )

    # === Kelly edge ===
    if metrics.win_rate is not None:
        metrics.kelly_edge = calculate_kelly_edge(
            metrics.win_rate, metrics.avg_win, abs(metrics.avg_loss)
        )

    # === Consecutive streaks ===
    metrics.max_consecutive_wins, metrics.max_consecutive_losses = (
        calculate_consecutive_streaks(trades)
    )

    # === Drawdown metrics ===
    drawdowns, drawdowns_pct = calculate_drawdowns(equity_curve)

    if drawdowns:
        metrics.max_drawdown = max(drawdowns)
        metrics.max_drawdown_pct = max(drawdowns_pct) * 100
        metrics.avg_drawdown = np.mean(drawdowns)
        metrics.avg_drawdown_pct = np.mean(drawdowns_pct) * 100
        metrics.ulcer_index = calculate_ulcer_index(drawdowns_pct) * 100

    metrics.max_drawdown_duration_days, _ = calculate_drawdown_durations(
        equity_curve, drawdowns_pct
    )

    # === Per-bet ROI metrics ===
    bet_returns = rois

    if len(bet_returns) > 1:
        metrics.volatility = float(np.std(bet_returns))

        downside = np.minimum(bet_returns, 0.0)
        metrics.downside_deviation = float(np.sqrt(np.mean(downside**2)))

        # Higher moments
        metrics.skewness = float(stats.skew(bet_returns))
        metrics.kurtosis = float(stats.kurtosis(bet_returns))

        # VaR and CVaR on per-bet returns
        metrics.var_95, metrics.cvar_95 = calculate_var_cvar(bet_returns, 0.95)

    # === Daily returns from equity curve (for Sharpe/Sortino) ===
    daily_capitals = {}
    for ec in equity_curve:
        date = ec.timestamp.date()
        daily_capitals[date] = ec.capital  # Last capital of each day

    if len(daily_capitals) >= 2:
        sorted_dates = sorted(daily_capitals.keys())
        daily_returns = []
        for i in range(1, len(sorted_dates)):
            prev_cap = daily_capitals[sorted_dates[i - 1]]
            curr_cap = daily_capitals[sorted_dates[i]]
            if prev_cap > 0:
                daily_returns.append((curr_cap - prev_cap) / prev_cap)

        daily_returns = np.array(daily_returns)

        if len(daily_returns) > 1:
            mean_daily = np.mean(daily_returns)
            std_daily = np.std(daily_returns, ddof=1)
            if std_daily > 0:
                metrics.sharpe_ratio = float((mean_daily / std_daily) * np.sqrt(365))

            downside_daily = np.minimum(daily_returns, 0.0)
            downside_std = float(np.sqrt(np.mean(downside_daily**2)))
            if downside_std > 0:
                metrics.sortino_ratio = float((mean_daily / downside_std) * np.sqrt(365))
    else:
        # Fallback: use per-bet returns if not enough daily data
        metrics.sharpe_ratio = calculate_sharpe_ratio(
            bet_returns, metrics.num_bets, metrics.total_days
        )
        metrics.sortino_ratio = calculate_sortino_ratio(
            bet_returns, metrics.num_bets, metrics.total_days
        )

    if metrics.max_drawdown_pct != 0:
        metrics.calmar_ratio = metrics.annualized_return_pct / metrics.max_drawdown_pct

    metrics.omega_ratio = calculate_omega_ratio(bet_returns)

    # Gain to pain ratio
    if losses_pnl:
        total_negative = sum(abs(l) for l in losses_pnl)
        if total_negative > 0:
            metrics.gain_to_pain_ratio = metrics.total_pnl / total_negative

    # Sterling ratio (drawdowns as decimals)
    avg_dd_dec = np.mean(drawdowns_pct) if drawdowns_pct else 0.0
    eps = 1e-9
    if avg_dd_dec > 0:
        denom = max(avg_dd_dec - 0.10, eps)
        metrics.sterling_ratio = metrics.annualized_return / denom

    # Burke ratio (drawdowns as decimals)
    if drawdowns_pct:
        rms_dd = float(np.sqrt(np.mean([d**2 for d in drawdowns_pct])))
        if rms_dd > 0:
            metrics.burke_ratio = metrics.annualized_return / rms_dd

    metrics.tail_ratio = calculate_tail_ratio(bet_returns)

    # Distribution summary (percentiles + narrative)
    if len(bet_returns) > 0:
        percentiles = {
            "p5": float(np.percentile(bet_returns, 5)),
            "p25": float(np.percentile(bet_returns, 25)),
            "p50": float(np.percentile(bet_returns, 50)),
            "p75": float(np.percentile(bet_returns, 75)),
            "p95": float(np.percentile(bet_returns, 95)),
        }
        metrics.distribution_percentiles = percentiles
        metrics.distribution_summary = (
            "Distribution Metrics reveal return shape:\n"
            f"- Skewness: {metrics.skewness:.4f} (positive means more upside outliers)\n"
            f"- Kurtosis: {metrics.kurtosis:.4f} (higher = fatter tails)\n"
            f"- Percentiles: p5={percentiles['p5']:.4f}, "
            f"p25={percentiles['p25']:.4f}, p50={percentiles['p50']:.4f}, "
            f"p75={percentiles['p75']:.4f}, p95={percentiles['p95']:.4f}"
        )

    # === Robustness metrics ===
    if metrics.sharpe_ratio is not None and metrics.num_bets > 0:
        metrics.sample_efficiency = metrics.sharpe_ratio * np.sqrt(metrics.num_bets)

    if len(bet_returns) >= 10:
        metrics.bootstrap_p_value = bootstrap_sharpe_pvalue(bet_returns)

    if (
        metrics.avg_roi is not None
        and metrics.roi_std is not None
        and metrics.roi_std > 0
    ):
        metrics.robustness_score = metrics.avg_roi / (1 + metrics.roi_std)

    # Worst case return (95% confidence)
    if metrics.avg_roi is not None and metrics.roi_std is not None:
        metrics.worst_case_return = metrics.avg_roi - 2 * metrics.roi_std

    # === Composite scores ===
    if metrics.win_rate is not None:
        metrics.composite_score = (metrics.total_return_pct * metrics.win_rate) / (
            1 + abs(metrics.max_drawdown_pct) / 100
        )

    if metrics.sharpe_ratio is not None and metrics.num_bets > 0:
        metrics.quality_score = metrics.sharpe_ratio * np.sqrt(metrics.num_bets)

    if (
        metrics.sharpe_ratio is not None
        and metrics.max_drawdown_pct > 0
        and metrics.num_bets > 0
    ):
        metrics.tradability_score = (
            metrics.sharpe_ratio * np.log(metrics.num_bets + 1)
        ) / (metrics.max_drawdown_pct / 100)

    return metrics


def metrics_to_dict(m: PerformanceMetrics) -> Dict[str, Any]:
    """Convert PerformanceMetrics to dictionary for API response."""
    return {
        # Return Metrics
        "total_return": m.total_return,
        "total_return_pct": m.total_return_pct,
        "annualized_return": m.annualized_return,
        "annualized_return_pct": m.annualized_return_pct,
        "total_pnl": m.total_pnl,
        # Capital Metrics
        "initial_capital": m.initial_capital,
        "final_capital": m.final_capital,
        "peak_capital": m.peak_capital,
        "min_capital": m.min_capital,
        # Risk Metrics
        "max_drawdown": m.max_drawdown,
        "max_drawdown_pct": m.max_drawdown_pct,
        "avg_drawdown": m.avg_drawdown,
        "avg_drawdown_pct": m.avg_drawdown_pct,
        "max_drawdown_duration_days": m.max_drawdown_duration_days,
        "volatility": m.volatility,
        "downside_deviation": m.downside_deviation,
        "ulcer_index": m.ulcer_index,
        "var_95": m.var_95,
        "cvar_95": m.cvar_95,
        "skewness": m.skewness,
        "kurtosis": m.kurtosis,
        "max_consecutive_losses": m.max_consecutive_losses,
        "max_consecutive_wins": m.max_consecutive_wins,
        # Risk-Adjusted Metrics
        "sharpe_ratio": m.sharpe_ratio,
        "sortino_ratio": m.sortino_ratio,
        "calmar_ratio": m.calmar_ratio,
        "omega_ratio": m.omega_ratio,
        "gain_to_pain_ratio": m.gain_to_pain_ratio,
        "sterling_ratio": m.sterling_ratio,
        "burke_ratio": m.burke_ratio,
        "tail_ratio": m.tail_ratio,
        # Betting Statistics
        "num_bets": m.num_bets,
        "num_wins": m.num_wins,
        "num_losses": m.num_losses,
        "win_rate": m.win_rate,
        "loss_rate": m.loss_rate,
        "avg_win": m.avg_win,
        "avg_loss": m.avg_loss,
        "win_loss_ratio": m.win_loss_ratio,
        "profit_factor": m.profit_factor,
        "expected_value": m.expected_value,
        "avg_roi": m.avg_roi,
        "median_roi": m.median_roi,
        "roi_std": m.roi_std,
        "kelly_edge": m.kelly_edge,
        "bets_per_day": m.bets_per_day,
        "capital_turnover": m.capital_turnover,
        "total_staked": m.total_staked,
        "avg_stake": m.avg_stake,
        # Time Metrics
        "start_date": m.start_date.isoformat() if m.start_date else None,
        "end_date": m.end_date.isoformat() if m.end_date else None,
        "total_days": m.total_days,
        "total_years": m.total_years,
        # Robustness Metrics
        "robustness_score": m.robustness_score,
        "worst_case_return": m.worst_case_return,
        "sample_efficiency": m.sample_efficiency,
        "bootstrap_p_value": m.bootstrap_p_value,
        # Composite Scores
        "composite_score": m.composite_score,
        "quality_score": m.quality_score,
        "tradability_score": m.tradability_score,
        # Distribution Summary
        "distribution_summary": m.distribution_summary,
        "distribution_percentiles": m.distribution_percentiles,
    }
