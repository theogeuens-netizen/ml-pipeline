"""
Historical data loading for backtesting.

Loads data from the historical_markets and historical_price_snapshots tables.
These tables contain migrated data from futarchy's PostgreSQL database.

Note: The historical_* tables are separate from polymarket-ml's operational
data (markets, snapshots tables). Historical data is less granular and
is used only for backtesting, not for XGBoost training.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Iterator

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from .engine import HistoricalBet


@dataclass
class HistoricalMarket:
    """
    Historical market data for backtesting.

    Represents a resolved market from the historical_markets table.
    """

    id: int
    external_id: str  # Polymarket condition_id
    question: str
    close_date: datetime

    # Resolution data (critical for backtesting)
    resolution_status: str  # resolved, unresolved, disputed
    winner: Optional[str]  # YES or NO
    resolved_at: Optional[datetime]

    # Categories
    macro_category: Optional[str] = None
    micro_category: Optional[str] = None

    # Market metrics
    volume: Optional[float] = None
    liquidity: Optional[float] = None

    @property
    def is_resolved(self) -> bool:
        return self.resolution_status == "resolved" and self.winner is not None

    @property
    def outcome(self) -> Optional[str]:
        """Normalized outcome (YES or NO)."""
        if not self.winner:
            return None
        w = self.winner.upper().strip()
        if w in ("YES", "Y", "TRUE", "1"):
            return "YES"
        elif w in ("NO", "N", "FALSE", "0"):
            return "NO"
        return w


@dataclass
class HistoricalPriceSnapshot:
    """
    Historical price snapshot for a market.

    Represents a point-in-time price observation from
    the historical_price_snapshots table.
    """

    id: int
    market_id: int
    timestamp: datetime

    # OHLC prices (0-1 scale)
    price: float  # Close price
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None

    # Bid/Ask
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None

    # Volume
    volume: Optional[float] = None


def load_resolved_markets(
    db: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    categories: Optional[List[str]] = None,
    min_volume: Optional[float] = None,
    limit: Optional[int] = None,
) -> List[HistoricalMarket]:
    """
    Load resolved markets from the historical_markets table.

    Args:
        db: SQLAlchemy session
        start_date: Filter by close_date >= start_date
        end_date: Filter by close_date <= end_date
        categories: Filter by macro_category (e.g., ["Crypto", "Sports"])
        min_volume: Filter by volume >= min_volume
        limit: Maximum number of markets to return

    Returns:
        List of HistoricalMarket objects
    """
    # Import here to avoid circular dependency
    from src.db.models import HistoricalMarketModel

    filters = [HistoricalMarketModel.resolution_status == "resolved"]

    if start_date:
        filters.append(HistoricalMarketModel.close_date >= start_date)
    if end_date:
        filters.append(HistoricalMarketModel.close_date <= end_date)
    if categories:
        filters.append(HistoricalMarketModel.macro_category.in_(categories))
    if min_volume:
        filters.append(HistoricalMarketModel.volume >= min_volume)

    query = select(HistoricalMarketModel).where(and_(*filters))

    if limit:
        query = query.limit(limit)

    results = db.execute(query).scalars().all()

    return [
        HistoricalMarket(
            id=r.id,
            external_id=r.external_id,
            question=r.question or "",
            close_date=r.close_date,
            resolution_status=r.resolution_status or "unresolved",
            winner=r.winner,
            resolved_at=r.resolved_at,
            macro_category=r.macro_category,
            micro_category=r.micro_category,
            volume=float(r.volume) if r.volume else None,
            liquidity=float(r.liquidity) if r.liquidity else None,
        )
        for r in results
    ]


def load_price_snapshots(
    db: Session,
    market_ids: List[int],
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[HistoricalPriceSnapshot]:
    """
    Load price snapshots for the given markets.

    Args:
        db: SQLAlchemy session
        market_ids: List of historical market IDs
        start_date: Filter by timestamp >= start_date
        end_date: Filter by timestamp <= end_date

    Returns:
        List of HistoricalPriceSnapshot objects
    """
    from src.db.models import HistoricalPriceSnapshotModel

    if not market_ids:
        return []

    filters = [HistoricalPriceSnapshotModel.market_id.in_(market_ids)]

    if start_date:
        filters.append(HistoricalPriceSnapshotModel.timestamp >= start_date)
    if end_date:
        filters.append(HistoricalPriceSnapshotModel.timestamp <= end_date)

    query = (
        select(HistoricalPriceSnapshotModel)
        .where(and_(*filters))
        .order_by(HistoricalPriceSnapshotModel.market_id, HistoricalPriceSnapshotModel.timestamp)
    )

    results = db.execute(query).scalars().all()

    return [
        HistoricalPriceSnapshot(
            id=r.id,
            market_id=r.market_id,
            timestamp=r.timestamp,
            price=float(r.price) if r.price else 0.5,
            open_price=float(r.open_price) if r.open_price else None,
            high_price=float(r.high_price) if r.high_price else None,
            low_price=float(r.low_price) if r.low_price else None,
            bid_price=float(r.bid_price) if r.bid_price else None,
            ask_price=float(r.ask_price) if r.ask_price else None,
            volume=float(r.volume) if r.volume else None,
        )
        for r in results
    ]


def generate_bets_from_snapshots(
    markets: List[HistoricalMarket],
    snapshots: List[HistoricalPriceSnapshot],
    side: str = "NO",
    hours_before_close: Optional[float] = None,
) -> Iterator[HistoricalBet]:
    """
    Generate betting opportunities from historical data.

    For each resolved market, creates a HistoricalBet using the price
    snapshot closest to the specified hours before close.

    Args:
        markets: List of resolved historical markets
        snapshots: List of price snapshots for those markets
        side: Which side to bet on ("YES" or "NO")
        hours_before_close: Hours before market close to use for entry price.
                           If None, uses the last available snapshot.

    Yields:
        HistoricalBet objects for backtesting
    """
    # Group snapshots by market_id
    snapshots_by_market = {}
    for snap in snapshots:
        if snap.market_id not in snapshots_by_market:
            snapshots_by_market[snap.market_id] = []
        snapshots_by_market[snap.market_id].append(snap)

    for market in markets:
        if not market.is_resolved or not market.outcome:
            continue

        market_snapshots = snapshots_by_market.get(market.id, [])
        if not market_snapshots:
            continue

        # Sort snapshots by timestamp
        market_snapshots.sort(key=lambda s: s.timestamp)

        # Find the appropriate snapshot for entry
        if hours_before_close is not None:
            from datetime import timedelta

            target_time = market.close_date - timedelta(hours=hours_before_close)
            # Find snapshot closest to target_time
            best_snap = None
            best_diff = float("inf")
            for snap in market_snapshots:
                diff = abs((snap.timestamp - target_time).total_seconds())
                if diff < best_diff:
                    best_diff = diff
                    best_snap = snap
            snap = best_snap
        else:
            # Use last snapshot before close
            snap = market_snapshots[-1]

        if not snap or not snap.price or snap.price <= 0 or snap.price >= 1:
            continue

        # Determine entry price based on side
        if side == "YES":
            entry_price = snap.price
        else:  # NO
            entry_price = 1 - snap.price

        yield HistoricalBet(
            entry_ts=snap.timestamp,
            resolution_ts=market.resolved_at or market.close_date,
            market_id=market.id,
            condition_id=market.external_id,
            question=market.question,
            side=side,
            entry_price=entry_price,
            outcome=market.outcome,
            macro_category=market.macro_category,
            micro_category=market.micro_category,
            volume=market.volume,
        )


def get_historical_stats(db: Session) -> dict:
    """
    Get summary statistics for historical data.

    Returns:
        Dict with counts and date ranges
    """
    from src.db.models import HistoricalMarketModel, HistoricalPriceSnapshotModel
    from sqlalchemy import func

    # Count markets
    market_count = db.execute(
        select(func.count(HistoricalMarketModel.id))
    ).scalar() or 0

    resolved_count = db.execute(
        select(func.count(HistoricalMarketModel.id)).where(
            HistoricalMarketModel.resolution_status == "resolved"
        )
    ).scalar() or 0

    # Count snapshots
    snapshot_count = db.execute(
        select(func.count(HistoricalPriceSnapshotModel.id))
    ).scalar() or 0

    # Date range
    min_date = db.execute(
        select(func.min(HistoricalMarketModel.close_date))
    ).scalar()

    max_date = db.execute(
        select(func.max(HistoricalMarketModel.close_date))
    ).scalar()

    # Category breakdown
    category_counts = db.execute(
        select(
            HistoricalMarketModel.macro_category,
            func.count(HistoricalMarketModel.id),
        )
        .where(HistoricalMarketModel.resolution_status == "resolved")
        .group_by(HistoricalMarketModel.macro_category)
    ).all()

    return {
        "total_markets": market_count,
        "resolved_markets": resolved_count,
        "price_snapshots": snapshot_count,
        "date_range": {
            "min": min_date.isoformat() if min_date else None,
            "max": max_date.isoformat() if max_date else None,
        },
        "categories": {cat: count for cat, count in category_counts if cat},
    }
