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
