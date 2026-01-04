"""
Config schemas for experiments.

Defines the structure of config.yaml with both backtest
and deployment sections for seamless flow to live trading.

Usage:
    from configs.schemas import ExperimentConfig, BacktestConfig
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any
import yaml


@dataclass
class BacktestConfig:
    """Backtest execution settings."""

    initial_capital: float = 1000.0
    stake_mode: str = "fixed"  # fixed, kelly, half_kelly, fixed_pct
    stake_per_bet: float = 10.0
    cost_per_bet: float = 0.0  # Trading fee per bet
    max_position_pct: float = 0.25  # Max % of capital per position

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class DeploymentConfig:
    """Live trading deployment settings."""

    allocated_usd: float = 400.0  # Initial wallet allocation
    order_type: str = "market"  # market, spread, limit
    size_pct: float = 0.01  # Position size as % of capital
    min_edge_after_spread: float = 0.03  # 3% minimum edge
    max_spread: Optional[float] = None  # Maximum spread to trade
    paper_trade: bool = True  # Paper trade before live

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeploymentConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class FilterConfig:
    """Universe filter settings."""

    categories: Optional[List[str]] = None  # Category whitelist (null = all)
    min_volume_24h: Optional[float] = None  # Minimum 24h volume
    min_liquidity: Optional[float] = None  # Minimum liquidity
    hours_min: Optional[float] = None  # Minimum hours to expiry
    hours_max: Optional[float] = None  # Maximum hours to expiry

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilterConfig":
        # Handle nested hours_to_expiry format
        if "hours_to_expiry" in data:
            expiry = data.pop("hours_to_expiry")
            data["hours_min"] = expiry.get("min")
            data["hours_max"] = expiry.get("max")
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class VariantConfig:
    """Single variant configuration."""

    id: str
    name: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.name is None:
            self.name = self.id

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VariantConfig":
        # Extract id and name, put rest in params
        variant_id = data.get("id", "v1")
        name = data.get("name", variant_id)
        params = {k: v for k, v in data.items() if k not in ["id", "name"]}
        return cls(id=variant_id, name=name, params=params)


@dataclass
class RobustnessConfig:
    """Robustness check settings."""

    time_split: bool = True
    liquidity_split: bool = True
    category_split: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RobustnessConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class KillCriteriaConfig:
    """Kill criteria thresholds."""

    sharpe: float = 0.5
    win_rate: float = 0.51
    trades: int = 50
    profit_factor: float = 1.1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KillCriteriaConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExperimentConfig:
    """
    Complete experiment configuration.

    This is the schema for config.yaml files in experiments/<exp_id>/.
    """

    experiment_id: str
    created_at: str  # ISO timestamp

    # Strategy identity
    strategy_type: str
    strategy_side: str = "NO"  # YES or NO

    # Configuration sections
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    robustness: RobustnessConfig = field(default_factory=RobustnessConfig)
    kill_criteria: KillCriteriaConfig = field(default_factory=KillCriteriaConfig)

    # Variants to test
    variants: List[VariantConfig] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "experiment_id": self.experiment_id,
            "created_at": self.created_at,
            "strategy_type": self.strategy_type,
            "strategy_side": self.strategy_side,
            "backtest": self.backtest.to_dict(),
            "deployment": self.deployment.to_dict(),
            "filters": self.filters.to_dict(),
            "robustness": self.robustness.to_dict(),
            "kill_criteria": self.kill_criteria.to_dict(),
            "variants": [v.to_dict() for v in self.variants],
        }

    def to_yaml(self) -> str:
        """Convert to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentConfig":
        """Create from dictionary (e.g., loaded from YAML)."""
        return cls(
            experiment_id=data.get("experiment_id", "unknown"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            strategy_type=data.get("strategy_type", "unknown"),
            strategy_side=data.get("strategy_side", "NO"),
            backtest=BacktestConfig.from_dict(data.get("backtest", {})),
            deployment=DeploymentConfig.from_dict(data.get("deployment", {})),
            filters=FilterConfig.from_dict(data.get("filters", {})),
            robustness=RobustnessConfig.from_dict(data.get("robustness", {})),
            kill_criteria=KillCriteriaConfig.from_dict(data.get("kill_criteria", {})),
            variants=[VariantConfig.from_dict(v) for v in data.get("variants", [])],
        )

    @classmethod
    def from_yaml_file(cls, path: str) -> "ExperimentConfig":
        """Load from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    def save_yaml(self, path: str) -> None:
        """Save to YAML file."""
        with open(path, "w") as f:
            f.write(self.to_yaml())


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_experiment_config(
    experiment_id: str,
    strategy_type: str,
    strategy_side: str = "NO",
    variants: Optional[List[Dict[str, Any]]] = None,
    **overrides,
) -> ExperimentConfig:
    """
    Create an experiment config with sensible defaults.

    Args:
        experiment_id: Experiment identifier (e.g., "exp-004")
        strategy_type: Strategy type name
        strategy_side: YES or NO
        variants: List of variant parameter dicts
        **overrides: Override any nested config values

    Returns:
        ExperimentConfig instance

    Example:
        config = create_experiment_config(
            "exp-004",
            "uncertain_zone",
            variants=[
                {"id": "v1", "yes_price_min": 0.45, "yes_price_max": 0.55},
                {"id": "v2", "yes_price_min": 0.40, "yes_price_max": 0.60},
            ],
            filters={"hours_min": 12, "hours_max": 36},
        )
    """
    config = ExperimentConfig(
        experiment_id=experiment_id,
        created_at=datetime.now().isoformat(),
        strategy_type=strategy_type,
        strategy_side=strategy_side,
    )

    # Apply overrides for nested configs
    if "backtest" in overrides:
        config.backtest = BacktestConfig.from_dict(overrides["backtest"])
    if "deployment" in overrides:
        config.deployment = DeploymentConfig.from_dict(overrides["deployment"])
    if "filters" in overrides:
        config.filters = FilterConfig.from_dict(overrides["filters"])
    if "robustness" in overrides:
        config.robustness = RobustnessConfig.from_dict(overrides["robustness"])
    if "kill_criteria" in overrides:
        config.kill_criteria = KillCriteriaConfig.from_dict(overrides["kill_criteria"])

    # Add variants
    if variants:
        config.variants = [VariantConfig.from_dict(v) for v in variants]

    return config
