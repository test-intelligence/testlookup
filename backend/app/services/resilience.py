"""
Resilience utilities for external API calls.

Provides async retry with exponential backoff and jitter,
designed for self-healing integration with Jira, Splunk, OCP, and other external services.
"""
import asyncio
import hashlib
import json
import logging
import random
from typing import Any, Callable, Optional, Sequence, Type, cast

import httpx

logger = logging.getLogger("services.resilience")

# Default retryable HTTP status codes (server errors + rate limiting)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Default retryable exception types
RETRYABLE_EXCEPTIONS: tuple[Type[Exception], ...] = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectError,
    ConnectionError,
    TimeoutError,
)


def _db_retryable_exceptions() -> tuple[Type[Exception], ...]:
    """Build the retryable-exception tuple for DB-bound operations.

    Lazily resolves SQLAlchemy and asyncpg exception classes so importing
    this module doesn't depend on either driver being installed (the
    same lazy-bootstrap concern that motivates ``get_engine`` in
    ``app.db.postgres``). Returns the empty tuple when neither package
    is present — callers handle the no-retry case gracefully.

    Covers ONLY transient DB faults that warrant retry. Deliberately
    EXCLUDES ``DBAPIError`` (the parent class) because that would also
    match ``IntegrityError`` — constraint violations are permanent and
    retrying them just wastes a round trip before failing again with
    the same error.

    * ``sqlalchemy.exc.OperationalError`` — connection drops, pool
      checkout failures, asyncpg "another operation in progress" races,
      idle-timeout disconnections.
    * ``asyncpg.PostgresError`` subclasses that signal transient
      conditions:

      - ``DeadlockDetectedError`` — Postgres killed our transaction to
        break a deadlock. Retry usually succeeds because the conflicting
        transaction has now completed.
      - ``SerializationFailureError`` — serializable isolation conflict.
        Retry resolves it.
      - ``ConnectionDoesNotExistError`` — connection silently dropped.
      - ``InterfaceError`` — connection in invalid state (typically
        after a previous failure on the same connection).
    """
    out: list[Type[Exception]] = []
    try:
        from sqlalchemy.exc import OperationalError
        out.append(OperationalError)
    except ImportError:  # pragma: no cover - sqlalchemy is a runtime dep
        pass
    try:
        import asyncpg.exceptions as _ax
        # asyncpg has historically renamed SerializationError →
        # SerializationFailureError; reference both for robustness.
        for name in (
            "DeadlockDetectedError",
            "SerializationFailureError",
            "SerializationError",
            "ConnectionDoesNotExistError",
            "InterfaceError",
        ):
            cls = getattr(_ax, name, None)
            if cls is not None:
                out.append(cls)
    except ImportError:  # pragma: no cover - asyncpg is a runtime dep
        pass
    return tuple(out)


DB_RETRYABLE_EXCEPTIONS: tuple[Type[Exception], ...] = _db_retryable_exceptions()


