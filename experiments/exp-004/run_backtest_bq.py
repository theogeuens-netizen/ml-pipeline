#!/usr/bin/env python3
"""
BigQuery-based backtest for exp-004: YES Favorite NO Bias.

Runs all filtering and aggregation in BigQuery, returning only metrics.

Usage:
    python3 experiments/exp-004/run_backtest_bq.py
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import yaml
from google.cloud import bigquery

# BigQuery config
PROJECT_ID = "polymarket-ml"
DATASET = "longshot"
MARKETS_TABLE = f"{PROJECT_ID}.{DATASET}.historical_markets"
SNAPSHOTS_TABLE = f"{PROJECT_ID}.{DATASET}.historical_snapshots"


def run_variant_query(
    client: bigquery.Client,
    yes_price_min: float,
    yes_price_max: float,
    hours_min: float,
    hours_max: float,
    min_volume: Optional[float] = None,
    min_liquidity: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run a single BigQuery to get backtest metrics for a variant.

    Returns aggregated metrics without loading raw data.
    """

    # Build volume filter (liquidity data is all 0, skip it)
    volume_filter = f"AND m.volume >= {min_volume}" if min_volume else ""
    liquidity_filter = ""  # No liquidity data available

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.winner,
            m.macro_category,
            m.volume as market_volume,
            s.price as yes_price,
            (1 - s.price) as no_entry_price,
            -- Calculate hours to close (timestamps are in nanoseconds)
            (m.close_date - s.timestamp) / 1e9 / 3600.0 as hours_to_close,
            -- Win/loss for NO bet
            CASE
                WHEN m.winner = "No" THEN 1
                ELSE 0
            END as won,
            -- P&L for NO bet: win pays (1 - entry_price), loss pays -entry_price
            CASE
                WHEN m.winner = "No" THEN (1 - (1 - s.price))  -- = yes_price
                ELSE -(1 - s.price)  -- = -no_price
            END as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {(hours_min + hours_max) / 2})
            ) as rn
        FROM `{MARKETS_TABLE}` m
        JOIN `{SNAPSHOTS_TABLE}` s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price > 0
            AND s.price < 1
            -- Price filter (YES price in range)
            AND s.price >= {yes_price_min}
            AND s.price <= {yes_price_max}
            -- Time filter (timestamps in nanoseconds)
            AND (m.close_date - s.timestamp) / 1e9 / 3600.0 >= {hours_min}
            AND (m.close_date - s.timestamp) / 1e9 / 3600.0 <= {hours_max}
            {volume_filter}
            {liquidity_filter}
    ),
    -- Take one bet per market (closest to midpoint of time window)
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    ),
    -- Calculate metrics
    metrics AS (
        SELECT
            COUNT(*) as total_trades,
            SUM(won) as wins,
            SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) as losses,
            AVG(won) as win_rate,
            SUM(pnl_per_dollar) as total_pnl,
            AVG(pnl_per_dollar) as avg_pnl,
            STDDEV(pnl_per_dollar) as std_pnl,
            SUM(CASE WHEN pnl_per_dollar > 0 THEN pnl_per_dollar ELSE 0 END) as gross_profit,
            SUM(CASE WHEN pnl_per_dollar < 0 THEN ABS(pnl_per_dollar) ELSE 0 END) as gross_loss
        FROM unique_bets
    )
    SELECT
        total_trades,
        wins,
        losses,
        win_rate,
        total_pnl,
        avg_pnl,
        std_pnl,
        gross_profit,
        gross_loss,
        CASE WHEN gross_loss > 0 THEN gross_profit / gross_loss ELSE 0 END as profit_factor,
        CASE WHEN std_pnl > 0 THEN avg_pnl / std_pnl * SQRT(252) ELSE 0 END as sharpe
    FROM metrics
    """

    result = client.query(query).result()
    row = list(result)[0]

    return {
        "total_trades": row.total_trades or 0,
        "wins": row.wins or 0,
        "losses": row.losses or 0,
        "win_rate": float(row.win_rate) if row.win_rate else 0,
        "total_pnl": float(row.total_pnl) if row.total_pnl else 0,
        "avg_pnl": float(row.avg_pnl) if row.avg_pnl else 0,
        "profit_factor": float(row.profit_factor) if row.profit_factor else 0,
        "sharpe": float(row.sharpe) if row.sharpe else 0,
    }


def run_time_split_query(
    client: bigquery.Client,
    yes_price_min: float,
    yes_price_max: float,
    hours_min: float,
    hours_max: float,
    min_volume: Optional[float] = None,
    min_liquidity: Optional[float] = None,
) -> Dict[str, Any]:
    """Run time-split robustness check."""

    volume_filter = f"AND m.volume >= {min_volume}" if min_volume else ""
    liquidity_filter = ""  # No liquidity data available

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.close_date,
            CASE WHEN m.winner = "No" THEN 1 ELSE 0 END as won,
            CASE
                WHEN m.winner = "No" THEN s.price
                ELSE -(1 - s.price)
            END as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {(hours_min + hours_max) / 2})
            ) as rn
        FROM `{MARKETS_TABLE}` m
        JOIN `{SNAPSHOTS_TABLE}` s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price >= {yes_price_min}
            AND s.price <= {yes_price_max}
            AND (m.close_date - s.timestamp) / 1e9 / 3600.0 >= {hours_min}
            AND (m.close_date - s.timestamp) / 1e9 / 3600.0 <= {hours_max}
            AND s.price > 0 AND s.price < 1
            {volume_filter}
            {liquidity_filter}
    ),
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    ),
    with_median AS (
        SELECT
            *,
            PERCENTILE_CONT(close_date, 0.5) OVER () as median_close
        FROM unique_bets
    )
    SELECT
        CASE WHEN close_date <= median_close THEN 'first_half' ELSE 'second_half' END as split,
        COUNT(*) as trades,
        AVG(won) as win_rate,
        SUM(pnl_per_dollar) as total_pnl,
        AVG(pnl_per_dollar) as avg_pnl,
        STDDEV(pnl_per_dollar) as std_pnl
    FROM with_median
    GROUP BY split
    """

    result = client.query(query).result()

    splits = {}
    for row in result:
        splits[row.split] = {
            "trades": row.trades or 0,
            "win_rate": float(row.win_rate) if row.win_rate else 0,
            "total_pnl": float(row.total_pnl) if row.total_pnl else 0,
            "sharpe": float(row.avg_pnl / row.std_pnl * math.sqrt(252)) if row.std_pnl and row.std_pnl > 0 else 0,
        }

    first = splits.get("first_half", {"trades": 0, "win_rate": 0, "total_pnl": 0, "sharpe": 0})
    second = splits.get("second_half", {"trades": 0, "win_rate": 0, "total_pnl": 0, "sharpe": 0})

    # Pass if both halves are profitable
    passed = first["total_pnl"] > 0 and second["total_pnl"] > 0 and first["trades"] >= 10 and second["trades"] >= 10

    return {
        "first_half": first,
        "second_half": second,
        "pass": passed,
        "notes": "Both halves profitable" if passed else "One or both halves not profitable or insufficient trades"
    }


