#!/usr/bin/env python3
"""
Custom backtest runner for exp-004: YES Favorite NO Bias.

Tests the hypothesis that markets with YES > 0.55-0.95 resolve to NO
more often than implied, due to behavioral biases.

Usage:
    DATABASE_URL=postgresql://postgres:postgres@localhost:5433/polymarket_ml python3 experiments/exp-004/run_backtest.py
"""

import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Iterator

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import get_session
from src.backtest import (
    BacktestConfig,
    BacktestResult,
    HistoricalBet,
    run_backtest,
    format_backtest_summary,
    load_resolved_markets,
    load_price_snapshots,
    time_split_backtest,
    liquidity_split_backtest,
    category_split_backtest,
)
from src.backtest.data import HistoricalMarket, HistoricalPriceSnapshot


@dataclass
class VariantResult:
    """Results for a single variant."""
    id: str
    name: str
    params: Dict[str, Any]
    metrics: Dict[str, float]
    robustness: Dict[str, Any]
    kill_criteria: Dict[str, Any]
    passed_all: bool


def generate_filtered_bets(
    markets: List[HistoricalMarket],
    snapshots: List[HistoricalPriceSnapshot],
    yes_price_min: float = 0.55,
    yes_price_max: float = 0.95,
    hours_min: float = 12,
    hours_max: float = 168,
    min_volume: Optional[float] = None,
    min_liquidity: Optional[float] = None,
) -> Iterator[HistoricalBet]:
    """
    Generate NO bets for markets where YES price is in the target range.

    Filters:
    - YES price between yes_price_min and yes_price_max
    - Hours to close between hours_min and hours_max
    - Volume >= min_volume (if specified)
    - Liquidity >= min_liquidity (if specified)
    """
    # Group snapshots by market_id
    snapshots_by_market = {}
    for snap in snapshots:
        if snap.market_id not in snapshots_by_market:
            snapshots_by_market[snap.market_id] = []
        snapshots_by_market[snap.market_id].append(snap)

    bets_generated = 0
    markets_checked = 0

    for market in markets:
        if not market.is_resolved or not market.outcome:
            continue

        markets_checked += 1

        # Apply volume filter
        if min_volume and (market.volume is None or market.volume < min_volume):
            continue

        # Apply liquidity filter
        if min_liquidity and (market.liquidity is None or market.liquidity < min_liquidity):
            continue

        market_snapshots = snapshots_by_market.get(market.id, [])
        if not market_snapshots:
            continue

        # Sort snapshots by timestamp
        market_snapshots.sort(key=lambda s: s.timestamp)

        # Find snapshots within the time window
        valid_snapshots = []
        for snap in market_snapshots:
            hours_to_close = (market.close_date - snap.timestamp).total_seconds() / 3600

            # Check time window
            if hours_min <= hours_to_close <= hours_max:
                valid_snapshots.append((snap, hours_to_close))

        if not valid_snapshots:
            continue

        # Use the snapshot closest to the midpoint of the time window
        target_hours = (hours_min + hours_max) / 2
        best_snap, best_hours = min(valid_snapshots, key=lambda x: abs(x[1] - target_hours))

        # Check price filter (YES price in range)
        yes_price = best_snap.price
        if yes_price is None or yes_price <= 0 or yes_price >= 1:
            continue

        if not (yes_price_min <= yes_price <= yes_price_max):
            continue

        # Calculate NO entry price
        no_price = 1 - yes_price

        bets_generated += 1

        yield HistoricalBet(
            entry_ts=best_snap.timestamp,
            resolution_ts=market.resolved_at or market.close_date,
            market_id=market.id,
            condition_id=market.external_id,
            question=market.question,
            side="NO",
            entry_price=no_price,
            outcome=market.outcome,
            macro_category=market.macro_category,
            micro_category=market.micro_category,
            volume=market.volume,
        )


