"""
Ledger query tool for experiment insights.

The ledger is the knowledge accumulation layer - every experiment
must produce a learning that compounds over time.

Usage:
    python -m cli.ledger stats                  # Summary statistics
    python -m cli.ledger search timing          # By friction bucket
    python -m cli.ledger search --status ship   # By status
    python -m cli.ledger search --tags esports  # By tags
    python -m cli.ledger recent 10              # Last N entries
    python -m cli.ledger export                 # Export to CSV
    python -m cli.ledger add <exp_id>           # Add entry interactively
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter
from typing import Optional, List, Dict, Any

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
LEDGER_PATH = PROJECT_ROOT / "ledger" / "insights.jsonl"


def load_ledger() -> List[Dict[str, Any]]:
    """Load all ledger entries."""
    if not LEDGER_PATH.exists():
        return []

    entries = []
    with open(LEDGER_PATH) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON on line {line_num}: {e}", file=sys.stderr)
    return entries


def get_stats() -> Dict[str, Any]:
    """Get ledger summary statistics."""
    entries = load_ledger()

    if not entries:
        return {
            "total_experiments": 0,
            "by_status": {},
            "by_friction_bucket": {},
            "ship_rate": 0.0,
            "kill_rate": 0.0,
            "last_entry": None,
        }

    by_status = Counter(e.get("status", "unknown") for e in entries)
    by_bucket = Counter(e.get("friction_bucket", "unknown") for e in entries)

    # Calculate average metrics for shipped strategies
    shipped = [e for e in entries if e.get("status") == "ship"]
    avg_sharpe = sum(e.get("result", {}).get("sharpe", 0) for e in shipped) / len(shipped) if shipped else 0

    return {
        "total_experiments": len(entries),
        "by_status": dict(by_status),
        "by_friction_bucket": dict(by_bucket),
        "ship_rate": by_status.get("ship", 0) / len(entries) if entries else 0,
        "kill_rate": by_status.get("kill", 0) / len(entries) if entries else 0,
        "iterate_rate": by_status.get("iterate", 0) / len(entries) if entries else 0,
        "avg_shipped_sharpe": round(avg_sharpe, 2),
        "last_entry": entries[-1].get("timestamp") if entries else None,
        "last_exp_id": entries[-1].get("id") if entries else None,
    }


def search(
    friction_bucket: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search ledger by criteria."""
    entries = load_ledger()

    results = []
    for e in entries:
        if friction_bucket and e.get("friction_bucket") != friction_bucket:
            continue
        if status and e.get("status") != status:
            continue
        if tags:
            entry_tags = e.get("tags", [])
            if not any(t in entry_tags for t in tags):
                continue
        results.append(e)

    return results


def recent(n: int = 10) -> List[Dict[str, Any]]:
    """Get N most recent entries."""
    entries = load_ledger()
    return entries[-n:]


def get_next_exp_id() -> str:
    """Get the next available experiment ID."""
    entries = load_ledger()

    # Also check experiments folder for any created but not yet in ledger
    experiments_dir = PROJECT_ROOT / "experiments"
    existing_ids = set()

    # From ledger
    for e in entries:
        exp_id = e.get("id", "")
        if exp_id.startswith("exp-"):
            try:
                num = int(exp_id.split("-")[1])
                existing_ids.add(num)
            except (IndexError, ValueError):
                pass

    # From experiments folder
    if experiments_dir.exists():
        for d in experiments_dir.iterdir():
            if d.is_dir() and d.name.startswith("exp-"):
                try:
                    num = int(d.name.split("-")[1])
                    existing_ids.add(num)
                except (IndexError, ValueError):
                    pass

    next_num = max(existing_ids, default=0) + 1
    return f"exp-{next_num:03d}"


