"""
Ship an experiment from Research Lab to strategies.yaml.

Reads experiment files (spec.md, config.yaml, results.json, verdict.md) and generates
a strategy entry that can be appended to strategies.yaml.

Usage:
    python -m cli.ship exp-001              # Preview YAML entry (best variant)
    python -m cli.ship exp-001:v3           # Preview specific variant
    python -m cli.ship exp-001 --apply      # Apply to strategies.yaml
    python -m cli.ship exp-001 --json       # Output as JSON
    python -m cli.ship --multi exp-001 exp-002:v3 exp-002:v6  # Multi-deploy
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import yaml

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
STRATEGIES_FILE = PROJECT_ROOT / "strategies.yaml"

# Try to import configs module for validation
try:
    from configs.paths import validate_strategy_params, DEPLOYMENT_FIELDS
    HAS_CONFIGS = True
except ImportError:
    HAS_CONFIGS = False


def parse_exp_arg(exp_arg: str) -> Tuple[str, Optional[str]]:
    """
    Parse experiment argument like 'exp-001' or 'exp-001:v3'.

    Returns:
        Tuple of (exp_id, variant_id or None)
    """
    if ":" in exp_arg:
        exp_id, variant_id = exp_arg.split(":", 1)
        return exp_id, variant_id
    return exp_arg, None


def load_experiment(exp_id: str) -> Dict[str, Any]:
    """Load all experiment files and parse into structured data."""
    exp_dir = EXPERIMENTS_DIR / exp_id

    if not exp_dir.exists():
        raise FileNotFoundError(f"Experiment directory not found: {exp_dir}")

    result = {
        "exp_id": exp_id,
        "spec": None,
        "config": None,
        "results": None,
        "verdict": None,
    }

    # Load spec.md
    spec_file = exp_dir / "spec.md"
    if spec_file.exists():
        result["spec"] = parse_spec_md(spec_file.read_text())
    else:
        raise FileNotFoundError(f"spec.md not found in {exp_dir}")

    # Load config.yaml (new - contains deployment settings)
    config_file = exp_dir / "config.yaml"
    if config_file.exists():
        with open(config_file) as f:
            result["config"] = yaml.safe_load(f)

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


def get_variant(exp: Dict, variant_id: Optional[str] = None) -> Optional[Dict]:
    """Get a specific variant from results, or the best variant if not specified."""
    results = exp.get("results", {})
    config = exp.get("config", {})

    # Try to get variants from results.json first, then config.yaml
    variants = results.get("variants", []) or config.get("variants", [])

    if not variants:
        return None

    if variant_id:
        # Look for specific variant
        for v in variants:
            if v.get("id") == variant_id:
                return v
        return None  # Variant not found

    # Return best variant or first
    best_id = results.get("best_variant", "v1")
    for v in variants:
        if v.get("id") == best_id:
            return v
    return variants[0]


def generate_yaml_entry(exp: Dict, variant_id: Optional[str] = None) -> str:
    """Generate YAML entry for strategies.yaml."""
    spec = exp["spec"]
    config = exp.get("config", {})
    results = exp.get("results", {})

    # Get the variant to deploy
    variant = get_variant(exp, variant_id)

    # Determine strategy type from config.yaml first, then spec
    strategy_type = config.get("strategy_type", spec.get("strategy_type", "no_bias"))

    # Get deployment config from config.yaml
    deployment = config.get("deployment", {})
    filters = config.get("filters", {})

    # Generate strategy name with experiment ID and variant
    base_name = generate_strategy_name(exp["exp_id"], spec)
    if variant and variant.get("id"):
        variant_suffix = f"_{exp['exp_id']}_{variant.get('id')}"
    else:
        variant_suffix = f"_{exp['exp_id']}"
    strategy_name = base_name + variant_suffix

    # Build YAML lines
    lines = []
    lines.append(f"  - name: {strategy_name}")

    # Category
    categories = filters.get("categories") or spec.get("categories")
    if categories and isinstance(categories, list) and len(categories) > 0:
        lines.append(f"    category: {categories[0]}")

    # Variant-specific params
    if variant:
        params = variant.get("params", {})
        # If params are at top level of variant dict (older format)
        if not params:
            params = {k: v for k, v in variant.items() if k not in ["id", "name", "metrics", "robustness", "kill_criteria"]}

        for key, value in params.items():
            if value is not None:
                lines.append(f"    {key}: {value}")

    # Strategy-type specific defaults (if not in variant params)
    if strategy_type == "no_bias" and not any("historical_no_rate" in l for l in lines):
        rate = 0.60
        if variant and variant.get("params", {}).get("historical_no_rate"):
            rate = variant["params"]["historical_no_rate"]
        lines.append(f"    historical_no_rate: {rate}")

    # Time window from filters or spec
    hours_min = filters.get("hours_min") or spec.get("hours_min")
    hours_max = filters.get("hours_max") or spec.get("hours_max")
    if hours_min is not None and not any("min_hours" in l for l in lines):
        lines.append(f"    min_hours: {hours_min}")
    if hours_max is not None and not any("max_hours" in l for l in lines):
        lines.append(f"    max_hours: {hours_max}")

    # Deployment config (from config.yaml)
    if deployment:
        if deployment.get("min_edge_after_spread") is not None and not any("min_edge_after_spread" in l for l in lines):
            lines.append(f"    min_edge_after_spread: {deployment['min_edge_after_spread']}")
        if deployment.get("order_type") and not any("order_type" in l for l in lines):
            lines.append(f"    order_type: {deployment['order_type']}")
        if deployment.get("size_pct") is not None and not any("size_pct" in l for l in lines):
            lines.append(f"    size_pct: {deployment['size_pct']}")
        if deployment.get("max_spread") is not None and not any("max_spread" in l for l in lines):
            lines.append(f"    max_spread: {deployment['max_spread']}")

    # Filters
    min_liquidity = filters.get("min_liquidity") or spec.get("min_liquidity")
    min_volume = filters.get("min_volume_24h") or spec.get("min_volume_24h")
    if min_liquidity and not any("min_liquidity" in l for l in lines):
        lines.append(f"    min_liquidity: {min_liquidity}")
    if min_volume and not any("min_volume" in l for l in lines):
        lines.append(f"    min_volume: {min_volume}")

    # Metadata (as comments)
    lines.append(f"    # Experiment: {exp['exp_id']}")
    if variant and variant.get("id"):
        lines.append(f"    # Variant: {variant.get('id')}")
    lines.append(f"    # Shipped: {datetime.now().strftime('%Y-%m-%d')}")

    # Include backtest metrics if available
    if variant and variant.get("metrics"):
        metrics = variant["metrics"]
        sharpe = metrics.get("sharpe", "N/A")
        win_rate = metrics.get("win_rate", "N/A")
        lines.append(f"    # Backtest: Sharpe={sharpe}, WR={win_rate}")

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
        "uncertain_zone": "uncertain_zone",
    }
    return type_map.get(strategy_type, "no_bias")


def get_strategy_type(exp: Dict) -> str:
    """Get strategy type from config.yaml or spec."""
    config = exp.get("config", {})
    spec = exp.get("spec", {})
    return config.get("strategy_type", spec.get("strategy_type", "no_bias"))


def apply_to_strategies_yaml(exp: Dict, yaml_entry: str) -> bool:
    """Append the strategy entry to strategies.yaml."""
    strategy_type = get_strategy_type(exp)
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
        print(f"Hint: Add '{section_name}:' section to strategies.yaml")
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


def create_wallet_entry(strategy_name: str, allocated_usd: float = 400.0) -> str:
    """Generate SQL to create wallet entry."""
    return f"""
