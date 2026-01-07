"""
Celery application configuration.

This module sets up Celery with:
- Redis as broker and result backend
- Beat schedule for automated tasks
- Task autodiscovery
- Automatic retries for transient failures

Production features:
- Exponential backoff on retries (2, 4, 8 seconds)
- Max 3 retries for transient failures
- Task time limits to prevent hangs
- Proper error handling and logging
"""
from celery import Celery
from celery.schedules import crontab

from src.config.settings import settings

# Create Celery app
app = Celery(
    "polymarket_ml",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Configure Celery
app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Task tracking
    task_track_started=True,
    task_time_limit=300,  # 5 minute max per task
    task_soft_time_limit=270,  # Soft limit at 4.5 minutes
    # Worker settings
    worker_prefetch_multiplier=1,  # Don't prefetch too many tasks
    worker_concurrency=4,
    # Result settings
    result_expires=3600,  # Results expire after 1 hour

    # === RETRY CONFIGURATION (production-grade) ===
    task_acks_late=True,  # Ack after task completes (prevents loss on worker crash)
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    task_default_retry_delay=2,  # 2 second initial retry delay
    task_max_retries=3,  # Max 3 retries
    task_autoretry_for=(
        # Transient errors that should be retried
        Exception,  # Will be narrowed in task decorators
    ),
    task_retry_backoff=True,  # Exponential backoff
    task_retry_backoff_max=60,  # Max 60 second delay
    task_retry_jitter=True,  # Add jitter to prevent thundering herd

    # Task routing
    task_routes={
        "src.tasks.snapshots.snapshot_tier": {"queue": "snapshots"},
        "src.tasks.snapshots.snapshot_tier_batch": {"queue": "snapshots"},
        "src.tasks.snapshots.warm_gamma_cache": {"queue": "snapshots"},
        "src.tasks.discovery.*": {"queue": "discovery"},
        "src.tasks.categorization.*": {"queue": "categorization"},
        "src.tasks.news.*": {"queue": "default"},
        "src.csgo.tasks.*": {"queue": "discovery"},  # Route CSGO tasks to discovery queue
    },
    # Default queue
    task_default_queue="default",
)

