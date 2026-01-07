"""
Alerting package for trade notifications and errors.
"""

from .telegram import (
    TelegramAlert,
    get_alerter,
    alert_trade,
    alert_error,
    alert_daily_summary,
)

__all__ = [
    "TelegramAlert",
    "get_alerter",
    "alert_trade",
    "alert_error",
    "alert_daily_summary",
]
