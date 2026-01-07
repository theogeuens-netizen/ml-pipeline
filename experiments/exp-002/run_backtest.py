#!/usr/bin/env python3
"""
Run comprehensive backtest for exp-002 variants.

Tests all 20 variants of the uncertain zone strategy with:
- Historical win rate calculation
- Sharpe, profit factor, drawdown metrics
- Time-split and liquidity-split robustness checks
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import math

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml
from sqlalchemy import text

from src.db.database import get_session
from src.backtest import (
    BacktestConfig,
    HistoricalBet,
    run_backtest,
    time_split_backtest,
    liquidity_split_backtest,
    category_split_backtest,
)


def load_historical_data_for_variant(
    db,
    min_hours: float,
    max_hours: float,
    yes_price_min: float,
    yes_price_max: float,
    min_volume: float,
    category: Optional[str] = None,
) -> List[HistoricalBet]:
    """
    Load betting opportunities for a specific variant configuration.

    This directly queries the historical_markets and historical_price_snapshots
    tables to find markets matching the uncertain zone criteria.
    """
    # Query to find resolved markets with snapshots in the uncertain zone
    query = text("""
        WITH market_snapshots AS (
            SELECT
                m.id as market_id,
                m.external_id,
                m.question,
                m.close_date,
                m.resolved_at,
                m.winner,
                m.macro_category,
                m.volume,
                s.timestamp as snap_time,
                s.price as yes_price,
                EXTRACT(EPOCH FROM (m.close_date - s.timestamp)) / 3600.0 as hours_to_close
            FROM historical_markets m
            JOIN historical_price_snapshots s ON s.market_id = m.id
            WHERE m.resolution_status = 'resolved'
            AND m.winner IN ('YES', 'NO', 'yes', 'no', 'Yes', 'No')
            AND m.volume >= :min_volume
            AND s.price >= :yes_price_min
            AND s.price <= :yes_price_max
            AND EXTRACT(EPOCH FROM (m.close_date - s.timestamp)) / 3600.0 >= :min_hours
            AND EXTRACT(EPOCH FROM (m.close_date - s.timestamp)) / 3600.0 <= :max_hours
            {category_filter}
        ),
        -- Get one snapshot per market (closest to target hours)
        ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY hours_to_close) as rn
            FROM market_snapshots
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY snap_time
    """.format(
        category_filter="AND m.macro_category = :category" if category else ""
    ))

    params = {
        "min_hours": min_hours,
        "max_hours": max_hours,
        "yes_price_min": yes_price_min,
        "yes_price_max": yes_price_max,
        "min_volume": min_volume,
    }
    if category:
        params["category"] = category

    results = db.execute(query, params).fetchall()

    bets = []
    for row in results:
        # Determine outcome
        winner = str(row.winner).upper().strip()
        if winner in ("YES", "Y", "TRUE", "1"):
            outcome = "YES"
        elif winner in ("NO", "N", "FALSE", "0"):
            outcome = "NO"
        else:
            continue

        # Entry price for NO side: 1 - yes_price
        yes_price = float(row.yes_price)
        no_price = 1 - yes_price

        if no_price <= 0 or no_price >= 1:
            continue

        bet = HistoricalBet(
            entry_ts=row.snap_time,
            resolution_ts=row.resolved_at or row.close_date,
            market_id=row.market_id,
            condition_id=row.external_id,
            question=row.question or "",
            side="NO",
            entry_price=no_price,
            outcome=outcome,
            macro_category=row.macro_category,
            volume=float(row.volume) if row.volume else None,
        )
        bets.append(bet)

    return bets


def calculate_historical_win_rate(bets: List[HistoricalBet]) -> Dict[str, Any]:
    """Calculate historical win rate and related stats."""
    if not bets:
        return {"win_rate": None, "total": 0, "wins": 0, "losses": 0}

    wins = sum(1 for b in bets if b.outcome == b.side)
    total = len(bets)

    return {
        "win_rate": wins / total if total > 0 else None,
        "total": total,
        "wins": wins,
        "losses": total - wins,
    }


def run_variant_backtest(
    db,
    variant: Dict[str, Any],
    config: BacktestConfig,
) -> Dict[str, Any]:
    """Run backtest for a single variant."""

    # Load bets for this variant
    bets = load_historical_data_for_variant(
        db=db,
        min_hours=variant.get("min_hours", 24),
        max_hours=variant.get("max_hours", 72),
        yes_price_min=variant.get("yes_price_min", 0.45),
        yes_price_max=variant.get("yes_price_max", 0.55),
        min_volume=variant.get("min_volume", 2000),
        category=variant.get("category"),
    )

    print(f"  Loaded {len(bets)} betting opportunities")

    if len(bets) < 10:
        return {
            "id": variant["id"],
            "name": variant.get("name", variant["id"]),
            "params": {k: v for k, v in variant.items() if k not in ("id", "name")},
            "metrics": {
                "sharpe": None,
                "win_rate": None,
                "profit_factor": None,
                "total_trades": len(bets),
                "total_pnl": 0,
                "max_drawdown_pct": 0,
            },
            "historical_wr": calculate_historical_win_rate(bets),
            "robustness": None,
            "kill_criteria": {
                "sharpe": {"threshold": 0.5, "actual": None, "pass": False},
                "win_rate": {"threshold": 0.52, "actual": None, "pass": False},
                "trades": {"threshold": 30, "actual": len(bets), "pass": False},
                "profit_factor": {"threshold": 1.15, "actual": None, "pass": False},
            },
            "status": "INSUFFICIENT_DATA",
        }

    # Run main backtest
    result = run_backtest(bets, config)
    m = result.metrics

    # Historical win rate
    hist_wr = calculate_historical_win_rate(bets)

    # Run robustness checks
    robustness = {}

    # Time split
    time_result = time_split_backtest(bets, config, min_trades_per_half=10)
    robustness["time_split"] = {
        "passed": time_result.passed,
        "first_half": {
            "sharpe": time_result.first_half.sharpe,
            "win_rate": time_result.first_half.win_rate,
            "trades": time_result.first_half.trades,
        },
        "second_half": {
            "sharpe": time_result.second_half.sharpe,
            "win_rate": time_result.second_half.win_rate,
            "trades": time_result.second_half.trades,
        },
    }

    # Liquidity split
    liq_result = liquidity_split_backtest(bets, config, min_trades_per_half=10)
    robustness["liquidity_split"] = {
        "passed": liq_result.passed,
        "high_volume": {
            "sharpe": liq_result.first_half.sharpe,
            "win_rate": liq_result.first_half.win_rate,
            "trades": liq_result.first_half.trades,
        },
        "low_volume": {
            "sharpe": liq_result.second_half.sharpe,
            "win_rate": liq_result.second_half.win_rate,
            "trades": liq_result.second_half.trades,
        },
    }

    # Check kill criteria
    kill_criteria = {
        "sharpe": {
            "threshold": 0.5,
            "actual": m.sharpe_ratio,
            "pass": m.sharpe_ratio is not None and m.sharpe_ratio >= 0.5,
        },
        "win_rate": {
            "threshold": 0.52,
            "actual": m.win_rate,
            "pass": m.win_rate is not None and m.win_rate >= 0.52,
        },
        "trades": {
            "threshold": 30,
            "actual": m.num_bets,
            "pass": m.num_bets >= 30,
        },
        "profit_factor": {
            "threshold": 1.15,
            "actual": m.profit_factor,
            "pass": m.profit_factor is not None and m.profit_factor >= 1.15,
        },
        "time_split": {
            "pass": time_result.passed,
        },
    }

    all_passed = all(v["pass"] for v in kill_criteria.values())

    return {
        "id": variant["id"],
        "name": variant.get("name", variant["id"]),
        "params": {k: v for k, v in variant.items() if k not in ("id", "name")},
        "metrics": {
            "sharpe": round(m.sharpe_ratio, 2) if m.sharpe_ratio else None,
            "win_rate": round(m.win_rate, 4) if m.win_rate else None,
            "profit_factor": round(m.profit_factor, 2) if m.profit_factor else None,
            "total_trades": m.num_bets,
            "total_pnl": round(m.total_pnl, 2),
            "max_drawdown_pct": round(m.max_drawdown_pct, 2) if m.max_drawdown_pct else 0,
        },
        "historical_wr": hist_wr,
        "robustness": robustness,
        "kill_criteria": kill_criteria,
        "status": "PASS" if all_passed else "FAIL",
    }


def main():
    # Load config
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config_data = yaml.safe_load(f)

    # Backtest config
    bt = config_data.get("backtest", {})
    backtest_config = BacktestConfig(
        initial_capital=bt.get("initial_capital", 1000),
        stake_per_bet=bt.get("stake_per_bet", 10),
        stake_mode=bt.get("stake_mode", "fixed"),
        cost_per_bet=bt.get("cost_per_bet", 0),
        max_position_pct=bt.get("max_position_pct", 0.25),
    )

    variants = config_data.get("variants", [])

    print("=" * 80)
    print("EXP-002: UNCERTAIN ZONE OPTIMIZATION BACKTEST")
    print("=" * 80)
    print(f"Testing {len(variants)} variants...")
    print()

    results = {
        "experiment_id": "exp-002",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "variants": [],
        "summary": {},
    }

    with get_session() as db:
        # Get data range
        data_range_query = text("""
            SELECT
                MIN(close_date) as min_date,
                MAX(close_date) as max_date,
                COUNT(*) as resolved_count
            FROM historical_markets
            WHERE resolution_status = 'resolved'
        """)
        range_result = db.execute(data_range_query).fetchone()

        results["data_range"] = {
            "start": range_result.min_date.isoformat() if range_result.min_date else None,
            "end": range_result.max_date.isoformat() if range_result.max_date else None,
            "resolved_markets": range_result.resolved_count,
        }

        print(f"Data range: {range_result.min_date} to {range_result.max_date}")
        print(f"Resolved markets: {range_result.resolved_count:,}")
        print()

        # Run each variant
        for i, variant in enumerate(variants, 1):
            print(f"[{i:2d}/{len(variants)}] Testing {variant['id']}: {variant.get('name', '')}...")

            try:
                variant_result = run_variant_backtest(db, variant, backtest_config)
                results["variants"].append(variant_result)

                # Print summary
                m = variant_result["metrics"]
                status = variant_result["status"]
                wr = variant_result["historical_wr"]

                sharpe_str = f"{m['sharpe']:.2f}" if m['sharpe'] else "N/A"
                wr_str = f"{m['win_rate']*100:.1f}%" if m['win_rate'] else "N/A"
                pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] else "N/A"
                hist_wr_str = f"{wr['win_rate']*100:.1f}%" if wr['win_rate'] else "N/A"

                print(f"       Sharpe={sharpe_str:>6} | WR={wr_str:>6} | PF={pf_str:>5} | "
                      f"Trades={m['total_trades']:>5} | HistWR={hist_wr_str:>6} | [{status}]")

            except Exception as e:
                print(f"       ERROR: {e}")
                results["variants"].append({
                    "id": variant["id"],
                    "name": variant.get("name", variant["id"]),
                    "error": str(e),
                    "status": "ERROR",
                })

    # Calculate summary
    passed = [v for v in results["variants"] if v.get("status") == "PASS"]
    failed = [v for v in results["variants"] if v.get("status") == "FAIL"]
    insufficient = [v for v in results["variants"] if v.get("status") == "INSUFFICIENT_DATA"]

    results["summary"] = {
        "total_variants": len(variants),
        "passed": len(passed),
        "failed": len(failed),
        "insufficient_data": len(insufficient),
        "pass_rate": len(passed) / len(variants) if variants else 0,
    }

    # Find best variant
    valid_variants = [v for v in results["variants"] if v.get("metrics", {}).get("sharpe") is not None]
    if valid_variants:
        best = max(valid_variants, key=lambda v: v["metrics"]["sharpe"])
        results["best_variant"] = best["id"]
        results["best_variant_reason"] = (
            f"Highest Sharpe ({best['metrics']['sharpe']:.2f}), "
            f"WR: {best['metrics']['win_rate']*100:.1f}%, "
            f"Trades: {best['metrics']['total_trades']}"
        )

    # Save results
    output_path = Path(__file__).parent / "results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Passed:            {len(passed)}/{len(variants)}")
    print(f"Failed:            {len(failed)}/{len(variants)}")
    print(f"Insufficient data: {len(insufficient)}/{len(variants)}")
    print()

    if results.get("best_variant"):
        print(f"Best variant: {results['best_variant']}")
        print(f"  {results['best_variant_reason']}")

    print()
    print(f"Results saved to: {output_path}")

    # Print detailed results table
    print()
    print("=" * 120)
    print(f"{'Variant':<20} {'Sharpe':>8} {'WinRate':>8} {'HistWR':>8} {'PF':>6} {'Trades':>7} {'P&L':>10} {'TimeSplit':>10} {'Status':>8}")
    print("-" * 120)

    for v in results["variants"]:
        if v.get("error"):
            print(f"{v['id']:<20} {'ERROR':<80}")
            continue

        m = v.get("metrics", {})
        wr = v.get("historical_wr", {})
        rob = v.get("robustness", {})

        sharpe = f"{m.get('sharpe', 0):.2f}" if m.get('sharpe') else "N/A"
        win_rate = f"{m.get('win_rate', 0)*100:.1f}%" if m.get('win_rate') else "N/A"
        hist_wr = f"{wr.get('win_rate', 0)*100:.1f}%" if wr.get('win_rate') else "N/A"
        pf = f"{m.get('profit_factor', 0):.2f}" if m.get('profit_factor') else "N/A"
        trades = m.get('total_trades', 0)
        pnl = f"${m.get('total_pnl', 0):.0f}"
        time_split = "PASS" if rob.get('time_split', {}).get('passed') else "FAIL"
        status = v.get('status', 'N/A')

        print(f"{v['id']:<20} {sharpe:>8} {win_rate:>8} {hist_wr:>8} {pf:>6} {trades:>7} {pnl:>10} {time_split:>10} {status:>8}")

    print("=" * 120)


if __name__ == "__main__":
    main()
