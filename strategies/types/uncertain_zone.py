"""Uncertain Zone Strategy - Bet in uncertain zone (45-55%) based on category-specific biases."""

import re
from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class UncertainZoneStrategy(Strategy):
    """
    Exploit behavioral biases in the 45-55% price zone.

    Default: Bet NO (YES is overpriced in most categories)
    With side="YES": Bet YES (for categories like CRYPTO where YES is underpriced)

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
        expected_no_rate: float = 0.55,  # Conservative estimate (ignored if side="YES")
        expected_yes_rate: float = None,  # Used when side="YES"
        side: str = "NO",  # "NO" (default) or "YES"
        min_edge_after_spread: float = 0.03,  # 3% minimum edge after spread
        max_spread: float = None,  # Optional: maximum spread to trade (e.g., 0.04 = 4%)
        categories: list = None,  # Optional: only trade these L1 categories
        excluded_categories: list = None,  # Optional: exclude these L1 categories
        l2_categories: list = None,  # Optional: only trade these L2 categories
        excluded_l2_categories: list = None,  # Optional: exclude these L2 categories
        l3_categories: list = None,  # Optional: only trade these L3 categories
        excluded_l3_categories: list = None,  # Optional: exclude these L3 categories
        include_l3_from_excluded_l1: dict = None,  # e.g., {"SPORTS": ["SPREAD", "OVER_UNDER"]} - whitelist L3s from excluded L1
        exclude_patterns: list = None,  # Optional: exclude markets matching these question patterns
        include_patterns: list = None,  # Optional: ALLOW uncategorized markets matching these patterns
        size_pct: float = 0.01,
        order_type: str = "market",
        max_positions: int = None,  # Max concurrent positions for live trading
        **kwargs,
    ):
        self.name = name
        self.version = "2.5.1"  # Added max_positions parameter
        self.max_positions = max_positions
        self.yes_price_min = yes_price_min
        self.yes_price_max = yes_price_max
        self.min_hours = min_hours
        self.max_hours = max_hours
        self.min_volume = min_volume
        self.side = side.upper()  # "YES" or "NO"
        self.expected_no_rate = expected_no_rate
        self.expected_yes_rate = expected_yes_rate or (1 - expected_no_rate)  # Derive if not set
        self.min_edge_after_spread = min_edge_after_spread
        self.max_spread = max_spread
        self.categories = categories or []  # Empty = all categories
        self.excluded_categories = excluded_categories or []
        self.l2_categories = l2_categories or []
        self.excluded_l2_categories = excluded_l2_categories or []
        self.l3_categories = l3_categories or []
        self.excluded_l3_categories = excluded_l3_categories or []
        self.include_l3_from_excluded_l1 = include_l3_from_excluded_l1 or {}
        self.exclude_patterns = exclude_patterns or []
        self.include_patterns = include_patterns or []  # Whitelist patterns for uncategorized markets
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Token check - need the token we're betting on
            if self.side == "YES":
                if not m.yes_token_id:
                    continue
            else:  # NO
                if not m.no_token_id:
                    continue

            # L1 Category filter
            # Handle uncategorized markets: allow if they match include_patterns
            if self.excluded_categories:
                if m.category_l1 is None:
                    # Uncategorized market - check if it matches include_patterns
                    if self.include_patterns:
                        question = getattr(m, 'question', '') or ''
                        pattern_match = False
                        for pattern in self.include_patterns:
                            if re.search(pattern, question, re.IGNORECASE):
                                pattern_match = True
                                break
                        if not pattern_match:
                            continue  # Uncategorized and doesn't match whitelist patterns
                        # else: matches whitelist pattern, allow through
                    else:
                        continue  # No include_patterns defined, reject uncategorized
                elif m.category_l1 in self.excluded_categories:
                    # Check if this L1 has whitelisted L3 categories
                    if m.category_l1 in self.include_l3_from_excluded_l1:
                        allowed_l3s = self.include_l3_from_excluded_l1[m.category_l1]
                        if m.category_l3 not in allowed_l3s:
                            continue  # L3 not in whitelist, skip
                        # else: L3 is whitelisted, allow through
                    else:
                        continue  # No whitelist for this L1, skip
            if self.categories and m.category_l1 not in self.categories:
                continue

            # L2 Category filter
            if self.l2_categories and m.category_l2 not in self.l2_categories:
                continue
            if self.excluded_l2_categories and m.category_l2 in self.excluded_l2_categories:
                continue

            # L3 Category filter
            if self.l3_categories and m.category_l3 not in self.l3_categories:
                continue
            if self.excluded_l3_categories and m.category_l3 in self.excluded_l3_categories:
                continue

            # Pattern-based exclusions (for uncategorized markets or additional filtering)
            if not self._passes_pattern_filters(m):
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

            # Calculate spread for filtering and logging
            spread = None
            if m.best_bid is not None and m.best_ask is not None:
                spread = m.best_ask - m.best_bid
                if self.max_spread is not None and spread > self.max_spread:
                    continue  # Skip if spread too wide

            # Calculate execution prices based on which side we're betting
            if self.side == "YES":
                # Betting YES: we buy at YES ask price
                yes_ask = m.best_ask if m.best_ask is not None else yes_price
                execution_price = yes_ask
                expected_rate = self.expected_yes_rate
                token_id = m.yes_token_id
                # YES orderbook is native
                best_bid = m.best_bid
                best_ask = m.best_ask if m.best_ask is not None else execution_price
            else:
                # Betting NO: we buy at NO ask = 1 - YES bid
                yes_bid = m.best_bid if m.best_bid is not None else yes_price
                execution_price = 1 - yes_bid  # NO ask price
                expected_rate = self.expected_no_rate
                token_id = m.no_token_id
                # Convert to NO orderbook
                best_bid = 1 - m.best_ask if m.best_ask is not None else None
                best_ask = 1 - m.best_bid if m.best_bid is not None else execution_price

            if execution_price <= 0 or execution_price >= 1:
                continue

            # Edge calculation AFTER spread:
            # - We BUY tokens at `execution_price` (not mid-price)
            # - If we win, we get $1 per share
            # - Expected value = expected_rate * $1
            # - Cost = execution_price
            # - Edge = (EV - Cost) / Cost
            edge_after_spread = (expected_rate - execution_price) / execution_price

            if edge_after_spread < self.min_edge_after_spread:
                continue  # Skip if edge < 3% (default threshold)

            # Confidence = expected win rate (used for Kelly sizing)
            confidence = expected_rate

            side_label = self.side
            no_price = 1 - yes_price
            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"UncertainZone({side_label}): YES@{yes_price:.1%}, NO@{no_price:.1%}, edge={edge_after_spread:.1%}",
                market_id=m.id,
                price_at_signal=execution_price,
                edge=edge_after_spread,
                confidence=confidence,
                size_usd=None,
                best_bid=best_bid,
                best_ask=best_ask,
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={
                    "yes_price": yes_price,
                    "side": self.side,
                    "execution_price": execution_price,
                    "edge_after_spread": edge_after_spread,
                    "spread": spread,
                    "hours_to_close": m.hours_to_close,
                    "expected_rate": expected_rate,
                    "size_pct": self.size_pct,
                },
            )

    def _passes_category_filters(self, m: MarketData) -> bool:
        """Check if market passes all category filters (L1, L2, L3)."""
        # L1 filter - CRITICAL: reject uncategorized when exclusions are set
        if self.excluded_categories:
            if m.category_l1 is None:
                return False  # Reject uncategorized markets
            if m.category_l1 in self.excluded_categories:
                # Check if this L1 has whitelisted L3 categories
                if m.category_l1 in self.include_l3_from_excluded_l1:
                    allowed_l3s = self.include_l3_from_excluded_l1[m.category_l1]
                    if m.category_l3 in allowed_l3s:
                        pass  # Allow this market through
                    else:
                        return False  # L3 not in whitelist, reject
                else:
                    return False  # No whitelist for this L1, reject
        if self.categories and m.category_l1 not in self.categories:
            return False
        # L2 filter
        if self.l2_categories and m.category_l2 not in self.l2_categories:
            return False
        if self.excluded_l2_categories and m.category_l2 in self.excluded_l2_categories:
            return False
        # L3 filter
        if self.l3_categories and m.category_l3 not in self.l3_categories:
            return False
        if self.excluded_l3_categories and m.category_l3 in self.excluded_l3_categories:
            return False
        return True

    def _passes_pattern_filters(self, m: MarketData) -> bool:
        """Check if market passes pattern-based filters."""
        if not self.exclude_patterns:
            return True
        question = getattr(m, 'question', '') or ''
        for pattern in self.exclude_patterns:
            if re.search(pattern, question, re.IGNORECASE):
                return False
        return True

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        """Return debug info about why strategy isn't trading."""
        total = len(markets)

        # Category filter (all levels)
        in_category = sum(1 for m in markets if self._passes_category_filters(m))

        in_time = sum(
            1 for m in markets
            if m.hours_to_close and self.min_hours <= m.hours_to_close <= self.max_hours
            and self._passes_category_filters(m)
        )

        in_zone = sum(
            1 for m in markets
            if m.hours_to_close and self.min_hours <= m.hours_to_close <= self.max_hours
            and m.price and self.yes_price_min <= m.price <= self.yes_price_max
            and self._passes_category_filters(m)
        )

        with_spread = 0
        with_edge = 0
        for m in markets:
            # Category filter (all levels)
            if not self._passes_category_filters(m):
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
        has_cat_filter = (self.categories or self.excluded_categories or
                         self.l2_categories or self.excluded_l2_categories or
                         self.l3_categories or self.excluded_l3_categories)
        cat_filter = f" → {in_category} (cats)" if has_cat_filter else ""
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
                "l2_categories": self.l2_categories if self.l2_categories else "all",
                "excluded_l2_categories": self.excluded_l2_categories if self.excluded_l2_categories else "none",
                "l3_categories": self.l3_categories if self.l3_categories else "all",
                "excluded_l3_categories": self.excluded_l3_categories if self.excluded_l3_categories else "none",
            },
            "funnel": f"{total}{cat_filter} → {in_time} (time) → {in_zone} (zone){spread_filter} → {with_edge} (edge≥{self.min_edge_after_spread:.0%})",
        }
