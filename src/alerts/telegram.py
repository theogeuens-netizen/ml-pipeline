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
    market: str,
    price: float,
    size: float,
    edge: Optional[float] = None,
) -> bool:
    """
    Send a trade alert.

    Args:
        strategy: Strategy name
        side: BUY or SELL
        market: Market question/description
        price: Execution price
        size: Trade size in USD
        edge: Optional edge estimate
    """
    emoji = "🟢" if side == "BUY" else "🔴"
    edge_str = f" (edge: {edge:.1%})" if edge else ""

    message = (
        f"{emoji} <b>{strategy}</b>\n"
        f"{side} ${size:.0f} @ {price:.1%}{edge_str}\n"
        f"<i>{market[:60]}</i>"
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
    pnl: float,
    hold_time_hours: float,
) -> bool:
    """
    Send alert when a position is closed.

    Args:
        strategy: Strategy name
        market: Market question/description
        pnl: Realized P&L
        hold_time_hours: How long position was held
    """
    emoji = "💰" if pnl >= 0 else "💸"
    pnl_color = "+" if pnl >= 0 else ""

    message = (
        f"{emoji} <b>Position Closed</b>\n"
        f"Strategy: {strategy}\n"
        f"P&L: {pnl_color}${pnl:.2f}\n"
        f"Hold Time: {hold_time_hours:.1f}h\n"
        f"<i>{market[:60]}</i>"
    )

    return get_alerter().send(message)