def run_variant_backtest(
    variant: Dict[str, Any],
    markets: List[HistoricalMarket],
    snapshots: List[HistoricalPriceSnapshot],
    config: BacktestConfig,
    kill_criteria: Dict[str, float],
) -> VariantResult:
    """Run backtest for a single variant."""

    # Extract variant params
    yes_price_min = variant.get("yes_price_min", 0.55)
    yes_price_max = variant.get("yes_price_max", 0.95)
    hours_min = variant.get("hours_min", 12)
    hours_max = variant.get("hours_max", 168)
    min_volume = variant.get("min_volume_24h")
    min_liquidity = variant.get("min_liquidity")

    # Generate filtered bets
    bets = list(generate_filtered_bets(
        markets=markets,
        snapshots=snapshots,
        yes_price_min=yes_price_min,
        yes_price_max=yes_price_max,
        hours_min=hours_min,
        hours_max=hours_max,
        min_volume=min_volume,
        min_liquidity=min_liquidity,
    ))

    if len(bets) < 10:
        # Not enough data
        return VariantResult(
            id=variant["id"],
            name=variant.get("name", variant["id"]),
            params={
                "yes_price_min": yes_price_min,
                "yes_price_max": yes_price_max,
                "hours_min": hours_min,
                "hours_max": hours_max,
                "min_volume_24h": min_volume,
                "min_liquidity": min_liquidity,
            },
            metrics={
                "sharpe": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_trades": len(bets),
                "total_pnl": 0,
                "max_drawdown_pct": 0,
            },
            robustness={
                "time_split": {"pass": False, "notes": "Insufficient data"},
                "liquidity_split": {"pass": False, "notes": "Insufficient data"},
            },
            kill_criteria={
                "sharpe": {"threshold": kill_criteria["sharpe"], "actual": 0, "pass": False},
                "win_rate": {"threshold": kill_criteria["win_rate"], "actual": 0, "pass": False},
                "trades": {"threshold": kill_criteria["trades"], "actual": len(bets), "pass": False},
                "profit_factor": {"threshold": kill_criteria["profit_factor"], "actual": 0, "pass": False},
            },
            passed_all=False,
        )

    # Run main backtest
    result = run_backtest(bets, config)

    # Calculate metrics
    metrics = {
        "sharpe": result.sharpe,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_trades": result.total_trades,
        "total_pnl": result.total_pnl,
        "max_drawdown_pct": result.max_drawdown_pct,
    }

    # Run robustness checks
    robustness = {}

    # Time split
    try:
        time_result = time_split_backtest(bets, config, min_trades_per_split=10)
        robustness["time_split"] = {
            "first_half": {
                "sharpe": time_result.first_half.sharpe,
                "win_rate": time_result.first_half.win_rate,
                "trades": time_result.first_half.trades,
                "pnl": time_result.first_half.total_pnl,
            },
            "second_half": {
                "sharpe": time_result.second_half.sharpe,
                "win_rate": time_result.second_half.win_rate,
                "trades": time_result.second_half.trades,
                "pnl": time_result.second_half.total_pnl,
            },
            "pass": time_result.passed,
            "notes": time_result.notes,
        }
    except Exception as e:
        robustness["time_split"] = {"pass": False, "notes": str(e)}

    # Liquidity split
    try:
        liq_result = liquidity_split_backtest(bets, config, min_trades_per_split=10)
        robustness["liquidity_split"] = {
            "high_liquidity": {
                "sharpe": liq_result.first_half.sharpe,
                "win_rate": liq_result.first_half.win_rate,
                "trades": liq_result.first_half.trades,
                "pnl": liq_result.first_half.total_pnl,
            },
            "low_liquidity": {
                "sharpe": liq_result.second_half.sharpe,
                "win_rate": liq_result.second_half.win_rate,
                "trades": liq_result.second_half.trades,
                "pnl": liq_result.second_half.total_pnl,
            },
            "pass": liq_result.passed,
            "notes": liq_result.notes,
        }
    except Exception as e:
        robustness["liquidity_split"] = {"pass": False, "notes": str(e)}

    # Check kill criteria
    kill_results = {
        "sharpe": {
            "threshold": kill_criteria["sharpe"],
            "actual": metrics["sharpe"],
            "pass": metrics["sharpe"] >= kill_criteria["sharpe"],
        },
        "win_rate": {
            "threshold": kill_criteria["win_rate"],
            "actual": metrics["win_rate"],
            "pass": metrics["win_rate"] >= kill_criteria["win_rate"],
        },
        "trades": {
            "threshold": kill_criteria["trades"],
            "actual": metrics["total_trades"],
            "pass": metrics["total_trades"] >= kill_criteria["trades"],
        },
        "profit_factor": {
            "threshold": kill_criteria["profit_factor"],
            "actual": metrics["profit_factor"],
            "pass": metrics["profit_factor"] >= kill_criteria["profit_factor"],
        },
        "time_split": {
            "pass": robustness.get("time_split", {}).get("pass", False),
        },
    }

    passed_all = all(k["pass"] for k in kill_results.values())

    return VariantResult(
        id=variant["id"],
        name=variant.get("name", variant["id"]),
        params={
            "yes_price_min": yes_price_min,
            "yes_price_max": yes_price_max,
            "hours_min": hours_min,
            "hours_max": hours_max,
            "min_volume_24h": min_volume,
            "min_liquidity": min_liquidity,
        },
        metrics=metrics,
        robustness=robustness,
        kill_criteria=kill_results,
        passed_all=passed_all,
    )


