"""
Tests for order type calculations.

Tests critical financial math:
- Share calculations from USD
- USD calculations from shares
- Order price calculations for market/limit/spread
"""

import pytest
from src.executor.execution.order_types import (
    calculate_shares_from_usd,
    calculate_usd_from_shares,
    OrderType,
    OrderRequest,
    MarketOrder,
    LimitOrder,
    SpreadOrder,
    create_order,
)


class TestCalculateSharesFromUsd:
    """Tests for calculate_shares_from_usd()."""

    def test_normal_case(self):
        """Standard calculation: $100 at $0.50 = 200 shares."""
        assert calculate_shares_from_usd(100, 0.50) == 200.0

    def test_price_near_one(self):
        """High probability market: $100 at $0.99 = ~101 shares."""
        result = calculate_shares_from_usd(100, 0.99)
        assert result == pytest.approx(101.01, rel=0.01)

    def test_price_near_zero(self):
        """Low probability market: $100 at $0.01 = 10000 shares."""
        result = calculate_shares_from_usd(100, 0.01)
        assert result == pytest.approx(10000.0, rel=0.01)

    def test_small_size(self):
        """Small position: $1 at $0.50 = 2 shares."""
        assert calculate_shares_from_usd(1, 0.50) == 2.0

    def test_large_size(self):
        """Large position: $10000 at $0.25 = 40000 shares."""
        assert calculate_shares_from_usd(10000, 0.25) == 40000.0

    def test_rounding(self):
        """Result should be rounded to 2 decimal places."""
        # $100 at $0.33 = 303.0303... should round to 303.03
        result = calculate_shares_from_usd(100, 0.33)
        assert result == pytest.approx(303.03, rel=0.01)

    def test_zero_price_raises(self):
        """Price of 0 should raise ValueError."""
        with pytest.raises(ValueError):
            calculate_shares_from_usd(100, 0)

    def test_negative_price_raises(self):
        """Negative price should raise ValueError."""
        with pytest.raises(ValueError):
            calculate_shares_from_usd(100, -0.5)


class TestCalculateUsdFromShares:
    """Tests for calculate_usd_from_shares()."""

    def test_normal_case(self):
        """Standard calculation: 200 shares at $0.50 = $100."""
        assert calculate_usd_from_shares(200, 0.50) == 100.0

    def test_price_near_one(self):
        """High price: 100 shares at $0.99 = $99."""
        assert calculate_usd_from_shares(100, 0.99) == 99.0

    def test_price_near_zero(self):
        """Low price: 1000 shares at $0.01 = $10."""
        assert calculate_usd_from_shares(1000, 0.01) == 10.0

    def test_rounding(self):
        """Result should be rounded to 2 decimal places."""
        # 100 shares at $0.333 = $33.3 -> $33.3
        result = calculate_usd_from_shares(100, 0.333)
        assert result == pytest.approx(33.30, rel=0.01)

    def test_zero_shares(self):
        """Zero shares = $0."""
        assert calculate_usd_from_shares(0, 0.50) == 0.0


class TestMarketOrder:
    """Tests for MarketOrder price calculation."""

    def test_buy_uses_ask(self):
        """Buy market order should use best ask price."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.MARKET,
        )
        order = MarketOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        assert price == 0.52

    def test_sell_uses_bid(self):
        """Sell market order should use best bid price."""
        request = OrderRequest(
            token_id="test",
            side="SELL",
            size_usd=100,
            order_type=OrderType.MARKET,
        )
        order = MarketOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        assert price == 0.48

    def test_always_crosses_spread(self):
        """Market orders always cross spread."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.MARKET,
        )
        order = MarketOrder(request)
        assert order.should_cross_spread(0) is True
        assert order.should_cross_spread(100) is True


class TestLimitOrder:
    """Tests for LimitOrder price calculation."""

    def test_buy_below_mid(self):
        """Buy limit order should be below mid price."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.LIMIT,
            limit_offset_bps=50,  # 0.5%
        )
        order = LimitOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        # mid (0.50) - 0.5% = 0.495
        assert price == pytest.approx(0.495, rel=0.01)

    def test_sell_above_mid(self):
        """Sell limit order should be above mid price."""
        request = OrderRequest(
            token_id="test",
            side="SELL",
            size_usd=100,
            order_type=OrderType.LIMIT,
            limit_offset_bps=50,  # 0.5%
        )
        order = LimitOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        # mid (0.50) + 0.5% = 0.505
        assert price == pytest.approx(0.505, rel=0.01)

    def test_buy_doesnt_cross_ask(self):
        """Buy limit shouldn't exceed best ask - 0.001."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.LIMIT,
            limit_offset_bps=1000,  # 10% - would cross spread
        )
        order = LimitOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        # Should be capped at ask - 0.001 = 0.519
        assert price <= 0.519

    def test_never_crosses_spread(self):
        """Limit orders don't automatically cross."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.LIMIT,
        )
        order = LimitOrder(request)
        assert order.should_cross_spread(0) is False
        assert order.should_cross_spread(1000) is False

    def test_price_bounds(self):
        """Price should stay in valid range [0.001, 0.999]."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.LIMIT,
            limit_offset_bps=5000,  # 50%
        )
        order = LimitOrder(request)
        price = order.calculate_price(
            best_bid=0.01,
            best_ask=0.02,
            mid_price=0.015,
        )
        assert price >= 0.001


class TestSpreadOrder:
    """Tests for SpreadOrder price calculation."""

    def test_buy_at_best_bid(self):
        """Buy spread order posts at/near best bid."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.SPREAD,
        )
        order = SpreadOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        # Should be at or slightly above best bid
        assert 0.48 <= price <= 0.50

    def test_sell_at_best_ask(self):
        """Sell spread order posts at/near best ask."""
        request = OrderRequest(
            token_id="test",
            side="SELL",
            size_usd=100,
            order_type=OrderType.SPREAD,
        )
        order = SpreadOrder(request)
        price = order.calculate_price(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
        )
        # Should be at or slightly below best ask
        assert 0.50 <= price <= 0.52

    def test_crosses_after_timeout(self):
        """Spread orders cross spread after timeout."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.SPREAD,
            spread_timeout_seconds=30,
        )
        order = SpreadOrder(request)
        assert order.should_cross_spread(0) is False
        assert order.should_cross_spread(29) is False
        assert order.should_cross_spread(30) is True
        assert order.should_cross_spread(31) is True


class TestCreateOrder:
    """Tests for the order factory function."""

    def test_creates_market_order(self):
        """Factory creates MarketOrder for MARKET type."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.MARKET,
        )
        order = create_order(request)
        assert isinstance(order, MarketOrder)

    def test_creates_limit_order(self):
        """Factory creates LimitOrder for LIMIT type."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.LIMIT,
        )
        order = create_order(request)
        assert isinstance(order, LimitOrder)

    def test_creates_spread_order(self):
        """Factory creates SpreadOrder for SPREAD type."""
        request = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.SPREAD,
        )
        order = create_order(request)
        assert isinstance(order, SpreadOrder)
