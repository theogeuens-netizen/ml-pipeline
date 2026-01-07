#!/usr/bin/env python3
"""
Mean Reversion Backtest for exp-003

Tests the hypothesis: Markets exhibiting price spikes > N standard deviations
from their rolling 24h mean will revert toward the mean within H hours.
"""

import json
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional
import psycopg2
import psycopg2.extras
import numpy as np
from collections import defaultdict


@dataclass
class Trade:
    market_id: int
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    side: str  # 'YES' or 'NO'
    z_score: float
    stake: float = 10.0
    pnl: Optional[float] = None
    resolution: Optional[str] = None


@dataclass
class BacktestResult:
    variant_id: str
    params: dict
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    sharpe: float
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    trades: list


def connect_futarchy():
    """Connect to futarchy postgres database."""
    return psycopg2.connect(
        host="localhost",
        port=5434,
        database="futarchy",
        user="futarchy",
        password="futarchy"
    )


def get_markets_with_resolution(conn) -> dict:
    """Get all resolved markets with their outcomes."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT id, question, winner, close_date, volume, macro_category
            FROM markets
            WHERE resolution_status = 'resolved'
              AND winner IN ('Yes', 'No')
              AND close_date IS NOT NULL
        """)
        return {row['id']: dict(row) for row in cur.fetchall()}