def main():
    # Load config
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config_data = yaml.safe_load(f)

    print("=" * 60)
    print(f"EXPERIMENT: {config_data['experiment_id']}")
    print("Hypothesis: YES > 0.55 markets resolve NO more often than implied")
    print("=" * 60)

    # Extract config sections
    backtest_config = config_data.get("backtest", {})
    kill_criteria = config_data.get("kill_criteria", {})
    variants = config_data.get("variants", [])

    # Create BacktestConfig
    bt_config = BacktestConfig(
        initial_capital=backtest_config.get("initial_capital", 1000),
        stake_per_bet=backtest_config.get("stake_per_bet", 10),
        stake_mode=backtest_config.get("stake_mode", "fixed"),
        cost_per_bet=backtest_config.get("cost_per_bet", 0),
        max_position_pct=backtest_config.get("max_position_pct", 0.25),
    )

    # Load historical data (all data, no date filter)
    print("\nLoading historical data...")

    with get_session() as db:
        # Load all resolved markets
        markets = load_resolved_markets(db=db)
        print(f"  Loaded {len(markets):,} resolved markets")

        # Load price snapshots
        market_ids = [m.id for m in markets]
        snapshots = load_price_snapshots(db=db, market_ids=market_ids)
        print(f"  Loaded {len(snapshots):,} price snapshots")

    # Run backtest for each variant
    results = []

    print(f"\nRunning {len(variants)} variants...")
    print("-" * 60)

    for i, variant in enumerate(variants):
        print(f"\n[{i+1}/{len(variants)}] Variant {variant['id']} ({variant.get('name', '')})...")

        result = run_variant_backtest(
            variant=variant,
            markets=markets,
            snapshots=snapshots,
            config=bt_config,
            kill_criteria=kill_criteria,
        )

        results.append(result)

        # Print summary
        status = "PASS" if result.passed_all else "FAIL"
        print(f"  Trades: {result.metrics['total_trades']}")
        print(f"  Sharpe: {result.metrics['sharpe']:.2f} | WR: {result.metrics['win_rate']:.1%} | PF: {result.metrics['profit_factor']:.2f}")
        print(f"  Time split: {'PASS' if result.robustness.get('time_split', {}).get('pass') else 'FAIL'}")
        print(f"  Status: {status}")

    # Find best variant
    passed_variants = [r for r in results if r.passed_all]
    if passed_variants:
        best = max(passed_variants, key=lambda r: r.metrics["sharpe"])
        best_variant = best.id
    else:
        best = max(results, key=lambda r: r.metrics["sharpe"])
        best_variant = f"{best.id} (did not pass all criteria)"

    # Generate results.json
    output = {
        "experiment_id": config_data["experiment_id"],
        "run_at": datetime.now(timezone.utc).isoformat(),
        "data_range": {
            "markets_tested": len(markets),
            "resolved_markets": len([m for m in markets if m.is_resolved]),
        },
        "variants": [
            {
                "id": r.id,
                "name": r.name,
                "params": r.params,
                "metrics": r.metrics,
                "robustness": r.robustness,
                "kill_criteria": r.kill_criteria,
                "passed_all": r.passed_all,
            }
            for r in results
        ],
        "best_variant": best_variant,
        "summary": {
            "total_variants": len(results),
            "passed_variants": len(passed_variants),
            "pass_rate": len(passed_variants) / len(results) if results else 0,
        },
    }

    # Save results
    results_path = Path(__file__).parent / "results.json"
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"\nVariants tested: {len(results)}")
    print(f"Variants passed: {len(passed_variants)}")
    print(f"Best variant: {best_variant}")

    if passed_variants:
        print("\nPassed variants:")
        for r in sorted(passed_variants, key=lambda x: -x.metrics["sharpe"]):
            print(f"  {r.id}: Sharpe={r.metrics['sharpe']:.2f}, WR={r.metrics['win_rate']:.1%}, Trades={r.metrics['total_trades']}")

    print(f"\nResults saved to: {results_path}")
    print(f"\nNext: /verdict exp-004")


if __name__ == "__main__":
    main()