def run_liquidity_split_query(
    client: bigquery.Client,
    yes_price_min: float,
    yes_price_max: float,
    hours_min: float,
    hours_max: float,
    min_volume: Optional[float] = None,
    min_liquidity: Optional[float] = None,
) -> Dict[str, Any]:
    """Run liquidity-split robustness check."""

    volume_filter = f"AND m.volume >= {min_volume}" if min_volume else ""
    liquidity_filter = ""  # No liquidity data available

    query = f"""
    WITH filtered_bets AS (
        SELECT
            m.id as market_id,
            m.volume as market_volume,
            CASE WHEN m.winner = "No" THEN 1 ELSE 0 END as won,
            CASE
                WHEN m.winner = "No" THEN s.price
                ELSE -(1 - s.price)
            END as pnl_per_dollar,
            ROW_NUMBER() OVER (
                PARTITION BY m.id
                ORDER BY ABS((m.close_date - s.timestamp) / 1e9 / 3600.0 - {(hours_min + hours_max) / 2})
            ) as rn
        FROM `{MARKETS_TABLE}` m
        JOIN `{SNAPSHOTS_TABLE}` s ON m.id = s.market_id
        WHERE
            m.resolution_status = "resolved"
            AND m.winner IN ("Yes", "No")
            AND s.price >= {yes_price_min}
            AND s.price <= {yes_price_max}
            AND (m.close_date - s.timestamp) / 1e9 / 3600.0 >= {hours_min}
            AND (m.close_date - s.timestamp) / 1e9 / 3600.0 <= {hours_max}
            AND s.price > 0 AND s.price < 1
            {volume_filter}
            {liquidity_filter}
    ),
    unique_bets AS (
        SELECT * FROM filtered_bets WHERE rn = 1
    ),
    with_median AS (
        SELECT
            *,
            PERCENTILE_CONT(market_volume, 0.5) OVER () as median_volume
        FROM unique_bets
    )
    SELECT
        CASE WHEN market_volume >= median_volume THEN 'high_liquidity' ELSE 'low_liquidity' END as split,
        COUNT(*) as trades,
        AVG(won) as win_rate,
        SUM(pnl_per_dollar) as total_pnl,
        AVG(pnl_per_dollar) as avg_pnl,
        STDDEV(pnl_per_dollar) as std_pnl
    FROM with_median
    GROUP BY split
    """

    result = client.query(query).result()

    splits = {}
    for row in result:
        splits[row.split] = {
            "trades": row.trades or 0,
            "win_rate": float(row.win_rate) if row.win_rate else 0,
            "total_pnl": float(row.total_pnl) if row.total_pnl else 0,
            "sharpe": float(row.avg_pnl / row.std_pnl * math.sqrt(252)) if row.std_pnl and row.std_pnl > 0 else 0,
        }

    high = splits.get("high_liquidity", {"trades": 0, "win_rate": 0, "total_pnl": 0, "sharpe": 0})
    low = splits.get("low_liquidity", {"trades": 0, "win_rate": 0, "total_pnl": 0, "sharpe": 0})

    # Pass if both halves are profitable
    passed = high["total_pnl"] > 0 and low["total_pnl"] > 0 and high["trades"] >= 10 and low["trades"] >= 10

    return {
        "high_liquidity": high,
        "low_liquidity": low,
        "pass": passed,
        "notes": "Both liquidity levels profitable" if passed else "One or both levels not profitable or insufficient trades"
    }


