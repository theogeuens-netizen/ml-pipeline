"""Built-in trading strategies."""

# Strategies are auto-discovered by the registry
# Just import them here to ensure they're loaded

from .longshot_yes import LongshotYesStrategy
from .longshot_no import LongshotNoStrategy
from .mean_reversion import MeanReversionStrategy
from .term_structure import TermStructureStrategy
from .volatility_hedge import VolatilityHedgeStrategy

__all__ = [
    "LongshotYesStrategy",
    "LongshotNoStrategy",
    "MeanReversionStrategy",
    "TermStructureStrategy",
    "VolatilityHedgeStrategy",
]
