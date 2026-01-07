"""
Telegram alerting for trades and errors.

Setup:
1. Create bot via @BotFather -> get token
2. Message bot, GET https://api.telegram.org/bot<TOKEN>/getUpdates -> find chat_id
3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import logging
from typing import Optional

import httpx

from src.config.settings import settings

logger = logging.getLogger(__name__)


class TelegramAlert:
    """Telegram bot for sending alerts."""

    def __init__(self):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to Telegram.

        Args:
            message: Message text (supports HTML formatting)
            parse_mode: Parse mode (HTML or Markdown)

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("Telegram alerts disabled (no token/chat_id configured)")
            return False

        try:
            response = httpx.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Telegram API error: {e.response.status_code} - {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False


# Singleton alerter instance
_alerter: Optional[TelegramAlert] = None


def get_alerter() -> TelegramAlert:
    """Get the singleton Telegram alerter instance."""
    global _alerter
    if _alerter is None:
        _alerter = TelegramAlert()
    return _alerter


def alert_trade(
    strategy: str,
    side: str,
    market_title: str,
    market_id: int,
    token_side: str,
    price: float,
    size: float,
    edge: Optional[float] = None,
    expected_win_rate: Optional[float] = None,
    order_type: str = "market",
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    hours_to_close: Optional[float] = None,
    signal_price: Optional[float] = None,
    is_live: bool = False,
) -> bool:
    """
    Send a detailed trade alert with full transparency on edge calculation.

    Args:
        strategy: Strategy/algorithm name
        side: BUY or SELL
        market_title: Market question/description
        market_id: Database market ID
        token_side: Which token was bought (YES or NO)
        price: Execution price (probability)
        size: Trade size in USD
        edge: Edge as calculated at signal time (may differ from execution)
        expected_win_rate: Expected win rate from backtests (for this token side)
        order_type: Order execution type (market/limit/spread)
        best_bid: Best bid at time of trade
        best_ask: Best ask at time of trade
        hours_to_close: Hours until market closes
        signal_price: Price when signal was generated (before slippage)
        is_live: True if this is a real money trade
    """
    from datetime import datetime, timezone

    # Use different emoji/indicator for live vs paper
    if is_live:
        mode_indicator = "💰 LIVE"
    else:
        mode_indicator = "📝 PAPER"

    emoji = "🟢" if token_side == "YES" else "🔴"
    now = datetime.now(timezone.utc)

    # Price in cents for clarity
    price_cents = price * 100

    # Calculate time to close
    if hours_to_close:
        if hours_to_close < 1:
            time_str = f"{int(hours_to_close * 60)}m"
        elif hours_to_close < 24:
            time_str = f"{hours_to_close:.1f}h"
        else:
            time_str = f"{hours_to_close / 24:.1f}d"
    else:
        time_str = "unknown"

    # Build the edge explanation
    # Edge = (expected_win_rate - price) / price
    # This is the expected return if the historical win rate holds
    edge_section = ""
    if expected_win_rate and price > 0:
        # Calculate expected value and profit per share
        ev_per_share = expected_win_rate  # If we win, we get $1
        cost_per_share = price
        profit_per_share = ev_per_share - cost_per_share
        expected_return = profit_per_share / cost_per_share

        edge_section = (
            f"\n━━━ <b>EDGE ANALYSIS</b> ━━━\n"
            f"Historical {token_side} win rate: <b>{expected_win_rate:.1%}</b>\n"
            f"We paid: {price_cents:.1f}¢/share\n"
            f"Expected value: {expected_win_rate * 100:.1f}¢/share\n"
            f"Expected profit: <b>{profit_per_share * 100:+.1f}¢/share ({expected_return:+.0%})</b>"
        )

    # Build execution details
    exec_section = f"\n━━━ <b>EXECUTION</b> ━━━\n"
    exec_section += f"Order type: {order_type.upper()}"

    if best_bid is not None and best_ask is not None:
        spread_cents = (best_ask - best_bid) * 100
        mid = (best_bid + best_ask) / 2
        exec_section += (
            f"\nBook: {best_bid*100:.1f}¢ / {best_ask*100:.1f}¢ (spread: {spread_cents:.1f}¢)\n"
            f"Filled at: {price_cents:.1f}¢"
        )
        # Show slippage from mid
        slippage = price - mid
        if abs(slippage) > 0.001:
            exec_section += f" (slippage: {slippage*100:+.2f}¢)"

    exec_section += f"\nTime: {now.strftime('%H:%M:%S')} UTC"

    message = (
        f"{emoji} <b>{token_side} BET PLACED</b> [{mode_indicator}]\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>{market_title[:70]}</b>\n"
        f"Closes in: {time_str}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Bet: <b>{token_side}</b> @ {price_cents:.1f}¢\n"
        f"Size: <b>${size:.2f}</b> ({size/price:.0f} shares)"
        f"{edge_section}"
        f"{exec_section}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Strategy: {strategy}"
    )

    return get_alerter().send(message)


def alert_error(component: str, error: str, context: Optional[str] = None) -> bool:
    """
    Send an error alert.

    Args:
        component: Component name (e.g., "executor", "websocket")
        error: Error message
        context: Optional additional context
    """
    message = f"🚨 <b>ERROR: {component}</b>\n<code>{error[:500]}</code>"
    if context:
        message += f"\n\n<i>{context[:200]}</i>"

    return get_alerter().send(message)


def alert_daily_summary(
    balance: float,
    pnl_today: float,
    open_positions: int,
    trades_today: int,
    win_rate: Optional[float] = None,
) -> bool:
    """
    Send daily summary alert.

    Args:
        balance: Current account balance
        pnl_today: Today's P&L
        open_positions: Number of open positions
        trades_today: Number of trades today
        win_rate: Optional win rate percentage
    """
    emoji = "📈" if pnl_today >= 0 else "📉"
    win_str = f" | Win Rate: {win_rate:.0%}" if win_rate is not None else ""

    message = (
        f"{emoji} <b>Daily Summary</b>\n"
        f"Balance: ${balance:,.2f}\n"
        f"Today P&L: ${pnl_today:+,.2f}\n"
        f"Positions: {open_positions} | Trades: {trades_today}{win_str}"
    )

    return get_alerter().send(message)


def alert_position_closed(
    strategy: str,
    market: str,
    side: str,
    outcome: str,
    cost_basis: float,
    payout: float,
    pnl: float,
    market_id: Optional[int] = None,
) -> bool:
    """
    Send alert when a position is closed on market resolution.

    Args:
        strategy: Strategy/algorithm name
        market: Market question/slug
        side: Position side (YES or NO)
        outcome: Market outcome (YES or NO)
        cost_basis: Original cost of position
        payout: Final payout received
        pnl: Realized P&L (payout - cost_basis)
        market_id: Optional market ID
    """
    # Determine if we won or lost
    won = pnl > 0
    emoji = "💰" if won else "💸"
    result_emoji = "✅" if won else "❌"

    pnl_sign = "+" if pnl >= 0 else ""
    pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

    market_id_str = f"\n<b>ID:</b> {market_id}" if market_id else ""

    message = (
        f"{emoji} <b>POSITION CLOSED</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Market:</b> {market[:80]}{market_id_str}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Bet:</b> {side}\n"
        f"<b>Outcome:</b> {outcome} {result_emoji}\n"
        f"<b>Cost:</b> ${cost_basis:.2f}\n"
        f"<b>Payout:</b> ${payout:.2f}\n"
        f"<b>P&L:</b> {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.0f}%)\n"
        f"<b>Algorithm:</b> {strategy}"
    )

    return get_alerter().send(message)
