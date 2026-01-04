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
from strategies.base import Signal

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
        strategy_capital: Optional[float] = None,
    ) -> float:
        """
        Calculate position size for a signal.

        Args:
            signal: Trading signal
            available_capital: Max available capital for this trade (after position limits)
            sizing_config: Optional sizing config override
            strategy_capital: Total capital allocated to this strategy (for Kelly sizing)

        Returns:
            Position size in USD
        """
        # If strategy already provided a fixed size, respect it (skip Kelly/other sizing)
        if signal.size_usd and signal.size_usd > 0:
            size = min(signal.size_usd, available_capital)
            size = max(size, 1.0)  # At least $1
            logger.debug(
                f"Using strategy-provided size: ${size:.2f} "
                f"(requested ${signal.size_usd:.2f}, available ${available_capital:.2f})"
            )
            return round(size, 2)

        sizing = sizing_config or self.config.get_effective_sizing(signal.strategy_name)

        # For Kelly sizing, use strategy_capital as the base for fraction calculation
        # Default to 400 if not provided (standard per-strategy allocation)
        kelly_capital = strategy_capital if strategy_capital is not None else 400.0

        # Check for per-strategy size_pct in decision_inputs (takes priority)
        size_pct = None
        if hasattr(signal, 'decision_inputs') and signal.decision_inputs:
            size_pct = signal.decision_inputs.get('size_pct')

        if size_pct is not None and size_pct > 0:
            # Use per-strategy fixed percentage sizing
            size = kelly_capital * size_pct
            logger.debug(
                f"Using per-strategy sizing: size_pct={size_pct:.1%}, "
                f"capital=${kelly_capital}, size=${size:.2f}"
            )
        # Calculate base size based on method from config
        elif sizing.method == SizingMethod.FIXED:
            size = self._fixed_size(sizing)
        elif sizing.method == SizingMethod.KELLY:
            # Kelly uses full strategy capital for fraction calculation
            size = self._kelly_size(signal, sizing, strategy_capital=kelly_capital)
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

    def _kelly_size(
        self,
        signal: Signal,
        sizing: SizingConfig,
        strategy_capital: float = 400.0,
    ) -> float:
        """
        Calculate position size using Kelly criterion.

        Kelly formula: f* = (p * b - q) / b
        Where:
        - f* = fraction of capital to bet
        - p = probability of winning (our estimate)
        - q = 1 - p (probability of losing)
        - b = odds = profit_if_win / loss_if_lose

        For prediction markets buying at price `c`:
        - profit_if_win = 1 - c (we get $1, paid $c)
        - loss_if_lose = c (we lose our stake)
        - b = (1 - c) / c

        We use fractional Kelly (kelly_fraction) to be more conservative.
        """
        # Use confidence as probability estimate (expected win rate)
        p = signal.confidence if signal.confidence else 0.5
        q = 1 - p

        # Edge is the expected return per dollar
        edge = signal.edge if signal.edge else 0.0

        if edge <= 0:
            # No edge, use minimum
            logger.debug(f"Kelly: no edge ({edge}), using minimum")
            return sizing.fixed_amount_usd

        # Get execution price from decision_inputs if available
        # For NO bets, this is the NO ask price (what we pay)
        price = None
        if hasattr(signal, 'decision_inputs') and signal.decision_inputs:
            price = signal.decision_inputs.get('no_ask')

        if price is None:
            # Fallback: derive price from signal's price_at_signal
            # For NO bets, NO price ≈ 1 - YES price
            if signal.price_at_signal:
                price = 1 - signal.price_at_signal
            else:
                price = 0.5  # Default to 50%

        if price <= 0 or price >= 1:
            logger.debug(f"Kelly: invalid price ({price}), using minimum")
            return sizing.fixed_amount_usd

        # Calculate odds: b = profit / loss = (1 - price) / price
        b = (1 - price) / price

        # Kelly formula: f* = (p * b - q) / b
        # Equivalent to: f* = p - q/b = p - q*price/(1-price)
        kelly = (p * b - q) / b if b > 0 else 0

        if kelly <= 0:
            # Kelly says don't bet (edge not sufficient for win rate)
            logger.debug(f"Kelly: negative ({kelly:.4f}), using minimum. p={p}, b={b}")
            return sizing.fixed_amount_usd

        # Apply fractional Kelly for safety
        fractional_kelly = kelly * sizing.kelly_fraction

        # Convert to position size using strategy capital
        size = strategy_capital * fractional_kelly

        logger.debug(
            f"Kelly sizing: p={p:.2f}, price={price:.3f}, b={b:.2f}, "
            f"full_kelly={kelly:.2%}, frac_kelly={fractional_kelly:.2%}, "
            f"capital=${strategy_capital}, size=${size:.2f}"
        )

        # Apply min/max bounds
        min_size = sizing.fixed_amount_usd
        max_size = sizing.max_size_usd or 100.0

        return max(min_size, min(size, max_size))

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
        # Default volatility (if not available in signal decision_inputs)
        volatility = getattr(signal, 'decision_inputs', {}).get("volatility", 0.1)

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
