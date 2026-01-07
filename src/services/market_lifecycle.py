"""
Market Lifecycle State Machine.

This module tracks and validates market state transitions following the
Polymarket/UMA lifecycle:

Trading Status:
- active + accepting_orders=true → Trading normally
- active + accepting_orders=false → Trading suspended (e.g., sports delay)
- closed=true → Market closed, no more orders

UMA Resolution Status:
- null → Not started
- "proposed" → Outcome proposed, in 2-hour challenge window
- "disputed" → Disputed, awaiting DVM vote
- "resolved" → Finalized, positions can redeem
- "flagged" → Flagged for review (rare)

State Machine (permissive mode - logs warnings, allows all transitions):

    TRADING ──────────────→ SUSPENDED ──────────────→ CLOSED
       │                         │                       │
       │                         │                       │
       ▼                         ▼                       ▼
   PROPOSED ←─────────────────────────────────────── PROPOSED
       │                                                 │
       ├───────────────────→ DISPUTED ←──────────────────┤
       │                         │                       │
       ▼                         ▼                       ▼
   RESOLVED ←─────────────── RESOLVED ←─────────────── RESOLVED

Note: This is permissive mode - we LOG unexpected transitions but allow them.
This helps us observe actual API behavior before adding strict validation.
"""

import structlog
from datetime import datetime
from typing import Optional
from dataclasses import dataclass
from enum import Enum

logger = structlog.get_logger()


class TradingStatus(Enum):
    """Trading status based on Gamma API fields."""
    TRADING = "trading"  # active=true, closed=false, accepting_orders=true
    SUSPENDED = "suspended"  # active=true, closed=false, accepting_orders=false
    CLOSED = "closed"  # closed=true
    RESOLVED = "resolved"  # resolved=true


class UmaStatus(Enum):
    """UMA oracle resolution status."""
    NONE = "none"  # null
    PROPOSED = "proposed"  # In 2-hour challenge window
    DISPUTED = "disputed"  # Awaiting DVM vote
    RESOLVED = "resolved"  # Finalized
    FLAGGED = "flagged"  # Flagged for review


@dataclass
class MarketState:
    """Represents the current state of a market."""
    trading_status: TradingStatus
    uma_status: UmaStatus
    timestamp: datetime


# Valid transitions for state machine (source → destinations)
# If transition not in this map, it's logged as unexpected
VALID_TRADING_TRANSITIONS = {
    TradingStatus.TRADING: {TradingStatus.SUSPENDED, TradingStatus.CLOSED, TradingStatus.RESOLVED},
    TradingStatus.SUSPENDED: {TradingStatus.TRADING, TradingStatus.CLOSED, TradingStatus.RESOLVED},
    TradingStatus.CLOSED: {TradingStatus.RESOLVED},
    TradingStatus.RESOLVED: set(),  # Terminal state
}

VALID_UMA_TRANSITIONS = {
    UmaStatus.NONE: {UmaStatus.PROPOSED, UmaStatus.RESOLVED},
    UmaStatus.PROPOSED: {UmaStatus.RESOLVED, UmaStatus.DISPUTED, UmaStatus.NONE},  # NONE for reset
    UmaStatus.DISPUTED: {UmaStatus.RESOLVED, UmaStatus.NONE},  # NONE for new proposal after DVM
    UmaStatus.RESOLVED: set(),  # Terminal state
    UmaStatus.FLAGGED: {UmaStatus.RESOLVED, UmaStatus.NONE},
}


def get_trading_status(
    active: bool,
    closed: bool,
    accepting_orders: bool,
    resolved: bool,
) -> TradingStatus:
    """
    Determine trading status from Gamma API fields.

    Args:
        active: Market.active field
        closed: Market.closed field
        accepting_orders: Market.accepting_orders field
        resolved: Market.resolved field

    Returns:
        Current TradingStatus
    """
    if resolved:
        return TradingStatus.RESOLVED
    if closed:
        return TradingStatus.CLOSED
    if not accepting_orders:
        return TradingStatus.SUSPENDED
    if active:
        return TradingStatus.TRADING
    # Default fallback
    return TradingStatus.CLOSED


