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
from src.executor.strategies.base import Signal
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
    ) -> RiskCheckResult:
        """
        Check if a signal passes all risk checks.

        Args:
            signal: Signal to check
            balance: Available balance
            db: Optional database session

        Returns:
            RiskCheckResult with approval status and details
        """
        # Get risk config
        risk = self.config.risk

        # Check 1: Position count limit
        position_count = self.position_manager.get_position_count(db)
        if position_count >= risk.max_positions:
            return RiskCheckResult(
                approved=False,
                reason=f"Max positions reached ({position_count}/{risk.max_positions})",
            )

        # Check 2: Already have position in this market
        existing = self.position_manager.get_position_by_market(signal.market_id, db)
        if existing is not None:
            return RiskCheckResult(
                approved=False,
                reason=f"Already have position in market {signal.market_id}",
            )

        # Check 3: Total exposure limit
        current_exposure = self.position_manager.get_total_exposure(db)
        max_exposure = risk.max_total_exposure_usd
        available_for_new = max_exposure - current_exposure

        if available_for_new <= 0:
            return RiskCheckResult(
                approved=False,
                reason=f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})",
            )

        # Check 4: Balance check
        if balance <= 0:
            return RiskCheckResult(
                approved=False,
                reason="Insufficient balance",
            )

        # Check 5: Drawdown check
        if not self._check_drawdown():
            return RiskCheckResult(
                approved=False,
                reason=f"Max drawdown exceeded ({risk.max_drawdown_pct:.1%})",
            )

        # Calculate available capital for this signal
        max_position = risk.max_position_usd
        available_capital = min(balance, available_for_new, max_position)

        if signal.suggested_size_usd and signal.suggested_size_usd > available_capital:
            # Reduce suggested size to available
            suggested_size = available_capital
        else:
            suggested_size = signal.suggested_size_usd

        return RiskCheckResult(
            approved=True,
            available_capital=available_capital,
            suggested_size=suggested_size,
        )

    def _check_drawdown(self) -> bool:
        """
        Check if current drawdown is within limits.

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

                current = float(balance.balance_usd)
                high_water = float(balance.high_water_mark)

                if high_water <= 0:
                    return True

                drawdown = (high_water - current) / high_water
                max_drawdown = self.config.risk.max_drawdown_pct

                if drawdown >= max_drawdown:
                    logger.warning(
                        f"Drawdown limit exceeded: {drawdown:.1%} >= {max_drawdown:.1%}"
                    )
                    return False

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
