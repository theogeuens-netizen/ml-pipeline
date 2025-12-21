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
) -> bool:
    """
    Send a trade alert.

    Args:
        strategy: Strategy/algorithm name
        side: BUY or SELL
        market_title: Market question/description
        market_id: Database market ID
        token_side: Which token was bought (YES or NO)
        price: Execution price (probability)
        size: Trade size in USD
        edge: Optional edge estimate
    """
    emoji = "🟢" if token_side == "YES" else "🔴"

    # Calculate YES/NO odds from price
    # If buying YES token, price is YES probability
    # If buying NO token, price is NO probability, so YES = 1 - price
    if token_side == "YES":
        yes_odds = price
        no_odds = 1 - price
    else:
        no_odds = price
        yes_odds = 1 - price

    edge_str = f"\nEdge: {edge:.1%}" if edge else ""

    message = (
        f"{emoji} <b>NEW TRADE</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Market:</b> {market_title[:80]}\n"
        f"<b>ID:</b> {market_id}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Odds:</b> YES {yes_odds:.0%} / NO {no_odds:.0%}\n"
        f"<b>Bet:</b> {token_side} @ {price:.1%}\n"
        f"<b>Amount:</b> ${size:.2f}\n"
        f"<b>Algorithm:</b> {strategy}"
        f"{edge_str}"
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
