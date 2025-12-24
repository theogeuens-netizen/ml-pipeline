"""
Risk Manager.

Enforces risk limits before signal execution:
- Max position size
- Max total exposure
- Max number of positions
- Max drawdown
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from src.executor.config import ExecutorConfig, get_config
from strategies.base import Signal
from .positions import PositionManager

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of a risk check."""
    approved: bool
    reason: Optional[str] = None
    available_capital: float = 0.0
    suggested_size: Optional[float] = None


class RiskManager:
    """
    Risk management for the executor.

    Enforces position limits, exposure limits, and drawdown limits.
    """

    def __init__(
        self,
        config: Optional[ExecutorConfig] = None,
        is_paper: bool = True,
    ):
        """
        Initialize risk manager.

        Args:
            config: Executor configuration
            is_paper: Whether managing paper or live positions
        """
        self.config = config or get_config()
        self.is_paper = is_paper
        self.position_manager = PositionManager(is_paper=is_paper)

    def check_signal(
        self,
        signal: Signal,
        balance: float,
        db: Optional[Session] = None,
        pending_positions: int = 0,
    ) -> RiskCheckResult:
        """
        Check if a signal passes all risk checks.

        Args:
            signal: Signal to check
            balance: Available balance
            db: Optional database session
            pending_positions: Number of positions already approved this cycle for this strategy

        Returns:
            RiskCheckResult with approval status and details
        """
        from src.db.models import Market
        from src.db.database import get_session as get_db_session

        # Get risk config
        risk = self.config.risk

        # Check 0: Market is still tradeable (could have closed between scan and execution)
        # This prevents race conditions where market closes after scan but before execution
        check_db = db
        should_close = False
        if check_db is None:
            check_db = get_db_session().__enter__()
            should_close = True

        try:
            market = check_db.query(Market).filter(Market.id == signal.market_id).first()
            if market:
                if market.resolved:
                    return RiskCheckResult(
                        approved=False,
                        reason=f"Market {signal.market_id} is resolved",
                    )
                if market.closed:
                    return RiskCheckResult(
                        approved=False,
                        reason=f"Market {signal.market_id} is closed",
                    )
                if not market.accepting_orders:
                    return RiskCheckResult(
                        approved=False,
                        reason=f"Market {signal.market_id} not accepting orders",
                    )
        finally:
            if should_close:
                check_db.close()

        # Get strategy name for isolated checks
        strategy_name = getattr(signal, 'strategy_name', None)

        # Check 1: Position count limit (per-strategy first, fall back to global)
        per_strategy_limit = getattr(risk, "max_positions_per_strategy", None)
        if strategy_name and per_strategy_limit:
            position_count = self.position_manager.get_position_count(
                db, strategy_name=strategy_name
            ) + pending_positions
            if position_count >= per_strategy_limit:
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"Max positions reached for {strategy_name} "
                        f"({position_count}/{per_strategy_limit})"
                    ),
                )

        position_count = self.position_manager.get_position_count(db)
        if risk.max_positions and position_count >= risk.max_positions:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Max positions reached ({position_count}/{risk.max_positions})"
                ),
            )

        # Check 2: Already have position in this market FOR THIS STRATEGY
        # Strategy isolation: each strategy can independently hold positions
        # Only block if the SAME strategy already has a position in this market
        existing = self.position_manager.get_position_by_market(
            signal.market_id, db, strategy_name=strategy_name
        )
        if existing is not None:
            return RiskCheckResult(
                approved=False,
                reason=f"Strategy {strategy_name} already has position in market {signal.market_id}",
            )

        # Check 3: Total exposure limit (global)
        current_exposure = self.position_manager.get_total_exposure(db)
        max_exposure = risk.max_total_exposure_usd
        available_for_new = max_exposure - current_exposure

        if available_for_new <= 0:
            return RiskCheckResult(
                approved=False,
                reason=f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})",
            )

        # Check 4: Balance check (use strategy balance if available)
        strategy_balance = self._get_strategy_balance(strategy_name, db)
        effective_balance = strategy_balance if strategy_balance is not None else balance

        if effective_balance <= 0:
            return RiskCheckResult(
                approved=False,
                reason=f"Insufficient balance for {strategy_name}: ${effective_balance:.2f}",
            )

        # Check 5: Drawdown check
        if not self._check_drawdown():
            return RiskCheckResult(
                approved=False,
                reason=f"Max drawdown exceeded ({risk.max_drawdown_pct:.1%})",
            )

        # Calculate available capital for this signal
        # Use strategy-specific balance if available
        max_position = risk.max_position_usd
        available_capital = min(effective_balance, available_for_new, max_position)

        # Support both old and new Signal attribute names
        signal_size = getattr(signal, 'size_usd', None) or getattr(signal, 'suggested_size_usd', None)
        if signal_size and signal_size > available_capital:
            # Reduce suggested size to available
            suggested_size = available_capital
        else:
            suggested_size = signal_size

        return RiskCheckResult(
            approved=True,
            available_capital=available_capital,
            suggested_size=suggested_size,
        )

    def _get_strategy_balance(
        self,
        strategy_name: Optional[str],
        db: Optional[Session] = None,
    ) -> Optional[float]:
        """
        Get available balance for a specific strategy.

        Args:
            strategy_name: Strategy name
            db: Optional database session

        Returns:
            Available USD balance for the strategy, or None if not found
        """
        if not strategy_name:
            return None

        from src.executor.models import StrategyBalance
        from src.db.database import get_session

        close_db = db is None
        if db is None:
            db = get_session().__enter__()

        try:
            balance = db.query(StrategyBalance).filter(
                StrategyBalance.strategy_name == strategy_name
            ).first()

            if balance is None:
                # Strategy doesn't have a balance record yet
                # Return default allocation
                return 400.0

            return float(balance.current_usd)

        finally:
            if close_db:
                db.close()

    def _check_drawdown(self) -> bool:
        """
        Check if current drawdown is within limits.

        Drawdown is calculated as:
            (high_water_mark - total_portfolio_value) / high_water_mark

        Where total_portfolio_value = cash + current_value_of_open_positions

        Returns:
            True if within limits, False if exceeded
        """
        from src.executor.models import PaperBalance
        from src.db.database import get_session

        if self.is_paper:
            with get_session() as db:
                balance = db.query(PaperBalance).first()
                if balance is None:
                    return True

                # Get cash balance
                cash = float(balance.balance_usd)

                # Get current value of open positions
                position_value = self.position_manager.get_total_position_value(db)

                # Total portfolio value = cash + positions
                total_value = cash + position_value

                high_water = float(balance.high_water_mark)

                if high_water <= 0:
                    return True

                # Drawdown from total portfolio value, not just cash
                drawdown = (high_water - total_value) / high_water
                max_drawdown = self.config.risk.max_drawdown_pct

                if drawdown >= max_drawdown:
                    logger.warning(
                        f"Drawdown limit exceeded: {drawdown:.1%} >= {max_drawdown:.1%} "
                        f"(cash=${cash:.2f}, positions=${position_value:.2f}, total=${total_value:.2f})"
                    )
                    return False

                logger.debug(
                    f"Drawdown check OK: {drawdown:.1%} < {max_drawdown:.1%} "
                    f"(cash=${cash:.2f}, positions=${position_value:.2f})"
                )
                return True
        else:
            # For live trading, would need to check actual balance vs high water
            # For now, always pass
            return True

    def get_available_capital(
        self,
        balance: float,
        db: Optional[Session] = None,
    ) -> float:
        """
        Get available capital for new positions.

        Args:
            balance: Current balance
            db: Optional database session

        Returns:
            Available capital in USD
        """
        risk = self.config.risk

        current_exposure = self.position_manager.get_total_exposure(db)
        available_exposure = risk.max_total_exposure_usd - current_exposure
        max_position = risk.max_position_usd

        return min(balance, available_exposure, max_position)

    def check_emergency_exit(
        self,
        db: Optional[Session] = None,
    ) -> bool:
        """
        Check if emergency exit should be triggered.

        Returns:
            True if emergency exit should be triggered
        """
        # Check drawdown
        if not self._check_drawdown():
            logger.error("EMERGENCY: Drawdown limit exceeded!")
            return True

        # Could add other emergency checks here:
        # - Unusual market conditions
        # - System health issues
        # - Manual kill switch

        return False

    def get_risk_status(
        self,
        balance: float,
        db: Optional[Session] = None,
    ) -> dict:
        """
        Get current risk status.

        Args:
            balance: Current balance
            db: Optional database session

        Returns:
            Dictionary with risk metrics
        """
        risk = self.config.risk
        positions = self.position_manager.get_open_positions(db)
        exposure = sum(float(p.cost_basis) for p in positions)

        return {
            "max_position_usd": risk.max_position_usd,
            "max_total_exposure_usd": risk.max_total_exposure_usd,
            "max_positions_per_strategy": getattr(risk, "max_positions_per_strategy", None),
            "max_positions": risk.max_positions,
            "max_drawdown_pct": risk.max_drawdown_pct,
            "current_balance": balance,
            "current_exposure": exposure,
            "current_positions": len(positions),
            "exposure_utilization": exposure / risk.max_total_exposure_usd if risk.max_total_exposure_usd else 0,
            "position_utilization": len(positions) / risk.max_positions if risk.max_positions else 0,
            "available_capital": self.get_available_capital(balance, db),
            "drawdown_ok": self._check_drawdown(),
        }
