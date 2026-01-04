"""
Config validation against DB schemas and strategy params.

Usage:
    python -m configs.validate experiments/exp-002/config.yaml
    python -m configs.validate experiments/exp-002/config.yaml --verbose
"""

import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import yaml

from configs.paths import (
    STRATEGY_PARAMS,
    DEPLOYMENT_FIELDS,
    validate_strategy_params,
    validate_deployment_config,
)


def validate_yaml_syntax(config_path: Path) -> Tuple[Optional[Dict], List[str]]:
    """
    Validate YAML syntax and load config.

    Returns:
        Tuple of (config dict or None, list of errors)
    """
    errors = []

    if not config_path.exists():
        return None, [f"Config file not found: {config_path}"]

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return None, [f"Invalid YAML syntax: {e}"]

    if not isinstance(config, dict):
        return None, ["Config must be a YAML dictionary"]

    return config, errors


def validate_required_fields(config: Dict[str, Any]) -> List[str]:
    """Check required top-level fields are present."""
    errors = []

    required_fields = ["experiment_id", "strategy_type"]
    for field in required_fields:
        if field not in config:
            errors.append(f"Missing required field: {field}")

    return errors


def validate_strategy_type(config: Dict[str, Any]) -> List[str]:
    """Validate strategy type exists."""
    errors = []

    strategy_type = config.get("strategy_type")
    if strategy_type and strategy_type not in STRATEGY_PARAMS:
        valid_types = list(STRATEGY_PARAMS.keys())
        errors.append(f"Unknown strategy_type: '{strategy_type}'. Valid types: {valid_types}")

    return errors


def validate_variants(config: Dict[str, Any]) -> List[str]:
    """Validate variant configurations against strategy type."""
    errors = []

    strategy_type = config.get("strategy_type")
    if not strategy_type or strategy_type not in STRATEGY_PARAMS:
        return errors  # Can't validate variants without valid strategy type

    variants = config.get("variants", [])
    if not isinstance(variants, list):
        return [f"'variants' must be a list, got {type(variants).__name__}"]

    schema = STRATEGY_PARAMS[strategy_type]
    known_params = set(schema.get("required", [])) | set(schema.get("optional", {}).keys())

    for i, variant in enumerate(variants):
        if not isinstance(variant, dict):
            errors.append(f"Variant {i}: must be a dictionary")
            continue

        variant_id = variant.get("id", f"variant_{i}")

        # Check for unknown params (excluding id, name which are meta-fields)
        for key in variant:
            if key in ["id", "name", "params"]:
                continue
            if key not in known_params:
                errors.append(f"Variant '{variant_id}': unknown param '{key}' for {strategy_type}")

        # If variant has nested params dict, validate those too
        if "params" in variant and isinstance(variant["params"], dict):
            for key in variant["params"]:
                if key not in known_params:
                    errors.append(f"Variant '{variant_id}': unknown param '{key}' for {strategy_type}")

    return errors


def validate_backtest_section(config: Dict[str, Any]) -> List[str]:
    """Validate backtest configuration."""
    errors = []

    backtest = config.get("backtest", {})
    if not isinstance(backtest, dict):
        return [f"'backtest' must be a dictionary, got {type(backtest).__name__}"]

    # Validate stake_mode
    valid_stake_modes = ["fixed", "kelly", "half_kelly", "fixed_pct"]
    stake_mode = backtest.get("stake_mode")
    if stake_mode and stake_mode not in valid_stake_modes:
        errors.append(f"Invalid stake_mode: '{stake_mode}'. Valid modes: {valid_stake_modes}")

    # Validate numeric fields
    numeric_fields = ["initial_capital", "stake_per_bet", "cost_per_bet", "max_position_pct"]
    for field in numeric_fields:
        value = backtest.get(field)
        if value is not None and not isinstance(value, (int, float)):
            errors.append(f"backtest.{field} must be a number, got {type(value).__name__}")

    return errors


def validate_deployment_section(config: Dict[str, Any]) -> List[str]:
    """Validate deployment configuration."""
    errors = []

    deployment = config.get("deployment", {})
    if not isinstance(deployment, dict):
        return [f"'deployment' must be a dictionary, got {type(deployment).__name__}"]

    # Use the helper from paths.py
    errors.extend(validate_deployment_config(deployment))

    return errors


