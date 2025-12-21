"""
Core backtesting engine for polymarket-ml strategies.

Runs strategies against historical data and computes P&L.

Ported from futarchy's backtesting infrastructure, adapted
for polymarket-ml's Strategy interface.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence, Dict, Any

from .metrics import (
    TradeRecord,
    EquityPoint,
    PerformanceMetrics,
    calculate_metrics,
    metrics_to_dict,
)
from .staking import calculate_stake


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    # Capital settings
    initial_capital: float = 1000.0
    stake_per_bet: float = 10.0
    stake_mode: str = "fixed"  # fixed, fixed_pct, kelly, half_kelly

    # Date range
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    # Filters
    categories: Optional[List[str]] = None  # Filter by macro_category
    min_volume: Optional[float] = None

    # Cost parameters
    cost_per_bet: float = 0.0  # Fixed cost per bet (trading fee)

    # Risk parameters
    max_position_pct: float = 0.25  # Max % of capital per position


@dataclass
class BacktestResult:
    """Result of a backtest run."""

    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    metrics: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    metrics_dict: Dict[str, Any] = field(default_factory=dict)
    bets_executed: int = 0
    bets_skipped: int = 0
    signals_generated: int = 0


@dataclass
class HistoricalBet:
    """
    A betting opportunity from historical data.

    Represents a signal that was generated at entry_ts
    and resolved at resolution_ts.
    """

    # Timing
    entry_ts: datetime
    resolution_ts: datetime

    # Market info
    market_id: int
    condition_id: str
    question: str

    # Trade details
    side: str  # YES or NO
    entry_price: float  # Price at entry (0-1)

    # Resolution
    outcome: str  # YES or NO (what actually happened)

    # P&L calculation: if we bet on side at entry_price
    # and outcome matches side, we win (1/entry_price - 1)
    # if outcome doesn't match, we lose stake (-1)
    @property
    def roi_per_stake(self) -> float:
        """Return on investment per unit stake."""
        if self.side == self.outcome:
            # Win: we bet on the winning side
            return (1 / self.entry_price) - 1 if self.entry_price > 0 else 0
        else:
            # Loss: we bet on the losing side, lose stake
            return -1.0

    # Categories
    macro_category: Optional[str] = None
    micro_category: Optional[str] = None
    volume: Optional[float] = None


def run_backtest(
    bets: Sequence[HistoricalBet],
    config: BacktestConfig,
) -> BacktestResult:
    """
    Run a backtest on historical betting opportunities.

    This is the simple version without capital lockup.
    Capital is immediately available after each bet.

    Args:
        bets: Sequence of historical betting opportunities
        config: Backtest configuration

    Returns:
        BacktestResult with trades, equity curve, and metrics
    """
    if not bets:
        return BacktestResult(
            metrics=PerformanceMetrics(initial_capital=config.initial_capital)
        )

    # Sort bets by resolution_ts since that's when P&L is realized
    bets_sorted = sorted(bets, key=lambda b: (b.resolution_ts, b.entry_ts))

    capital = config.initial_capital
    if capital <= 0:
        return BacktestResult(
            metrics=PerformanceMetrics(initial_capital=config.initial_capital)
        )

    trades: List[TradeRecord] = []
    first_resolution = bets_sorted[0].resolution_ts
    equity_curve: List[EquityPoint] = [
        EquityPoint(timestamp=first_resolution, capital=capital)
    ]

    bets_executed = 0
    bets_skipped = 0

    for bet in bets_sorted:
        roi = bet.roi_per_stake
        entry_price = bet.entry_price
        bet_side = bet.side

        # Calculate stake based on mode
        stake = calculate_stake(
            capital=capital,
            entry_price=entry_price,
            bet_side=bet_side,
            stake_mode=config.stake_mode,
            base_stake=config.stake_per_bet,
        )

        # Apply max position limit
        max_stake = capital * config.max_position_pct
        stake = min(stake, max_stake)

        # Include cost_per_bet in capital requirement check
        total_bet_cost = stake + config.cost_per_bet

        # Skip if not enough capital
        if total_bet_cost > capital:
            bets_skipped += 1
            continue

        # Deduct cost per bet
        capital -= config.cost_per_bet

        # Execute bet
        pnl = stake * roi
        capital += pnl
        bets_executed += 1

        # Record trade
        net_pnl = pnl - config.cost_per_bet
        trade = TradeRecord(
            entry_ts=bet.entry_ts,
            resolution_ts=bet.resolution_ts,
            stake=stake,
            pnl=net_pnl,
            roi=net_pnl / stake if stake > 0 else 0,
            won=roi > 0,
            side=bet_side,
            entry_price=entry_price,
            condition_id=bet.condition_id,
            market_id=bet.market_id,
            macro_category=bet.macro_category,
            micro_category=bet.micro_category,
            volume=bet.volume,
        )
        trades.append(trade)

        # Update equity curve at resolution time
        equity_curve.append(EquityPoint(timestamp=bet.resolution_ts, capital=capital))

    # Calculate comprehensive metrics
    metrics = calculate_metrics(trades, equity_curve, config.initial_capital)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        metrics_dict=metrics_to_dict(metrics),
        bets_executed=bets_executed,
        bets_skipped=bets_skipped,
        signals_generated=len(bets),
    )


def run_backtest_with_lockup(
    bets: Sequence[HistoricalBet],
    config: BacktestConfig,
) -> BacktestResult:
    """
    Backtest with capital lockup (realistic simulation).

    Capital is locked when bet is placed and released on resolution.
    This models real prediction market behavior where funds are tied up.

    Args:
        bets: Sequence of historical betting opportunities
        config: Backtest configuration

    Returns:
        BacktestResult with trades, equity curve, and metrics
    """
    if not bets:
        return BacktestResult(
            metrics=PerformanceMetrics(initial_capital=config.initial_capital)
        )

    # Sort by entry_ts, then resolution_ts for deterministic ordering
    bets_sorted = sorted(bets, key=lambda b: (b.entry_ts, b.resolution_ts))

    available = config.initial_capital
    if available <= 0:
        return BacktestResult(
            metrics=PerformanceMetrics(initial_capital=config.initial_capital)
        )

    # locked: (release_ts, stake, roi, bet)
    locked: List[tuple] = []

    trades: List[TradeRecord] = []
    equity_curve: List[EquityPoint] = []

    # Record initial equity
    equity_curve.append(
        EquityPoint(timestamp=bets_sorted[0].entry_ts, capital=config.initial_capital)
    )

    bets_executed = 0
    bets_skipped = 0

    def get_total_capital() -> float:
        """Calculate total capital including locked funds at cost."""
        locked_value = sum(stake for _, stake, _, _ in locked)
        return available + locked_value

    def release_funds(up_to: datetime):
        """Release locked funds that have resolved by the given time."""
        nonlocal available, locked

        # Separate bets into releasable and still locked
        to_release = []
        still_locked = []

        for item in locked:
            release_ts = item[0]
            if release_ts <= up_to:
                to_release.append(item)
            else:
                still_locked.append(item)

        if not to_release:
            return

        # Sort by resolution timestamp
        to_release.sort(key=lambda x: x[0])

        for release_ts, stake, roi, bet in to_release:
            # Payout: stake * (1 + roi)
            # roi > 0: win, get stake + profit
            # roi < 0 (= -1): loss, get nothing
            payout = stake * (1 + roi)
            available += payout

            # Record trade
            trade = TradeRecord(
                entry_ts=bet.entry_ts,
                resolution_ts=release_ts,
                stake=stake,
                pnl=stake * roi,
                roi=roi,
                won=roi > 0,
                side=bet.side,
                entry_price=bet.entry_price,
                condition_id=bet.condition_id,
                market_id=bet.market_id,
                macro_category=bet.macro_category,
                micro_category=bet.micro_category,
                volume=bet.volume,
            )
            trades.append(trade)

        # Record equity point after releases
        if to_release:
            latest_release = max(item[0] for item in to_release)
            total_capital = get_total_capital()
            equity_curve.append(EquityPoint(timestamp=latest_release, capital=total_capital))

        locked[:] = still_locked

    for bet in bets_sorted:
        # First release any funds that have resolved
        release_funds(bet.entry_ts)

        roi = bet.roi_per_stake
        entry_price = bet.entry_price
        bet_side = bet.side

        # Calculate stake based on mode and available capital
        stake = calculate_stake(
            capital=available,
            entry_price=entry_price,
            bet_side=bet_side,
            stake_mode=config.stake_mode,
            base_stake=config.stake_per_bet,
        )

        # Apply max position limit
        max_stake = available * config.max_position_pct
        stake = min(stake, max_stake)

        # Include cost_per_bet in capital requirement check
        total_bet_cost = stake + config.cost_per_bet

        # Skip if not enough available capital
        if total_bet_cost > available:
            bets_skipped += 1
            continue

        # Deduct cost per bet and lock the stake
        available -= config.cost_per_bet
        available -= stake
        release_ts = bet.resolution_ts

        # Store net roi (adjusted for cost per bet relative to stake)
        cost_adjusted_roi = roi - (config.cost_per_bet / stake) if stake > 0 else roi
        locked.append((release_ts, stake, cost_adjusted_roi, bet))

        bets_executed += 1

    # Release all remaining funds
    if bets_sorted:
        max_resolution = max(bet.resolution_ts for bet in bets_sorted)
        release_funds(max_resolution)

        # Final equity point
        equity_curve.append(EquityPoint(timestamp=max_resolution, capital=available))

    # Sort equity curve by timestamp
    equity_curve.sort(key=lambda p: p.timestamp)

    # Remove duplicate timestamps (keep last value)
    if equity_curve:
        seen_timestamps: Dict[datetime, EquityPoint] = {}
        for point in equity_curve:
            seen_timestamps[point.timestamp] = point
        equity_curve = sorted(seen_timestamps.values(), key=lambda p: p.timestamp)

    # Calculate comprehensive metrics
    metrics = calculate_metrics(trades, equity_curve, config.initial_capital)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        metrics_dict=metrics_to_dict(metrics),
        bets_executed=bets_executed,
        bets_skipped=bets_skipped,
        signals_generated=len(bets),
    )


def format_backtest_summary(result: BacktestResult, strategy_name: str = "") -> str:
    """
    Format a human-readable summary of backtest results.

    Args:
        result: BacktestResult from run_backtest
        strategy_name: Name of the strategy (for header)

    Returns:
        Formatted string for CLI output
    """
    m = result.metrics
    lines = []

    # Header
    lines.append("=" * 60)
    if strategy_name:
        lines.append(f"BACKTEST: {strategy_name}")
    if m.start_date and m.end_date:
        lines.append(
            f"Period: {m.start_date.strftime('%Y-%m-%d')} to "
            f"{m.end_date.strftime('%Y-%m-%d')} ({m.total_days:.0f} days)"
        )
    lines.append("=" * 60)
    lines.append("")

    # Performance Summary
    lines.append("PERFORMANCE SUMMARY")
    lines.append("-" * 20)
    lines.append(f"Initial Capital:    ${m.initial_capital:,.2f}")
    lines.append(f"Final Capital:      ${m.final_capital:,.2f}")

    sign = "+" if m.total_return_pct >= 0 else ""
    lines.append(f"Total Return:       {sign}{m.total_return_pct:.2f}%")

    if m.sharpe_ratio is not None:
        lines.append(f"Sharpe Ratio:       {m.sharpe_ratio:.2f}")
    lines.append(f"Max Drawdown:       -{m.max_drawdown_pct:.1f}%")
    lines.append("")

    # Trade Statistics
    lines.append("TRADE STATISTICS")
    lines.append("-" * 20)
    lines.append(f"Total Trades:       {m.num_bets}")
    lines.append(f"Signals Generated:  {result.signals_generated}")
    lines.append(f"Bets Executed:      {result.bets_executed}")
    lines.append(f"Bets Skipped:       {result.bets_skipped}")
    if m.win_rate is not None:
        lines.append(f"Win Rate:           {m.win_rate * 100:.1f}%")
    lines.append(f"Avg Win:            ${m.avg_win:.2f}")
    lines.append(f"Avg Loss:           ${m.avg_loss:.2f}")
    if m.profit_factor is not None:
        lines.append(f"Profit Factor:      {m.profit_factor:.2f}")
    lines.append("")

    # Equity Curve Summary (weekly if enough data)
    if len(result.equity_curve) >= 2:
        lines.append("EQUITY CURVE")
        lines.append("-" * 20)

        # Get first and last few points
        ec = result.equity_curve
        for i, point in enumerate(ec[:min(5, len(ec))]):
            lines.append(f"  {point.timestamp.strftime('%Y-%m-%d')}: ${point.capital:,.2f}")
        if len(ec) > 5:
            lines.append("  ...")
            lines.append(f"  {ec[-1].timestamp.strftime('%Y-%m-%d')}: ${ec[-1].capital:,.2f}")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
