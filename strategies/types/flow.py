"""Flow Strategy - Fade volume spikes and order flow imbalances."""

from typing import Iterator
from strategies.base import Strategy, Signal, Side, MarketData


class FlowStrategy(Strategy):
    """Fade volume spikes, book imbalances, or flow ratios."""

    def __init__(
        self,
        name: str,
        type: str,  # volume_spike, book_imbalance, flow_ratio
        spike_multiplier: float = 3.0,
        min_volume: float = 1000,
        min_directional_ratio: float = 0.6,
        min_imbalance: float = 0.7,
        min_flow_ratio: float = 0.8,
        min_trade_count: int = 10,
        min_liquidity: float = 3000,
        size_pct: float = 0.01,
        order_type: str = "spread",
        **kwargs,
    ):
        self.name = name
        self.version = "2.0.0"
        self.type = type
        self.spike_multiplier = spike_multiplier
        self.min_volume = min_volume
        self.min_directional_ratio = min_directional_ratio
        self.min_imbalance = min_imbalance
        self.min_flow_ratio = min_flow_ratio
        self.min_trade_count = min_trade_count
        self.min_liquidity = min_liquidity
        self.size_pct = size_pct
        self.order_type = order_type
        super().__init__()

    def scan(self, markets: list[MarketData]) -> Iterator[Signal]:
        for m in markets:
            # Skip expired markets
            if m.hours_to_close is not None and m.hours_to_close <= 0:
                continue

            if not m.snapshot:
                continue
            if not m.yes_token_id or not m.no_token_id:
                continue
            if m.liquidity and m.liquidity < self.min_liquidity:
                continue

            signal = None
            if self.type == "volume_spike":
                signal = self._check_volume_spike(m)
            elif self.type == "book_imbalance":
                signal = self._check_book_imbalance(m)
            elif self.type == "flow_ratio":
                signal = self._check_flow_ratio(m)

            if signal:
                yield signal

    def _check_volume_spike(self, m: MarketData) -> Signal | None:
        s = m.snapshot
        vol_1h = s.get("volume_1h", 0) or 0
        vol_24h = s.get("volume_24h", 0) or 0
        buy_vol = s.get("buy_volume_1h", 0) or 0
        sell_vol = s.get("sell_volume_1h", 0) or 0

        if vol_1h < self.min_volume:
            return None

        normal = vol_24h / 24 if vol_24h else 0
        if normal == 0 or vol_1h / normal < self.spike_multiplier:
            return None

        total = buy_vol + sell_vol
        if total == 0:
            return None

        buy_ratio = buy_vol / total
        if max(buy_ratio, 1 - buy_ratio) < self.min_directional_ratio:
            return None

        if buy_ratio > 0.5:
            token_id, reason = m.no_token_id, f"Volume spike {vol_1h/normal:.1f}x (buy heavy)"
        else:
            token_id, reason = m.yes_token_id, f"Volume spike {vol_1h/normal:.1f}x (sell heavy)"

        return self._make_signal(m, token_id, reason, 0.02, 0.5)

    def _check_book_imbalance(self, m: MarketData) -> Signal | None:
        imbalance = m.snapshot.get("book_imbalance", 0) or 0
        if abs(imbalance) < self.min_imbalance:
            return None

        if imbalance > 0:
            token_id, reason = m.no_token_id, f"Book bid-heavy ({imbalance:+.0%})"
        else:
            token_id, reason = m.yes_token_id, f"Book ask-heavy ({imbalance:+.0%})"

        return self._make_signal(m, token_id, reason, 0.015, 0.5)

    def _check_flow_ratio(self, m: MarketData) -> Signal | None:
        s = m.snapshot
        buy_count = s.get("buy_count_1h", 0) or 0
        sell_count = s.get("sell_count_1h", 0) or 0
        buy_vol = s.get("buy_volume_1h", 0) or 0
        sell_vol = s.get("sell_volume_1h", 0) or 0

        if buy_count + sell_count < self.min_trade_count:
            return None

        total = buy_vol + sell_vol
        if total == 0:
            return None

        buy_ratio = buy_vol / total
        if buy_ratio >= self.min_flow_ratio:
            token_id, reason = m.no_token_id, f"Flow exhaustion (buy {buy_ratio:.0%})"
        elif (1 - buy_ratio) >= self.min_flow_ratio:
            token_id, reason = m.yes_token_id, f"Flow exhaustion (sell {1-buy_ratio:.0%})"
        else:
            return None

        return self._make_signal(m, token_id, reason, 0.02, 0.5)

    def _make_signal(self, m: MarketData, token_id: str, reason: str, edge: float, conf: float) -> Signal:
        return Signal(
            token_id=token_id,
            side=Side.BUY,
            reason=reason,
            market_id=m.id,
            price_at_signal=m.price,
            edge=edge,
            confidence=conf,
            size_usd=None,
            best_bid=m.best_bid,
            best_ask=m.best_ask,
            strategy_name=self.name,
            strategy_sha=self.get_sha(),
            market_snapshot=m.snapshot,
            decision_inputs={"type": self.type},
        )

    def get_debug_stats(self, markets: list[MarketData]) -> dict:
        return {"total_markets": len(markets), "type": self.type}
