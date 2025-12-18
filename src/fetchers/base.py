"""
Base HTTP client with production-grade reliability patterns.

Features:
- Circuit breaker to avoid hammering failed endpoints
- Exponential backoff with jitter to prevent thundering herd
- Separate connect/read timeouts
- Request ID tracking for log correlation
- Smart retry logic (only retries transient errors)
- Response validation
- Memory-safe response limits
"""
import asyncio
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()

# Maximum response size to prevent OOM (10MB)
MAX_RESPONSE_SIZE = 10 * 1024 * 1024


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class CircuitBreaker:
    """
    Circuit breaker to prevent cascading failures.

    Opens after `failure_threshold` consecutive failures.
    Stays open for `recovery_timeout` seconds before testing.
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3

    # State
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    success_count: int = field(default=0)
    last_failure_time: Optional[float] = field(default=None)
    half_open_calls: int = field(default=0)

    # Thread safety
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def can_execute(self) -> bool:
        """Check if request should be allowed."""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if self.last_failure_time is not None:
                    elapsed = time.monotonic() - self.last_failure_time
                    if elapsed >= self.recovery_timeout:
                        self.state = CircuitState.HALF_OPEN
                        self.half_open_calls = 0
                        logger.info("Circuit breaker half-open, testing recovery")
                        return True
                return False

            # HALF_OPEN - allow limited test calls
            if self.half_open_calls < self.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False

    def record_success(self) -> None:
        """Record successful request."""
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    logger.info("Circuit breaker closed, service recovered")
            else:
                self.failure_count = 0

    def record_failure(self) -> None:
        """Record failed request."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()

            if self.state == CircuitState.HALF_OPEN:
                # Failed during recovery test
                self.state = CircuitState.OPEN
                self.success_count = 0
                logger.warning("Circuit breaker re-opened after failed recovery test")
            elif self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker opened",
                    failure_count=self.failure_count,
                    threshold=self.failure_threshold,
                )


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if an error is transient and worth retrying.

    Retryable:
    - Connection errors (network issues)
    - Timeouts
    - 429 Too Many Requests
    - 500, 502, 503, 504 (server errors)

    NOT Retryable:
    - 400, 401, 403, 404, 422 (client errors - our fault)
    """
    if isinstance(error, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return True

    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        # Retry server errors and rate limits
        return status == 429 or status >= 500

    return False


def calculate_backoff(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Calculate exponential backoff with full jitter.

    Full jitter prevents thundering herd by randomizing the entire delay.
    Formula: random(0, min(cap, base * 2^attempt))
    """
    exp_backoff = min(max_delay, base * (2 ** attempt))
    return random.uniform(0, exp_backoff)


class RateLimiter:
    """Token bucket rate limiter for async operations."""

    def __init__(self, rate: float):
        """
        Initialize rate limiter.

        Args:
            rate: Maximum requests per second
        """
        self.rate = rate
        self.tokens = rate
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                logger.debug("Rate limited, waiting", wait_seconds=round(wait_time, 2))
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