INSERT INTO strategy_balances (strategy_name, allocated_usd, current_usd)
VALUES ('{strategy_name}', {allocated_usd}, {allocated_usd})
ON CONFLICT (strategy_name) DO UPDATE SET allocated_usd = {allocated_usd}, updated_at = NOW();
"""


def extract_strategy_name(yaml_entry: str) -> Optional[str]:
    """Extract strategy name from YAML entry."""
    match = re.search(r"name:\s*(\S+)", yaml_entry)
    return match.group(1) if match else None


def process_multi_deploy(exp_args: List[str], apply: bool = False) -> int:
    """
    Process multiple experiment deployments.

    Returns exit code (0 success, 1 failure).
    """
    deployments = []
    errors = []

    # Phase 1: Validate all experiments
    print(f"\n{'='*60}")
    print("MULTI-DEPLOY: Validating experiments...")
    print(f"{'='*60}\n")

    for exp_arg in exp_args:
        exp_id, variant_id = parse_exp_arg(exp_arg)

        try:
            exp = load_experiment(exp_id)
        except FileNotFoundError as e:
            errors.append(f"{exp_arg}: {e}")
            continue

        # Check verdict
        verdict = exp.get("verdict", {})
        decision = verdict.get("decision", "").upper()

        if decision != "SHIP":
            errors.append(f"{exp_arg}: Verdict is '{decision}', not SHIP")
            continue

        # Check variant exists if specified
        if variant_id:
            variant = get_variant(exp, variant_id)
            if not variant:
                errors.append(f"{exp_arg}: Variant '{variant_id}' not found")
                continue

        # Generate YAML entry
        yaml_entry = generate_yaml_entry(exp, variant_id)
        strategy_name = extract_strategy_name(yaml_entry)
        strategy_type = get_strategy_type(exp)

        deployments.append({
            "exp_arg": exp_arg,
            "exp_id": exp_id,
            "variant_id": variant_id,
            "exp": exp,
            "yaml_entry": yaml_entry,
            "strategy_name": strategy_name,
            "strategy_type": strategy_type,
        })

        print(f"  [OK] {exp_arg} -> {strategy_name}")

    if errors:
        print(f"\n{'='*60}")
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        print(f"\nAborting all deployments.")
        return 1

    # Phase 2: Show summary
    print(f"\n{'='*60}")
    print(f"DEPLOYING {len(deployments)} STRATEGIES:")
    print(f"{'='*60}")

    for d in deployments:
        print(f"\n{d['strategy_name']} (from {d['exp_arg']}):")
        print("-" * 40)
        print(d['yaml_entry'])

    if not apply:
        print(f"\n{'='*60}")
        print("PREVIEW ONLY - Use --apply to deploy")
        print(f"{'='*60}")
        return 0

    # Phase 3: Apply all deployments
    print(f"\n{'='*60}")
    print("APPLYING DEPLOYMENTS...")
    print(f"{'='*60}")

    wallet_sqls = []
    for d in deployments:
        if apply_to_strategies_yaml(d["exp"], d["yaml_entry"]):
            print(f"  [OK] {d['strategy_name']} added to strategies.yaml")
            # Get allocated_usd from deployment config
            deployment_config = d["exp"].get("config", {}).get("deployment", {})
            allocated_usd = deployment_config.get("allocated_usd", 400.0)
            wallet_sqls.append(create_wallet_entry(d["strategy_name"], allocated_usd))
        else:
            print(f"  [FAIL] {d['strategy_name']}")
            return 1

    # Phase 4: Output wallet SQL
    print(f"\n{'='*60}")
    print("WALLET ENTRIES (run in PostgreSQL):")
    print(f"{'='*60}")
    for sql in wallet_sqls:
        print(sql.strip())

    print(f"\n{'='*60}")
    print("DEPLOYMENT COMPLETE!")
    print(f"{'='*60}")
    print(f"\nDeployed {len(deployments)} strategies.")
    print("Executor will auto-reload within 30 seconds.")
    print("\nTo verify: python3 -m cli.deploy --list")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Ship an experiment to strategies.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m cli.ship exp-001              # Preview YAML entry (best variant)
  python -m cli.ship exp-001:v3           # Preview specific variant
  python -m cli.ship exp-001 --apply      # Apply to strategies.yaml
  python -m cli.ship exp-001 --json       # Output as JSON for parsing
  python -m cli.ship --multi exp-001 exp-002:v3 exp-002:v6  # Multi-deploy
  python -m cli.ship --multi exp-001 exp-002:v3 --apply     # Multi-deploy and apply
        """,
    )
    parser.add_argument("exp_ids", nargs="*", help="Experiment ID(s) (e.g., exp-001, exp-001:v3)")
    parser.add_argument("--multi", action="store_true", help="Multi-deploy mode")
    parser.add_argument("--apply", action="store_true", help="Apply to strategies.yaml")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--force", action="store_true", help="Skip SHIP verdict check")

    args = parser.parse_args()

    # Handle no arguments
    if not args.exp_ids:
        parser.print_help()
        sys.exit(1)

    # Multi-deploy mode
    if args.multi or len(args.exp_ids) > 1:
        sys.exit(process_multi_deploy(args.exp_ids, apply=args.apply))

    # Single experiment mode
    exp_arg = args.exp_ids[0]
    exp_id, variant_id = parse_exp_arg(exp_arg)

    try:
        exp = load_experiment(exp_id)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Check verdict
    verdict = exp.get("verdict", {})
    decision = verdict.get("decision", "").upper()

    if decision != "SHIP" and not args.force:
        print(f"Error: Experiment {exp_id} verdict is '{decision}', not SHIP")
        print("Use --force to override")
        sys.exit(1)

    # Check variant exists if specified
    if variant_id:
        variant = get_variant(exp, variant_id)
        if not variant:
            print(f"Error: Variant '{variant_id}' not found in {exp_id}")
            available = [v.get("id") for v in exp.get("results", {}).get("variants", [])]
            print(f"Available variants: {available}")
            sys.exit(1)

    # Generate YAML entry
    yaml_entry = generate_yaml_entry(exp, variant_id)
    strategy_type = get_strategy_type(exp)
    strategy_name = extract_strategy_name(yaml_entry)

    if args.json:
        variant = get_variant(exp, variant_id)
        output = {
            "exp_id": exp_id,
            "variant_id": variant_id,
            "strategy_type": strategy_type,
            "strategy_name": strategy_name,
            "yaml_entry": yaml_entry,
            "spec": exp["spec"],
            "config": exp.get("config"),
            "results_summary": {
                "best_variant": exp.get("results", {}).get("best_variant"),
                "metrics": variant.get("metrics", {}) if variant else {},
            },
        }
        print(json.dumps(output, indent=2))
    else:
        # Display preview
        print(f"\n{'='*60}")
        print(f"SHIPPING {exp_arg}")
        print(f"{'='*60}")
        print(f"\nHypothesis: {exp['spec'].get('hypothesis', 'N/A')}")
        print(f"Friction: {exp['spec'].get('friction_bucket', 'N/A')}")
        print(f"Strategy Type: {strategy_type}")
        print(f"Verdict: {decision}")

        variant = get_variant(exp, variant_id)
        if variant and variant.get("metrics"):
            metrics = variant["metrics"]
            print(f"\nVariant {variant.get('id', 'best')} Metrics:")
            print(f"  Sharpe: {metrics.get('sharpe', 'N/A')}")
            print(f"  Win Rate: {metrics.get('win_rate', 'N/A')}")
            print(f"  Trades: {metrics.get('total_trades', 'N/A')}")

        # Show deployment config if available
        deployment = exp.get("config", {}).get("deployment", {})
        if deployment:
            print(f"\nDeployment Config (from config.yaml):")
            print(f"  Allocated USD: ${deployment.get('allocated_usd', 400)}")
            print(f"  Order Type: {deployment.get('order_type', 'market')}")
            print(f"  Min Edge: {deployment.get('min_edge_after_spread', 0.03):.1%}")

        print(f"\nYAML Entry (for {strategy_type} section):")
        print("-" * 40)
        print(yaml_entry)
        print("-" * 40)

        if args.apply:
            print(f"\nApplying to {STRATEGIES_FILE}...")
            if apply_to_strategies_yaml(exp, yaml_entry):
                print("Success! Executor will auto-reload within 30 seconds.")

                # Output wallet SQL
                allocated_usd = deployment.get("allocated_usd", 400.0)
                print(f"\nTo create wallet entry, run:")
                print(create_wallet_entry(strategy_name, allocated_usd).strip())
            else:
                print("Failed to apply.")
                sys.exit(1)
        else:
            print(f"\nTo apply: python -m cli.ship {exp_arg} --apply")


if __name__ == "__main__":
    main()