def get_uma_status(uma_resolution_status: Optional[str]) -> UmaStatus:
    """
    Convert Gamma API uma_resolution_status to enum.

    Args:
        uma_resolution_status: Raw string from API

    Returns:
        UmaStatus enum
    """
    if not uma_resolution_status:
        return UmaStatus.NONE

    status_map = {
        "proposed": UmaStatus.PROPOSED,
        "disputed": UmaStatus.DISPUTED,
        "resolved": UmaStatus.RESOLVED,
        "flagged": UmaStatus.FLAGGED,
    }

    return status_map.get(uma_resolution_status.lower(), UmaStatus.NONE)


def log_state_transition(
    market_id: int,
    condition_id: str,
    slug: str,
    old_trading: TradingStatus,
    new_trading: TradingStatus,
    old_uma: UmaStatus,
    new_uma: UmaStatus,
) -> None:
    """
    Log a state transition and warn if unexpected.

    This is permissive mode - all transitions are allowed, but unexpected
    ones are logged as warnings for observation.

    Args:
        market_id: Database ID
        condition_id: Polymarket condition ID
        slug: Market slug for logging
        old_trading: Previous trading status
        new_trading: New trading status
        old_uma: Previous UMA status
        new_uma: New UMA status
    """
    trading_changed = old_trading != new_trading
    uma_changed = old_uma != new_uma

    if not trading_changed and not uma_changed:
        return  # No change

    # Check if transitions are expected
    trading_unexpected = False
    uma_unexpected = False

    if trading_changed:
        valid_destinations = VALID_TRADING_TRANSITIONS.get(old_trading, set())
        if new_trading not in valid_destinations:
            trading_unexpected = True

    if uma_changed:
        valid_destinations = VALID_UMA_TRANSITIONS.get(old_uma, set())
        if new_uma not in valid_destinations:
            uma_unexpected = True

    # Log the transition
    if trading_unexpected or uma_unexpected:
        logger.warning(
            "Unexpected market state transition",
            market_id=market_id,
            slug=slug,
            condition_id=condition_id[:16] if condition_id else "N/A",
            old_trading=old_trading.value,
            new_trading=new_trading.value,
            old_uma=old_uma.value,
            new_uma=new_uma.value,
            trading_unexpected=trading_unexpected,
            uma_unexpected=uma_unexpected,
        )
    else:
        logger.info(
            "Market state transition",
            market_id=market_id,
            slug=slug,
            old_trading=old_trading.value,
            new_trading=new_trading.value,
            old_uma=old_uma.value,
            new_uma=new_uma.value,
        )


def can_trade(market) -> bool:
    """
    Check if a market is tradeable based on its state.

    Args:
        market: Market model instance

    Returns:
        True if market is tradeable
    """
    if market.resolved:
        return False
    if market.closed:
        return False
    if not market.accepting_orders:
        return False
    if not market.active:
        return False
    return True


def get_lifecycle_summary(market) -> dict:
    """
    Get a summary of market lifecycle state for debugging.

    Args:
        market: Market model instance

    Returns:
        Dictionary with lifecycle information
    """
    trading_status = get_trading_status(
        market.active,
        market.closed,
        market.accepting_orders,
        market.resolved,
    )
    uma_status = get_uma_status(market.uma_resolution_status)

    return {
        "market_id": market.id,
        "condition_id": market.condition_id,
        "slug": market.slug,
        "trading_status": trading_status.value,
        "uma_status": uma_status.value,
        "can_trade": can_trade(market),
        "fields": {
            "active": market.active,
            "closed": market.closed,
            "closed_at": market.closed_at.isoformat() if market.closed_at else None,
            "accepting_orders": market.accepting_orders,
            "resolved": market.resolved,
            "resolved_at": market.resolved_at.isoformat() if market.resolved_at else None,
            "outcome": market.outcome,
            "uma_resolution_status": market.uma_resolution_status,
            "uma_status_updated_at": (
                market.uma_status_updated_at.isoformat()
                if market.uma_status_updated_at else None
            ),
        },
    }
