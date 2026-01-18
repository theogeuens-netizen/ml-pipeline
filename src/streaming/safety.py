"""
Safety checks for streaming signals.

Stricter than polling executor because we're reacting to real-time events.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from .config import StreamingConfig
from .signals import StreamingSignal

if TYPE_CHECKING:
    from src.executor.clients.order_client import PolymarketOrderClient

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheckResult:
    """Result of safety validation."""

    passed: bool
    reason: str = ""

    @staticmethod
    def success() -> "SafetyCheckResult":
        """Create a successful result."""
        return SafetyCheckResult(passed=True)

    @staticmethod
    def fail(reason: str) -> "SafetyCheckResult":
        """Create a failed result."""
        return SafetyCheckResult(passed=False, reason=reason)


class StreamingSafetyChecker:
    """
    Safety checks for streaming signals.

    Stricter than polling executor because:
    - We're reacting to real-time events
    - Higher risk of duplicates if not careful
    - Need fresh price validation before execution
    """

    def __init__(self, config: StreamingConfig):
        """
        Initialize safety checker.

        Args:
            config: Streaming configuration
        """
        self.config = config

    def check_all(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """
        Run all safety checks.

        Returns first failure or success.

        Args:
            signal: Signal to validate
            order_client: Order client for API calls
            db: Database session
            is_paper: Whether this is paper trading

        Returns:
            SafetyCheckResult with pass/fail and reason
        """
        checks = [
            ("signal_age", self._check_signal_age),
            ("spread", self._check_spread),
            ("duplicate_position", self._check_duplicate_position),
            ("recent_orders", self._check_recent_orders),
        ]

        # Only check price deviation for live trading
        # (paper trades don't need fresh orderbook validation)
        if not is_paper:
            checks.insert(1, ("price_deviation", self._check_price_deviation))
            checks.append(("fee_rate", self._check_fee_rate))

        for check_name, check_fn in checks:
            try:
                result = check_fn(signal, order_client, db, is_paper)
                if not result.passed:
                    logger.debug(f"Safety check '{check_name}' failed: {result.reason}")
                    return result
            except Exception as e:
                logger.warning(f"Safety check '{check_name}' error: {e}")
                # Fail closed on errors for safety
                return SafetyCheckResult.fail(f"Safety check error: {e}")

        return SafetyCheckResult.success()

    def _check_signal_age(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """
        Signal must be fresh (< 5 seconds for streaming).

        This is much stricter than polling's 120s limit because
        streaming signals should be acted on immediately.
        """
        age = signal.age_seconds
        max_age = self.config.max_signal_age_seconds

        if age > max_age:
            return SafetyCheckResult.fail(
                f"Signal too old: {age:.1f}s > {max_age}s"
            )

        return SafetyCheckResult.success()

    def _check_price_deviation(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """
        Fresh orderbook price must match signal price within tolerance.

        Fetches fresh orderbook from CLOB API and compares to signal price.
        This catches cases where the orderbook changed between signal
        generation and execution attempt.
        """
        try:
            orderbook = order_client.get_orderbook(signal.token_id)
            best_bid, best_ask = order_client.get_best_bid_ask(orderbook)

            if best_bid is None or best_ask is None:
                return SafetyCheckResult.fail("No liquidity in orderbook")

            live_mid = (best_bid + best_ask) / 2
            signal_mid = signal.mid_price

            if signal_mid <= 0:
                return SafetyCheckResult.fail("Invalid signal mid price")

            deviation = abs(live_mid - signal_mid) / signal_mid
            max_dev = self.config.max_price_deviation

            if deviation > max_dev:
                return SafetyCheckResult.fail(
                    f"Price moved: signal={signal_mid:.2%}, live={live_mid:.2%}, "
                    f"deviation={deviation:.1%} > max {max_dev:.0%}"
                )

            return SafetyCheckResult.success()

        except Exception as e:
            return SafetyCheckResult.fail(f"Orderbook fetch failed: {e}")

    def _check_spread(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """Spread must be within tolerance."""
        if signal.spread > self.config.max_spread:
            return SafetyCheckResult.fail(
                f"Spread too high: {signal.spread:.1%} > {self.config.max_spread:.0%}"
            )
        return SafetyCheckResult.success()

    def _check_fee_rate(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """Fee rate must be acceptable."""
        try:
            fee_bps = order_client.get_fee_rate_bps(signal.token_id)
            max_fee = self.config.max_fee_rate_bps

            if fee_bps > max_fee:
                return SafetyCheckResult.fail(
                    f"Fee too high: {fee_bps} bps > {max_fee} bps"
                )

            return SafetyCheckResult.success()

        except Exception as e:
            # Log warning but allow trade (fees are usually 0 on Polymarket)
            logger.warning(f"Could not check fee rate: {e}")
            return SafetyCheckResult.success()

    def _check_duplicate_position(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """Check for existing position on this token FOR THIS STRATEGY.

        Each strategy can trade independently - only block if THIS strategy
        already has an open position on the token.
        """
        from src.executor.models import Position, PositionStatus

        existing = (
            db.query(Position)
            .filter(
                Position.token_id == signal.token_id,
                Position.strategy_name == signal.strategy_name,
                Position.is_paper == is_paper,
                Position.status == PositionStatus.OPEN.value,
            )
            .first()
        )

        if existing:
            return SafetyCheckResult.fail(
                f"Position already exists for {signal.strategy_name}: ID {existing.id}"
            )

        return SafetyCheckResult.success()

    def _check_recent_orders(
        self,
        signal: StreamingSignal,
        order_client: "PolymarketOrderClient",
        db: Session,
        is_paper: bool,
    ) -> SafetyCheckResult:
        """
        Check for recent orders on this token.

        This catches untracked fills that might not have created positions yet.
        Note: ExecutorOrder doesn't have strategy_name, so we check by token_id only.
        This is less precise but still prevents rapid duplicate orders.
        """
        from src.executor.models import ExecutorOrder

        two_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=2)

        recent = (
            db.query(ExecutorOrder)
            .filter(
                ExecutorOrder.token_id == signal.token_id,
                ExecutorOrder.is_paper == is_paper,
                ExecutorOrder.created_at >= two_mins_ago,
            )
            .first()
        )

        if recent:
            return SafetyCheckResult.fail(
                f"Recent order exists: ID {recent.id}"
            )

        return SafetyCheckResult.success()
