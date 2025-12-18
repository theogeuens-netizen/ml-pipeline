"""
Database engine and session management with production-grade reliability.

Features:
- Connection retry with exponential backoff
- pool_pre_ping for stale connection detection
- Configurable pool sizes for high concurrency
- Proper session lifecycle management
"""
import time
from contextlib import contextmanager
from functools import wraps
from typing import Generator, TypeVar, Callable

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError, InterfaceError, DisconnectionError
from sqlalchemy.orm import Session, sessionmaker
import structlog

from src.config.settings import settings

logger = structlog.get_logger()

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # seconds

# Create engine with robust settings
engine = create_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,  # Verify connections before use
    pool_recycle=3600,   # Recycle connections after 1 hour
    pool_timeout=30,     # Wait up to 30s for a connection
    echo=settings.debug,
)

# Session factory
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def is_connection_error(error: Exception) -> bool:
    """Check if error is a database connection issue worth retrying."""
    return isinstance(error, (OperationalError, InterfaceError, DisconnectionError))


T = TypeVar('T')


def with_retry(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to retry database operations on connection failures.

    Uses exponential backoff: 1s, 2s, 4s
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if not is_connection_error(e) or attempt >= MAX_RETRIES - 1:
                    raise

                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    "Database connection error, retrying",
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay=delay,
                    error=str(e),
                )
                time.sleep(delay)

        raise last_error or Exception("Database operation failed")
    return wrapper


@contextmanager
def get_session(auto_commit: bool = True) -> Generator[Session, None, None]:
    """
    Context manager for database sessions with retry support.

    Args:
        auto_commit: If True, automatically commit on successful exit.
                     Set to False if you want manual transaction control.

    Usage:
        with get_session() as session:
            session.query(Market).all()

    Note: On exception, always rolls back. Caller can catch and handle.
    """
    session = SessionLocal()
    try:
        yield session
        if auto_commit:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_session_with_retry() -> Generator[Session, None, None]:
    """
    Context manager with built-in connection retry.

    Retries on connection errors before yielding session.
    Once session is yielded, caller is responsible for operations.
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            session = SessionLocal()
            # Test the connection
            session.execute("SELECT 1")
            try:
                yield session
                session.commit()
                return
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            last_error = e
            if not is_connection_error(e) or attempt >= MAX_RETRIES - 1:
                raise

            delay = RETRY_DELAY_BASE * (2 ** attempt)
            logger.warning(
                "Database connection error, retrying session",
                attempt=attempt + 1,
                delay=delay,
                error=str(e),
            )
            time.sleep(delay)

    raise last_error or Exception("Failed to establish database session")


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency for database sessions.

    Usage:
        @app.get("/markets")
        def get_markets(db: Session = Depends(get_db)):
            return db.query(Market).all()
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# Data validation utilities

def validate_price(value: float, field_name: str = "price") -> bool:
    """Validate price is within reasonable bounds."""
    if value is None:
        return True  # None is valid (missing data)
    if not isinstance(value, (int, float)):
        logger.warning(f"Invalid {field_name}: not a number", value=value)
        return False
    if value < 0 or value > 1:
        logger.warning(f"Invalid {field_name}: out of range [0,1]", value=value)
        return False
    return True


def validate_volume(value: float, field_name: str = "volume") -> bool:
    """Validate volume is non-negative."""
    if value is None:
        return True
    if not isinstance(value, (int, float)):
        logger.warning(f"Invalid {field_name}: not a number", value=value)
        return False
    if value < 0:
        logger.warning(f"Invalid {field_name}: negative", value=value)
        return False
    return True


def validate_timestamp(ts, field_name: str = "timestamp") -> bool:
    """Validate timestamp is reasonable (not in far future or past)."""
    from datetime import datetime, timezone, timedelta

    if ts is None:
        return True

    now = datetime.now(timezone.utc)
    min_date = datetime(2020, 1, 1, tzinfo=timezone.utc)  # Polymarket didn't exist before
    max_date = now + timedelta(days=365)  # Markets shouldn't resolve > 1 year out

    if ts < min_date or ts > max_date:
        logger.warning(f"Invalid {field_name}: out of reasonable range", value=ts)
        return False
    return True
