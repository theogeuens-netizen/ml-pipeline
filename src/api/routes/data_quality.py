"""
Data quality monitoring endpoints.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.db.database import get_db
from src.db.models import Market, Snapshot

router = APIRouter()

# Expected snapshots per hour by tier
EXPECTED_SNAPSHOTS_PER_HOUR = {
    0: 1,     # T0: hourly
    1: 12,    # T1: every 5 min
    2: 60,    # T2: every 1 min
    3: 120,   # T3: every 30 sec
    4: 240,   # T4: every 15 sec
}


@router.get("/data-quality/coverage")
async def get_coverage(db: Session = Depends(get_db)):
    """
    Get data collection coverage by tier.

    Compares expected vs actual snapshots per hour.
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    coverage = {}
    for tier in range(5):
        # Markets in tier
        market_count = db.execute(
            select(func.count(Market.id)).where(
                Market.tier == tier,
                Market.active == True,
                Market.resolved == False,
            )
        ).scalar()

        # Expected snapshots per hour
        expected_per_market = EXPECTED_SNAPSHOTS_PER_HOUR[tier]
        expected_total = market_count * expected_per_market

        # Actual snapshots in last hour
        actual = db.execute(
            select(func.count(Snapshot.id)).where(
                Snapshot.tier == tier,
                Snapshot.timestamp >= one_hour_ago,
            )
        ).scalar()

        # Coverage percentage
        coverage_pct = (actual / expected_total * 100) if expected_total > 0 else 100

        coverage[f"tier_{tier}"] = {
            "markets": market_count,
            "expected_per_hour": expected_total,
            "actual_per_hour": actual,
            "coverage_pct": round(coverage_pct, 1),
        }

    # Overall coverage
    total_expected = sum(c["expected_per_hour"] for c in coverage.values())
    total_actual = sum(c["actual_per_hour"] for c in coverage.values())
    overall_coverage = (total_actual / total_expected * 100) if total_expected > 0 else 100

    return {
        "timestamp": now.isoformat(),
        "period": "1h",
        "overall_coverage_pct": round(overall_coverage, 1),
        "by_tier": coverage,
    }


@router.get("/data-quality/gaps")
async def get_gaps(db: Session = Depends(get_db)):
    """
    Find markets with missing or stale data.

    A market is considered stale if it hasn't had a snapshot
    in longer than its tier interval.
    """
    now = datetime.now(timezone.utc)

    # Tier staleness thresholds (in seconds, 2x the expected interval)
    staleness_thresholds = {
        0: 2 * settings.tier_0_interval,
        1: 2 * settings.tier_1_interval,
        2: 2 * settings.tier_2_interval,
        3: 2 * settings.tier_3_interval,
        4: 2 * settings.tier_4_interval,
    }

    gaps = []
    for tier in range(5):
        threshold = staleness_thresholds[tier]
        stale_cutoff = now - timedelta(seconds=threshold)

        # Find markets that should have recent data but don't
        stale_markets = db.execute(
            select(Market).where(
                Market.tier == tier,
                Market.active == True,
                Market.resolved == False,
                (Market.last_snapshot_at < stale_cutoff) | (Market.last_snapshot_at.is_(None)),
            )
        ).scalars().all()

        for m in stale_markets:
            last_snapshot = m.last_snapshot_at
            if last_snapshot:
                seconds_since = (now - last_snapshot).total_seconds()
            else:
                seconds_since = None

            gaps.append({
                "market_id": m.id,
                "condition_id": m.condition_id,
                "question": m.question[:100] if m.question else None,
                "tier": tier,
                "last_snapshot_at": last_snapshot.isoformat() if last_snapshot else None,
                "seconds_since_last": int(seconds_since) if seconds_since else None,
                "expected_interval": staleness_thresholds[tier] // 2,
            })

    return {
        "timestamp": now.isoformat(),
        "gap_count": len(gaps),
        "gaps": sorted(gaps, key=lambda x: x["tier"], reverse=True),
    }


@router.get("/data-quality/resolution-tracking")
async def get_resolution_tracking(db: Session = Depends(get_db)):
    """
    Get markets approaching resolution.

    Shows markets by time to resolution to help verify
    tier assignments are correct.
    """
    now = datetime.now(timezone.utc)

    # Get active markets with end dates
    markets = db.execute(
        select(Market).where(
            Market.active == True,
            Market.resolved == False,
            Market.end_date.isnot(None),
        ).order_by(Market.end_date)
    ).scalars().all()

    # Group by time to resolution
    groups = {
        "under_1h": [],
        "1h_to_4h": [],
        "4h_to_12h": [],
        "12h_to_48h": [],
        "over_48h": [],
        "past_due": [],
    }

    for m in markets:
        hours_to_close = (m.end_date - now).total_seconds() / 3600

        info = {
            "id": m.id,
            "question": m.question[:80] if m.question else None,
            "tier": m.tier,
            "hours_to_close": round(hours_to_close, 2),
            "end_date": m.end_date.isoformat(),
            "snapshot_count": m.snapshot_count,
        }

        if hours_to_close < 0:
            groups["past_due"].append(info)
        elif hours_to_close < 1:
            groups["under_1h"].append(info)
        elif hours_to_close < 4:
            groups["1h_to_4h"].append(info)
        elif hours_to_close < 12:
            groups["4h_to_12h"].append(info)
        elif hours_to_close < 48:
            groups["12h_to_48h"].append(info)
        else:
            groups["over_48h"].append(info)

    return {
        "timestamp": now.isoformat(),
        "counts": {k: len(v) for k, v in groups.items()},
        "markets": groups,
    }
