"""
Polymarket Order Client for Live Trading.

Wrapper around py_clob_client for order execution on Polymarket.
Handles:
- Balance queries
- Orderbook fetching
- Order placement (limit orders)
- Order status tracking
- Order cancellation

Supports SOCKS5 proxy for bypassing IP blocks.

IMPORTANT: Proxy configuration is deferred to client initialization to avoid
polluting global environment variables that affect other HTTP clients.
"""

import logging
import os
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional, TYPE_CHECKING

from src.config.settings import settings

logger = logging.getLogger(__name__)

# Type hints only - actual imports deferred to avoid setting proxy at module load
if TYPE_CHECKING:
    from py_clob_client.client import ClobClient

# Track if proxy has been configured (done once per process, lazily)
_proxy_configured = False


def _configure_proxy_if_needed():
    """
    Configure proxy environment variables before importing py_clob_client.

    This is done lazily (only when client is first created) to avoid
    polluting the environment for other HTTP clients like Gamma/CLOB fetchers.
    """
    global _proxy_configured

    if _proxy_configured:
        return

    if hasattr(settings, 'trading_proxy_url') and settings.trading_proxy_url:
        os.environ["ALL_PROXY"] = settings.trading_proxy_url
        os.environ["HTTPS_PROXY"] = settings.trading_proxy_url
        os.environ["HTTP_PROXY"] = settings.trading_proxy_url
        proxy_host = settings.trading_proxy_url.split('@')[-1] if '@' in settings.trading_proxy_url else settings.trading_proxy_url
        logger.info(f"Trading proxy configured: {proxy_host}")

    _proxy_configured = True


def _get_clob_client_class():
    """Import ClobClient lazily after proxy is configured."""
    from py_clob_client.client import ClobClient
    return ClobClient