async def async_retry(
    coro_factory: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: Sequence[Type[Exception]] = RETRYABLE_EXCEPTIONS,
    retryable_status_codes: frozenset[int] = RETRYABLE_STATUS_CODES,
    operation_name: str = "external_api_call",
    **kwargs: Any,
) -> Any:
    """
    Execute an async callable with exponential backoff retry.

    Args:
        coro_factory: Async function to call
        max_retries: Maximum number of retry attempts (0 = no retry)
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay cap in seconds
        retryable_exceptions: Exception types that trigger retry
        retryable_status_codes: HTTP status codes that trigger retry
        operation_name: Name for logging context

    Returns:
        Result of the coroutine

    Raises:
        The last exception if all retries are exhausted
    """
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_factory(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            last_exception = exc
            if exc.response.status_code not in retryable_status_codes:
                raise  # Non-retryable HTTP error — fail immediately
            if attempt == max_retries:
                logger.error(
                    "%s failed after %d attempts: HTTP %d",
                    operation_name, attempt + 1, exc.response.status_code,
                )
                raise
            delay = _backoff_delay(attempt, base_delay, max_delay)
            logger.warning(
                "%s attempt %d/%d failed (HTTP %d), retrying in %.1fs",
                operation_name, attempt + 1, max_retries + 1,
                exc.response.status_code, delay,
            )
            await asyncio.sleep(delay)
        except tuple(retryable_exceptions) as exc:
            last_exception = exc
            if attempt == max_retries:
                logger.error(
                    "%s failed after %d attempts: %s",
                    operation_name, attempt + 1, exc,
                )
                raise
            delay = _backoff_delay(attempt, base_delay, max_delay)
            logger.warning(
                "%s attempt %d/%d failed (%s), retrying in %.1fs",
                operation_name, attempt + 1, max_retries + 1,
                type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)

    # The loop body either returns on success or raises on the final attempt,
    # so this is reachable only when max_retries < 0 (caller misuse). Surface
    # that as a clear error rather than silently returning None.
    if last_exception is not None:
        raise last_exception
    raise ValueError(
        f"async_retry: max_retries must be >= 0 (got {max_retries})"
    )


def _backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with ±20% jitter, capped at max_delay."""
    delay = min(base * (2 ** attempt), cap)
    jitter = delay * 0.2 * (2 * random.random() - 1)
    return cast(float, max(0.1, delay + jitter))


def estimate_token_count(text: str) -> int:
    """
    Fast approximate token count (4 chars ≈ 1 token for English text).
    This avoids importing tiktoken or calling the LLM tokenizer.
    """
    return max(1, len(text) // 4)


def truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate text to fit within an approximate token budget."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated to fit token budget]"


def compute_analysis_cache_key(
    test_name: str,
    error_message: str,
    stack_trace: str,
) -> str:
    """
    Compute a deterministic cache key for AI analysis results.
    Uses SHA-256 of normalized inputs so identical failures share a cache entry.
    """
    normalized = json.dumps(
        {
            "test_name": test_name.strip().lower(),
            "error_message": error_message.strip()[:500],
            "stack_trace": stack_trace.strip()[:2000],
        },
        sort_keys=True,
    )
    return f"testlookup:ai_cache:{hashlib.sha256(normalized.encode()).hexdigest()}"


# ── Degradation primitive ────────────────────────────────────────────────────
# The user-visible failure we are guarding against: a third-party service
# (ChromaDB, MongoDB, MinIO, …) becomes unreachable or returns unexpected
# data, and the page that depends on it goes blank — even though the
# source-of-truth data is still in PostgreSQL. ``with_fallback`` makes the
# "show data from Postgres when X is down" pattern a one-liner instead of
# an inline try/except that every contributor copy-pastes inconsistently.

from typing import Awaitable, TypeVar

T = TypeVar("T")


async def with_fallback(
    primary: Callable[[], Awaitable[T]],
    fallback: Callable[[], Awaitable[T]],
    *,
    name: str,
    is_empty: Optional[Callable[[T], bool]] = None,
) -> T:
    """Run ``primary``; on failure or empty result, run ``fallback``.

    Parameters
    ----------
    primary:
        Zero-arg async callable returning the desired result. Typically a
        call into a third-party service.
    fallback:
        Zero-arg async callable that returns the same shape as ``primary``
        but reads from a more durable source (almost always PostgreSQL).
    name:
        Short identifier used in structured logs so we can graph
        degradation rates per call site.
    is_empty:
        Optional predicate that treats a successful-but-empty primary
        result as a failure — e.g. ChromaDB returning ``[]`` because the
        collection is unreachable. When omitted, only exceptions trigger
        the fallback.

    Notes
    -----
    Intentionally minimal: no retry, timeout, or circuit-breaker state —
    those concerns live in the caller (use ``async_retry`` above if a
    retry is wanted).
    """
    try:
        result = await primary()
    except Exception as exc:
        logger.warning(
            "resilience.fallback site=%s reason=primary_raised error=%s (%s)",
            name, str(exc), type(exc).__name__,
        )
        return await fallback()

    if is_empty is not None and is_empty(result):
        logger.info(
            "resilience.fallback site=%s reason=primary_empty", name,
        )
        return await fallback()

    logger.debug("resilience.primary_ok site=%s", name)
    return result