def append_entry(entry: Dict[str, Any]) -> None:
    """Append a new entry to the ledger."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Ensure required fields
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.utcnow().isoformat() + "Z"

    with open(LEDGER_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def format_entry_short(e: Dict[str, Any]) -> str:
    """Format an entry for single-line display."""
    exp_id = e.get("id", "???")
    status = e.get("status", "?").upper()
    bucket = e.get("friction_bucket", "?")
    hypothesis = e.get("hypothesis", "")[:60]

    result = e.get("result", {})
    sharpe = result.get("sharpe", "?")
    win_rate = result.get("win_rate", "?")

    status_icon = {"SHIP": "+", "KILL": "x", "ITERATE": "~"}.get(status, "?")

    return f"[{status_icon}] {exp_id} ({bucket}): {hypothesis}... [S:{sharpe}, WR:{win_rate}]"


def format_entry_detailed(e: Dict[str, Any]) -> str:
    """Format an entry for detailed display."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"Experiment: {e.get('id', '???')}")
    lines.append(f"Status: {e.get('status', '?').upper()}")
    lines.append(f"Friction Bucket: {e.get('friction_bucket', '?')}")
    lines.append(f"Timestamp: {e.get('timestamp', '?')}")
    lines.append("")
    lines.append(f"Hypothesis: {e.get('hypothesis', '?')}")
    lines.append("")

    result = e.get("result", {})
    if result:
        lines.append("Results:")
        lines.append(f"  Sharpe: {result.get('sharpe', '?')}")
        lines.append(f"  Win Rate: {result.get('win_rate', '?')}")
        lines.append(f"  Trades: {result.get('sample_size', result.get('trades', '?'))}")
        lines.append(f"  Robustness: {result.get('robustness', '?')}")

    learnings = e.get("learnings", [])
    if learnings:
        lines.append("")
        lines.append("Learnings:")
        for learning in learnings:
            lines.append(f"  - {learning}")

    kill_reason = e.get("kill_reason")
    if kill_reason:
        lines.append("")
        lines.append(f"Kill Reason: {kill_reason}")

    tags = e.get("tags", [])
    if tags:
        lines.append("")
        lines.append(f"Tags: {', '.join(tags)}")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Query experiment ledger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m cli.ledger stats
  python -m cli.ledger search timing
  python -m cli.ledger search --status ship
  python -m cli.ledger recent 5
  python -m cli.ledger export
        """,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Stats command
    subparsers.add_parser("stats", help="Show ledger statistics")

    # Search command
    search_p = subparsers.add_parser("search", help="Search ledger")
    search_p.add_argument("bucket", nargs="?", help="Friction bucket (timing, liquidity, behavioral, mechanism, cross-market)")
    search_p.add_argument("--status", choices=["ship", "kill", "iterate"], help="Filter by status")
    search_p.add_argument("--tags", nargs="+", help="Filter by tags")
    search_p.add_argument("--detailed", "-d", action="store_true", help="Show detailed output")

    # Recent command
    recent_p = subparsers.add_parser("recent", help="Show recent entries")
    recent_p.add_argument("n", type=int, nargs="?", default=10, help="Number of entries (default: 10)")
    recent_p.add_argument("--detailed", "-d", action="store_true", help="Show detailed output")

    # Export command
    export_p = subparsers.add_parser("export", help="Export to CSV")
    export_p.add_argument("--output", "-o", help="Output file (default: stdout)")

    # Next ID command
    subparsers.add_parser("next-id", help="Get next available experiment ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "stats":
        stats = get_stats()
        print("\n" + "=" * 50)
        print("LEDGER STATISTICS")
        print("=" * 50)
        print(f"\nTotal experiments: {stats['total_experiments']}")
        print(f"\nBy status:")
        for status, count in stats.get("by_status", {}).items():
            pct = count / stats["total_experiments"] * 100 if stats["total_experiments"] > 0 else 0
            icon = {"ship": "+", "kill": "x", "iterate": "~"}.get(status, "?")
            print(f"  [{icon}] {status}: {count} ({pct:.0f}%)")
        print(f"\nBy friction bucket:")
        for bucket, count in stats.get("by_friction_bucket", {}).items():
            print(f"  {bucket}: {count}")
        if stats.get("avg_shipped_sharpe"):
            print(f"\nAvg shipped Sharpe: {stats['avg_shipped_sharpe']}")
        if stats.get("last_exp_id"):
            print(f"\nLast experiment: {stats['last_exp_id']} ({stats['last_entry']})")
        print("=" * 50 + "\n")

    elif args.command == "search":
        results = search(
            friction_bucket=args.bucket,
            status=args.status,
            tags=args.tags,
        )

        if not results:
            print("No matching entries found.")
            return

        print(f"\nFound {len(results)} entries:\n")
        for e in results:
            if args.detailed:
                print(format_entry_detailed(e))
            else:
                print(format_entry_short(e))
        print()

    elif args.command == "recent":
        entries = recent(args.n)

        if not entries:
            print("No entries in ledger.")
            return

        print(f"\nLast {len(entries)} entries:\n")
        for e in entries:
            if args.detailed:
                print(format_entry_detailed(e))
            else:
                print(format_entry_short(e))
        print()

    elif args.command == "export":
        entries = load_ledger()

        if not entries:
            print("No entries to export.", file=sys.stderr)
            return

        # CSV header
        header = "id,timestamp,friction_bucket,status,sharpe,win_rate,sample_size,hypothesis"
        lines = [header]

        for e in entries:
            r = e.get("result", {})
            # Escape hypothesis for CSV
            hypothesis = e.get("hypothesis", "").replace('"', '""')
            line = ",".join([
                e.get("id", ""),
                e.get("timestamp", ""),
                e.get("friction_bucket", ""),
                e.get("status", ""),
                str(r.get("sharpe", "")),
                str(r.get("win_rate", "")),
                str(r.get("sample_size", r.get("trades", ""))),
                f'"{hypothesis}"',
            ])
            lines.append(line)

        output = "\n".join(lines)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Exported {len(entries)} entries to {args.output}")
        else:
            print(output)

    elif args.command == "next-id":
        print(get_next_exp_id())


if __name__ == "__main__":
    main()
