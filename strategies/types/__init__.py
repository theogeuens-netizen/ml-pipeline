"""Strategy type classes - one class per strategy family."""

from strategies.types.no_bias import NoBiasStrategy
from strategies.types.longshot import LongshotStrategy
from strategies.types.mean_reversion import MeanReversionStrategy
from strategies.types.whale_fade import WhaleFadeStrategy
from strategies.types.flow import FlowStrategy
from strategies.types.new_market import NewMarketStrategy
from strategies.types.uncertain_zone import UncertainZoneStrategy
from strategies.types.book_imbalance_momentum import BookImbalanceMomentumStrategy
from strategies.types.xgb_imbalance import XGBImbalanceStrategy

STRATEGY_TYPES = {
    "no_bias": NoBiasStrategy,
    "longshot": LongshotStrategy,
    "mean_reversion": MeanReversionStrategy,
    "whale_fade": WhaleFadeStrategy,
    "flow": FlowStrategy,
    "new_market": NewMarketStrategy,
    "uncertain_zone": UncertainZoneStrategy,
    "book_imbalance_momentum": BookImbalanceMomentumStrategy,
    "xgb_imbalance": XGBImbalanceStrategy,
}

__all__ = [
    "NoBiasStrategy",
    "LongshotStrategy",
    "MeanReversionStrategy",
    "WhaleFadeStrategy",
    "FlowStrategy",
    "NewMarketStrategy",
    "UncertainZoneStrategy",
    "BookImbalanceMomentumStrategy",
    "XGBImbalanceStrategy",
    "STRATEGY_TYPES",
]
