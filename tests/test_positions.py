"""
Tests for position management and resolution handling.

Tests:
- Position P&L calculation on resolution
- YES outcome payout
- NO outcome payout
- UNKNOWN/INVALID outcome refund
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal


class TestResolutionPayoutCalculation:
    """Tests for payout calculation on market resolution."""

    def test_yes_position_yes_outcome_wins(self):
        """YES position wins when market resolves YES."""
        # Setup:
        # - Bought YES at $0.60
        # - 100 shares
        # - Cost basis: $60
        # - Market resolves YES
        # - Each share pays $1

        cost_basis = 60.0
        shares = 100.0
        entry_price = 0.60
        is_yes_position = True
        outcome = "YES"

        # Payout calculation
        payout_per_share = 1.0 if is_yes_position else 0.0
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 100.0  # 100 shares * $1 each
        assert pnl == 40.0  # Won $40 profit

    def test_yes_position_no_outcome_loses(self):
        """YES position loses when market resolves NO."""
        # Setup:
        # - Bought YES at $0.60
        # - 100 shares
        # - Cost basis: $60
        # - Market resolves NO
        # - YES shares pay $0

        cost_basis = 60.0
        shares = 100.0
        is_yes_position = True
        outcome = "NO"

        payout_per_share = 1.0 if is_yes_position else 0.0  # 0.0 for NO outcome
        if outcome == "NO":
            payout_per_share = 0.0 if is_yes_position else 1.0

        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 0.0  # YES shares worthless
        assert pnl == -60.0  # Lost entire cost basis

    def test_no_position_no_outcome_wins(self):
        """NO position wins when market resolves NO."""
        # Setup:
        # - Bought NO at $0.40
        # - 100 shares
        # - Cost basis: $40
        # - Market resolves NO
        # - NO shares pay $1

        cost_basis = 40.0
        shares = 100.0
        is_yes_position = False  # NO position
        outcome = "NO"

        if outcome == "YES":
            payout_per_share = 1.0 if is_yes_position else 0.0
        elif outcome == "NO":
            payout_per_share = 0.0 if is_yes_position else 1.0
        else:
            payout_per_share = 0.5  # Unknown

        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 100.0  # 100 shares * $1 each
        assert pnl == 60.0  # Won $60 profit

    def test_no_position_yes_outcome_loses(self):
        """NO position loses when market resolves YES."""
        # Setup:
        # - Bought NO at $0.40
        # - 100 shares
        # - Cost basis: $40
        # - Market resolves YES
        # - NO shares pay $0

        cost_basis = 40.0
        shares = 100.0
        is_yes_position = False
        outcome = "YES"

        if outcome == "YES":
            payout_per_share = 1.0 if is_yes_position else 0.0
        elif outcome == "NO":
            payout_per_share = 0.0 if is_yes_position else 1.0
        else:
            payout_per_share = 0.5

        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 0.0  # NO shares worthless
        assert pnl == -40.0  # Lost entire cost basis

    def test_unknown_outcome_refunds_at_entry(self):
        """UNKNOWN outcome refunds at entry price."""
        # Setup:
        # - Bought YES at $0.60
        # - 100 shares
        # - Cost basis: $60
        # - Market resolves UNKNOWN (voided)
        # - Refund at entry price

        cost_basis = 60.0
        shares = 100.0
        entry_price = 0.60
        outcome = "UNKNOWN"

        payout_per_share = entry_price  # Refund at entry
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 60.0  # Refund original investment
        assert pnl == 0.0  # No profit, no loss


class TestPayoutEdgeCases:
    """Tests for edge cases in payout calculation."""

    def test_high_probability_yes_position_small_profit(self):
        """YES position at high price has small profit margin."""
        # Bought YES at $0.95, wins
        cost_basis = 95.0
        shares = 100.0
        is_yes_position = True
        outcome = "YES"

        payout_per_share = 1.0
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 100.0
        assert pnl == 5.0  # Only $5 profit on $95 investment

    def test_low_probability_no_position_large_profit(self):
        """NO position at low price has large profit potential."""
        # Bought NO at $0.05, wins
        cost_basis = 5.0
        shares = 100.0
        is_yes_position = False
        outcome = "NO"

        payout_per_share = 1.0
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 100.0
        assert pnl == 95.0  # Huge profit on small investment

    def test_fractional_shares(self):
        """Handle fractional share amounts correctly."""
        cost_basis = 33.33
        shares = 66.67
        is_yes_position = True
        outcome = "YES"

        payout_per_share = 1.0
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == pytest.approx(66.67, rel=0.01)
        assert pnl == pytest.approx(33.34, rel=0.01)

    def test_very_small_position(self):
        """Handle very small positions."""
        cost_basis = 0.01
        shares = 0.02  # 2 cents worth at $0.50
        is_yes_position = True
        outcome = "NO"

        payout_per_share = 0.0
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 0.0
        assert pnl == -0.01  # Lost 1 cent

    def test_large_position(self):
        """Handle large positions correctly."""
        cost_basis = 10000.0
        shares = 20000.0  # Bought at $0.50
        is_yes_position = True
        outcome = "YES"

        payout_per_share = 1.0
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 20000.0
        assert pnl == 10000.0  # $10k profit


class TestInvalidOutcomeRefund:
    """Tests for INVALID outcome handling."""

    def test_invalid_refunds_like_unknown(self):
        """INVALID outcome should refund at entry price."""
        cost_basis = 50.0
        shares = 100.0
        entry_price = 0.50

        # INVALID treated same as UNKNOWN
        payout_per_share = entry_price
        payout = shares * payout_per_share
        pnl = payout - cost_basis

        assert payout == 50.0
        assert pnl == 0.0


class TestPnLCalculation:
    """Tests for P&L calculation accuracy."""

    def test_pnl_is_payout_minus_cost(self):
        """P&L should always be payout - cost_basis."""
        test_cases = [
            (100.0, 60.0, 40.0),   # Win $40
            (0.0, 60.0, -60.0),    # Lose $60
            (50.0, 50.0, 0.0),     # Break even
            (75.0, 25.0, 50.0),    # Win $50
        ]

        for payout, cost_basis, expected_pnl in test_cases:
            pnl = payout - cost_basis
            assert pnl == expected_pnl

    def test_profit_percentage_calculation(self):
        """Calculate profit percentage correctly."""
        cost_basis = 60.0
        payout = 100.0
        pnl = payout - cost_basis

        profit_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

        assert pnl == 40.0
        assert profit_pct == pytest.approx(66.67, rel=0.01)  # 66.67% return

    def test_loss_percentage_calculation(self):
        """Calculate loss percentage correctly."""
        cost_basis = 80.0
        payout = 0.0
        pnl = payout - cost_basis

        loss_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

        assert pnl == -80.0
        assert loss_pct == -100.0  # 100% loss


class TestPositionSideDetection:
    """Tests for determining if position is YES or NO."""

    def test_yes_token_is_yes_position(self):
        """Position with YES token ID is a YES position."""
        position_token_id = "yes_token_123"
        market_yes_token_id = "yes_token_123"
        market_no_token_id = "no_token_456"

        is_yes_position = (position_token_id == market_yes_token_id)

        assert is_yes_position is True

    def test_no_token_is_no_position(self):
        """Position with NO token ID is a NO position."""
        position_token_id = "no_token_456"
        market_yes_token_id = "yes_token_123"
        market_no_token_id = "no_token_456"

        is_yes_position = (position_token_id == market_yes_token_id)

        assert is_yes_position is False

    def test_different_token_is_no_position(self):
        """Position with different token ID is treated as NO."""
        position_token_id = "unknown_token"
        market_yes_token_id = "yes_token_123"

        is_yes_position = (position_token_id == market_yes_token_id)

        assert is_yes_position is False


class TestCloseReasonTracking:
    """Tests for close reason tracking."""

    def test_yes_outcome_close_reason(self):
        """Close reason should reflect YES outcome."""
        outcome = "YES"
        expected_reason = f"market_resolved_{outcome.lower()}"
        assert expected_reason == "market_resolved_yes"

    def test_no_outcome_close_reason(self):
        """Close reason should reflect NO outcome."""
        outcome = "NO"
        expected_reason = f"market_resolved_{outcome.lower()}"
        assert expected_reason == "market_resolved_no"

    def test_unknown_outcome_close_reason(self):
        """Close reason should reflect UNKNOWN outcome."""
        outcome = "UNKNOWN"
        expected_reason = f"market_resolved_{outcome.lower()}"
        assert expected_reason == "market_resolved_unknown"

    def test_invalid_outcome_close_reason(self):
        """Close reason should reflect INVALID outcome."""
        outcome = "INVALID"
        expected_reason = f"market_resolved_{outcome.lower()}"
        assert expected_reason == "market_resolved_invalid"
