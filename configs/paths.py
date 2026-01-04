"""
Path registry for slash commands and experiment pipeline.

Provides centralized path definitions and helper functions
to eliminate per-session path discovery.

Usage:
    from configs.paths import PATHS, get_experiment_files, validate_strategy_params
"""

from pathlib import Path
from typing import Dict, List, Any, Optional

# Project root (parent of configs/)
PROJECT_ROOT = Path(__file__).parent.parent

# =============================================================================
# PATH REGISTRY
# =============================================================================

PATHS = {
    # Experiment system
    "experiments": PROJECT_ROOT / "experiments",
    "ledger": PROJECT_ROOT / "ledger" / "insights.jsonl",

    # Strategy system
    "strategies_yaml": PROJECT_ROOT / "strategies.yaml",
    "strategy_types": PROJECT_ROOT / "strategies" / "types",
    "strategy_base": PROJECT_ROOT / "strategies" / "base.py",
    "strategy_loader": PROJECT_ROOT / "strategies" / "loader.py",

    # CLI tools
    "cli": PROJECT_ROOT / "cli",
    "cli_ship": PROJECT_ROOT / "cli" / "ship.py",
    "cli_robustness": PROJECT_ROOT / "cli" / "robustness.py",
    "cli_ledger": PROJECT_ROOT / "cli" / "ledger.py",
    "cli_backtest": PROJECT_ROOT / "cli" / "backtest.py",

    # Backtest module
    "backtest": PROJECT_ROOT / "src" / "backtest",
    "backtest_engine": PROJECT_ROOT / "src" / "backtest" / "engine.py",
    "backtest_robustness": PROJECT_ROOT / "src" / "backtest" / "robustness.py",

    # Executor
    "executor_models": PROJECT_ROOT / "src" / "executor" / "models.py",
    "executor_runner": PROJECT_ROOT / "src" / "executor" / "engine" / "runner.py",

    # Config module (self-reference)
    "configs": PROJECT_ROOT / "configs",

    # Documentation
    "research_lab_md": PROJECT_ROOT / "RESEARCH_LAB.md",
    "claude_md": PROJECT_ROOT / "CLAUDE.md",
}


# =============================================================================
# DATABASE TABLES
# =============================================================================

DB_TABLES = {
    "strategy_balances": {
        "description": "Per-strategy wallet allocation and P&L",
        "primary_key": "strategy_name",
        "columns": [
            "strategy_name",
            "allocated_usd",
            "current_usd",
            "realized_pnl",
            "unrealized_pnl",
            "total_pnl",
            "position_count",
            "trade_count",
            "win_count",
            "loss_count",
            "high_water_mark",
            "low_water_mark",
            "max_drawdown_usd",
            "max_drawdown_pct",
            "created_at",
            "updated_at",
        ],
    },
    "positions": {
        "description": "Open and closed trading positions",
        "primary_key": "id",
        "columns": [
            "id",
            "strategy_name",
            "market_id",
            "token_id",
            "side",
            "entry_price",
            "entry_time",
            "size_shares",
            "cost_basis",
            "current_price",
            "unrealized_pnl",
            "exit_price",
            "exit_time",
            "realized_pnl",
            "status",
        ],
    },
    "signals": {
        "description": "Trading signals from strategies",
        "primary_key": "id",
        "columns": [
            "id",
            "strategy_name",
            "market_id",
            "token_id",
            "side",
            "reason",
            "edge",
            "confidence",
            "price_at_signal",
            "best_bid",
            "best_ask",
            "size_usd",
            "status",
            "created_at",
        ],
    },
    "trade_decisions": {
        "description": "Strategy decision audit trail",
        "primary_key": "id",
        "columns": [
            "id",
            "strategy_name",
            "strategy_sha",
            "market_id",
            "market_snapshot",
            "decision_inputs",
            "signal_side",
            "signal_reason",
            "signal_edge",
            "signal_size_usd",
            "executed",
            "rejected_reason",
            "execution_price",
            "position_id",
            "created_at",
        ],
    },
    "historical_markets": {
        "description": "Historical market data for backtesting",
        "primary_key": "id",
        "columns": [
            "id",
            "external_id",
            "question",
            "close_date",
            "resolution_status",
            "winner",
            "resolved_at",
            "macro_category",
            "micro_category",
            "volume",
            "liquidity",
        ],
    },
    "historical_price_snapshots": {
        "description": "Historical price data for backtesting",
        "primary_key": "id",
        "columns": [
            "id",
            "market_id",
            "timestamp",
            "price",
            "open_price",
            "high_price",
            "low_price",
            "bid_price",
            "ask_price",
            "volume",
        ],
    },
}


# =============================================================================
# STRATEGY PARAMETERS
# Maps each strategy type to its constructor params
# =============================================================================

