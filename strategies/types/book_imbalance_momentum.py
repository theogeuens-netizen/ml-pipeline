"""Book Imbalance Momentum Strategy - Follow orderbook pressure."""

from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from strategies.base import Strategy, Signal, Side, MarketData


class BookImbalanceMomentumStrategy(Strategy):
    """
    Follow strong orderbook imbalances as momentum signals.

    Entry: book_imbalance > threshold (buy YES) or < -threshold (buy NO)
    Exit: After max_hold_hours OR profit_target_pct reached
    Deduplication: cooldown_minutes between entries on same market

    This is a MOMENTUM strategy - we follow the imbalance direction:
    - Bid-heavy (imbalance > 0): Buy YES expecting price to rise
    - Ask-heavy (imbalance < 0): Buy NO expecting price to fall

    Contrast with FlowStrategy which FADES imbalances (contrarian).
    """

    def __init__(
        self,
        name: str,
        min_imbalance: float = 0.5,  # Minimum absolute imbalance to trigger
        yes_price_min: float = 0.30,  # Price zone filters
        yes_price_max: float = 0.70,
        max_spread: float = 0.01,  # 1 cent max spread
        categories: list = None,  # L1 categories to trade
        max_hold_hours: float = 1.0,  # Exit after 1 hour
        profit_target_pct: float = 0.05,  # Exit at 5% profit
        cooldown_minutes: float = 60,  # 60 min between entries on same market
        size_pct: float = 0.01,
        order_type: str = "limit",
        fixed_size_usd: float = None,  # Fixed USD size per trade (overrides size_pct)
        max_positions: int = 10,  # Max concurrent positions for this strategy
        min_minutes_to_close: float = 0,  # Min minutes to market close (live safety)
        **kwargs,
    ):
        self.name = name
        self.version = "1.0.0"
        self.min_imbalance = min_imbalance
        self.yes_price_min = yes_price_min
        self.yes_price_max = yes_price_max
        self.max_spread = max_spread
        self.categories = categories or []
        self.max_hold_hours = max_hold_hours
        self.profit_target_pct = profit_target_pct
        self.cooldown_minutes = cooldown_minutes
        self.size_pct = size_pct
        self.order_type = order_type
        self.fixed_size_usd = fixed_size_usd
        self.max_positions = max_positions
        self.min_minutes_to_close = min_minutes_to_close
        self.live = kwargs.get('live', False)  # Enable live trading

        # Track last entry time per market for deduplication
        self._last_entry: dict[int, datetime] = {}

        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        now = datetime.now(timezone.utc)

        for m in markets:
            # Token check
            if not m.yes_token_id or not m.no_token_id:
                continue

            # Category filter
            if self.categories and m.category_l1 not in self.categories:
                continue

            # Time to close filter (live safety)
            if self.min_minutes_to_close > 0 and m.hours_to_close is not None:
                minutes_to_close = m.hours_to_close * 60
                if minutes_to_close < self.min_minutes_to_close:
                    continue

            # Price zone filter
            if m.price < self.yes_price_min or m.price > self.yes_price_max:
                continue

            # Spread filter
            if m.best_bid is not None and m.best_ask is not None:
                spread = m.best_ask - m.best_bid
                if spread > self.max_spread:
                    continue
            else:
                continue  # Need orderbook data

            # Get book imbalance
            book_imbalance = m.snapshot.get("book_imbalance")
            if book_imbalance is None:
                continue

            # Check imbalance threshold
            if abs(book_imbalance) < self.min_imbalance:
                continue

            # Deduplication: check cooldown
            last = self._last_entry.get(m.id)
            if last:
                elapsed = (now - last).total_seconds() / 60
                if elapsed < self.cooldown_minutes:
                    continue

            # Determine direction based on imbalance (MOMENTUM - follow the flow)
            if book_imbalance > 0:
                # Bid-heavy: expect price to rise, BUY YES
                token_id = m.yes_token_id
                side_label = "YES"
                execution_price = m.best_ask
                best_bid = m.best_bid
                best_ask = m.best_ask
            else:
                # Ask-heavy: expect price to fall, BUY NO
                token_id = m.no_token_id
                side_label = "NO"
                # Convert to NO orderbook
                execution_price = 1 - m.best_bid  # NO ask
                best_bid = 1 - m.best_ask  # NO bid
                best_ask = 1 - m.best_bid  # NO ask

            reason = f"Book imbalance {book_imbalance:+.0%} → {side_label}"

            # Record entry time for deduplication
            self._last_entry[m.id] = now

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=reason,
                market_id=m.id,
                price_at_signal=execution_price,
                edge=abs(book_imbalance) * 0.1,  # Simple edge estimate
                confidence=0.6,
                size_usd=self.fixed_size_usd,  # Fixed size if set, else None (use size_pct)
                best_bid=best_bid,
                best_ask=best_ask,
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={
                    "book_imbalance": book_imbalance,
                    "yes_price": m.price,
                    "spread": m.best_ask - m.best_bid,
                    "hours_to_close": m.hours_to_close,
                    "max_hold_hours": self.max_hold_hours,
                    "profit_target_pct": self.profit_target_pct,
                    "size_pct": self.size_pct,
                    "fixed_size_usd": self.fixed_size_usd,
                    "max_positions": self.max_positions,
                    "min_minutes_to_close": self.min_minutes_to_close,
                },
            )

    def should_exit(self, position: Any, market: MarketData) -> Optional[Signal]:
        """
        Check if position should exit.

        Exit conditions:
        1. Time: position held >= max_hold_hours
        2. Profit: unrealized P&L >= profit_target_pct
        """
        # Check time exit
        time_exit = self.check_time_exit(position, self.max_hold_hours)
        if time_exit:
            return time_exit

        # Check profit exit
        profit_exit = self.check_profit_exit(position, self.profit_target_pct)
        if profit_exit:
            return profit_exit

        return None

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        """Debug funnel statistics."""
        total = len(markets)
        in_category = sum(
            1 for m in markets
            if not self.categories or m.category_l1 in self.categories
        )
        in_price_zone = sum(
            1 for m in markets
            if (not self.categories or m.category_l1 in self.categories)
            and self.yes_price_min <= m.price <= self.yes_price_max
        )
        with_spread = sum(
            1 for m in markets
            if (not self.categories or m.category_l1 in self.categories)
            and self.yes_price_min <= m.price <= self.yes_price_max
            and m.best_bid and m.best_ask
            and (m.best_ask - m.best_bid) <= self.max_spread
        )
        with_imbalance = sum(
            1 for m in markets
            if (not self.categories or m.category_l1 in self.categories)
            and self.yes_price_min <= m.price <= self.yes_price_max
            and m.best_bid and m.best_ask
            and (m.best_ask - m.best_bid) <= self.max_spread
            and m.snapshot.get("book_imbalance") is not None
            and abs(m.snapshot.get("book_imbalance", 0)) >= self.min_imbalance
        )

        return {
            "total_markets": total,
            "in_category": in_category,
            "in_price_zone": in_price_zone,
            "with_valid_spread": with_spread,
            "with_imbalance_signal": with_imbalance,
            "active_cooldowns": len(self._last_entry),
            "params": {
                "min_imbalance": f"{self.min_imbalance:.0%}",
                "price_zone": f"{self.yes_price_min:.0%}-{self.yes_price_max:.0%}",
                "max_spread": f"{self.max_spread:.2f}",
                "categories": self.categories if self.categories else "all",
                "max_hold_hours": self.max_hold_hours,
                "profit_target_pct": f"{self.profit_target_pct:.0%}",
            },
            "funnel": f"{total} → {in_category} (cat) → {in_price_zone} (price) → {with_spread} (spread) → {with_imbalance} (imbalance)",
        }