def validate_filters_section(config: Dict[str, Any]) -> List[str]:
    """Validate filters configuration."""
    errors = []

    filters = config.get("filters", {})
    if not isinstance(filters, dict):
        return [f"'filters' must be a dictionary, got {type(filters).__name__}"]

    # Validate categories is a list or null
    categories = filters.get("categories")
    if categories is not None and not isinstance(categories, list):
        errors.append(f"filters.categories must be a list or null, got {type(categories).__name__}")

    # Validate numeric fields
    numeric_fields = ["min_volume_24h", "min_liquidity", "hours_min", "hours_max"]
    for field in numeric_fields:
        value = filters.get(field)
        if value is not None and not isinstance(value, (int, float)):
            errors.append(f"filters.{field} must be a number, got {type(value).__name__}")

    # Handle nested hours_to_expiry format
    hours_to_expiry = filters.get("hours_to_expiry")
    if hours_to_expiry is not None:
        if not isinstance(hours_to_expiry, dict):
            errors.append(f"filters.hours_to_expiry must be a dictionary")
        else:
            for key in ["min", "max"]:
                value = hours_to_expiry.get(key)
                if value is not None and not isinstance(value, (int, float)):
                    errors.append(f"filters.hours_to_expiry.{key} must be a number")

    return errors


def validate_robustness_section(config: Dict[str, Any]) -> List[str]:
    """Validate robustness configuration."""
    errors = []

    robustness = config.get("robustness", {})
    if not isinstance(robustness, dict):
        return [f"'robustness' must be a dictionary, got {type(robustness).__name__}"]

    # Validate boolean fields
    bool_fields = ["time_split", "liquidity_split", "category_split"]
    for field in bool_fields:
        value = robustness.get(field)
        if value is not None and not isinstance(value, bool):
            errors.append(f"robustness.{field} must be a boolean, got {type(value).__name__}")

    return errors


def validate_kill_criteria_section(config: Dict[str, Any]) -> List[str]:
    """Validate kill criteria configuration."""
    errors = []

    kill_criteria = config.get("kill_criteria", {})
    if not isinstance(kill_criteria, dict):
        return [f"'kill_criteria' must be a dictionary, got {type(kill_criteria).__name__}"]

    # Validate numeric fields
    numeric_fields = {"sharpe": float, "win_rate": float, "trades": int, "profit_factor": float}
    for field, expected_type in numeric_fields.items():
        value = kill_criteria.get(field)
        if value is not None and not isinstance(value, (int, float)):
            errors.append(f"kill_criteria.{field} must be a number, got {type(value).__name__}")

    return errors


def validate_config(config_path: Path, verbose: bool = False) -> List[str]:
    """
    Validate experiment config.yaml.

    Checks:
    1. YAML syntax
    2. Required fields present
    3. Strategy type exists in STRATEGY_PARAMS
    4. Each variant's params match constructor
    5. Deployment fields are valid
    6. Backtest, filters, robustness sections are valid

    Args:
        config_path: Path to config.yaml
        verbose: Print detailed validation info

    Returns:
        List of error messages (empty if valid)
    """
    all_errors = []

    if verbose:
        print(f"Validating: {config_path}")

    # Step 1: YAML syntax
    config, errors = validate_yaml_syntax(config_path)
    if errors:
        return errors
    all_errors.extend(errors)

    if verbose:
        print(f"  YAML syntax: OK")

    # Step 2: Required fields
    errors = validate_required_fields(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Required fields: OK")

    # Step 3: Strategy type
    errors = validate_strategy_type(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Strategy type: OK ({config.get('strategy_type')})")

    # Step 4: Variants
    errors = validate_variants(config)
    all_errors.extend(errors)

    if verbose and not errors:
        variant_count = len(config.get("variants", []))
        print(f"  Variants: OK ({variant_count} variants)")

    # Step 5: Backtest section
    errors = validate_backtest_section(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Backtest config: OK")

    # Step 6: Deployment section
    errors = validate_deployment_section(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Deployment config: OK")

    # Step 7: Filters section
    errors = validate_filters_section(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Filters config: OK")

    # Step 8: Robustness section
    errors = validate_robustness_section(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Robustness config: OK")

    # Step 9: Kill criteria section
    errors = validate_kill_criteria_section(config)
    all_errors.extend(errors)

    if verbose and not errors:
        print(f"  Kill criteria: OK")

    return all_errors


def main():
    parser = argparse.ArgumentParser(
        description="Validate experiment config.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m configs.validate experiments/exp-002/config.yaml
    python -m configs.validate experiments/exp-002/config.yaml --verbose

Exit codes:
    0 - Validation passed
    1 - Validation failed
        """,
    )
    parser.add_argument("config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    errors = validate_config(Path(args.config), verbose=args.verbose)

    if errors:
        print("\nValidation FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\nValidation PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
