"""Whale Fade Strategy - Fade large trades expecting reversion."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class WhaleFadeStrategy(Strategy):
    """Fade whale trades in specified direction."""

    def __init__(
        self,
        name: str,
        direction: str,  # YES, NO, or ANY
        min_whale_volume: float = 5000,
        min_whale_ratio: float = 0.7,
        min_imbalance_ratio: float = 0.6,
        min_liquidity: float = 3000,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "2.0.0"
        self.direction = direction
        self.min_whale_volume = min_whale_volume
        self.min_whale_ratio = min_whale_ratio
        self.min_imbalance_ratio = min_imbalance_ratio
        self.min_liquidity = min_liquidity
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            if not m.snapshot:
                continue
            if not m.yes_token_id or not m.no_token_id:
                continue

            # Liquidity
            if m.liquidity and m.liquidity < self.min_liquidity:
                continue

            snapshot = m.snapshot
            whale_buy = snapshot.get("whale_buy_volume_1h", 0) or 0
            whale_sell = snapshot.get("whale_sell_volume_1h", 0) or 0
            whale_total = whale_buy + whale_sell
            whale_net = snapshot.get("whale_net_flow_1h", 0) or 0

            if whale_total < self.min_whale_volume:
                continue

            # Determine if we should fade
            if self.direction == "YES":
                ratio = whale_buy / whale_total if whale_total else 0
                if ratio < self.min_whale_ratio:
                    continue
                token_id = m.no_token_id
                fade_desc = f"whale YES ${whale_buy:,.0f}"
            elif self.direction == "NO":
                ratio = whale_sell / whale_total if whale_total else 0
                if ratio < self.min_whale_ratio:
                    continue
                token_id = m.yes_token_id
                fade_desc = f"whale NO ${whale_sell:,.0f}"
            else:  # ANY
                imbalance = abs(whale_net) / whale_total if whale_total else 0
                if imbalance < self.min_imbalance_ratio:
                    continue
                if whale_net > 0:
                    token_id = m.no_token_id
                    fade_desc = f"whale YES net ${whale_net:,.0f}"
                else:
                    token_id = m.yes_token_id
                    fade_desc = f"whale NO net ${abs(whale_net):,.0f}"
                ratio = imbalance

            edge = 0.03 * min(1.5, whale_total / 10000)
            confidence = 0.5 + (ratio - 0.6) * 1.0

            yield Signal(
                token_id=token_id,
                side=Side.BUY,
                reason=f"Fade {fade_desc}",
                market_id=m.id,
                price_at_signal=m.price,
                edge=edge,
                confidence=confidence,
                size_usd=None,
                best_bid=m.best_bid,
                best_ask=m.best_ask,
                strategy_name=self.name,
                strategy_sha=self.get_sha(),
                market_snapshot=m.snapshot,
                decision_inputs={"whale_buy": whale_buy, "whale_sell": whale_sell},
            )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        total = len(markets)
        with_whale = 0
        with_imbalance = 0
        for m in markets:
            if not m.snapshot:
                continue
            whale_buy = m.snapshot.get("whale_buy_volume_1h", 0) or 0
            whale_sell = m.snapshot.get("whale_sell_volume_1h", 0) or 0
            whale_total = whale_buy + whale_sell
            if whale_total >= self.min_whale_volume:
                with_whale += 1
                ratio = max(whale_buy, whale_sell) / whale_total if whale_total else 0
                if ratio >= self.min_whale_ratio:
                    with_imbalance += 1

        return {
            "total_markets": total,
            "with_whale_volume": with_whale,
            "with_directional_imbalance": with_imbalance,
            "funnel": f"{total} → {with_whale} (whale vol) → {with_imbalance} (imbalance)",
        }
