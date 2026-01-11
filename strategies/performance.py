"""
Performance Tracker - calculates Sharpe, drawdown, and other metrics.

Tracks per-strategy performance using the strategy_balances table and
closed positions from the positions table.

Usage:
    from strategies.performance import PerformanceTracker

    tracker = PerformanceTracker(db_session)
    metrics = tracker.get_strategy_metrics("esports_no_1h")
    leaderboard = tracker.get_leaderboard()
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.executor.models import Position, StrategyBalance, TradeDecision

logger = logging.getLogger(__name__)

# Risk-free rate for Sharpe calculation (annualized)
RISK_FREE_RATE = 0.05  # 5% annual


@dataclass
class StrategyMetrics:
    """Performance metrics for a single strategy."""
    strategy_name: str

    # Balance
    allocated_usd: float = 0
    current_usd: float = 0
    total_pnl: float = 0
    realized_pnl: float = 0
    unrealized_pnl: float = 0

    # Trade stats
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    total_cost_basis: float = 0  # Sum of cost_basis for all closed trades

    # Risk metrics
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown_usd: float = 0
    max_drawdown_pct: float = 0
    current_drawdown_pct: float = 0

    # Return metrics
    total_return_pct: float = 0
    avg_win_usd: float = 0
    avg_loss_usd: float = 0
    profit_factor: float = 0  # gross_profit / gross_loss
    expectancy_usd: float = 0  # avg profit per trade

    # Time metrics
    avg_hold_hours: Optional[float] = None
    open_positions: int = 0
    first_trade: Optional[datetime] = None
    last_trade: Optional[datetime] = None

    # Daily returns for Sharpe calc
    daily_returns: list[float] = field(default_factory=list)


class PerformanceTracker:
    """
    Calculates and tracks strategy performance metrics.

    Pulls data from:
    - strategy_balances: Current balance and allocation
    - positions: Closed positions for P&L history
    - trade_decisions: Decision audit trail
    """

    def __init__(self, session: Session):
        self.session = session

    def ensure_strategy_balances(self):
        """Ensure every enabled strategy has a balance row so it surfaces in analytics/UI."""
        from strategies.loader import load_strategies

        existing = {
            name for (name,) in self.session.query(StrategyBalance.strategy_name).all()
        }
        created = False

        for strat in load_strategies(enabled_only=True):
            if strat.name in existing:
                continue

            allocated = getattr(strat, "allocated_usd", 400.0)
            balance = StrategyBalance(
                strategy_name=strat.name,
                allocated_usd=allocated,
                current_usd=allocated,
                high_water_mark=allocated,
                low_water_mark=allocated,
            )
            self.session.add(balance)
            created = True

        if created:
            self.session.commit()

    def get_strategy_metrics(
        self,
        strategy_name: str,
        live_only: bool = False
    ) -> Optional[StrategyMetrics]:
        """
        Get comprehensive metrics for a single strategy.

        Args:
            strategy_name: Strategy name to analyze
            live_only: If True, only include live (non-paper) positions

        Returns:
            StrategyMetrics or None if strategy not found
        """
        # If live_only, calculate directly from positions
        if live_only:
            return self._get_live_only_metrics(strategy_name)

        # Seed balances so new strategies show up immediately in the UI
        self.ensure_strategy_balances()

        # Get balance info
        balance = self.session.query(StrategyBalance).filter(
            StrategyBalance.strategy_name == strategy_name
        ).first()

        if not balance:
            return None

        # Calculate unrealized P&L from actual open positions (not stale DB value)
        open_positions = self.session.query(Position).filter(
            Position.strategy_name == strategy_name,
            Position.status == "open",
            Position.is_paper == True,
        ).all()

        unrealized_pnl = sum(
            float(p.unrealized_pnl) if p.unrealized_pnl else 0
            for p in open_positions
        )
        cost_basis = sum(
            float(p.cost_basis) if p.cost_basis else 0
            for p in open_positions
        )

        realized_pnl = float(balance.realized_pnl)
        total_pnl = realized_pnl + unrealized_pnl

        # Cash = allocated - cost_basis + realized
        cash = float(balance.allocated_usd) - cost_basis + realized_pnl

        metrics = StrategyMetrics(
            strategy_name=strategy_name,
            allocated_usd=float(balance.allocated_usd),
            current_usd=round(cash, 2),  # Recalculated cash
            total_pnl=round(total_pnl, 2),  # Recalculated
            realized_pnl=round(realized_pnl, 2),
            unrealized_pnl=round(unrealized_pnl, 2),  # From actual positions
            trade_count=balance.trade_count,
            win_count=balance.win_count,
            loss_count=balance.loss_count,
            max_drawdown_usd=float(balance.max_drawdown_usd),
            max_drawdown_pct=float(balance.max_drawdown_pct),
        )

        # Calculate derived metrics
        if metrics.trade_count > 0:
            metrics.win_rate = metrics.win_count / metrics.trade_count

        if metrics.allocated_usd > 0:
            metrics.total_return_pct = (metrics.total_pnl / metrics.allocated_usd) * 100
            # Calculate drawdown from current portfolio value vs high water mark
            position_value = sum(
                float(p.current_value) if p.current_value else float(p.cost_basis or 0)
                for p in open_positions
            )
            portfolio_value = cash + position_value
            high_water = max(float(balance.high_water_mark), metrics.allocated_usd)
            metrics.current_drawdown_pct = (
                (high_water - portfolio_value) / high_water * 100
                if high_water > 0 else 0
            )

        # Get position stats
        self._calculate_position_stats(metrics)

        # Calculate Sharpe if we have enough data
        self._calculate_risk_metrics(metrics)

        return metrics

    def _get_live_only_metrics(self, strategy_name: str) -> Optional[StrategyMetrics]:
        """
        Calculate metrics from live (non-paper) positions only.

        This bypasses the StrategyBalance table and calculates directly from
        positions where is_paper=False.
        """
        # Get closed live positions
        closed = self.session.query(Position).filter(
            Position.strategy_name == strategy_name,
            Position.status == "closed",
            Position.is_paper == False,
        ).all()

        # Get open live positions
        open_positions = self.session.query(Position).filter(
            Position.strategy_name == strategy_name,
            Position.status == "open",
            Position.is_paper == False,
        ).all()

        if not closed and not open_positions:
            return None

        # Calculate P&L from positions
        realized_pnl = sum(float(p.realized_pnl or 0) for p in closed)
        unrealized_pnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)
        total_pnl = realized_pnl + unrealized_pnl

        # Count wins/losses
        wins = [p for p in closed if float(p.realized_pnl or 0) > 0]
        losses = [p for p in closed if float(p.realized_pnl or 0) < 0]
        trade_count = len(closed)

        # Total cost basis
        total_cost_basis = sum(float(p.cost_basis or 0) for p in closed)
        open_cost_basis = sum(float(p.cost_basis or 0) for p in open_positions)

        # Estimated allocated (use 400 as default, but could be tracked separately)
        allocated_usd = 400.0
        current_usd = allocated_usd - open_cost_basis + realized_pnl

        metrics = StrategyMetrics(
            strategy_name=strategy_name,
            allocated_usd=allocated_usd,
            current_usd=round(current_usd, 2),
            total_pnl=round(total_pnl, 2),
            realized_pnl=round(realized_pnl, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            trade_count=trade_count,
            win_count=len(wins),
            loss_count=len(losses),
            total_cost_basis=total_cost_basis,
            open_positions=len(open_positions),
        )

        # Calculate derived metrics
        if metrics.trade_count > 0:
            metrics.win_rate = metrics.win_count / metrics.trade_count

        if metrics.allocated_usd > 0:
            metrics.total_return_pct = (metrics.total_pnl / metrics.allocated_usd) * 100

        # Avg win/loss
        if wins:
            metrics.avg_win_usd = sum(float(p.realized_pnl) for p in wins) / len(wins)
        if losses:
            metrics.avg_loss_usd = sum(float(p.realized_pnl) for p in losses) / len(losses)

        # Profit factor
        gross_profit = sum(float(p.realized_pnl) for p in wins) if wins else 0
        gross_loss = abs(sum(float(p.realized_pnl) for p in losses)) if losses else 0
        if gross_loss > 0:
            metrics.profit_factor = gross_profit / gross_loss

        # Expectancy
        if metrics.trade_count > 0:
            metrics.expectancy_usd = metrics.realized_pnl / metrics.trade_count

        # Hold time
        hold_times = []
        for p in closed:
            if p.entry_time and p.exit_time:
                delta = p.exit_time - p.entry_time
                hold_times.append(delta.total_seconds() / 3600)
        if hold_times:
            metrics.avg_hold_hours = sum(hold_times) / len(hold_times)

        # Time range
        sorted_by_exit = sorted(
            [p for p in closed if p.exit_time],
            key=lambda p: p.exit_time
        )
        if sorted_by_exit:
            metrics.first_trade = sorted_by_exit[0].exit_time
            metrics.last_trade = sorted_by_exit[-1].exit_time

        return metrics

    def _calculate_position_stats(self, metrics: StrategyMetrics):
        """Calculate stats from closed and open positions."""
        # Always count open positions first
        metrics.open_positions = self.session.query(Position).filter(
            Position.strategy_name == metrics.strategy_name,
            Position.status == "open"
        ).count()

        # Get closed positions for P&L stats
        closed = self.session.query(Position).filter(
            Position.strategy_name == metrics.strategy_name,
            Position.status == "closed"
        ).all()

        if not closed:
            return

        # Total cost basis for all closed trades (for ROI calculation)
        metrics.total_cost_basis = sum(
            float(p.cost_basis) if p.cost_basis else 0
            for p in closed
        )

        wins = [p for p in closed if float(p.realized_pnl) > 0]
        losses = [p for p in closed if float(p.realized_pnl) < 0]

        # Avg win/loss
        if wins:
            metrics.avg_win_usd = sum(float(p.realized_pnl) for p in wins) / len(wins)
        if losses:
            metrics.avg_loss_usd = sum(float(p.realized_pnl) for p in losses) / len(losses)

        # Profit factor
        gross_profit = sum(float(p.realized_pnl) for p in wins) if wins else 0
        gross_loss = abs(sum(float(p.realized_pnl) for p in losses)) if losses else 0
        if gross_loss > 0:
            metrics.profit_factor = gross_profit / gross_loss

        # Expectancy
        if metrics.trade_count > 0:
            metrics.expectancy_usd = metrics.realized_pnl / metrics.trade_count

        # Hold time
        hold_times = []
        for p in closed:
            if p.entry_time and p.exit_time:
                delta = p.exit_time - p.entry_time
                hold_times.append(delta.total_seconds() / 3600)
        if hold_times:
            metrics.avg_hold_hours = sum(hold_times) / len(hold_times)

        # Time range
        sorted_by_exit = sorted(
            [p for p in closed if p.exit_time],
            key=lambda p: p.exit_time
        )
        if sorted_by_exit:
            metrics.first_trade = sorted_by_exit[0].exit_time
            metrics.last_trade = sorted_by_exit[-1].exit_time

    def _calculate_risk_metrics(self, metrics: StrategyMetrics):
        """Calculate Sharpe and Sortino ratios from daily returns."""
        # Get daily P&L from positions
        daily_pnl = self._get_daily_returns(metrics.strategy_name)

        if len(daily_pnl) < 5:
            # Not enough data for meaningful Sharpe
            return

        # Convert to returns
        if metrics.allocated_usd > 0:
            daily_returns = [pnl / metrics.allocated_usd for pnl in daily_pnl]
        else:
            return

        metrics.daily_returns = daily_returns

        # Mean and std
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
        std_return = math.sqrt(variance) if variance > 0 else 0

        # Annualized (252 trading days)
        annualized_return = mean_return * 252
        annualized_std = std_return * math.sqrt(252)

        # Sharpe ratio
        if annualized_std > 0:
            metrics.sharpe_ratio = (annualized_return - RISK_FREE_RATE) / annualized_std

        # Sortino ratio (downside deviation only)
        negative_returns = [r for r in daily_returns if r < 0]
        if negative_returns:
            downside_variance = sum(r ** 2 for r in negative_returns) / len(daily_returns)
            downside_std = math.sqrt(downside_variance) * math.sqrt(252)
            if downside_std > 0:
                metrics.sortino_ratio = (annualized_return - RISK_FREE_RATE) / downside_std

    def _get_daily_returns(self, strategy_name: str) -> list[float]:
        """Get daily P&L series for a strategy."""
        # Get closed positions grouped by exit date
        query = text("""
            SELECT DATE(exit_time) as exit_date, SUM(realized_pnl) as daily_pnl
            FROM positions
            WHERE strategy_name = :name
            AND status = 'closed'
            AND exit_time IS NOT NULL
            GROUP BY DATE(exit_time)
            ORDER BY exit_date
        """)

        result = self.session.execute(query, {"name": strategy_name})
        return [float(row[1]) for row in result]

    def get_leaderboard(
        self,
        sort_by: str = "total_pnl",
        limit: int = 25,
        live_only: bool = False
    ) -> list[StrategyMetrics]:
        """
        Get leaderboard of all strategies sorted by performance.

        Args:
            sort_by: Metric to sort by (total_pnl, sharpe_ratio, win_rate, etc.)
            limit: Max strategies to return
            live_only: If True, only include live (non-paper) positions

        Returns:
            List of StrategyMetrics sorted by chosen metric
        """
        if live_only:
            # Get unique strategy names from live positions
            strategy_names = self.session.query(Position.strategy_name).filter(
                Position.is_paper == False
            ).distinct().all()
            strategy_names = [name for (name,) in strategy_names]

            metrics_list = []
            for name in strategy_names:
                metrics = self.get_strategy_metrics(name, live_only=True)
                if metrics:
                    metrics_list.append(metrics)
        else:
            # Ensure we have balance rows for every enabled strategy (including newly added)
            self.ensure_strategy_balances()

            # Get all strategy names
            balances = self.session.query(StrategyBalance).all()

            metrics_list = []
            for balance in balances:
                metrics = self.get_strategy_metrics(balance.strategy_name)
                if metrics:
                    metrics_list.append(metrics)

        # Sort by chosen metric
        def get_sort_key(m: StrategyMetrics):
            val = getattr(m, sort_by, 0)
            return val if val is not None else float('-inf')

        metrics_list.sort(key=get_sort_key, reverse=True)

        return metrics_list[:limit]

    def update_strategy_balance(
        self,
        strategy_name: str,
        pnl_change: float,
        is_win: bool,
    ):
        """
        Update strategy balance after a position closes.

        Args:
            strategy_name: Strategy to update
            pnl_change: P&L from closed position (positive or negative)
            is_win: Whether the trade was profitable
        """
        balance = self.session.query(StrategyBalance).filter(
            StrategyBalance.strategy_name == strategy_name
        ).with_for_update().first()

        if not balance:
            # Create if doesn't exist
            balance = StrategyBalance(
                strategy_name=strategy_name,
                allocated_usd=400,
                current_usd=400,
            )
            self.session.add(balance)

        # Update balances
        balance.current_usd = float(balance.current_usd) + pnl_change
        balance.realized_pnl = float(balance.realized_pnl) + pnl_change
        balance.total_pnl = float(balance.realized_pnl) + float(balance.unrealized_pnl)
        balance.trade_count += 1

        if is_win:
            balance.win_count += 1
        else:
            balance.loss_count += 1

        # Update high/low water marks
        if balance.current_usd > balance.high_water_mark:
            balance.high_water_mark = balance.current_usd
        if balance.current_usd < balance.low_water_mark:
            balance.low_water_mark = balance.current_usd

        # Update drawdown
        drawdown_usd = float(balance.high_water_mark) - float(balance.current_usd)
        drawdown_pct = drawdown_usd / float(balance.high_water_mark) if float(balance.high_water_mark) > 0 else 0

        if drawdown_usd > float(balance.max_drawdown_usd):
            balance.max_drawdown_usd = drawdown_usd
            balance.max_drawdown_pct = drawdown_pct

        self.session.commit()
        logger.info(
            f"Updated {strategy_name}: pnl={pnl_change:+.2f}, "
            f"current={float(balance.current_usd):.2f}, "
            f"total_pnl={float(balance.total_pnl):.2f}"
        )

    def update_unrealized_pnl(self, strategy_name: str, unrealized: float):
        """
        Update strategy's unrealized P&L from open positions.

        Called periodically to update position values.
        """
        balance = self.session.query(StrategyBalance).filter(
            StrategyBalance.strategy_name == strategy_name
        ).first()

        if balance:
            balance.unrealized_pnl = unrealized
            balance.total_pnl = float(balance.realized_pnl) + unrealized
            self.session.commit()

    def get_debug_info(self, strategy_name: str) -> dict:
        """
        Get debug information for "why isn't this trading?" analysis.

        Returns funnel data from the strategy's get_debug_stats if available.
        """
        from strategies.loader import get_strategy_by_name

        strategy = get_strategy_by_name(strategy_name)
        if not strategy:
            return {"error": f"Strategy {strategy_name} not found"}

        # Get recent decisions
        recent = self.session.query(TradeDecision).filter(
            TradeDecision.strategy_name == strategy_name
        ).order_by(TradeDecision.timestamp.desc()).limit(10).all()

        recent_decisions = [
            {
                "timestamp": d.timestamp.isoformat(),
                "market_id": d.market_id,
                "signal_side": d.signal_side,
                "executed": d.executed,
                "rejected_reason": d.rejected_reason,
            }
            for d in recent
        ]

        # Get counts
        decision_stats = self.session.execute(text("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN executed THEN 1 ELSE 0 END) as executed,
                SUM(CASE WHEN rejected_reason IS NOT NULL THEN 1 ELSE 0 END) as rejected
            FROM trade_decisions
            WHERE strategy_name = :name
            AND timestamp > NOW() - INTERVAL '24 hours'
        """), {"name": strategy_name}).first()

        return {
            "strategy_name": strategy_name,
            "strategy_type": type(strategy).__name__,
            "params": {
                k: getattr(strategy, k)
                for k in dir(strategy)
                if not k.startswith("_") and k not in ("name", "version", "logger", "scan", "filter", "get_sha", "get_params", "should_exit", "on_signal_executed", "on_position_closed", "get_debug_stats")
                and not callable(getattr(strategy, k, None))
            },
            "last_24h": {
                "total_decisions": decision_stats[0] if decision_stats else 0,
                "executed": decision_stats[1] if decision_stats else 0,
                "rejected": decision_stats[2] if decision_stats else 0,
            },
            "recent_decisions": recent_decisions,
        }


def format_metrics_table(metrics_list: list[StrategyMetrics]) -> str:
    """Format metrics as ASCII table for CLI display."""
    if not metrics_list:
        return "No strategies found"

    lines = []
    lines.append("=" * 100)
    lines.append(f"{'Strategy':<25} {'P&L':>10} {'Return':>8} {'Win%':>6} "
                f"{'Sharpe':>7} {'MaxDD':>7} {'Trades':>6} {'Open':>5}")
    lines.append("-" * 100)

    for m in metrics_list:
        sharpe_str = f"{m.sharpe_ratio:+.2f}" if m.sharpe_ratio else "  N/A"
        lines.append(
            f"{m.strategy_name:<25} "
            f"{m.total_pnl:>+10.2f} "
            f"{m.total_return_pct:>+7.1f}% "
            f"{m.win_rate*100:>5.0f}% "
            f"{sharpe_str:>7} "
            f"{m.max_drawdown_pct:>6.1f}% "
            f"{m.trade_count:>6} "
            f"{m.open_positions:>5}"
        )

    lines.append("=" * 100)
    return "\n".join(lines)
