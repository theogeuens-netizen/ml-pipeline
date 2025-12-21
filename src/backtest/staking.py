"""
Stake calculation modes for backtesting.

Ported from futarchy's backtesting infrastructure.

Stake Modes:
- fixed: Fixed stake amount per bet
- fixed_pct: Fixed percentage of current capital
- kelly: Full Kelly criterion (optimal growth)
- half_kelly: Half Kelly (more conservative, less variance)
"""

from typing import Optional


def calculate_kelly_stake(
    capital: float,
    entry_price: float,
    bet_side: str,
    min_stake: float = 1.0,
    max_stake_pct: float = 0.25,
    half_kelly: bool = False,
    historical_win_rate: Optional[float] = None,
) -> float:
    """
    Calculate optimal stake using Kelly criterion.

    For a binary bet:
    - Betting on YES at price p: Win pays (1/p - 1), probability of winning = p
    - Betting on NO at price p: Win pays (1/(1-p) - 1), probability of winning = 1-p

    Kelly fraction = (p * b - q) / b
    where b = odds (payout), p = estimated win probability, q = 1 - p

    Edge Calculation:
    - If historical_win_rate is provided, use it directly as our probability estimate
    - Otherwise, estimate edge based on price bucket (extreme prices tend to have edge)
    - Prices near 0.5 have minimal edge; prices near 0 or 1 may be mispriced

    Args:
        capital: Current available capital
        entry_price: Entry price for the side being bet (0-1)
        bet_side: "YES" or "NO"
        min_stake: Minimum stake amount
        max_stake_pct: Maximum fraction of capital to stake (default 25%)
        half_kelly: If True, use half-Kelly for reduced variance
        historical_win_rate: Optional historical win rate for this price bucket (0-1)
    """
    # Calculate payout odds
    if bet_side == "YES":
        implied_prob = entry_price
        odds = (1 / entry_price) - 1 if entry_price > 0 else 0
    else:  # NO
        implied_prob = 1 - entry_price
        odds = (1 / (1 - entry_price)) - 1 if entry_price < 1 else 0

    if odds <= 0:
        return min_stake

    # Determine our estimated win probability
    if historical_win_rate is not None:
        # Use provided historical win rate directly
        estimated_prob = historical_win_rate
    else:
        # Estimate edge based on price bucket
        # Extreme prices (near 0 or 1) tend to have more mispricing
        # This is a simplified model - real edge should come from historical data
        price_distance_from_center = abs(implied_prob - 0.5)

        # Edge increases with distance from 0.5, max ~5% edge at extremes
        # Conservative: assume market is mostly efficient
        edge_multiplier = price_distance_from_center * 0.10  # Max 5% at p=0 or p=1

        # Add small base edge (1%) to justify betting at all
        estimated_edge = 0.01 + edge_multiplier
        estimated_prob = min(0.95, max(0.05, implied_prob + estimated_edge))

    # Kelly formula: f* = (p * b - q) / b
    q = 1 - estimated_prob
    kelly_fraction = (estimated_prob * odds - q) / odds

    # Kelly can be negative if no edge - don't bet
    if kelly_fraction <= 0:
        return min_stake

    # Apply half-kelly if requested (reduces variance significantly)
    if half_kelly:
        kelly_fraction *= 0.5

    # Clamp to reasonable range
    kelly_fraction = max(0.01, min(kelly_fraction, max_stake_pct))

    stake = capital * kelly_fraction
    return max(min_stake, stake)


def calculate_stake(
    capital: float,
    entry_price: float,
    bet_side: str,
    stake_mode: str,
    base_stake: float,
    min_stake: float = 1.0,
) -> float:
    """
    Calculate stake based on mode.

    Args:
        capital: Current available capital
        entry_price: Entry price for the side being bet (0-1)
        bet_side: "YES" or "NO"
        stake_mode: One of "fixed", "fixed_pct", "kelly", "half_kelly"
        base_stake: Base stake amount or percentage depending on mode
        min_stake: Minimum stake amount

    Modes:
    - fixed: Use base_stake directly
    - fixed_pct: Use base_stake as percentage of capital (e.g., 2.0 = 2%)
    - kelly: Full Kelly criterion
    - half_kelly: Half Kelly for reduced variance
    """
    if stake_mode == "fixed":
        return base_stake

    elif stake_mode == "fixed_pct":
        # base_stake interpreted as percentage (e.g., 2.0 = 2%)
        pct = base_stake / 100.0
        return max(min_stake, capital * pct)

    elif stake_mode == "kelly":
        return calculate_kelly_stake(
            capital, entry_price, bet_side, min_stake=min_stake, half_kelly=False
        )

    elif stake_mode == "half_kelly":
        return calculate_kelly_stake(
            capital, entry_price, bet_side, min_stake=min_stake, half_kelly=True
        )

    else:
        # Unknown mode, default to base_stake
        return base_stake