STRATEGY_PARAMS = {
    "uncertain_zone": {
        "description": "Bet NO when YES is priced in uncertain zone (45-55%)",
        "required": ["name"],
        "optional": {
            "yes_price_min": 0.45,
            "yes_price_max": 0.55,
            "min_hours": 1.0,
            "max_hours": 4.0,
            "min_volume": 0.0,
            "expected_no_rate": 0.55,
            "min_edge_after_spread": 0.03,
            "max_spread": None,
            "size_pct": 0.01,
            "order_type": "market",
        },
    },
    "no_bias": {
        "description": "Buy NO based on historical resolution rates by category",
        "required": ["name", "category", "historical_no_rate"],
        "optional": {
            "min_hours": 0.0,
            "max_hours": 168.0,
            "min_liquidity": 0.0,
            "size_pct": 0.01,
            "order_type": "spread",
        },
    },
    "longshot": {
        "description": "Buy high-probability outcomes near expiry",
        "required": ["name", "side"],
        "optional": {
            "min_probability": 0.85,
            "max_probability": 0.99,
            "max_hours": 72.0,
            "min_liquidity": 0.0,
            "excluded_categories": [],
            "size_pct": 0.01,
            "order_type": "spread",
        },
    },
    "mean_reversion": {
        "description": "Fade price deviations beyond N standard deviations",
        "required": ["name"],
        "optional": {
            "std_threshold": 2.0,
            "min_deviation_pct": 0.05,
            "lookback_hours": 24.0,
            "min_history_points": 10,
            "min_liquidity": 5000.0,
            "category": None,
            "size_pct": 0.01,
            "order_type": "spread",
        },
    },
    "whale_fade": {
        "description": "Fade whale trades in specified direction",
        "required": ["name", "direction"],
        "optional": {
            "min_whale_volume": 5000.0,
            "min_whale_ratio": 0.7,
            "min_imbalance_ratio": 0.6,
            "min_liquidity": 3000.0,
            "size_pct": 0.01,
            "order_type": "spread",
        },
    },
    "flow": {
        "description": "Fade volume spikes, book imbalances, or flow ratios",
        "required": ["name", "type"],
        "optional": {
            "spike_multiplier": 3.0,
            "min_volume": 1000.0,
            "min_directional_ratio": 0.6,
            "min_imbalance": 0.7,
            "min_flow_ratio": 0.8,
            "min_trade_count": 10,
            "min_liquidity": 3000.0,
            "size_pct": 0.01,
            "order_type": "spread",
        },
    },
    "new_market": {
        "description": "Buy NO on new markets (convergence hypothesis)",
        "required": ["name"],
        "optional": {
            "min_no_probability": 0.50,
            "max_no_probability": 0.90,
            "min_hours_to_expiry": 168.0,
            "min_liquidity": 500.0,
            "assumed_no_rate": 0.60,
            "size_pct": 0.01,
            "order_type": "spread",
        },
    },
}


# =============================================================================
# DEPLOYMENT CONFIG FIELDS
# Valid fields for the deployment section of config.yaml
# =============================================================================

DEPLOYMENT_FIELDS = {
    "allocated_usd": {
        "type": float,
        "default": 400.0,
        "description": "Initial wallet allocation per strategy",
    },
    "order_type": {
        "type": str,
        "default": "market",
        "choices": ["market", "spread", "limit"],
        "description": "Order execution method",
    },
    "size_pct": {
        "type": float,
        "default": 0.01,
        "description": "Position size as percentage of capital",
    },
    "min_edge_after_spread": {
        "type": float,
        "default": 0.03,
        "description": "Minimum edge after spread to trade",
    },
    "max_spread": {
        "type": float,
        "default": None,
        "description": "Maximum spread to trade (null = no limit)",
    },
    "paper_trade": {
        "type": bool,
        "default": True,
        "description": "Paper trade before live",
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_experiment_path(exp_id: str) -> Path:
    """Get path to experiment directory."""
    return PATHS["experiments"] / exp_id


def get_experiment_files(exp_id: str) -> Dict[str, Path]:
    """Get paths to all experiment files."""
    base = get_experiment_path(exp_id)
    return {
        "dir": base,
        "spec": base / "spec.md",
        "config": base / "config.yaml",
        "results": base / "results.json",
        "verdict": base / "verdict.md",
    }


def validate_strategy_params(strategy_type: str, params: Dict[str, Any]) -> List[str]:
    """
    Validate params against strategy type constructor.

    Args:
        strategy_type: One of the keys in STRATEGY_PARAMS
        params: Dictionary of parameter values

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    schema = STRATEGY_PARAMS.get(strategy_type)
    if not schema:
        return [f"Unknown strategy type: {strategy_type}. Valid types: {list(STRATEGY_PARAMS.keys())}"]

    # Check required params
    for req in schema.get("required", []):
        if req not in params:
            errors.append(f"Missing required param: {req}")

    # Check for unknown params
    known = set(schema.get("required", [])) | set(schema.get("optional", {}).keys())
    for key in params:
        if key not in known:
            errors.append(f"Unknown param for {strategy_type}: {key}")

    return errors


def validate_deployment_config(config: Dict[str, Any]) -> List[str]:
    """
    Validate deployment section of config.yaml.

    Args:
        config: Deployment config dictionary

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    for key, value in config.items():
        if key not in DEPLOYMENT_FIELDS:
            errors.append(f"Unknown deployment field: {key}")
            continue

        field_def = DEPLOYMENT_FIELDS[key]

        # Type check (allow None for nullable fields)
        if value is not None and not isinstance(value, field_def["type"]):
            errors.append(f"Deployment field '{key}' must be {field_def['type'].__name__}, got {type(value).__name__}")

        # Choice validation
        if "choices" in field_def and value is not None:
            if value not in field_def["choices"]:
                errors.append(f"Deployment field '{key}' must be one of {field_def['choices']}, got '{value}'")

    return errors


def get_default_deployment_config() -> Dict[str, Any]:
    """Get default deployment config values."""
    return {key: field["default"] for key, field in DEPLOYMENT_FIELDS.items()}


def list_strategy_types() -> List[str]:
    """Return list of valid strategy types."""
    return list(STRATEGY_PARAMS.keys())


def get_strategy_description(strategy_type: str) -> Optional[str]:
    """Get description for a strategy type."""
    schema = STRATEGY_PARAMS.get(strategy_type)
    return schema.get("description") if schema else None
