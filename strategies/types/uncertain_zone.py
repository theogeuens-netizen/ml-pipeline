"""Uncertain Zone Strategy - Bet NO when YES is priced in uncertain zone (45-55%)."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class UncertainZoneStrategy(Strategy):
    """
    Exploit behavioral bias where YES outcomes in the 45-55% price range
    are systematically overpriced. Bet NO in the "uncertain zone".

    Based on exp-001/exp-002 analysis of 385K historical markets.
    Edge exists across all time windows. Uses after-spread edge for Kelly sizing.
    """

    def __init__(
        self,
        name: str,
        yes_price_min: float = 0.45,
        yes_price_max: float = 0.55,
        min_hours: float = 1,
        max_hours: float = 4,
        min_volume: float = 0,  # No volume filter by default
        expected_no_rate: float = 0.55,  # Conservative estimate
        min_edge_after_spread: float = 0.03,  # 3% minimum edge after spread
        max_spread: float = None,  # Optional: maximum spread to trade (e.g., 0.04 = 4%)
        categories: list = None,  # Optional: only trade these L1 categories
        excluded_categories: list = None,  # Optional: exclude these L1 categories
        size_pct: float = 0.01,
        order_type: str = "market",
        **kwargs,
    ):
        self.name = name
        self.version = "2.2.0"  # Fixed NO token pricing (was using YES orderbook)
        self.yes_price_min = yes_price_min
        self.yes_price_max = yes_price_max
        self.min_hours = min_hours
        self.max_hours = max_hours
        self.min_volume = min_volume
        self.expected_no_rate = expected_no_rate
        self.min_edge_after_spread = min_edge_after_spread
        self.max_spread = max_spread
        self.categories = categories or []  # Empty = all categories
        self.excluded_categories = excluded_categories or []
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Token check - need NO token
            if not m.no_token_id:
                continue

            # Category filter (if specified)
            if self.categories and m.category_l1 not in self.categories:
                continue
            if self.excluded_categories and m.category_l1 in self.excluded_categories:
                continue

            # Time window - must be positive and within range
            if m.hours_to_close is None or m.hours_to_close <= 0:
                continue
            if m.hours_to_close < self.min_hours or m.hours_to_close > self.max_hours:
                continue

            # Volume filter (optional - default is 0 = no filter)
            if self.min_volume > 0 and (m.volume_24h is None or m.volume_24h < self.min_volume):
                continue

            # Price in uncertain zone
            yes_price = m.price
            if yes_price < self.yes_price_min or yes_price > self.yes_price_max:
                continue

            # Spread filter (optional)
            # Calculate spread from YES bid/ask, filter if too wide
            if m.best_bid is not None and m.best_ask is not None:
                spread = m.best_ask - m.best_bid
                if self.max_spread is not None and spread > self.max_spread:
                    continue  # Skip if spread too wide

            # Calculate NO ask price (what we actually pay when buying NO)
            # NO ask ≈ 1 - YES bid (we're crossing the spread on the YES side)
            yes_bid = m.best_bid if m.best_bid is not None else yes_price
            no_ask = 1 - yes_bid  # This is what we pay for NO tokens

            if no_ask <= 0 or no_ask >= 1:
                continue

            # Edge calculation AFTER spread:
            # - We BUY NO tokens at price `no_ask` (not mid-price)
            # - If NO wins, we get $1 per share
            # - Expected value = expected_no_rate * $1 = expected_no_rate
            # - Cost = no_ask (actual execution price)
            # - Edge = (EV - Cost) / Cost
            #
            # Example: expected_no_rate=57%, yes_bid=49%, no_ask=51%
            # Edge = (0.57 - 0.51) / 0.51 = 11.8% expected return
            #
            # This is the REAL edge after paying the spread.
            edge_after_spread = (self.expected_no_rate - no_ask) / no_ask

            if edge_after_spread < self.min_edge_after_spread:
                continue  # Skip if edge < 3% (default threshold)

            # Confidence = expected win rate (used for Kelly sizing)
            confidence = self.expected_no_rate

            # Calculate spread for logging
            spread = None
            if m.best_bid is not None and m.best_ask is not None:
                spread = m.best_ask - m.best_bid

            # Convert YES orderbook to NO orderbook for correct execution pricing
            # NO bid = 1 - YES ask (someone buying NO = someone selling YES)
            # NO ask = 1 - YES bid (someone selling NO = someone buying YES)
            no_best_bid = 1 - m.best_ask if m.best_ask is not None else None
            no_best_ask = 1 - m.best_bid if m.best_bid is not None else None

            yield Signal(
                token_id=m.no_token_id,
                side=Side.BUY,
                reason=f"UncertainZone: YES@{yes_price:.1%}, NO@{no_ask:.1%}, edge={edge_after_spread:.1%}",
                market_id=m.id,
                price_at_signal=no_ask,  # NO price, not YES price
                edge=edge_after_spread,  # After-spread edge for Kelly sizing
                confidence=confidence,
                size_usd=None,
                best_bid=no_best_bid,    # NO orderbook, not YES
                best_ask=no_best_ask,    # NO orderbook, not YES
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={
                    "yes_price": yes_price,
                    "no_ask": no_ask,
                    "edge_after_spread": edge_after_spread,
                    "spread": spread,
                    "hours_to_close": m.hours_to_close,
                    "expected_no_rate": self.expected_no_rate,
                    "size_pct": self.size_pct,  # Per-strategy sizing
                },
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        """Return debug info about why strategy isn't trading."""
        total = len(markets)

        # Category filter
        if self.categories:
            in_category = sum(1 for m in markets if m.category_l1 in self.categories)
        elif self.excluded_categories:
            in_category = sum(1 for m in markets if m.category_l1 not in self.excluded_categories)
        else:
            in_category = total

        in_time = sum(
            1 for m in markets
            if m.hours_to_close and self.min_hours <= m.hours_to_close <= self.max_hours
            and (not self.categories or m.category_l1 in self.categories)
            and (not self.excluded_categories or m.category_l1 not in self.excluded_categories)
        )

        in_zone = sum(
            1 for m in markets
            if m.hours_to_close and self.min_hours <= m.hours_to_close <= self.max_hours
            and m.price and self.yes_price_min <= m.price <= self.yes_price_max
            and (not self.categories or m.category_l1 in self.categories)
            and (not self.excluded_categories or m.category_l1 not in self.excluded_categories)
        )

        with_spread = 0
        with_edge = 0
        for m in markets:
            # Category filter
            if self.categories and m.category_l1 not in self.categories:
                continue
            if self.excluded_categories and m.category_l1 in self.excluded_categories:
                continue

            if not m.hours_to_close:
                continue
            if not (self.min_hours <= m.hours_to_close <= self.max_hours):
                continue
            if not m.price or not (self.yes_price_min <= m.price <= self.yes_price_max):
                continue

            # Check spread filter
            if self.max_spread is not None and m.best_bid and m.best_ask:
                spread = m.best_ask - m.best_bid
                if spread > self.max_spread:
                    continue
            with_spread += 1

            # Check edge after spread
            yes_bid = m.best_bid if m.best_bid is not None else m.price
            no_ask = 1 - yes_bid
            if no_ask > 0 and no_ask < 1:
                edge = (self.expected_no_rate - no_ask) / no_ask
                if edge >= self.min_edge_after_spread:
                    with_edge += 1

        # Build funnel string
        cat_filter = f" → {in_category} (cats)" if (self.categories or self.excluded_categories) else ""
        spread_filter = f" → {with_spread} (spread)" if self.max_spread else ""

        return {
            "total_markets": total,
            "in_category": in_category,
            "in_time_window": in_time,
            "in_price_zone": in_zone,
            "with_valid_spread": with_spread,
            "with_sufficient_edge": with_edge,
            "params": {
                "yes_price_range": f"{self.yes_price_min:.0%}-{self.yes_price_max:.0%}",
                "time_window": f"{self.min_hours}-{self.max_hours}h",
                "expected_no_rate": f"{self.expected_no_rate:.0%}",
                "min_edge": f"{self.min_edge_after_spread:.0%}",
                "max_spread": f"{self.max_spread:.0%}" if self.max_spread else "none",
                "categories": self.categories if self.categories else "all",
                "excluded_categories": self.excluded_categories if self.excluded_categories else "none",
            },
            "funnel": f"{total}{cat_filter} → {in_time} (time) → {in_zone} (zone){spread_filter} → {with_edge} (edge≥{self.min_edge_after_spread:.0%})",
        }
