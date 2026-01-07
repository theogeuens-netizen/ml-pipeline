#!/usr/bin/env python3
"""Quick backtest script for price range comparison."""

from google.cloud import bigquery
import os

os.environ['GOOGLE_CLOUD_PROJECT'] = 'elite-buttress-480609-b0'

client = bigquery.Client(project='elite-buttress-480609-b0')

def run_backtest(yes_min, yes_max, hours_min, hours_max, label):
    query = f'''
    WITH entry_snapshots AS (
        SELECT
            s.market_id,
            m.winner,
            s.yes_price,
            s.hours_to_close,
            ROW_NUMBER() OVER (PARTITION BY s.market_id ORDER BY s.timestamp DESC) as rn
        FROM `elite-buttress-480609-b0.longshot.historical_snapshots` s
        JOIN `elite-buttress-480609-b0.longshot.historical_markets` m
            ON s.market_id = m.market_id
        WHERE m.winner IS NOT NULL
        AND s.yes_price BETWEEN {yes_min} AND {yes_max}
        AND s.hours_to_close BETWEEN {hours_min} AND {hours_max}
    )
    SELECT
        COUNT(*) as total_trades,
        SUM(CASE WHEN winner = 'No' THEN 1 ELSE 0 END) as wins,
        ROUND(100.0 * SUM(CASE WHEN winner = 'No' THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate,
        ROUND(AVG(CASE WHEN winner = 'No' THEN (1 - yes_price) ELSE -yes_price END), 4) as avg_pnl
    FROM entry_snapshots
    WHERE rn = 1
    '''
    result = client.query(query).result()
    for row in result:
        r = dict(row)
        print(f"=== {label} ===")
        print(f"Trades: {r['total_trades']:,}")
        print(f"Wins: {r['wins']:,}")
        print(f"Win Rate: {r['win_rate']}%")
        print(f"Avg P&L per $1: ${r['avg_pnl']:.4f}")
        print()
        return r

# Run comparisons
print("Backtest: NO bias strategy, 2-4h window\n")

r1 = run_backtest(0.48, 0.52, 2, 4, "CURRENT: YES 0.48-0.52")
r2 = run_backtest(0.50, 0.55, 2, 4, "PROPOSED: YES 0.50-0.55")

# Also test some variations
r3 = run_backtest(0.50, 0.52, 2, 4, "VARIANT A: YES 0.50-0.52 (narrower)")
r4 = run_backtest(0.48, 0.55, 2, 4, "VARIANT B: YES 0.48-0.55 (wider)")