class BaseClient:
    """Base HTTP client with production-grade reliability."""

    def __init__(
        self,
        base_url: str,
        rate_limit: float,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        headers: Optional[dict[str, str]] = None,
        max_retries: int = 3,
    ):
        """
        Initialize the HTTP client.

        Args:
            base_url: Base URL for all requests
            rate_limit: Maximum requests per second
            connect_timeout: Connection timeout in seconds
            read_timeout: Read timeout in seconds
            headers: Additional headers to include in requests
            max_retries: Maximum retry attempts for transient errors
        """
        self.base_url = base_url
        self.rate_limiter = RateLimiter(rate_limit)
        self.max_retries = max_retries
        self.circuit_breaker = CircuitBreaker()

        default_headers = {
            "Accept": "application/json",
            "User-Agent": "PolymarketML/1.0",
        }
        if headers:
            default_headers.update(headers)

        # Separate connect and read timeouts
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )

        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=default_headers,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        self._closed = False

    async def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Any:
        """
        Make a rate-limited GET request with automatic retries.

        Args:
            path: URL path (appended to base_url)
            params: Query parameters
            request_id: Optional request ID for log correlation

        Returns:
            JSON response data

        Raises:
            httpx.HTTPStatusError: On non-2xx response after retries
            CircuitOpenError: If circuit breaker is open
        """
        request_id = request_id or str(uuid.uuid4())[:8]

        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            logger.warning("Request rejected by circuit breaker", path=path, request_id=request_id)
            raise CircuitOpenError(f"Circuit breaker open for {self.base_url}")

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                await self.rate_limiter.acquire()

                logger.debug(
                    "HTTP GET",
                    path=path,
                    params=params,
                    attempt=attempt + 1,
                    request_id=request_id,
                )

                response = await self.client.get(path, params=params)

                # Check response size before parsing
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                    raise ValueError(f"Response too large: {content_length} bytes")

                response.raise_for_status()

                # Parse and validate JSON
                try:
                    data = response.json()
                except Exception as e:
                    logger.error("Invalid JSON response", path=path, error=str(e), request_id=request_id)
                    raise ValueError(f"Invalid JSON response: {e}")

                self.circuit_breaker.record_success()
                return data

            except Exception as e:
                last_error = e

                # Log the error with context
                logger.warning(
                    "Request failed",
                    path=path,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                    error_type=type(e).__name__,
                    request_id=request_id,
                )

                # Check if we should retry
                if not is_retryable_error(e) or attempt >= self.max_retries - 1:
                    self.circuit_breaker.record_failure()
                    raise

                # Wait before retry with exponential backoff + jitter
                backoff = calculate_backoff(attempt)
                logger.debug("Retrying after backoff", wait_seconds=round(backoff, 2), request_id=request_id)
                await asyncio.sleep(backoff)

        self.circuit_breaker.record_failure()
        raise last_error or Exception("Request failed with no error details")

    async def post(
        self,
        path: str,
        json: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Any:
        """
        Make a rate-limited POST request with automatic retries.

        Args:
            path: URL path (appended to base_url)
            json: JSON body data
            request_id: Optional request ID for log correlation

        Returns:
            JSON response data
        """
        request_id = request_id or str(uuid.uuid4())[:8]

        if not self.circuit_breaker.can_execute():
            logger.warning("Request rejected by circuit breaker", path=path, request_id=request_id)
            raise CircuitOpenError(f"Circuit breaker open for {self.base_url}")

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                await self.rate_limiter.acquire()

                logger.debug("HTTP POST", path=path, attempt=attempt + 1, request_id=request_id)
                response = await self.client.post(path, json=json)
                response.raise_for_status()

                data = response.json()
                self.circuit_breaker.record_success()
                return data

            except Exception as e:
                last_error = e
                logger.warning(
                    "POST request failed",
                    path=path,
                    attempt=attempt + 1,
                    error=str(e),
                    request_id=request_id,
                )

                if not is_retryable_error(e) or attempt >= self.max_retries - 1:
                    self.circuit_breaker.record_failure()
                    raise

                backoff = calculate_backoff(attempt)
                await asyncio.sleep(backoff)

        self.circuit_breaker.record_failure()
        raise last_error or Exception("Request failed")

    async def close(self) -> None:
        """Close the HTTP client."""
        if not self._closed:
            await self.client.aclose()
            self._closed = True

    async def __aenter__(self) -> "BaseClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class SyncRateLimiter:
    """Token bucket rate limiter for synchronous operations."""

    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                logger.debug("Rate limited, waiting", wait_seconds=round(wait_time, 2))
                time.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


class SyncBaseClient:
    """
    Synchronous HTTP client with production-grade reliability.

    Use this for Celery tasks to avoid asyncio event loop issues.
    """

    def __init__(
        self,
        base_url: str,
        rate_limit: float,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        headers: Optional[dict[str, str]] = None,
        max_retries: int = 3,
    ):
        self.base_url = base_url
        self.rate_limiter = SyncRateLimiter(rate_limit)
        self.max_retries = max_retries
        self.circuit_breaker = CircuitBreaker()

        default_headers = {
            "Accept": "application/json",
            "User-Agent": "PolymarketML/1.0",
        }
        if headers:
            default_headers.update(headers)

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )

        self.client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=default_headers,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        self._closed = False

    def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Any:
        """
        Make a rate-limited GET request with automatic retries.

        Args:
            path: URL path (appended to base_url)
            params: Query parameters
            request_id: Optional request ID for log correlation

        Returns:
            JSON response data

        Raises:
            httpx.HTTPStatusError: On non-2xx response after retries
            CircuitOpenError: If circuit breaker is open
        """
        request_id = request_id or str(uuid.uuid4())[:8]

        if not self.circuit_breaker.can_execute():
            logger.warning("Request rejected by circuit breaker", path=path, request_id=request_id)
            raise CircuitOpenError(f"Circuit breaker open for {self.base_url}")

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()

                logger.debug(
                    "HTTP GET (sync)",
                    path=path,
                    params=params,
                    attempt=attempt + 1,
                    request_id=request_id,
                )

                response = self.client.get(path, params=params)

                # Check response size
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                    raise ValueError(f"Response too large: {content_length} bytes")

                response.raise_for_status()

                try:
                    data = response.json()
                except Exception as e:
                    logger.error("Invalid JSON response", path=path, error=str(e), request_id=request_id)
                    raise ValueError(f"Invalid JSON response: {e}")

                self.circuit_breaker.record_success()
                return data

            except Exception as e:
                last_error = e
                logger.warning(
                    "Request failed (sync)",
                    path=path,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                    error_type=type(e).__name__,
                    request_id=request_id,
                )

                if not is_retryable_error(e) or attempt >= self.max_retries - 1:
                    self.circuit_breaker.record_failure()
                    raise

                backoff = calculate_backoff(attempt)
                logger.debug("Retrying after backoff", wait_seconds=round(backoff, 2), request_id=request_id)
                time.sleep(backoff)

        self.circuit_breaker.record_failure()
        raise last_error or Exception("Request failed")

    def post(
        self,
        path: str,
        json: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Any:
        """Make a rate-limited POST request with automatic retries."""
        request_id = request_id or str(uuid.uuid4())[:8]

        if not self.circuit_breaker.can_execute():
            logger.warning("Request rejected by circuit breaker", path=path, request_id=request_id)
            raise CircuitOpenError(f"Circuit breaker open for {self.base_url}")

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                logger.debug("HTTP POST (sync)", path=path, attempt=attempt + 1, request_id=request_id)
                response = self.client.post(path, json=json)
                response.raise_for_status()
                data = response.json()
                self.circuit_breaker.record_success()
                return data

            except Exception as e:
                last_error = e
                logger.warning(
                    "POST request failed (sync)",
                    path=path,
                    attempt=attempt + 1,
                    error=str(e),
                    request_id=request_id,
                )

                if not is_retryable_error(e) or attempt >= self.max_retries - 1:
                    self.circuit_breaker.record_failure()
                    raise

                backoff = calculate_backoff(attempt)
                time.sleep(backoff)

        self.circuit_breaker.record_failure()
        raise last_error or Exception("Request failed")

    def close(self) -> None:
        """Close the HTTP client."""
        if not self._closed:
            self.client.close()
            self._closed = True

    def __enter__(self) -> "SyncBaseClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
