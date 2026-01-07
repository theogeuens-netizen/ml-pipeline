"""
Configuration module for experiment pipeline.

Provides:
- Path registry (paths.py)
- Schema definitions (schemas.py)
- Config validation (validate.py)
"""

from configs.paths import (
    PROJECT_ROOT,
    PATHS,
    DB_TABLES,
    STRATEGY_PARAMS,
    get_experiment_path,
    get_experiment_files,
    validate_strategy_params,
)

from configs.schemas import (
    BacktestConfig,
    DeploymentConfig,
    FilterConfig,
    VariantConfig,
    ExperimentConfig,
)

__all__ = [
    # Paths
    "PROJECT_ROOT",
    "PATHS",
    "DB_TABLES",
    "STRATEGY_PARAMS",
    "get_experiment_path",
    "get_experiment_files",
    "validate_strategy_params",
    # Schemas
    "BacktestConfig",
    "DeploymentConfig",
    "FilterConfig",
    "VariantConfig",
    "ExperimentConfig",
]
