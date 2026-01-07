from unittest.mock import MagicMock

from src.executor.engine.runner import ExecutorRunner
from src.executor.portfolio.risk import RiskCheckResult
from strategies.base import Signal, Side
from src.executor.config import get_config


def test_dedupes_signals_per_strategy_market():
    """Runner should only approve one signal per strategy/market in a batch."""
    runner = ExecutorRunner(config=get_config())

    # Stub dependencies
    runner.risk_manager = MagicMock()
    runner.position_sizer = MagicMock()
    runner.position_sizer.calculate_size.return_value = 25.0
    runner.risk_manager.check_signal.return_value = RiskCheckResult(
        approved=True,
        available_capital=100.0,
    )

    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()

    signals = [
        Signal(
            token_id="yes",
            side=Side.BUY,
            reason="alpha",
            market_id=1,
            price_at_signal=0.5,
            strategy_name="alpha",
        ),
        Signal(
            token_id="yes",
            side=Side.BUY,
            reason="duplicate",
            market_id=1,
            price_at_signal=0.5,
            strategy_name="alpha",
        ),
    ]

    approved = runner._process_signals(signals, balance=1000.0, db=db)

    assert len(approved) == 1
    # Second signal should have been marked rejected
    rejected_signal = signals[1]
    assert rejected_signal.strategy_name == "alpha"
