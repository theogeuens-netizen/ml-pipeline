"""
Deploy a strategy for live/paper execution.

Usage:
    python -m cli.deploy strategies/longshot_yes_v1.py
    python -m cli.deploy strategies/longshot_yes_v1.py --disable
    python -m cli.deploy --list

Validates the strategy and adds it to deployed_strategies.yaml
with SHA for version tracking.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import load_strategy

CONFIG_PATH = Path(__file__).parent.parent / "deployed_strategies.yaml"


def load_config() -> dict:
    """Load deployed strategies config."""
    if not CONFIG_PATH.exists():
        return {"strategies": []}

    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {"strategies": []}


def save_config(config: dict):
    """Save deployed strategies config."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def main():
    parser = argparse.ArgumentParser(
        description="Deploy a strategy for execution"
    )
    parser.add_argument(
        "strategy",
        nargs="?",
        help="Path to strategy file (e.g., strategies/longshot_yes_v1.py)"
    )
    parser.add_argument(
        "--disable",
        action="store_true",
        help="Disable the strategy instead of enabling"
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the strategy from deployment"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all deployed strategies"
    )
    args = parser.parse_args()

    config = load_config()
    strategies = config.get("strategies", [])

    # List mode
    if args.list:
        print(f"\n{'='*60}")
        print("DEPLOYED STRATEGIES")
        print(f"{'='*60}\n")

        if not strategies:
            print("No strategies deployed.")
            print("Use: python -m cli.deploy <strategy_file>")
        else:
            for s in strategies:
                status = "ENABLED" if s.get("enabled") else "DISABLED"
                print(f"[{status}] {s.get('path')}")
                print(f"    SHA: {s.get('sha')}")
                print(f"    Deployed: {s.get('deployed_at')}")
                print()

        sys.exit(0)

    # Need strategy path for other operations
    if not args.strategy:
        parser.print_help()
        sys.exit(1)

    strategy_path = args.strategy

    # Normalize path
    if not strategy_path.startswith("strategies/"):
        if strategy_path.startswith("./"):
            strategy_path = strategy_path[2:]

    # Find existing entry
    existing_idx = None
    for i, s in enumerate(strategies):
        if s.get("path") == strategy_path:
            existing_idx = i
            break

    # Remove mode
    if args.remove:
        if existing_idx is not None:
            removed = strategies.pop(existing_idx)
            config["strategies"] = strategies
            save_config(config)
            print(f"Removed: {removed.get('path')}")
        else:
            print(f"Strategy not found in deployment: {strategy_path}")
            sys.exit(1)
        sys.exit(0)

    # Load and validate strategy
    strategy = load_strategy(strategy_path)
    if not strategy:
        print(f"Error: Failed to load strategy from {strategy_path}")
        sys.exit(1)

    # Get strategy info
    sha = strategy.get_sha()
    now = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*60}")
    print(f"Deploying: {strategy.name} v{strategy.version}")
    print(f"SHA: {sha}")
    print(f"Parameters:")
    for key, value in strategy.get_params().items():
        print(f"  {key}: {value}")
    print(f"{'='*60}\n")

    # Create or update entry
    entry = {
        "path": strategy_path,
        "enabled": not args.disable,
        "sha": sha,
        "deployed_at": now,
        "name": strategy.name,
        "version": strategy.version,
    }

    if existing_idx is not None:
        old_sha = strategies[existing_idx].get("sha")
        if old_sha != sha:
            print(f"Strategy updated: SHA {old_sha} -> {sha}")
        strategies[existing_idx] = entry
        action = "Updated"
    else:
        strategies.append(entry)
        action = "Added"

    config["strategies"] = strategies
    save_config(config)

    status = "enabled" if not args.disable else "disabled"
    print(f"{action}: {strategy_path} ({status})")
    print(f"Config saved to: {CONFIG_PATH}")


if __name__ == "__main__":
    main()
