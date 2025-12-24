"""
Ship an experiment from Research Lab to strategies.yaml.

Reads experiment files (spec.md, results.json, verdict.md) and generates
a strategy entry that can be appended to strategies.yaml.

Usage:
    python -m cli.ship exp-001              # Preview YAML entry
    python -m cli.ship exp-001 --apply      # Apply to strategies.yaml
    python -m cli.ship exp-001 --json       # Output as JSON
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
STRATEGIES_FILE = PROJECT_ROOT / "strategies.yaml"


def load_experiment(exp_id: str) -> Dict[str, Any]:
    """Load all experiment files and parse into structured data."""
    exp_dir = EXPERIMENTS_DIR / exp_id

    if not exp_dir.exists():
        raise FileNotFoundError(f"Experiment directory not found: {exp_dir}")

    result = {
        "exp_id": exp_id,
        "spec": None,
        "results": None,
        "verdict": None,
    }

    # Load spec.md
    spec_file = exp_dir / "spec.md"
    if spec_file.exists():
        result["spec"] = parse_spec_md(spec_file.read_text())
    else:
        raise FileNotFoundError(f"spec.md not found in {exp_dir}")

    # Load results.json
    results_file = exp_dir / "results.json"
    if results_file.exists():
        result["results"] = json.loads(results_file.read_text())

    # Load verdict.md
    verdict_file = exp_dir / "verdict.md"
    if verdict_file.exists():
        result["verdict"] = parse_verdict_md(verdict_file.read_text())

    return result


def parse_spec_md(content: str) -> Dict[str, Any]:
    """Parse spec.md content into structured data."""
    spec = {
        "friction_bucket": None,
        "hypothesis": None,
        "categories": [],
        "min_volume_24h": None,
        "min_liquidity": None,
        "hours_min": None,
        "hours_max": None,
        "side": "NO",
        "strategy_type": "no_bias",
    }

    # Extract friction bucket
    match = re.search(r"##\s*Friction Bucket\s*\n+(\w+)", content, re.IGNORECASE)
    if match:
        spec["friction_bucket"] = match.group(1).lower()

    # Extract hypothesis
    match = re.search(r"##\s*Hypothesis\s*\n+(.+?)(?=\n##|\Z)", content, re.DOTALL)
    if match:
        spec["hypothesis"] = match.group(1).strip().split("\n")[0]

    # Extract universe filter
    filter_match = re.search(r"##\s*Universe Filter\s*\n(.+?)(?=\n##|\Z)", content, re.DOTALL)
    if filter_match:
        filter_text = filter_match.group(1)

        # Categories
        cat_match = re.search(r"Categories[:\s]*\[?([^\]\n]+)", filter_text, re.IGNORECASE)
        if cat_match:
            cats = cat_match.group(1).strip("[] ")
            spec["categories"] = [c.strip().strip('"\'') for c in cats.split(",")]

        # Volume
        vol_match = re.search(r"volume[^:]*:\s*(\d+)", filter_text, re.IGNORECASE)
        if vol_match:
            spec["min_volume_24h"] = int(vol_match.group(1))

        # Liquidity
        liq_match = re.search(r"liquidity[^:]*:\s*(\d+)", filter_text, re.IGNORECASE)
        if liq_match:
            spec["min_liquidity"] = int(liq_match.group(1))

        # Hours
        hours_match = re.search(r"expiry[^:]*:\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", filter_text, re.IGNORECASE)
        if hours_match:
            spec["hours_min"] = float(hours_match.group(1))
            spec["hours_max"] = float(hours_match.group(2))

    # Detect side from hypothesis
    if "YES" in spec.get("hypothesis", "").upper():
        spec["side"] = "YES"

    # Detect strategy type from hypothesis
    hypothesis_lower = spec.get("hypothesis", "").lower()
    if "mean reversion" in hypothesis_lower or "revert" in hypothesis_lower:
        spec["strategy_type"] = "mean_reversion"
    elif "whale" in hypothesis_lower or "fade" in hypothesis_lower:
        spec["strategy_type"] = "whale_fade"
    elif "flow" in hypothesis_lower or "volume" in hypothesis_lower:
        spec["strategy_type"] = "flow"
    elif "longshot" in hypothesis_lower or "high probability" in hypothesis_lower:
        spec["strategy_type"] = "longshot"
    elif "new market" in hypothesis_lower:
        spec["strategy_type"] = "new_market"

    return spec


def parse_verdict_md(content: str) -> Dict[str, Any]:
    """Parse verdict.md content into structured data."""
    verdict = {
        "decision": None,
        "reasoning": None,
    }

    # Extract decision
    match = re.search(r"##\s*Decision[:\s]*(\w+)", content, re.IGNORECASE)
    if match:
        verdict["decision"] = match.group(1).upper()

    # Extract reasoning
    match = re.search(r"##\s*Reasoning\s*\n+(.+?)(?=\n##|\Z)", content, re.DOTALL)
    if match:
        verdict["reasoning"] = match.group(1).strip()

    return verdict


def generate_strategy_name(exp_id: str, spec: Dict) -> str:
    """Generate a strategy name from experiment spec."""
    parts = []

    # Category
    if spec.get("categories"):
        parts.append(spec["categories"][0].lower())

    # Side
    parts.append(spec.get("side", "no").lower())

    # Time window
    hours_max = spec.get("hours_max")
    if hours_max:
        if hours_max <= 1:
            parts.append("1h")
        elif hours_max <= 4:
            parts.append("4h")
        elif hours_max <= 24:
            parts.append("24h")
        elif hours_max <= 168:
            parts.append("7d")
        else:
            parts.append(f"{int(hours_max)}h")

    return "_".join(parts)


def generate_yaml_entry(exp: Dict) -> str:
    """Generate YAML entry for strategies.yaml."""
    spec = exp["spec"]
    results = exp.get("results", {})

    # Get best variant parameters
    best_variant = None
    if results and results.get("variants"):
        best_id = results.get("best_variant", "v1")
        for v in results["variants"]:
            if v.get("id") == best_id:
                best_variant = v
                break
        if not best_variant:
            best_variant = results["variants"][0]

    # Generate strategy name
    strategy_name = generate_strategy_name(exp["exp_id"], spec)

    # Build YAML lines
    lines = []
    lines.append(f"  - name: {strategy_name}")

    # Category
    if spec.get("categories"):
        lines.append(f"    category: {spec['categories'][0]}")

    # Strategy-type specific params
    strategy_type = spec.get("strategy_type", "no_bias")

    if strategy_type == "no_bias":
        # Extract historical_no_rate from best variant or default
        rate = 0.60
        if best_variant and best_variant.get("params"):
            rate = best_variant["params"].get("historical_no_rate",
                   best_variant["params"].get("threshold", 0.60))
        lines.append(f"    historical_no_rate: {rate}")

    # Time window
    if spec.get("hours_min") is not None:
        lines.append(f"    min_hours: {spec['hours_min']}")
    if spec.get("hours_max") is not None:
        lines.append(f"    max_hours: {spec['hours_max']}")

    # Filters
    if spec.get("min_liquidity"):
        lines.append(f"    min_liquidity: {spec['min_liquidity']}")
    if spec.get("min_volume_24h"):
        lines.append(f"    min_volume: {spec['min_volume_24h']}")

    # Metadata
    lines.append(f"    # Experiment: {exp['exp_id']}")
    lines.append(f"    # Shipped: {datetime.now().strftime('%Y-%m-%d')}")

    return "\n".join(lines)


def get_strategy_type_section(strategy_type: str) -> str:
    """Get the YAML section name for a strategy type."""
    type_map = {
        "no_bias": "no_bias",
        "longshot": "longshot",
        "mean_reversion": "mean_reversion",
        "whale_fade": "whale_fade",
        "flow": "flow",
        "new_market": "new_market",
    }
    return type_map.get(strategy_type, "no_bias")


def apply_to_strategies_yaml(exp: Dict, yaml_entry: str) -> bool:
    """Append the strategy entry to strategies.yaml."""
    strategy_type = exp["spec"].get("strategy_type", "no_bias")
    section_name = get_strategy_type_section(strategy_type)

    if not STRATEGIES_FILE.exists():
        print(f"Error: {STRATEGIES_FILE} not found")
        return False

    content = STRATEGIES_FILE.read_text()

    # Find the section and append
    section_pattern = rf"^{section_name}:\s*$"
    match = re.search(section_pattern, content, re.MULTILINE)

    if not match:
        print(f"Error: Section '{section_name}:' not found in strategies.yaml")
        return False

    # Find the end of this section (next section or EOF)
    section_start = match.end()
    next_section = re.search(r"^\w+:\s*$", content[section_start:], re.MULTILINE)

    if next_section:
        insert_pos = section_start + next_section.start()
        # Insert before the next section with proper spacing
        new_content = content[:insert_pos] + "\n" + yaml_entry + "\n\n" + content[insert_pos:]
    else:
        # Append at end
        new_content = content.rstrip() + "\n\n" + yaml_entry + "\n"

    STRATEGIES_FILE.write_text(new_content)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Ship an experiment to strategies.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m cli.ship exp-001              # Preview YAML entry
  python -m cli.ship exp-001 --apply      # Apply to strategies.yaml
  python -m cli.ship exp-001 --json       # Output as JSON for parsing
        """,
    )
    parser.add_argument("exp_id", help="Experiment ID (e.g., exp-001)")
    parser.add_argument("--apply", action="store_true", help="Apply to strategies.yaml")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--force", action="store_true", help="Skip SHIP verdict check")

    args = parser.parse_args()

    try:
        exp = load_experiment(args.exp_id)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Check verdict
    verdict = exp.get("verdict", {})
    decision = verdict.get("decision", "").upper()

    if decision != "SHIP" and not args.force:
        print(f"Error: Experiment {args.exp_id} verdict is '{decision}', not SHIP")
        print("Use --force to override")
        sys.exit(1)

    # Generate YAML entry
    yaml_entry = generate_yaml_entry(exp)
    strategy_type = exp["spec"].get("strategy_type", "no_bias")

    if args.json:
        output = {
            "exp_id": args.exp_id,
            "strategy_type": strategy_type,
            "yaml_entry": yaml_entry,
            "spec": exp["spec"],
            "results_summary": {
                "best_variant": exp.get("results", {}).get("best_variant"),
                "metrics": exp.get("results", {}).get("variants", [{}])[0].get("metrics", {}),
            },
        }
        print(json.dumps(output, indent=2))
    else:
        # Display preview
        print(f"\n{'='*60}")
        print(f"SHIPPING {args.exp_id}")
        print(f"{'='*60}")
        print(f"\nHypothesis: {exp['spec'].get('hypothesis', 'N/A')}")
        print(f"Friction: {exp['spec'].get('friction_bucket', 'N/A')}")
        print(f"Strategy Type: {strategy_type}")
        print(f"Verdict: {decision}")

        if exp.get("results"):
            results = exp["results"]
            if results.get("variants"):
                best = results["variants"][0]
                metrics = best.get("metrics", {})
                print(f"\nBest Variant Metrics:")
                print(f"  Sharpe: {metrics.get('sharpe', 'N/A')}")
                print(f"  Win Rate: {metrics.get('win_rate', 'N/A')}")
                print(f"  Trades: {metrics.get('total_trades', 'N/A')}")

        print(f"\nYAML Entry (for {strategy_type} section):")
        print("-" * 40)
        print(yaml_entry)
        print("-" * 40)

        if args.apply:
            print(f"\nApplying to {STRATEGIES_FILE}...")
            if apply_to_strategies_yaml(exp, yaml_entry):
                print("Success! Executor will auto-reload within 30 seconds.")
            else:
                print("Failed to apply.")
                sys.exit(1)
        else:
            print(f"\nTo apply: python -m cli.ship {args.exp_id} --apply")


if __name__ == "__main__":
    main()
