"""
Tests for risk management.

Tests:
- Position count limits
- Exposure limits
- Drawdown checks
- Signal approval logic
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

from src.executor.portfolio.risk import RiskManager, RiskCheckResult


@dataclass
class MockSignal:
    """Mock signal for testing."""
    market_id: int = 1
    token_id: str = "test_token"
    side: str = "BUY"
    edge: float = 0.05
    confidence: float = 0.6
    size_usd: float = 25.0
    strategy_name: str = "test_strategy"


@dataclass
class MockRiskConfig:
    """Mock risk config for testing."""
    max_position_usd: float = 100.0
    max_total_exposure_usd: float = 10000.0
    max_positions: int = 500
    max_drawdown_pct: float = 0.20


@dataclass
class MockConfig:
    """Mock executor config for testing."""
    risk: MockRiskConfig = None

    def __post_init__(self):
        if self.risk is None:
            self.risk = MockRiskConfig()


class TestRiskCheckResult:
    """Tests for RiskCheckResult dataclass."""

    def test_approved_result(self):
        """Approved result should have approved=True."""
        result = RiskCheckResult(
            approved=True,
            available_capital=100.0,
        )
        assert result.approved is True
        assert result.reason is None

    def test_rejected_result(self):
        """Rejected result should have approved=False with reason."""
        result = RiskCheckResult(
            approved=False,
            reason="Max positions reached",
        )
        assert result.approved is False
        assert result.reason == "Max positions reached"


class TestPositionCountLimit:
    """Tests for position count limit enforcement."""

    def test_under_limit_approved(self):
        """Signal should be approved when under position limit."""
        with patch('src.executor.portfolio.risk.get_config') as mock_config:
            mock_config.return_value = MockConfig()

            manager = RiskManager(config=MockConfig())

            # Mock position manager to return low count
            manager.position_manager.get_position_count = Mock(return_value=10)
            manager.position_manager.get_position_by_market = Mock(return_value=None)
            manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
            manager._check_drawdown = Mock(return_value=True)

            signal = MockSignal()
            result = manager.check_signal(signal, balance=1000.0)

            assert result.approved is True

    def test_at_limit_rejected(self):
        """Signal should be rejected when at position limit."""
        config = MockConfig(risk=MockRiskConfig(max_positions=100))
        manager = RiskManager(config=config)

        # Mock position manager to return count at limit
        manager.position_manager.get_position_count = Mock(return_value=100)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is False
        assert "Max positions" in result.reason

    def test_over_limit_rejected(self):
        """Signal should be rejected when over position limit."""
        config = MockConfig(risk=MockRiskConfig(max_positions=50))
        manager = RiskManager(config=config)

        # Mock position manager to return count over limit
        manager.position_manager.get_position_count = Mock(return_value=55)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is False
        assert "Max positions" in result.reason


class TestDuplicatePositionCheck:
    """Tests for duplicate position detection."""

    def test_new_market_approved(self):
        """Signal for new market should pass duplicate check."""
        config = MockConfig()
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal(market_id=123)
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is True

    def test_existing_position_rejected(self):
        """Signal for market with existing position should be rejected."""
        config = MockConfig()
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        # Return a mock existing position
        manager.position_manager.get_position_by_market = Mock(return_value=Mock())

        signal = MockSignal(market_id=123)
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is False
        assert "Already have position" in result.reason


class TestExposureLimit:
    """Tests for total exposure limit enforcement."""

    def test_under_exposure_limit_approved(self):
        """Signal should be approved when under exposure limit."""
        config = MockConfig(risk=MockRiskConfig(max_total_exposure_usd=10000.0))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=5000.0)
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is True
        assert result.available_capital > 0

    def test_at_exposure_limit_rejected(self):
        """Signal should be rejected when at exposure limit."""
        config = MockConfig(risk=MockRiskConfig(max_total_exposure_usd=10000.0))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=10000.0)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is False
        assert "Max exposure" in result.reason


class TestBalanceCheck:
    """Tests for balance validation."""

    def test_positive_balance_approved(self):
        """Signal should be approved with positive balance."""
        config = MockConfig()
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=100.0)

        assert result.approved is True

    def test_zero_balance_rejected(self):
        """Signal should be rejected with zero balance."""
        config = MockConfig()
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=0.0)

        assert result.approved is False
        assert "Insufficient balance" in result.reason

    def test_negative_balance_rejected(self):
        """Signal should be rejected with negative balance."""
        config = MockConfig()
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=-50.0)

        assert result.approved is False
        assert "Insufficient balance" in result.reason


class TestDrawdownCheck:
    """Tests for drawdown limit enforcement."""

    def test_within_drawdown_approved(self):
        """Signal should be approved when within drawdown limit."""
        config = MockConfig(risk=MockRiskConfig(max_drawdown_pct=0.20))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is True

    def test_exceeded_drawdown_rejected(self):
        """Signal should be rejected when drawdown exceeded."""
        config = MockConfig(risk=MockRiskConfig(max_drawdown_pct=0.20))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=False)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is False
        assert "drawdown" in result.reason.lower()


class TestAvailableCapitalCalculation:
    """Tests for available capital calculation."""

    def test_respects_max_position_size(self):
        """Available capital should respect max position size."""
        config = MockConfig(risk=MockRiskConfig(
            max_position_usd=100.0,
            max_total_exposure_usd=10000.0,
        ))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=5000.0)  # Large balance

        assert result.approved is True
        # Should be capped at max_position_usd
        assert result.available_capital <= 100.0

    def test_respects_available_exposure(self):
        """Available capital should respect remaining exposure limit."""
        config = MockConfig(risk=MockRiskConfig(
            max_position_usd=1000.0,
            max_total_exposure_usd=5000.0,
        ))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=4900.0)  # $100 remaining
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=5000.0)

        assert result.approved is True
        # Should be capped at remaining exposure (5000 - 4900 = 100)
        assert result.available_capital <= 100.0

    def test_respects_balance(self):
        """Available capital should respect actual balance."""
        config = MockConfig(risk=MockRiskConfig(
            max_position_usd=1000.0,
            max_total_exposure_usd=10000.0,
        ))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        signal = MockSignal()
        result = manager.check_signal(signal, balance=50.0)  # Small balance

        assert result.approved is True
        # Should be capped at balance
        assert result.available_capital <= 50.0


class TestSuggestedSizeAdjustment:
    """Tests for signal size adjustment."""

    def test_reduces_large_signal_size(self):
        """Signal size should be reduced if larger than available capital."""
        config = MockConfig(risk=MockRiskConfig(max_position_usd=50.0))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        # Signal requests $100 but only $50 available
        signal = MockSignal(size_usd=100.0)
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is True
        assert result.suggested_size <= 50.0

    def test_keeps_small_signal_size(self):
        """Small signal size should be preserved."""
        config = MockConfig(risk=MockRiskConfig(max_position_usd=100.0))
        manager = RiskManager(config=config)

        manager.position_manager.get_position_count = Mock(return_value=10)
        manager.position_manager.get_position_by_market = Mock(return_value=None)
        manager.position_manager.get_total_exposure = Mock(return_value=1000.0)
        manager._check_drawdown = Mock(return_value=True)

        # Signal requests $25 which is under limit
        signal = MockSignal(size_usd=25.0)
        result = manager.check_signal(signal, balance=1000.0)

        assert result.approved is True
        assert result.suggested_size == 25.0
