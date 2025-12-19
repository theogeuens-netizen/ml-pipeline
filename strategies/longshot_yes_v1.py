"""
Longshot v1 - Buy on high-probability events near expiry.

Rationale:
Markets often underprice near-certainties due to:
1. Opportunity cost (capital tied up for small gain)
2. Lack of arbitrageurs (too small for institutions)
3. Retail focus on high-odds bets

This strategy buys tokens when probability is 92-99% and
the market is expiring within 72 hours.

Parameters:
  - bet_side: "YES" or "NO" - which side to bet on
  - When YES: buys YES when YES price is high (92-99%)
  - When NO: buys NO when NO price is high (YES price 1-8%)

To create a variant: copy this file, rename, adjust parameters.
"""

from typing import Iterator

from strategies.base import Strategy, Signal, Side, MarketData


class LongshotYesV1(Strategy):
    """Buy on high-probability events near expiry."""

    name = "longshot_yes_v1"
    version = "1.1.0"

    # === PARAMETERS (edit directly to create variants) ===
    bet_side = "YES"  # "YES" or "NO" - which side to bet on
    min_probability = 0.92  # Minimum probability for chosen side
    max_probability = 0.99  # Maximum probability (avoid 100% events)
    max_hours_to_expiry = 72  # Maximum hours until market closes
    min_liquidity_usd = 5000  # Minimum liquidity in USD
    size_usd = 25  # Default position size in USD

    def filter(self, market: MarketData) -> bool:
        """Quick filter to skip obviously unsuitable markets."""
        # Must have required token
        if self.bet_side == "YES" and not market.yes_token_id:
            return False
        if self.bet_side == "NO" and not market.no_token_id:
            return False

        # Calculate effective probability for chosen side
        if self.bet_side == "YES":
            prob = market.price
        else:
            prob = 1 - market.price  # NO probability

        # Must be trading in our probability range
        if prob < self.min_probability or prob > self.max_probability:
            return False

        # Must be expiring soon enough
        if market.hours_to_close is None or market.hours_to_close > self.max_hours_to_expiry:
            return False

        return True

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """
        Scan markets for high-probability opportunities.

        Yields signals for markets that meet criteria.
        """
        for m in markets:
            # Apply pre-filter
            if not self.filter(m):
                continue

            # Check liquidity
            if m.liquidity and m.liquidity < self.min_liquidity_usd:
                continue

            # Calculate effective probability for chosen side
            if self.bet_side == "YES":
                prob = m.price
                token_id = m.yes_token_id
            else:
                prob = 1 - m.price
                token_id = m.no_token_id

            # Calculate edge estimate
            # Assume true probability is slightly higher than market price
            # due to opportunity cost discount
            estimated_true_prob = min(prob + 0.02, 0.995)
            edge = (estimated_true_prob - prob) / prob

            # Higher confidence for:
            # - Closer to expiry (more certainty)
            # - Higher liquidity (more efficient pricing)
            time_factor = max(0, 1 - (m.hours_to_close / self.max_hours_to_expiry)) if m.hours_to_close else 0.5
            liquidity_factor = min(1, (m.liquidity / 20000)) if m.liquidity else 0.5
            confidence = 0.5 + (time_factor * 0.3) + (liquidity_factor * 0.2)

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"High {self.bet_side} prob ({prob:.1%}) near expiry ({m.hours_to_close:.1f}h)",
                market_id=m.id,
                price_at_signal=m.price,
                edge=edge,
                confidence=confidence,
                size_usd=self.size_usd,
                best_bid=m.best_bid,
                best_ask=m.best_ask,
                # Audit trail
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={
                    "bet_side": self.bet_side,
                    "probability": prob,
                    "hours_to_close": m.hours_to_close,
                    "liquidity": m.liquidity,
                    "params": self.get_params(),
                },
            )


# Module-level strategy instance
# This is what the loader looks for
strategy = LongshotYesV1()
