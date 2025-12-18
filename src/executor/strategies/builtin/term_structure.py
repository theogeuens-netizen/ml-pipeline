"""
Term Structure Strategy.

Exploit probability violations across related markets with different deadlines.

Mathematical basis: For cumulative events, P(event by T1) <= P(event by T2) when T1 < T2.
When this relationship is violated, there's an arbitrage opportunity.

Example: "BTC hits $100k by March" should have higher probability than "BTC hits $100k by February".
If February is priced higher, sell February and buy March.
"""

from typing import Iterator, Optional
from collections import defaultdict

from ..base import Strategy, Signal, Side, MarketData


class TermStructureStrategy(Strategy):
    """Exploit term structure violations across related markets."""

    name = "term_structure"
    description = "Arbitrage probability violations across time-linked markets"
    version = "1.0.0"

    # Default parameters
    DEFAULT_MIN_VIOLATION = 0.03  # Minimum 3% violation
    DEFAULT_MIN_LIQUIDITY_USD = 5000
    DEFAULT_MIN_EDGE = 0.02

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        """
        Scan for term structure violations.

        Groups markets by event, sorts by deadline, and finds violations
        where earlier deadline has higher probability than later deadline.
        """
        min_violation = self.get_param("min_violation", self.DEFAULT_MIN_VIOLATION)
        min_liquidity = self.get_param("min_liquidity_usd", self.DEFAULT_MIN_LIQUIDITY_USD)
        min_edge = self.get_param("min_edge", self.DEFAULT_MIN_EDGE)

        # Group markets by event
        event_markets = defaultdict(list)
        for market in markets:
            if not market.event_id:
                continue
            if market.liquidity is not None and market.liquidity < min_liquidity:
                continue
            if not market.yes_token_id or not market.no_token_id:
                continue
            event_markets[market.event_id].append(market)

        # Analyze each event group
        for event_id, event_group in event_markets.items():
            if len(event_group) < 2:
                continue

            # Sort by end date (earliest first)
            sorted_markets = sorted(
                [m for m in event_group if m.end_date is not None],
                key=lambda m: m.end_date
            )

            if len(sorted_markets) < 2:
                continue

            # Check adjacent pairs for violations
            for i in range(len(sorted_markets) - 1):
                earlier = sorted_markets[i]
                later = sorted_markets[i + 1]

                # Violation: earlier deadline priced higher than later
                violation = earlier.price - later.price

                if violation < min_violation:
                    continue

                # Calculate edge - the violation amount represents mispricing
                edge = violation / 2  # Split edge between both legs

                if edge < min_edge:
                    continue

                # Confidence based on liquidity and violation size
                min_liq = min(
                    earlier.liquidity or min_liquidity,
                    later.liquidity or min_liquidity
                )
                liquidity_factor = min(1.0, min_liq / (min_liquidity * 5))
                violation_factor = min(1.0, violation / 0.10)  # Max at 10% violation
                confidence = 0.5 + 0.25 * liquidity_factor + 0.25 * violation_factor

                self.logger.info(
                    f"Term structure violation: {earlier.question[:30]}... "
                    f"({earlier.price:.3f}) vs {later.question[:30]}... "
                    f"({later.price:.3f}), violation={violation:.3f}"
                )

                # Yield signal to SELL the earlier (overpriced) YES
                yield Signal(
                    token_id=earlier.yes_token_id,
                    side=Side.SELL,  # Sell overpriced
                    reason=f"Term structure: earlier ({earlier.price:.1%}) > later ({later.price:.1%})",
                    edge=edge,
                    confidence=confidence,
                    market_id=earlier.id,
                    price_at_signal=earlier.price,
                    best_bid=earlier.best_bid,
                    best_ask=earlier.best_ask,
                    strategy_name=self.name,
                    metadata={
                        "question_early": earlier.question,
                        "question_late": later.question,
                        "price_early": earlier.price,
                        "price_late": later.price,
                        "violation": violation,
                        "event_id": event_id,
                        "pair_market_id": later.id,
                        "is_short_leg": True,
                    },
                )

                # Yield signal to BUY the later (underpriced) YES
                yield Signal(
                    token_id=later.yes_token_id,
                    side=Side.BUY,  # Buy underpriced
                    reason=f"Term structure: later ({later.price:.1%}) < earlier ({earlier.price:.1%})",
                    edge=edge,
                    confidence=confidence,
                    market_id=later.id,
                    price_at_signal=later.price,
                    best_bid=later.best_bid,
                    best_ask=later.best_ask,
                    strategy_name=self.name,
                    metadata={
                        "question_early": earlier.question,
                        "question_late": later.question,
                        "price_early": earlier.price,
                        "price_late": later.price,
                        "violation": violation,
                        "event_id": event_id,
                        "pair_market_id": earlier.id,
                        "is_long_leg": True,
                    },
                )

    def filter(self, market: MarketData) -> bool:
        """Pre-filter markets."""
        # Must have event ID for grouping
        if not market.event_id:
            return False

        # Must have end date for sorting
        if not market.end_date:
            return False

        # Must have both tokens
        if not market.yes_token_id or not market.no_token_id:
            return False

        return True

    def should_exit(self, position, market: MarketData) -> Optional[Signal]:
        """
        Exit when:
        1. Violation has closed (term structure restored)
        2. Market approaching expiry
        """
        # Exit if near expiry
        if market.hours_to_close is not None and market.hours_to_close < 2:
            return Signal(
                token_id=position.token_id,
                side=Side.SELL if position.side == "BUY" else Side.BUY,
                reason="Term structure: closing before expiry",
                edge=0,
                confidence=0.9,
                market_id=market.id,
                price_at_signal=market.price,
                strategy_name=self.name,
            )

        # Note: Full exit logic would need to track the paired position
        # and check if the term structure violation has closed
        # This requires position metadata tracking not implemented yet

        return None