def main():
    # Load config
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config_data = yaml.safe_load(f)

    print("=" * 60)
    print(f"EXPERIMENT: {config_data['experiment_id']} (BigQuery)")
    print("Hypothesis: YES > 0.55 markets resolve NO more often than implied")
    print("=" * 60)

    # Initialize BigQuery client (EU region)
    client = bigquery.Client(project=PROJECT_ID, location="EU")

    # Extract config
    kill_criteria = config_data.get("kill_criteria", {})
    variants = config_data.get("variants", [])

    results = []

    print(f"\nRunning {len(variants)} variants...")
    print("-" * 60)

    for i, variant in enumerate(variants):
        vid = variant["id"]
        vname = variant.get("name", vid)

        # Extract params
        yes_price_min = variant.get("yes_price_min", 0.55)
        yes_price_max = variant.get("yes_price_max", 0.95)
        hours_min = variant.get("hours_min", 12)
        hours_max = variant.get("hours_max", 168)
        min_volume = variant.get("min_volume_24h")
        min_liquidity = variant.get("min_liquidity")

        print(f"\n[{i+1}/{len(variants)}] {vid} ({vname})...")

        # Run main metrics query
        metrics = run_variant_query(
            client, yes_price_min, yes_price_max,
            hours_min, hours_max, min_volume, min_liquidity
        )

        # Run robustness checks
        time_split = run_time_split_query(
            client, yes_price_min, yes_price_max,
            hours_min, hours_max, min_volume, min_liquidity
        )

        liq_split = run_liquidity_split_query(
            client, yes_price_min, yes_price_max,
            hours_min, hours_max, min_volume, min_liquidity
        )

        # Check kill criteria
        kill_results = {
            "sharpe": {
                "threshold": kill_criteria.get("sharpe", 0.5),
                "actual": metrics["sharpe"],
                "pass": metrics["sharpe"] >= kill_criteria.get("sharpe", 0.5),
            },
            "win_rate": {
                "threshold": kill_criteria.get("win_rate", 0.51),
                "actual": metrics["win_rate"],
                "pass": metrics["win_rate"] >= kill_criteria.get("win_rate", 0.51),
            },
            "trades": {
                "threshold": kill_criteria.get("trades", 50),
                "actual": metrics["total_trades"],
                "pass": metrics["total_trades"] >= kill_criteria.get("trades", 50),
            },
            "profit_factor": {
                "threshold": kill_criteria.get("profit_factor", 1.1),
                "actual": metrics["profit_factor"],
                "pass": metrics["profit_factor"] >= kill_criteria.get("profit_factor", 1.1),
            },
            "time_split": {"pass": time_split["pass"]},
        }

        passed_all = all(k["pass"] for k in kill_results.values())

        result = {
            "id": vid,
            "name": vname,
            "params": {
                "yes_price_min": yes_price_min,
                "yes_price_max": yes_price_max,
                "hours_min": hours_min,
                "hours_max": hours_max,
                "min_volume_24h": min_volume,
                "min_liquidity": min_liquidity,
            },
            "metrics": metrics,
            "robustness": {
                "time_split": time_split,
                "liquidity_split": liq_split,
            },
            "kill_criteria": kill_results,
            "passed_all": passed_all,
        }
        results.append(result)

        # Print summary
        status = "PASS" if passed_all else "FAIL"
        print(f"  Trades: {metrics['total_trades']}")
        print(f"  Sharpe: {metrics['sharpe']:.2f} | WR: {metrics['win_rate']:.1%} | PF: {metrics['profit_factor']:.2f}")
        print(f"  Time split: {'PASS' if time_split['pass'] else 'FAIL'} | Liq split: {'PASS' if liq_split['pass'] else 'FAIL'}")
        print(f"  Status: {status}")

    # Find best variant
    passed_variants = [r for r in results if r["passed_all"]]
    if passed_variants:
        best = max(passed_variants, key=lambda r: r["metrics"]["sharpe"])
        best_variant = best["id"]
    else:
        best = max(results, key=lambda r: r["metrics"]["sharpe"])
        best_variant = f"{best['id']} (did not pass all criteria)"

    # Generate results.json
    output = {
        "experiment_id": config_data["experiment_id"],
        "run_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "bigquery",
        "variants": results,
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
        for r in sorted(passed_variants, key=lambda x: -x["metrics"]["sharpe"]):
            print(f"  {r['id']}: Sharpe={r['metrics']['sharpe']:.2f}, WR={r['metrics']['win_rate']:.1%}, Trades={r['metrics']['total_trades']}")

    print(f"\nResults saved to: {results_path}")
    print(f"\nNext: /verdict exp-004")


if __name__ == "__main__":
    main()
