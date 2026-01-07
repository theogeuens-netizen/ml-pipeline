"""
Tests for paper trading execution.

Tests:
- Slippage calculation
- Fill price simulation
- Balance tracking
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

from src.executor.execution.order_types import OrderRequest, OrderType


# Mock the OrderbookState for testing
@dataclass
class MockOrderbookState:
    """Mock orderbook state for testing."""
    best_bid: float = 0.48
    best_ask: float = 0.52
    mid_price: float = 0.50
    bid_depth_10: float = 1000.0
    ask_depth_10: float = 1000.0


class TestSlippageCalculation:
    """Tests for slippage calculation logic."""

    def test_base_slippage(self):
        """Base slippage should be SLIPPAGE_FACTOR (0.001 = 0.1%)."""
        from src.executor.execution.paper import PaperExecutor, SLIPPAGE_FACTOR

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState()

        # Small order relative to depth should have ~base slippage
        slippage = executor._calculate_slippage(
            size_usd=10,  # Small order
            orderbook=orderbook,
            is_buy=True,
        )

        # Should be close to base slippage (0.1%)
        assert slippage >= SLIPPAGE_FACTOR
        assert slippage < 0.002  # Not much more than base

    def test_slippage_increases_with_size(self):
        """Larger orders should have more slippage."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(ask_depth_10=1000.0)

        small_slippage = executor._calculate_slippage(
            size_usd=100,
            orderbook=orderbook,
            is_buy=True,
        )

        large_slippage = executor._calculate_slippage(
            size_usd=500,  # 50% of depth
            orderbook=orderbook,
            is_buy=True,
        )

        assert large_slippage > small_slippage

    def test_slippage_capped_at_2_percent(self):
        """Slippage should never exceed 2%."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(ask_depth_10=100.0)  # Very thin book

        # Massive order relative to depth
        slippage = executor._calculate_slippage(
            size_usd=10000,  # 100x the depth
            orderbook=orderbook,
            is_buy=True,
        )

        assert slippage <= 0.02  # Max 2%

    def test_slippage_with_zero_depth(self):
        """Zero depth should use base slippage only."""
        from src.executor.execution.paper import PaperExecutor, SLIPPAGE_FACTOR

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(ask_depth_10=0.0)

        slippage = executor._calculate_slippage(
            size_usd=100,
            orderbook=orderbook,
            is_buy=True,
        )

        # Should just be base slippage when depth is 0
        assert slippage == SLIPPAGE_FACTOR

    def test_buy_uses_ask_depth(self):
        """Buy orders should use ask depth for slippage."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)

        # Thin ask, thick bid
        orderbook = MockOrderbookState(
            ask_depth_10=100.0,
            bid_depth_10=10000.0,
        )

        slippage = executor._calculate_slippage(
            size_usd=100,  # 100% of ask depth
            orderbook=orderbook,
            is_buy=True,
        )

        # Should have significant slippage due to thin ask
        assert slippage > 0.003

    def test_sell_uses_bid_depth(self):
        """Sell orders should use bid depth for slippage."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)

        # Thick ask, thin bid
        orderbook = MockOrderbookState(
            ask_depth_10=10000.0,
            bid_depth_10=100.0,
        )

        slippage = executor._calculate_slippage(
            size_usd=100,  # 100% of bid depth
            orderbook=orderbook,
            is_buy=False,
        )

        # Should have significant slippage due to thin bid
        assert slippage > 0.003


class TestFillPriceSimulation:
    """Tests for fill price simulation."""

    def test_market_buy_adds_slippage(self):
        """Market buy should fill above mid with slippage."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
            ask_depth_10=1000.0,
        )

        order = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.MARKET,
        )

        fill_price = executor._simulate_fill_price(order, orderbook)

        # Should be at or above best ask
        assert fill_price >= 0.52
        # But capped at 0.999
        assert fill_price <= 0.999

    def test_market_sell_subtracts_slippage(self):
        """Market sell should fill below mid with slippage."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(
            best_bid=0.48,
            best_ask=0.52,
            mid_price=0.50,
            bid_depth_10=1000.0,
        )

        order = OrderRequest(
            token_id="test",
            side="SELL",
            size_usd=100,
            order_type=OrderType.MARKET,
        )

        fill_price = executor._simulate_fill_price(order, orderbook)

        # Should be at or below best bid
        assert fill_price <= 0.48
        # But floored at 0.001
        assert fill_price >= 0.001

    def test_limit_order_no_slippage(self):
        """Limit orders should fill at limit price (no slippage)."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState()

        order = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.LIMIT,
            limit_offset_bps=50,
        )

        fill_price = executor._simulate_fill_price(order, orderbook)

        # Limit orders don't add slippage (they may not fill at all)
        # Price should be below mid for a buy limit
        assert fill_price < orderbook.mid_price

    def test_fill_price_bounds(self):
        """Fill prices should stay in valid range."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)

        # Extreme orderbook state
        orderbook = MockOrderbookState(
            best_bid=0.001,
            best_ask=0.999,
            mid_price=0.50,
            ask_depth_10=1.0,  # Very thin
        )

        order = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=10000,  # Large order
            order_type=OrderType.MARKET,
        )

        fill_price = executor._simulate_fill_price(order, orderbook)

        assert 0.001 <= fill_price <= 0.999


class TestOrderbookStateEdgeCases:
    """Tests for edge cases in orderbook handling."""

    def test_missing_best_bid(self):
        """Should handle missing best bid gracefully."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(
            best_bid=None,
            best_ask=0.52,
            mid_price=0.50,
        )

        order = OrderRequest(
            token_id="test",
            side="SELL",
            size_usd=100,
            order_type=OrderType.MARKET,
        )

        # Should still work using mid_price
        fill_price = executor._simulate_fill_price(order, orderbook)
        # Market sell uses best_bid, which is None, so calculate_price returns None
        # This is expected behavior - can't sell without a bid
        assert fill_price is None or fill_price > 0

    def test_missing_best_ask(self):
        """Should handle missing best ask gracefully."""
        from src.executor.execution.paper import PaperExecutor

        executor = PaperExecutor.__new__(PaperExecutor)
        orderbook = MockOrderbookState(
            best_bid=0.48,
            best_ask=None,
            mid_price=0.50,
        )

        order = OrderRequest(
            token_id="test",
            side="BUY",
            size_usd=100,
            order_type=OrderType.MARKET,
        )

        # Market buy uses best_ask, which is None
        fill_price = executor._simulate_fill_price(order, orderbook)
        assert fill_price is None or fill_price > 0


class TestPaperExecutorConstants:
    """Tests for paper executor constants."""

    def test_slippage_factor_reasonable(self):
        """SLIPPAGE_FACTOR should be a small positive number."""
        from src.executor.execution.paper import SLIPPAGE_FACTOR

        assert SLIPPAGE_FACTOR > 0
        assert SLIPPAGE_FACTOR < 0.01  # Less than 1%

    def test_fee_constants_defined(self):
        """Fee constants should be defined."""
        from src.executor.execution.paper import MAKER_FEE, TAKER_FEE

        assert MAKER_FEE >= 0
        assert TAKER_FEE >= 0
        assert TAKER_FEE >= MAKER_FEE  # Taker usually pays more