# Beat schedule - all automated tasks
app.conf.beat_schedule = {
    # === CACHE WARMING ===
    # Pre-warm Gamma cache every 8 seconds for high-frequency tiers
    "warm-gamma-cache": {
        "task": "src.tasks.snapshots.warm_gamma_cache",
        "schedule": 8.0,  # Every 8 seconds
    },
    # === DISCOVERY TASKS ===
    # Find new markets every hour
    "discover-markets": {
        "task": "src.tasks.discovery.discover_markets",
        "schedule": crontab(minute=0),  # Every hour at :00
    },
    # === CATEGORIZATION TASKS ===
    # Rule-based categorization (fast, free) - every hour at :05
    "categorize-with-rules": {
        "task": "src.tasks.categorization.categorize_with_rules",
        "schedule": crontab(minute=5),
        "kwargs": {"limit": 1000},
    },
    # NOTE: Claude-based tasks disabled in Celery - they require the Claude CLI
    # which is only available on the host machine. Use scripts/cron_categorize.sh
    # or run manually: python3 scripts/categorize_with_claude.py --batch 30
    #
    # "categorize-with-claude": {
    #     "task": "src.tasks.categorization.categorize_with_claude",
    #     "schedule": crontab(minute=15),
    #     "kwargs": {"batch_size": 15},
    # },
    # "validate-rule-accuracy": {
    #     "task": "src.tasks.categorization.validate_rule_accuracy",
    #     "schedule": crontab(hour="*/6", minute=30),
    #     "kwargs": {"sample_per_rule": 10},
    # },
    # "suggest-rule-improvements": {
    #     "task": "src.tasks.categorization.suggest_rule_improvements",
    #     "schedule": crontab(day_of_week=0, hour=4, minute=0),
    #     "kwargs": {"min_occurrences": 10},
    # },
    # Reassign tiers every 5 minutes
    "update-tiers": {
        "task": "src.tasks.discovery.update_market_tiers",
        "schedule": crontab(minute="*/5"),
    },
    # Check for resolved markets every 15 minutes (closes positions)
    "check-resolutions": {
        "task": "src.tasks.discovery.check_resolutions",
        "schedule": crontab(minute="*/15"),
    },
    # Capture resolutions for ALL markets (for ML training data)
    "capture-all-resolutions": {
        "task": "src.tasks.discovery.capture_all_resolutions",
        "schedule": crontab(minute="*/10"),  # Every 10 minutes
    },
    # Cleanup stale T4 markets (expired or no trades in 1 hour)
    "cleanup-stale-markets": {
        "task": "src.tasks.discovery.cleanup_stale_markets",
        "schedule": crontab(minute="*/10"),  # Every 10 minutes
    },
    # Cleanup old task_runs (operational data, keeps 7 days)
    "cleanup-old-task-runs": {
        "task": "src.tasks.discovery.cleanup_old_task_runs",
        "schedule": crontab(hour=3, minute=30),  # Daily at 3:30 AM UTC
    },
    # === ALERTS ===
    # Daily trading summary at 9 AM UTC
    "daily-summary": {
        "task": "src.tasks.alerts.send_daily_summary",
        "schedule": crontab(hour=9, minute=0),  # Daily at 9:00 AM UTC
    },
    # === NEWS COLLECTION ===
    # Fetch crypto news from Marketaux (100 req/day free tier)
    # 15-min intervals = 96 req/day, leaves margin for retries
    "fetch-marketaux-news": {
        "task": "src.tasks.news.fetch_marketaux_news",
        "schedule": crontab(minute="*/15"),  # Every 15 minutes
    },
    # === CS:GO PIPELINE ===
    # Sync CS:GO markets to csgo_matches table every 10 minutes
    "sync-csgo-markets": {
        "task": "src.csgo.tasks.sync_csgo_markets",
        "schedule": crontab(minute="*/10"),
    },
    # Enrich new CS:GO matches with Gamma API data every 10 minutes
    "enrich-csgo-matches": {
        "task": "src.csgo.tasks.enrich_csgo_matches",
        "schedule": crontab(minute="2,12,22,32,42,52"),  # Offset by 2 min from sync
    },
    # Refresh game start times for upcoming matches every 30 minutes
    "refresh-csgo-game-times": {
        "task": "src.csgo.tasks.refresh_csgo_game_times",
        "schedule": crontab(minute="5,35"),  # At :05 and :35
    },
    # Poll market status for in-play/upcoming matches (critical for lifecycle)
    # This is CSGO-specific and uses its own Gamma API calls (separate from main pipeline)
    "poll-csgo-market-status": {
        "task": "src.csgo.tasks.poll_csgo_market_status",
        "schedule": 5.0,  # Every 5 seconds for near real-time prices
    },
    # Refresh volume/liquidity for upcoming matches every 5 minutes
    "refresh-csgo-volume": {
        "task": "src.csgo.tasks.refresh_csgo_volume",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
    },
    # Clean up old csgo_price_ticks daily (7-day retention)
    "cleanup-csgo-price-ticks": {
        "task": "src.csgo.tasks.cleanup_csgo_price_ticks",
        "schedule": crontab(hour=4, minute=0),  # Daily at 4:00 AM UTC
    },
    # === SNAPSHOT TASKS ===
    # Tier 0: > 48h to resolution, hourly snapshots
    "snapshot-tier-0": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": crontab(minute=5),  # Every hour at :05
        "args": [0],
    },
    # Tier 1: 12-48h to resolution, 5 minute snapshots
    "snapshot-tier-1": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
        "args": [1],
    },
    # Tier 2: 4-12h to resolution, 1 minute snapshots
    "snapshot-tier-2": {
        "task": "src.tasks.snapshots.snapshot_tier",
        "schedule": 60.0,  # Every 60 seconds
        "args": [2],
    },
    # Tier 3: 1-4h to resolution, 30 second snapshots (batched for throughput)
    "snapshot-tier-3-batch-0": {
        "task": "src.tasks.snapshots.snapshot_tier_batch",
        "schedule": 30.0,  # Every 30 seconds
        "args": [3, 0, 2],  # tier=3, batch=0, total_batches=2
    },
    "snapshot-tier-3-batch-1": {
        "task": "src.tasks.snapshots.snapshot_tier_batch",
        "schedule": 30.0,  # Every 30 seconds
        "args": [3, 1, 2],  # tier=3, batch=1, total_batches=2
    },
    # Tier 4: < 1h to resolution, 15 second snapshots (batched for throughput)
    "snapshot-tier-4-batch-0": {
        "task": "src.tasks.snapshots.snapshot_tier_batch",
        "schedule": 15.0,  # Every 15 seconds
        "args": [4, 0, 2],  # tier=4, batch=0, total_batches=2
    },
    "snapshot-tier-4-batch-1": {
        "task": "src.tasks.snapshots.snapshot_tier_batch",
        "schedule": 15.0,  # Every 15 seconds
        "args": [4, 1, 2],  # tier=4, batch=1, total_batches=2
    },
}

# Import tasks to register them with Celery
app.autodiscover_tasks(["src.tasks", "src.csgo"])

# Explicitly import to ensure registration
from src.tasks import discovery, snapshots, alerts, categorization, news  # noqa: F401, E402
from src.csgo import tasks as csgo_tasks  # noqa: F401, E402
