"""Longshot Strategy - Buy high-probability outcomes near expiry."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class LongshotStrategy(Strategy):
    """Buy tokens when probability is very high and expiry is near."""

    def __init__(
        self,
        name: str,
        side: str,  # YES or NO
        min_probability: float = 0.85,
        max_probability: float = 0.99,
        min_hours: float = 0,
        max_hours: float = 72,
        min_liquidity: float = 0,
        excluded_categories: list = None,
        size_pct: float = 0.01,
        order_type: str = "spread",
        spread_at_min_prob: float = None,  # Max spread at min_probability (e.g., 0.03 for 3%)
        spread_at_max_prob: float = None,  # Max spread at max_probability (e.g., 0.01 for 1%)
        **kwargs,
    ):
        self.name = name
        self.version = "2.3.0"  # Fixed NO orderbook conversion when bid is missing
        self.side = side
        self.min_probability = min_probability
        self.max_probability = max_probability
        self.min_hours = min_hours
        self.max_hours = max_hours
        self.min_liquidity = min_liquidity
        self.excluded_categories = excluded_categories or []
        self.size_pct = size_pct
        self.order_type = order_type
        self.spread_at_min_prob = spread_at_min_prob
        self.spread_at_max_prob = spread_at_max_prob
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Category exclusion
            if m.category_l1 in self.excluded_categories:
                continue

            # Token check
            if self.side == "YES" and not m.yes_token_id:
                continue
            if self.side == "NO" and not m.no_token_id:
                continue

            # Calculate probability for chosen side
            prob = m.price if self.side == "YES" else 1 - m.price

            # Probability range
            if prob < self.min_probability or prob > self.max_probability:
                continue

            # Time window - must be between min_hours and max_hours
            if m.hours_to_close is None or m.hours_to_close < self.min_hours or m.hours_to_close > self.max_hours:
                continue

            # Liquidity
            if self.min_liquidity and m.liquidity and m.liquidity < self.min_liquidity:
                continue

            # Dynamic spread filter (optional)
            spread = None
            if m.best_bid is not None and m.best_ask is not None:
                spread = m.best_ask - m.best_bid
                if self.spread_at_min_prob is not None and self.spread_at_max_prob is not None:
                    # Linear interpolation: at min_probability → spread_at_min_prob, at max_probability → spread_at_max_prob
                    t = (prob - self.min_probability) / (self.max_probability - self.min_probability)
                    t = max(0.0, min(1.0, t))  # Clamp to [0,1]
                    max_spread = self.spread_at_min_prob + t * (self.spread_at_max_prob - self.spread_at_min_prob)
                    if spread > max_spread:
                        continue

            # Calculate actual execution price (we buy the cross)
            # For NO: we pay no_ask = 1 - yes_bid
            # For YES: we pay yes_ask
            if self.side == "NO":
                yes_bid = m.best_bid if m.best_bid else m.price
                execution_price = 1 - yes_bid  # NO ask price
            else:
                execution_price = m.best_ask if m.best_ask else m.price  # YES ask price

            # Edge: assume true prob slightly higher (prob + 2% heuristic)
            estimated_true = min(prob + 0.02, 0.995)
            edge = (estimated_true - execution_price) / execution_price

            # Confidence
            time_factor = 1 - (m.hours_to_close / self.max_hours)
            confidence = 0.5 + (time_factor * 0.3)

            token_id = m.yes_token_id if self.side == "YES" else m.no_token_id

            # Convert orderbook for NO tokens (like uncertain_zone does)
            # YES orderbook: bid/ask are for YES tokens
            # NO orderbook: bid = 1 - YES_ask, ask = 1 - YES_bid
            if self.side == "NO":
                # Convert each price individually if available
                # Use execution_price as fallback for NO ask (since we BUY NO tokens)
                signal_best_bid = (1 - m.best_ask) if m.best_ask is not None else None
                signal_best_ask = (1 - m.best_bid) if m.best_bid is not None else execution_price
            else:
                signal_best_bid = m.best_bid
                signal_best_ask = m.best_ask if m.best_ask is not None else execution_price

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"High {self.side} ({prob:.1%}) near expiry ({m.hours_to_close:.1f}h)",
                market_id=m.id,
                price_at_signal=execution_price,
                edge=edge,
                confidence=confidence,
                size_usd=None,
                best_bid=signal_best_bid,
                best_ask=signal_best_ask,
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={
                    "prob": prob,
                    "hours": m.hours_to_close,
                    "execution_price": execution_price,
                    "spread": spread,
                    "size_pct": self.size_pct,
                },
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        total = len(markets)
        after_exclusion = sum(1 for m in markets if m.category_l1 not in self.excluded_categories)
        in_prob_range = 0
        in_time = 0
        after_spread = 0
        for m in markets:
            if m.category_l1 in self.excluded_categories:
                continue
            prob = m.price if self.side == "YES" else 1 - m.price
            if self.min_probability <= prob <= self.max_probability:
                in_prob_range += 1
                if m.hours_to_close and self.min_hours <= m.hours_to_close <= self.max_hours:
                    in_time += 1
                    # Check spread filter
                    if self.spread_at_min_prob is None or self.spread_at_max_prob is None:
                        after_spread += 1
                    elif m.best_bid is not None and m.best_ask is not None:
                        spread = m.best_ask - m.best_bid
                        t = (prob - self.min_probability) / (self.max_probability - self.min_probability)
                        t = max(0.0, min(1.0, t))
                        max_spread = self.spread_at_min_prob + t * (self.spread_at_max_prob - self.spread_at_min_prob)
                        if spread <= max_spread:
                            after_spread += 1

        spread_filter = f" → {after_spread} (spread)" if self.spread_at_min_prob is not None else ""
        return {
            "total_markets": total,
            "after_category_filter": after_exclusion,
            "in_probability_range": in_prob_range,
            "in_time_window": in_time,
            "after_spread_filter": after_spread,
            "funnel": f"{total} → {after_exclusion} → {in_prob_range} (prob) → {in_time} (time){spread_filter}",
        }
