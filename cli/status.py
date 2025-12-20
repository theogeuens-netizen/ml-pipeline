"""
Show current executor status.

Usage:
    python -m cli.status

Displays:
- Trading mode (paper/live)
- Current balance and P&L
- Open positions
- Deployed strategies
- Recent trade decisions
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_deployed_strategies() -> list[dict]:
    """Load strategies from strategies.yaml."""
    try:
        from strategies.loader import load_strategies
        strategies = load_strategies()
        return [
            {
                "name": s.name,
                "type": type(s).__name__,
                "version": s.version,
                "enabled": True,
            }
            for s in strategies
        ]
    except ImportError:
        return []


def main():
    # Check if running in Docker environment with dependencies
    try:
        from sqlalchemy import select, func, desc
        from src.db.database import get_session
        from src.executor.models import (
            Position, PositionStatus, Signal, SignalStatus,
            PaperBalance, TradeDecision
        )
        from src.executor.config import get_config
        has_db = True
    except ImportError:
        has_db = False

    print(f"\n{'='*60}")
    print("POLYMARKET EXECUTOR STATUS")
    print(f"{'='*60}\n")

    # Show deployed strategies
    strategies = load_deployed_strategies()
    print("DEPLOYED STRATEGIES")
    print("-" * 40)
    if not strategies:
        print("  No strategies found")
        print("  Check strategies.yaml configuration")
    else:
        for s in strategies:
            enabled = "ON " if s.get("enabled", False) else "OFF"
            name = s.get("name", "unknown")
            stype = s.get("type", "Strategy")
            version = s.get("version", "1.0.0")
            print(f"  [{enabled}] {name} v{version} ({stype})")
    print()

    if not has_db:
        print("(Database not available - run from Docker for full status)")
        return

    # Get config
    try:
        config = get_config()
        mode = config.mode.value
    except Exception:
        mode = "unknown"

    with get_session() as db:
        # Get balance
        balance_row = db.execute(select(PaperBalance)).scalar_one_or_none()
        balance = float(balance_row.balance_usd) if balance_row else 10000.0
        starting = float(balance_row.starting_balance_usd) if balance_row else 10000.0
        total_pnl = balance - starting

        # Get open positions
        open_positions = db.execute(
            select(func.count(Position.id)).where(
                Position.status == PositionStatus.OPEN.value
            )
        ).scalar()

        # Get position value
        position_value_raw = db.execute(
            select(func.sum(Position.current_value)).where(
                Position.status == PositionStatus.OPEN.value
            )
        ).scalar() or 0
        position_value = float(position_value_raw)

        # Get today's signals
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_signals = db.execute(
            select(func.count(Signal.id)).where(
                Signal.created_at >= today_start
            )
        ).scalar()

        today_executed = db.execute(
            select(func.count(Signal.id)).where(
                Signal.created_at >= today_start,
                Signal.status == SignalStatus.EXECUTED.value
            )
        ).scalar()

        print("ACCOUNT STATUS")
        print("-" * 40)
        print(f"  Mode: {mode.upper()}")
        print(f"  Balance: ${balance:,.2f}")
        print(f"  Position Value: ${position_value:,.2f}")
        print(f"  Total Value: ${balance + position_value:,.2f}")
        print(f"  Total P&L: ${total_pnl:+,.2f} ({total_pnl/starting*100:+.1f}%)")
        print()

        print("TODAY'S ACTIVITY")
        print("-" * 40)
        print(f"  Open Positions: {open_positions}")
        print(f"  Signals Generated: {today_signals}")
        print(f"  Signals Executed: {today_executed}")
        print()

        # Get recent trade decisions
        try:
            decisions = db.execute(
                select(TradeDecision)
                .order_by(desc(TradeDecision.timestamp))
                .limit(5)
            ).scalars().all()

            if decisions:
                print("RECENT DECISIONS")
                print("-" * 40)
                for d in decisions:
                    status = "EXEC" if d.executed else "REJ"
                    age = datetime.now(timezone.utc) - d.timestamp
                    age_str = f"{age.seconds // 3600}h" if age.seconds >= 3600 else f"{age.seconds // 60}m"
                    print(f"  [{status}] {d.strategy_name} {d.signal_side} @ {float(d.signal_edge or 0)*100:.1f}% edge")
                    print(f"       {d.signal_reason[:50]}... ({age_str} ago)")
        except Exception:
            # TradeDecision table might not exist yet
            pass

        # Get open positions details
        if open_positions > 0:
            positions = db.execute(
                select(Position)
                .where(Position.status == PositionStatus.OPEN.value)
                .order_by(desc(Position.created_at))
                .limit(5)
            ).scalars().all()

            print()
            print("OPEN POSITIONS")
            print("-" * 40)
            for p in positions:
                pnl = float(p.unrealized_pnl or 0)
                pnl_pct = float(p.unrealized_pnl_pct or 0) * 100
                pnl_str = f"${pnl:+.2f} ({pnl_pct:+.1f}%)"
                print(f"  {p.strategy_name}: {p.side} ${float(p.cost_basis):.2f}")
                print(f"       Entry: {float(p.entry_price):.4f} | Current: {float(p.current_price or 0):.4f} | P&L: {pnl_str}")

    print()
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