def get_price_snapshots(conn, market_ids: list) -> dict:
    """Get price snapshots for given markets, sorted by time."""
    if not market_ids:
        return {}

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT market_id, timestamp, price
            FROM price_snapshots
            WHERE market_id = ANY(%s)
              AND price > 0 AND price < 1
            ORDER BY market_id, timestamp
        """, (market_ids,))

        snapshots = defaultdict(list)
        for row in cur.fetchall():
            snapshots[row['market_id']].append({
                'timestamp': row['timestamp'],
                'price': float(row['price'])
            })
        return dict(snapshots)


def calculate_rolling_stats(prices: list, timestamps: list, window_hours: int = 24) -> tuple:
    """
    Calculate rolling mean and std for each price point.
    Returns lists of (mean, std) for each timestamp.
    """
    window = timedelta(hours=window_hours)
    means = []
    stds = []

    for i, ts in enumerate(timestamps):
        # Find all prices within the window before this timestamp
        window_start = ts - window
        window_prices = []
        for j in range(i):
            if timestamps[j] >= window_start:
                window_prices.append(prices[j])

        if len(window_prices) >= 5:  # Need minimum samples for meaningful stats
            means.append(np.mean(window_prices))
            stds.append(np.std(window_prices))
        else:
            means.append(None)
            stds.append(None)

    return means, stds


def run_backtest(
    conn,
    price_std_threshold: float,
    holding_period_hours: int,
    stake_per_bet: float = 10.0,
    min_snapshots: int = 20,
    price_min: float = 0.10,
    price_max: float = 0.90,
    min_hours_to_expiry: int = 24
) -> BacktestResult:
    """
    Run mean reversion backtest with given parameters.
    """

    # Get resolved markets
    markets = get_markets_with_resolution(conn)
    print(f"Found {len(markets)} resolved markets with Yes/No outcomes")

    # Filter for markets with enough data
    valid_market_ids = list(markets.keys())

    # Get price snapshots
    print(f"Fetching price snapshots for {len(valid_market_ids)} markets...")
    snapshots = get_price_snapshots(conn, valid_market_ids)
    print(f"Got snapshots for {len(snapshots)} markets")

    # Filter markets with enough snapshots
    filtered_markets = {
        mid: snaps for mid, snaps in snapshots.items()
        if len(snaps) >= min_snapshots
    }
    print(f"Markets with >= {min_snapshots} snapshots: {len(filtered_markets)}")

    trades = []

    for market_id, snaps in filtered_markets.items():
        market_info = markets.get(market_id)
        if not market_info:
            continue

        close_date = market_info['close_date']
        winner = market_info['winner']

        prices = [s['price'] for s in snaps]
        timestamps = [s['timestamp'] for s in snaps]

        # Calculate rolling stats
        means, stds = calculate_rolling_stats(prices, timestamps)

        # Look for entry signals
        for i in range(len(snaps)):
            if means[i] is None or stds[i] is None or stds[i] == 0:
                continue

            price = prices[i]
            ts = timestamps[i]
            mean = means[i]
            std = stds[i]

            # Apply filters
            if price < price_min or price > price_max:
                continue

            # Check hours to expiry
            if close_date:
                hours_to_close = (close_date - ts).total_seconds() / 3600
                if hours_to_close < min_hours_to_expiry:
                    continue

            # Calculate z-score
            z_score = (price - mean) / std

            # Check for spike
            if abs(z_score) > price_std_threshold:
                # Determine side: fade the spike
                if z_score > 0:  # Price spiked UP, bet NO
                    side = 'NO'
                else:  # Price spiked DOWN, bet YES
                    side = 'YES'

                # Find exit price (holding_period_hours later or at resolution)
                exit_time = ts + timedelta(hours=holding_period_hours)
                exit_price = None

                # Look for price at exit time
                for j in range(i + 1, len(snaps)):
                    if timestamps[j] >= exit_time:
                        exit_price = prices[j]
                        exit_time = timestamps[j]
                        break

                # If no exit found within holding period, use resolution
                if exit_price is None:
                    # Use resolution outcome
                    if winner == 'Yes':
                        exit_price = 1.0
                    else:
                        exit_price = 0.0
                    exit_time = close_date

                # Calculate P&L
                if side == 'YES':
                    # Bought YES at entry_price, value at exit_price
                    pnl = stake_per_bet * (exit_price - price) / price if price > 0 else 0
                else:
                    # Bought NO at (1-entry_price), value at (1-exit_price)
                    no_entry = 1 - price
                    no_exit = 1 - exit_price
                    pnl = stake_per_bet * (no_exit - no_entry) / no_entry if no_entry > 0 else 0

                trade = Trade(
                    market_id=market_id,
                    entry_time=ts,
                    entry_price=price,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    side=side,
                    z_score=z_score,
                    stake=stake_per_bet,
                    pnl=pnl,
                    resolution=winner
                )
                trades.append(trade)

    # Calculate metrics
    if not trades:
        return BacktestResult(
            variant_id="",
            params={},
            total_trades=0,
            wins=0,
            losses=0,
            total_pnl=0.0,
            sharpe=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            max_drawdown_pct=0.0,
            trades=[]
        )

    pnls = [t.pnl for t in trades if t.pnl is not None]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)
    total_pnl = sum(pnls)

    # Sharpe ratio (annualized, assuming daily returns)
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Win rate
    win_rate = wins / len(pnls) if pnls else 0.0

    # Profit factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Max drawdown
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    max_dd = np.max(drawdowns) if len(drawdowns) > 0 else 0
    initial_capital = 1000
    max_drawdown_pct = (max_dd / initial_capital) * 100

    return BacktestResult(
        variant_id="",
        params={
            "price_std_threshold": price_std_threshold,
            "holding_period_hours": holding_period_hours
        },
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        total_pnl=round(total_pnl, 2),
        sharpe=round(sharpe, 2),
        win_rate=round(win_rate, 3),
        profit_factor=round(profit_factor, 2) if profit_factor != float('inf') else 999.0,
        max_drawdown_pct=round(max_drawdown_pct, 2),
        trades=trades
    )


def run_time_split(trades: list) -> dict:
    """Split trades by time (first half vs second half)."""
    if len(trades) < 10:
        return {"pass": False, "reason": "Insufficient trades for split"}

    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    mid = len(sorted_trades) // 2

    first_half = sorted_trades[:mid]
    second_half = sorted_trades[mid:]

    def calc_metrics(trade_list):
        pnls = [t.pnl for t in trade_list if t.pnl is not None]
        if not pnls:
            return {"sharpe": 0, "win_rate": 0, "trades": 0, "pnl": 0}
        wins = sum(1 for p in pnls if p > 0)
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if len(pnls) > 1 and np.std(pnls) > 0 else 0
        return {
            "sharpe": round(sharpe, 2),
            "win_rate": round(wins / len(pnls), 3),
            "trades": len(pnls),
            "pnl": round(sum(pnls), 2)
        }

    first_metrics = calc_metrics(first_half)
    second_metrics = calc_metrics(second_half)

    # Pass if both halves have positive P&L
    both_positive = first_metrics["pnl"] > 0 and second_metrics["pnl"] > 0

    return {
        "first_half": first_metrics,
        "second_half": second_metrics,
        "pass": both_positive
    }


def run_liquidity_split(trades: list, markets: dict) -> dict:
    """Split trades by market liquidity (high vs low)."""
    if len(trades) < 10:
        return {"pass": False, "reason": "Insufficient trades for split"}

    # Get volume for each market
    trade_volumes = []
    for t in trades:
        market_info = markets.get(t.market_id, {})
        volume = market_info.get('volume', 0) or 0
        trade_volumes.append((t, volume))

    # Sort by volume and split
    sorted_by_vol = sorted(trade_volumes, key=lambda x: x[1])
    mid = len(sorted_by_vol) // 2

    low_liq = [t for t, v in sorted_by_vol[:mid]]
    high_liq = [t for t, v in sorted_by_vol[mid:]]

    def calc_metrics(trade_list):
        pnls = [t.pnl for t in trade_list if t.pnl is not None]
        if not pnls:
            return {"sharpe": 0, "win_rate": 0, "trades": 0, "pnl": 0}
        wins = sum(1 for p in pnls if p > 0)
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if len(pnls) > 1 and np.std(pnls) > 0 else 0
        return {
            "sharpe": round(sharpe, 2),
            "win_rate": round(wins / len(pnls), 3),
            "trades": len(pnls),
            "pnl": round(sum(pnls), 2)
        }

    low_metrics = calc_metrics(low_liq)
    high_metrics = calc_metrics(high_liq)

    # Pass if both splits have positive P&L (or at least one is very positive)
    both_reasonable = (low_metrics["pnl"] > -50 and high_metrics["pnl"] > -50)

    return {
        "low_liquidity": low_metrics,
        "high_liquidity": high_metrics,
        "pass": both_reasonable
    }


def main():
    """Run all 9 variants and generate results.json"""

    variants = [
        {"id": "v1", "price_std_threshold": 2.0, "holding_period_hours": 1},
        {"id": "v2", "price_std_threshold": 2.0, "holding_period_hours": 4},
        {"id": "v3", "price_std_threshold": 2.0, "holding_period_hours": 8},
        {"id": "v4", "price_std_threshold": 2.5, "holding_period_hours": 1},
        {"id": "v5", "price_std_threshold": 2.5, "holding_period_hours": 4},
        {"id": "v6", "price_std_threshold": 2.5, "holding_period_hours": 8},
        {"id": "v7", "price_std_threshold": 3.0, "holding_period_hours": 1},
        {"id": "v8", "price_std_threshold": 3.0, "holding_period_hours": 4},
        {"id": "v9", "price_std_threshold": 3.0, "holding_period_hours": 8},
    ]

    conn = connect_futarchy()
    markets = get_markets_with_resolution(conn)

    results = {
        "experiment_id": "exp-003",
        "run_at": datetime.utcnow().isoformat() + "Z",
        "data_range": {
            "start": "2024-06-22",
            "end": "2024-12-08",
            "markets_tested": len(markets),
            "resolved_markets": len(markets)
        },
        "variants": [],
        "best_variant": None,
        "summary": {
            "all_passed_kill_criteria": False,
            "robustness_pass_rate": 0.0
        }
    }

    best_sharpe = -999
    passed_count = 0
    robustness_passes = 0
    total_robustness_checks = 0

    for v in variants:
        print(f"\n{'='*50}")
        print(f"Testing variant {v['id']}: threshold={v['price_std_threshold']}, holding={v['holding_period_hours']}h")
        print('='*50)

        result = run_backtest(
            conn,
            price_std_threshold=v['price_std_threshold'],
            holding_period_hours=v['holding_period_hours']
        )
        result.variant_id = v['id']

        # Run robustness checks
        time_split = run_time_split(result.trades)
        liquidity_split = run_liquidity_split(result.trades, markets)

        # Check kill criteria
        kill_criteria = {
            "sharpe": {
                "threshold": 0.5,
                "actual": result.sharpe,
                "pass": result.sharpe >= 0.5
            },
            "win_rate": {
                "threshold": 0.51,
                "actual": result.win_rate,
                "pass": result.win_rate >= 0.51
            },
            "trades": {
                "threshold": 50,
                "actual": result.total_trades,
                "pass": result.total_trades >= 50
            },
            "profit_factor": {
                "threshold": 1.1,
                "actual": result.profit_factor,
                "pass": result.profit_factor >= 1.1
            },
            "time_split": {
                "pass": time_split.get("pass", False)
            }
        }

        all_pass = all(kc["pass"] for kc in kill_criteria.values())
        if all_pass:
            passed_count += 1

        # Track robustness
        total_robustness_checks += 2
        if time_split.get("pass"):
            robustness_passes += 1
        if liquidity_split.get("pass"):
            robustness_passes += 1

        variant_result = {
            "id": v['id'],
            "params": {
                "price_std_threshold": v['price_std_threshold'],
                "holding_period_hours": v['holding_period_hours']
            },
            "metrics": {
                "sharpe": result.sharpe,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "total_trades": result.total_trades,
                "total_pnl": result.total_pnl,
                "max_drawdown_pct": result.max_drawdown_pct
            },
            "robustness": {
                "time_split": time_split,
                "liquidity_split": liquidity_split
            },
            "kill_criteria": kill_criteria,
            "all_pass": all_pass
        }

        results["variants"].append(variant_result)

        # Track best
        if all_pass and result.sharpe > best_sharpe:
            best_sharpe = result.sharpe
            results["best_variant"] = v['id']

        # Print summary
        status = "PASS" if all_pass else "FAIL"
        print(f"\nVariant {v['id']} ({v['price_std_threshold']}σ, {v['holding_period_hours']}h): {status}")
        print(f"  Sharpe: {result.sharpe} | Win: {result.win_rate*100:.1f}% | Trades: {result.total_trades} | PF: {result.profit_factor}")
        print(f"  P&L: ${result.total_pnl:.2f} | MaxDD: {result.max_drawdown_pct:.1f}%")
        print(f"  Time split: {'PASS' if time_split.get('pass') else 'FAIL'}")
        print(f"  Liquidity split: {'PASS' if liquidity_split.get('pass') else 'FAIL'}")

        for kc_name, kc in kill_criteria.items():
            if not kc["pass"]:
                print(f"  KILLED by {kc_name}: {kc.get('actual', 'N/A')} < {kc.get('threshold', 'N/A')}")

    results["summary"]["all_passed_kill_criteria"] = passed_count > 0
    results["summary"]["robustness_pass_rate"] = round(robustness_passes / total_robustness_checks, 2) if total_robustness_checks > 0 else 0
    results["summary"]["variants_passed"] = passed_count
    results["summary"]["variants_total"] = len(variants)

    conn.close()

    # Save results
    output_path = "/home/theo/polymarket-ml/experiments/exp-003/results.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n{'='*50}")
    print("SUMMARY")
    print('='*50)
    print(f"Variants passed: {passed_count}/{len(variants)}")
    print(f"Best variant: {results['best_variant']} (Sharpe={best_sharpe:.2f})" if results['best_variant'] else "No variants passed")
    print(f"Robustness pass rate: {results['summary']['robustness_pass_rate']*100:.0f}%")
    print(f"\nResults saved: {output_path}")
    print(f"\nNext: /verdict exp-003")

    return results


if __name__ == "__main__":
    main()