class PolymarketOrderClient:
    """
    Client for placing and managing orders on Polymarket.

    Uses py_clob_client for authenticated trading operations.
    Derives API credentials from private key at initialization.
    """

    CHAIN_ID = 137  # Polygon mainnet
    HOST = "https://clob.polymarket.com"
    SIGNATURE_TYPE = 2  # Gnosis Safe (MetaMask wallet)

    def __init__(self, private_key: Optional[str] = None):
        """
        Initialize the order client.

        Args:
            private_key: Ethereum private key (with or without 0x prefix).
                        If not provided, uses POLYMARKET_PRIVATE_KEY from settings.
        """
        self.private_key = private_key or getattr(settings, 'polymarket_private_key', None)

        if not self.private_key:
            raise ValueError(
                "No private key provided. Set POLYMARKET_PRIVATE_KEY in .env or pass to constructor."
            )

        # Configure proxy BEFORE importing py_clob_client
        _configure_proxy_if_needed()

        # Now import and create the client
        ClobClient = _get_clob_client_class()

        self.proxy_enabled = _proxy_configured and bool(getattr(settings, 'trading_proxy_url', ''))

        # Initialize client with private key and funder address
        funder = getattr(settings, 'polymarket_funder_address', None)
        self.client = ClobClient(
            host=self.HOST,
            chain_id=self.CHAIN_ID,
            key=self.private_key,
            signature_type=self.SIGNATURE_TYPE,
            funder=funder,
        )

        # Derive and set API credentials
        self._setup_credentials()

        logger.info(f"PolymarketOrderClient initialized for wallet: {self.get_address()}")

    def _setup_credentials(self):
        """Derive API credentials from private key and set them on the client."""
        try:
            creds = self.client.derive_api_key()
            self.client.set_api_creds(creds)
            logger.debug("API credentials derived and set successfully")
        except Exception as e:
            logger.error(f"Failed to derive API credentials: {e}")
            raise

    def get_address(self) -> str:
        """Get the wallet address associated with this client."""
        return self.client.get_address()

    def get_balance(self) -> float:
        """
        Get USDC balance available for trading.

        Returns:
            USDC balance as float
        """
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self.SIGNATURE_TYPE
            )
            result = self.client.get_balance_allowance(params)

            # Result format: {'balance': '1000000', 'allowances': {...}}
            # Balance is in USDC units (6 decimals)
            balance_raw = int(result.get("balance", 0))
            balance = balance_raw / 1_000_000  # Convert from 6 decimals

            logger.debug(f"USDC balance: {balance}")
            return balance

        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """
        Get current orderbook for a token.

        Args:
            token_id: Polymarket token ID (outcome's external_id)

        Returns:
            Orderbook dict with 'bids' and 'asks' arrays
        """
        try:
            orderbook = self.client.get_order_book(token_id)
            return orderbook
        except Exception as e:
            logger.error(f"Failed to get orderbook for {token_id}: {e}")
            raise

    def calculate_midmarket_price(self, orderbook: dict[str, Any]) -> Optional[float]:
        """
        Calculate midmarket price from orderbook.

        Args:
            orderbook: Orderbook dict with 'bids' and 'asks'

        Returns:
            Midmarket price (best_bid + best_ask) / 2, or None if no liquidity
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            logger.warning("No bids or asks in orderbook")
            return None

        # Bids and asks are sorted by price (best first)
        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 0))

        if best_bid <= 0 or best_ask <= 0:
            logger.warning(f"Invalid prices: bid={best_bid}, ask={best_ask}")
            return None

        midmarket = (best_bid + best_ask) / 2

        logger.debug(f"Orderbook: bid={best_bid}, ask={best_ask}, mid={midmarket}")
        return midmarket

    def get_best_bid_ask(self, orderbook: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
        """
        Get best bid and ask prices from orderbook.

        Best bid = highest bid price (someone willing to buy at this price)
        Best ask = lowest ask price (someone willing to sell at this price)

        Returns:
            Tuple of (best_bid, best_ask)
        """
        # Handle both dict and OrderBookSummary object
        if hasattr(orderbook, 'bids'):
            bids = orderbook.bids or []
            asks = orderbook.asks or []
        else:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

        # Extract all prices - handle both dict and object formats
        bid_prices = []
        ask_prices = []

        for bid in bids:
            price = float(bid.price if hasattr(bid, 'price') else bid.get("price", 0))
            if price > 0:
                bid_prices.append(price)

        for ask in asks:
            price = float(ask.price if hasattr(ask, 'price') else ask.get("price", 0))
            if price > 0:
                ask_prices.append(price)

        # Best bid is highest, best ask is lowest
        best_bid = max(bid_prices) if bid_prices else None
        best_ask = min(ask_prices) if ask_prices else None

        return best_bid, best_ask

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size_usd: float,
    ) -> dict[str, Any]:
        """
        Place a limit order at specified price.

        Args:
            token_id: Polymarket token ID
            side: "BUY" or "SELL"
            price: Limit price (0-1)
            size_usd: Order size in USDC

        Returns:
            Order response from Polymarket
        """
        try:
            # Calculate number of shares: size_usd / price
            # Round down to avoid rounding errors
            size_shares = Decimal(str(size_usd)) / Decimal(str(price))
            size_shares = float(size_shares.quantize(Decimal("0.01"), rounding=ROUND_DOWN))

            # Get tick size for this market
            tick_size = self.client.get_tick_size(token_id)

            # Round price to tick size
            price_decimal = Decimal(str(price))
            tick_decimal = Decimal(str(tick_size))
            rounded_price = float(
                (price_decimal / tick_decimal).quantize(Decimal("1"), rounding=ROUND_DOWN)
                * tick_decimal
            )

            logger.info(
                f"Placing order: token={token_id}, side={side}, "
                f"price={rounded_price}, size_shares={size_shares}, size_usd={size_usd}"
            )

            # Create and post order
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL
            order = self.client.create_and_post_order(
                OrderArgs(
                    token_id=token_id,
                    price=rounded_price,
                    size=size_shares,
                    side=BUY if side.upper() == "BUY" else SELL,
                )
            )

            logger.info(f"Order placed successfully: {order}")
            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            raise

    def place_market_order(
        self,
        token_id: str,
        side: str,
        size_usd: float,
    ) -> dict[str, Any]:
        """
        Place a market order (crosses the spread immediately).

        Args:
            token_id: Polymarket token ID
            side: "BUY" or "SELL"
            size_usd: Order size in USDC

        Returns:
            Order response from Polymarket
        """
        try:
            # Get current orderbook
            orderbook = self.get_orderbook(token_id)
            best_bid, best_ask = self.get_best_bid_ask(orderbook)

            if side.upper() == "BUY":
                if best_ask is None:
                    raise ValueError("No ask price available for market order")
                price = best_ask
            else:
                if best_bid is None:
                    raise ValueError("No bid price available for market order")
                price = best_bid

            return self.place_limit_order(token_id, side, price, size_usd)

        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            raise

    def get_order(self, order_id: str) -> dict[str, Any]:
        """
        Get order details by ID.

        Args:
            order_id: Polymarket order ID

        Returns:
            Order details including status, filled amount, etc.
        """
        try:
            order = self.client.get_order(order_id)
            return order
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            raise

    def get_open_orders(self, market: Optional[str] = None, asset_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Get all open orders, optionally filtered by market or asset.

        Args:
            market: Optional market/condition ID to filter by
            asset_id: Optional asset/token ID to filter by

        Returns:
            List of open orders
        """
        try:
            from py_clob_client.clob_types import OpenOrderParams
            params = OpenOrderParams(market=market, asset_id=asset_id)
            orders = self.client.get_orders(params)
            return orders if orders else []
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.

        Args:
            order_id: Polymarket order ID

        Returns:
            True if cancelled successfully
        """
        try:
            result = self.client.cancel(order_id)
            logger.info(f"Order {order_id} cancelled: {result}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.

        Returns:
            Number of orders cancelled
        """
        try:
            result = self.client.cancel_all()
            cancelled_count = len(result) if result else 0
            logger.info(f"Cancelled {cancelled_count} orders")
            return cancelled_count
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            raise

    def get_trades(self, market: Optional[str] = None, asset_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Get recent trades/fills for this account.

        Args:
            market: Optional market/condition ID to filter by
            asset_id: Optional asset/token ID to filter by

        Returns:
            List of trade records
        """
        try:
            from py_clob_client.clob_types import TradeParams
            params = TradeParams(market=market, asset_id=asset_id) if market or asset_id else None
            trades = self.client.get_trades(params)
            return trades if trades else []
        except Exception as e:
            logger.error(f"Failed to get trades: {e}")
            raise

    def get_positions(self) -> list[dict[str, Any]]:
        """
        Get all positions (assets held) for this wallet.

        Returns:
            List of position records with asset_id, size, avg_price, etc.
        """
        try:
            # Aggregate trades to compute positions
            trades = self.get_trades()
            positions = {}

            for trade in trades:
                asset_id = trade.get('asset_id')
                if not asset_id:
                    continue

                side = trade.get('side', '').upper()
                size = float(trade.get('size', 0))
                price = float(trade.get('price', 0))

                if asset_id not in positions:
                    positions[asset_id] = {
                        'asset_id': asset_id,
                        'market': trade.get('market'),
                        'outcome': trade.get('outcome'),
                        'size': 0,
                        'cost_basis': 0,
                        'trades': [],
                    }

                pos = positions[asset_id]
                if side == 'BUY':
                    pos['cost_basis'] += size * price
                    pos['size'] += size
                elif side == 'SELL':
                    # Reduce position
                    if pos['size'] > 0:
                        avg_price = pos['cost_basis'] / pos['size'] if pos['size'] > 0 else 0
                        pos['cost_basis'] -= size * avg_price
                        pos['size'] -= size

                pos['trades'].append(trade['id'])

            # Filter to non-zero positions and calculate avg price
            result = []
            for pos in positions.values():
                if pos['size'] > 0.01:  # Filter dust
                    pos['avg_price'] = pos['cost_basis'] / pos['size'] if pos['size'] > 0 else 0
                    result.append(pos)

            return result
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            raise


# Singleton instance for reuse
_client_instance: Optional[PolymarketOrderClient] = None


def get_order_client() -> PolymarketOrderClient:
    """
    Get or create a singleton order client instance.

    Returns:
        PolymarketOrderClient instance
    """
    global _client_instance

    if _client_instance is None:
        _client_instance = PolymarketOrderClient()

    return _client_instance
