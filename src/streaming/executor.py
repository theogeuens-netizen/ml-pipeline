"""
Streaming executor.

Executes streaming signals with paper/live mode support.
Reuses existing order client and position models.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.db.database import get_session
from .config import StreamingConfig
from .safety import StreamingSafetyChecker
from .signals import StreamingSignal
from .state import StreamingStateManager

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of signal execution."""

    success: bool
    reason: str = ""
    position_id: Optional[int] = None
    executed_price: Optional[float] = None
    executed_shares: Optional[float] = None
    executed_usd: Optional[float] = None


class StreamingExecutor:
    """
    Execute streaming signals.

    Supports both paper and live modes with seamless switching.
    Reuses existing order client and position models from the
    polling executor infrastructure.
    """

    def __init__(self, config: StreamingConfig):
        """
        Initialize executor.

        Args:
            config: Streaming configuration
        """
        self.config = config
        self.is_paper = not config.live
        self._order_client = None
        self.safety_checker = StreamingSafetyChecker(config)

        mode = "PAPER" if self.is_paper else "LIVE"
        logger.info(f"StreamingExecutor initialized in {mode} mode")

    @property
    def order_client(self):
        """Lazy initialization of order client."""
        if self._order_client is None:
            from src.executor.clients.order_client import get_order_client
            self._order_client = get_order_client()
        return self._order_client

    def execute(
        self,
        signal: StreamingSignal,
        state: StreamingStateManager,
    ) -> ExecutionResult:
        """
        Execute a streaming signal.

        Flow:
        1. Run safety checks
        2. Create audit records
        3. Execute order (paper or live)
        4. Update state
        5. Send alerts

        Args:
            signal: Signal to execute
            state: State manager for updates

        Returns:
            ExecutionResult with outcome details
        """
        with get_session() as db:
            # Run safety checks
            check = self.safety_checker.check_all(
                signal,
                self.order_client,
                db,
                self.is_paper,
            )

            if not check.passed:
                self._log_decision(signal, None, check.reason, db)
                return ExecutionResult(success=False, reason=check.reason)

            try:
                # Execute based on mode
                if self.is_paper:
                    result = self._execute_paper(signal, db)
                else:
                    result = self._execute_live(signal, db)

                if result.success:
                    # Update state
                    state.set_cooldown(signal.strategy_name, signal.market_id)
                    state.add_position(signal.strategy_name, signal.market_id)
                    state.increment_stat("signals_executed")

                    # Send alert
                    self._send_alert(signal, result)

                    logger.info(
                        f"Executed: {signal.token_side} @ ${result.executed_price:.4f}, "
                        f"position={result.position_id}"
                    )
                else:
                    logger.warning(f"Execution failed: {result.reason}")

                return result

            except Exception as e:
                logger.error(f"Execution error: {e}", exc_info=True)
                self._log_decision(signal, None, str(e), db)
                return ExecutionResult(success=False, reason=str(e))

    def _execute_paper(
        self,
        signal: StreamingSignal,
        db: Session,
    ) -> ExecutionResult:
        """
        Execute paper trade.

        Uses the existing PaperExecutor infrastructure.
        """
        from src.executor.execution.paper import PaperExecutor, OrderbookState
        from src.executor.models import Signal as SignalModel, SignalStatus

        # Create signal model for paper executor
        signal_model = SignalModel(
            strategy_name=signal.strategy_name,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            reason=signal.reason,
            edge=signal.edge,
            confidence=signal.confidence,
            price_at_signal=signal.price_at_signal,
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            suggested_size_usd=signal.size_usd,
            status=SignalStatus.APPROVED.value,
            created_at=signal.created_at,
        )
        db.add(signal_model)
        db.flush()

        # Build orderbook state
        orderbook = OrderbookState(
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            mid_price=signal.mid_price,
            bid_depth_10=1000.0,  # Reasonable default
            ask_depth_10=1000.0,
            spread=signal.spread,
        )

        # Execute via paper executor
        paper_executor = PaperExecutor()
        result = paper_executor.execute_signal(signal_model, orderbook, db=db)

        # Log decision
        self._log_decision(
            signal,
            result,
            result.message if not result.success else None,
            db,
        )

        if result.success:
            return ExecutionResult(
                success=True,
                position_id=getattr(result, "position_id", None),
                executed_price=result.executed_price,
                executed_shares=result.executed_shares,
                executed_usd=getattr(result, "executed_usd", signal.size_usd),
            )
        else:
            return ExecutionResult(success=False, reason=result.message)

    def _execute_live(
        self,
        signal: StreamingSignal,
        db: Session,
    ) -> ExecutionResult:
        """
        Execute live trade.

        Uses the existing LiveExecutor infrastructure which handles:
        - Fresh orderbook fetch for safety
        - Order placement with retries
        - Position tracking
        """
        from src.executor.execution.live import LiveExecutor
        from src.executor.models import Signal as SignalModel, SignalStatus

        # Create signal model for live executor
        signal_model = SignalModel(
            strategy_name=signal.strategy_name,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            reason=signal.reason,
            edge=signal.edge,
            confidence=signal.confidence,
            price_at_signal=signal.price_at_signal,
            best_bid=signal.best_bid,
            best_ask=signal.best_ask,
            suggested_size_usd=signal.size_usd,
            status=SignalStatus.APPROVED.value,
            created_at=signal.created_at,
        )
        db.add(signal_model)
        db.flush()

        # Execute via live executor
        # Note: LiveExecutor fetches fresh orderbook internally for safety
        live_executor = LiveExecutor(self.order_client)
        result = live_executor.execute_signal(signal_model, db=db)

        # Log decision
        self._log_decision(
            signal,
            result,
            result.message if not result.success else None,
            db,
        )

        if result.success:
            return ExecutionResult(
                success=True,
                position_id=getattr(result, "position_id", None),
                executed_price=result.executed_price,
                executed_shares=result.executed_shares,
                executed_usd=getattr(result, "executed_usd", signal.size_usd),
            )
        else:
            return ExecutionResult(success=False, reason=result.message)

    def _log_decision(
        self,
        signal: StreamingSignal,
        result: Optional[ExecutionResult],
        rejection_reason: Optional[str],
        db: Session,
    ):
        """Log trade decision for audit trail."""
        from src.executor.models import TradeDecision

        executed = result is not None and result.success if result else False

        decision = TradeDecision(
            strategy_name=signal.strategy_name,
            strategy_sha="streaming-v1",
            market_id=signal.market_id,
            condition_id=signal.condition_id,
            market_snapshot={
                "imbalance": signal.imbalance,
                "spread": signal.spread,
                "mid_price": signal.mid_price,
                "hours_to_close": signal.hours_to_close,
            },
            decision_inputs={
                "imbalance": signal.imbalance,
                "hours_to_close": signal.hours_to_close,
                "token_side": signal.token_side,
                "signal_age_seconds": signal.age_seconds,
                "is_paper": self.is_paper,
            },
            signal_side=signal.side,
            signal_reason=signal.reason,
            signal_edge=signal.edge,
            signal_size_usd=signal.size_usd,
            executed=executed,
            rejected_reason=rejection_reason,
            execution_price=result.executed_price if executed and result else None,
            position_id=result.position_id if executed and result else None,
        )
        db.add(decision)

        try:
            db.commit()
        except Exception as e:
            logger.error(f"Failed to log decision: {e}")
            db.rollback()

    def _send_alert(self, signal: StreamingSignal, result: ExecutionResult):
        """Send Telegram alert for executed trade."""
        try:
            from src.alerts.telegram import alert_trade

            alert_trade(
                strategy=signal.strategy_name,
                side=signal.side,
                market_title=signal.market_question or f"Market {signal.market_id}",
                market_id=signal.market_id,
                token_side=signal.token_side,
                price=result.executed_price,
                size=signal.size_usd,
                edge=signal.edge,
                expected_win_rate=0.9,  # Based on paper backtest
                order_type="market",
                best_bid=signal.best_bid,
                best_ask=signal.best_ask,
                hours_to_close=signal.hours_to_close,
                is_live=not self.is_paper,
            )
        except Exception as e:
            logger.warning(f"Failed to send alert: {e}")
