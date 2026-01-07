"""
Main Executor.

Orchestrates order execution across paper and live modes.
Provides a unified interface for the runner and API.
"""

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.executor.config import ExecutorConfig, TradingMode, get_config
from src.executor.models import Signal, Position
from .paper import PaperExecutor, OrderbookState
from .live import LiveExecutor, LiveOrderbookState
from .order_types import OrderResult, OrderType

logger = logging.getLogger(__name__)


class Executor:
    """
    Main executor that manages paper and live trading.

    Provides a unified interface regardless of mode.
    Mode can be switched at runtime via configuration.
    """

    def __init__(self, config: Optional[ExecutorConfig] = None):
        """
        Initialize the executor.

        Args:
            config: Optional executor configuration
        """
        self.config = config or get_config()

        # Initialize executors
        self._paper_executor: Optional[PaperExecutor] = None
        self._live_executor: Optional[LiveExecutor] = None

    @property
    def mode(self) -> TradingMode:
        """Get current trading mode."""
        return self.config.mode

    @property
    def paper_executor(self) -> PaperExecutor:
        """Lazy initialization of paper executor."""
        if self._paper_executor is None:
            from src.config.settings import settings
            starting_balance = getattr(settings, 'paper_starting_balance', 10000.0)
            self._paper_executor = PaperExecutor(starting_balance=starting_balance)
        return self._paper_executor

    @property
    def live_executor(self) -> LiveExecutor:
        """Lazy initialization of live executor."""
        if self._live_executor is None:
            self._live_executor = LiveExecutor()
        return self._live_executor

    def get_balance(self) -> float:
        """
        Get current balance based on mode.

        Returns:
            Available balance in USD
        """
        if self.config.mode == TradingMode.LIVE:
            return self.live_executor.get_balance()
        else:
            return self.paper_executor.get_balance()

    def get_total_value(self) -> float:
        """
        Get total portfolio value (balance + positions).

        Returns:
            Total value in USD
        """
        if self.config.mode == TradingMode.LIVE:
            stats = self.live_executor.get_stats()
            return stats.get("total_value", 0)
        else:
            return self.paper_executor.get_total_value()

    def execute_signal(
        self,
        signal: Signal,
        orderbook: Optional[OrderbookState] = None,
        db: Optional[Session] = None,
    ) -> OrderResult:
        """
        Execute a trading signal.

        Routes to paper or live executor based on mode.

        Args:
            signal: Trading signal to execute
            orderbook: Optional orderbook state
            db: Optional database session

        Returns:
            OrderResult with execution details
        """
        # Get execution config for this strategy
        execution = self.config.get_effective_execution(signal.strategy_name)
        order_type = execution.default_order_type
        limit_offset = execution.limit_offset_bps

        if self.config.mode == TradingMode.LIVE:
            # Convert to LiveOrderbookState if needed
            live_ob = None
            if orderbook:
                live_ob = LiveOrderbookState(
                    best_bid=orderbook.best_bid,
                    best_ask=orderbook.best_ask,
                    mid_price=orderbook.mid_price,
                    bid_depth_10=orderbook.bid_depth_10,
                    ask_depth_10=orderbook.ask_depth_10,
                    spread=orderbook.spread,
                )

            return self.live_executor.execute_signal(
                signal=signal,
                orderbook=live_ob,
                order_type=order_type,
                limit_offset_bps=limit_offset,
                db=db,
            )
        else:
            return self.paper_executor.execute_signal(
                signal=signal,
                orderbook=orderbook,
                order_type=order_type,
                limit_offset_bps=limit_offset,
                db=db,
            )

    def close_position(
        self,
        position_id: int,
        exit_price: Optional[float] = None,
        reason: str = "manual",
    ) -> OrderResult:
        """
        Close a position.

        Args:
            position_id: Position ID to close
            exit_price: Price to close at (paper) or None (live fetches price)
            reason: Reason for closing

        Returns:
            OrderResult with details
        """
        if self.config.mode == TradingMode.LIVE:
            execution = self.config.execution
            return self.live_executor.close_position(
                position_id=position_id,
                order_type=execution.default_order_type,
                limit_offset_bps=execution.limit_offset_bps,
            )
        else:
            if exit_price is None:
                # Need to get current price for paper mode
                logger.warning("No exit_price provided for paper close, using 0.5")
                exit_price = 0.5
            return self.paper_executor.close_position(
                position_id=position_id,
                exit_price=exit_price,
                reason=reason,
            )

    def get_open_positions(self) -> list[Position]:
        """Get all open positions for current mode."""
        if self.config.mode == TradingMode.LIVE:
            return self.live_executor.get_open_positions()
        else:
            return self.paper_executor.get_open_positions()

    def update_position_prices(self, price_updates: dict[int, float]):
        """
        Update current prices for open positions.

        Args:
            price_updates: Dict of market_id -> current_price
        """
        # Update paper positions regardless of mode (for tracking)
        self.paper_executor.update_position_prices(price_updates)

        # Live positions would need to fetch real prices from orderbook
        # For now, also update live position records with the provided prices
        if self.config.mode == TradingMode.LIVE:
            from src.db.database import get_session
            from src.executor.models import PositionStatus

            with get_session() as db:
                positions = db.query(Position).filter(
                    Position.is_paper == False,
                    Position.status == PositionStatus.OPEN.value,
                ).all()

                for position in positions:
                    if position.market_id in price_updates:
                        current_price = price_updates[position.market_id]
                        position.current_price = current_price
                        position.current_value = float(position.size_shares) * current_price
                        position.unrealized_pnl = position.current_value - float(position.cost_basis)
                        position.unrealized_pnl_pct = (
                            position.unrealized_pnl / float(position.cost_basis)
                            if position.cost_basis else 0
                        )

                db.commit()

    def get_stats(self) -> dict[str, Any]:
        """Get trading statistics for current mode."""
        if self.config.mode == TradingMode.LIVE:
            return self.live_executor.get_stats()
        else:
            return self.paper_executor.get_stats()

    def get_all_stats(self) -> dict[str, Any]:
        """Get statistics for both paper and live trading."""
        return {
            "mode": self.config.mode.value,
            "paper": self.paper_executor.get_stats(),
            "live": self.live_executor.get_stats() if self._live_executor else None,
        }

    def cancel_open_orders(self) -> int:
        """Cancel all open orders (live mode only)."""
        if self.config.mode == TradingMode.LIVE:
            return self.live_executor.cancel_open_orders()
        else:
            logger.info("Cancel orders called in paper mode - no action")
            return 0

    def reset_paper(self, starting_balance: Optional[float] = None):
        """
        Reset paper trading state.

        Args:
            starting_balance: New starting balance
        """
        self.paper_executor.reset(starting_balance)

    def reload_config(self, config: ExecutorConfig):
        """
        Reload configuration.

        Args:
            config: New configuration
        """
        old_mode = self.config.mode
        self.config = config

        if old_mode != config.mode:
            logger.info(f"Trading mode changed: {old_mode.value} -> {config.mode.value}")

    def get_status(self) -> dict[str, Any]:
        """Get executor status summary."""
        return {
            "mode": self.config.mode.value,
            "balance": self.get_balance(),
            "total_value": self.get_total_value(),
            "open_positions": len(self.get_open_positions()),
            "paper_initialized": self._paper_executor is not None,
            "live_initialized": self._live_executor is not None,
        }


# Singleton instance
_executor_instance: Optional[Executor] = None


def get_executor(config: Optional[ExecutorConfig] = None) -> Executor:
    """
    Get or create the singleton executor instance.

    Args:
        config: Optional configuration (only used on first call)

    Returns:
        Executor instance
    """
    global _executor_instance

    if _executor_instance is None:
        _executor_instance = Executor(config)

    return _executor_instance


def reset_executor():
    """Reset the singleton executor instance."""
    global _executor_instance
    _executor_instance = None
