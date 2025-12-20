"""
Strategy management CLI.

Usage:
    python -m cli.deploy --list           # List all strategies
    python -m cli.deploy --validate       # Validate strategies.yaml

Note: With the config-driven approach, strategies are managed by editing
strategies.yaml directly. This CLI validates the configuration.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(
        description="Strategy management (config-driven)"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all strategies from strategies.yaml"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate strategies.yaml configuration"
    )
    args = parser.parse_args()

    if not args.list and not args.validate:
        args.list = True  # Default to list

    from strategies.loader import load_strategies, validate_config

    if args.validate:
        print("\nValidating strategies.yaml...")
        errors = validate_config()
        if errors:
            print("\nErrors found:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("Configuration valid!")
        print()

    if args.list:
        print(f"\n{'='*60}")
        print("STRATEGIES (from strategies.yaml)")
        print(f"{'='*60}\n")

        strategies = load_strategies()

        if not strategies:
            print("No strategies found in strategies.yaml")
            sys.exit(0)

        # Group by type
        by_type = {}
        for s in strategies:
            type_name = type(s).__name__
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(s)

        for type_name, strats in by_type.items():
            print(f"{type_name} ({len(strats)})")
            print("-" * 40)
            for s in strats:
                print(f"  {s.name}")
                # Show key parameters
                params = []
                for k in dir(s):
                    if k.startswith("_") or k in ("name", "version", "logger"):
                        continue
                    v = getattr(s, k, None)
                    if callable(v):
                        continue
                    if isinstance(v, (int, float, str, bool)):
                        if k in ("category", "side", "direction", "type"):
                            params.append(f"{k}={v}")
                if params:
                    print(f"    ({', '.join(params[:3])})")
            print()

        print(f"Total: {len(strategies)} strategies")
        print()


if __name__ == "__main__":
    main()
