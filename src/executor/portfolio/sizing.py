"""
Position Sizer.

Calculates appropriate position sizes based on:
- Fixed size
- Kelly criterion
- Volatility-scaled sizing
"""

import logging
import math
from typing import Optional

from src.executor.config import ExecutorConfig, SizingConfig, SizingMethod, get_config
from src.executor.strategies.base import Signal

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Position sizing calculator.

    Supports multiple sizing methods:
    - Fixed: Use a fixed USD amount
    - Kelly: Size based on edge and confidence
    - Volatility-scaled: Size inversely to volatility
    """

    def __init__(self, config: Optional[ExecutorConfig] = None):
        """
        Initialize position sizer.

        Args:
            config: Executor configuration
        """
        self.config = config or get_config()

    def calculate_size(
        self,
        signal: Signal,
        available_capital: float,
        sizing_config: Optional[SizingConfig] = None,
    ) -> float:
        """
        Calculate position size for a signal.

        Args:
            signal: Trading signal
            available_capital: Max available capital for this trade
            sizing_config: Optional sizing config override

        Returns:
            Position size in USD
        """
        sizing = sizing_config or self.config.get_effective_sizing(signal.strategy_name)

        # Calculate base size based on method
        if sizing.method == SizingMethod.FIXED:
            size = self._fixed_size(sizing)
        elif sizing.method == SizingMethod.KELLY:
            size = self._kelly_size(signal, sizing)
        elif sizing.method == SizingMethod.VOLATILITY_SCALED:
            size = self._volatility_scaled_size(signal, sizing, available_capital)
        else:
            size = sizing.fixed_amount_usd

        # Apply max size cap
        if sizing.max_size_usd:
            size = min(size, sizing.max_size_usd)

        # Ensure we don't exceed available capital
        size = min(size, available_capital)

        # Minimum position size
        size = max(size, 1.0)  # At least $1

        logger.debug(
            f"Sized signal: method={sizing.method.value}, "
            f"base=${size:.2f}, available=${available_capital:.2f}"
        )

        return round(size, 2)

    def _fixed_size(self, sizing: SizingConfig) -> float:
        """Calculate fixed position size."""
        return sizing.fixed_amount_usd

    def _kelly_size(self, signal: Signal, sizing: SizingConfig) -> float:
        """
        Calculate position size using Kelly criterion.

        Kelly formula: f = (p * (b + 1) - 1) / b
        Where:
        - f = fraction of capital to bet
        - p = probability of winning
        - b = odds (profit if win / loss if lose)

        For prediction markets:
        - p = signal.confidence (estimated probability we're right)
        - b = (1 - price) / price for buying YES at price

        We use fractional Kelly (kelly_fraction) to be more conservative.
        """
        # Use confidence as probability estimate
        p = signal.confidence if signal.confidence else 0.5

        # Edge is the expected return per dollar
        edge = signal.edge if signal.edge else 0.0

        if edge <= 0 or p <= 0.5:
            # No edge or not confident, use minimum
            return sizing.fixed_amount_usd

        # Calculate odds from edge and probability
        # edge = p * profit - (1-p) * loss
        # For unit bet: edge = p * (1/price - 1) - (1-p)
        # Simplify to: b = edge / (1 - p) approximately
        if p < 1:
            b = max(edge / (1 - p), 0.1)
        else:
            b = 10  # Cap odds

        # Full Kelly
        kelly = (p * (b + 1) - 1) / b if b > 0 else 0

        # Apply fractional Kelly
        kelly = kelly * sizing.kelly_fraction

        # Convert to position size (using available capital as base)
        # Note: This should be applied to total capital, but we use
        # fixed_amount as a proxy for capital allocation to this strategy
        base_capital = sizing.fixed_amount_usd * 10  # Assume 10x allocation
        size = base_capital * kelly

        # Clamp to reasonable range
        size = max(sizing.fixed_amount_usd, min(size, sizing.fixed_amount_usd * 4))

        return size

    def _volatility_scaled_size(
        self,
        signal: Signal,
        sizing: SizingConfig,
        available_capital: float,
    ) -> float:
        """
        Calculate position size scaled inversely to volatility.

        Lower volatility = larger position
        Higher volatility = smaller position

        This helps maintain similar risk across different market conditions.
        """
        # Default volatility (if not available in signal metadata)
        volatility = signal.metadata.get("volatility", 0.1)

        if volatility <= 0:
            volatility = 0.1  # Default 10% volatility

        # Target volatility contribution (e.g., 1% portfolio risk)
        target_vol = 0.01

        # Calculate size to achieve target volatility
        # size = (target_vol * capital) / volatility
        size = (target_vol * available_capital) / volatility

        # Clamp to reasonable range
        min_size = sizing.fixed_amount_usd
        max_size = sizing.max_size_usd or (sizing.fixed_amount_usd * 4)

        return max(min_size, min(size, max_size))

    def get_sizing_info(self, strategy_name: str) -> dict:
        """
        Get sizing configuration for a strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            Dictionary with sizing configuration
        """
        sizing = self.config.get_effective_sizing(strategy_name)
        return {
            "method": sizing.method.value,
            "fixed_amount_usd": sizing.fixed_amount_usd,
            "kelly_fraction": sizing.kelly_fraction,
            "max_size_usd": sizing.max_size_usd,
        }
