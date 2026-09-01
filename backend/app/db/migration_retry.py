"""Bounded retry support for establishing the Alembic database connection.

Only transient connection-establishment failures are retried. Authentication
errors, invalid migration code, and DDL failures still fail immediately so
deployment problems are not hidden behind a generic retry loop.
"""

from __future__ import annotations

import asyncio
import errno
import socket
from collections.abc import Awaitable, Callable
from typing import TypeVar


ConnectionT = TypeVar("ConnectionT")

_RETRYABLE_ERRNOS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
    }
)
_RETRYABLE_SQLSTATES = frozenset(
    {
        "57P03",  # cannot_connect_now: PostgreSQL is still starting/recovering
    }
)


def is_retryable_connection_error(exc: BaseException) -> bool:
    """Return whether *exc* or a wrapped cause is a transient startup fault."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()

    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)

        if isinstance(current, (ConnectionRefusedError, ConnectionResetError, TimeoutError)):
            return True
        if isinstance(current, socket.gaierror) and current.errno == socket.EAI_AGAIN:
            return True
        if isinstance(current, OSError) and current.errno in _RETRYABLE_ERRNOS:
            return True
        if getattr(current, "sqlstate", None) in _RETRYABLE_SQLSTATES:
            return True

        for attribute in ("orig", "__cause__", "__context__"):
            wrapped = getattr(current, attribute, None)
            if isinstance(wrapped, BaseException):
                pending.append(wrapped)

    return False


async def connect_with_retry(
    connect: Callable[[], Awaitable[ConnectionT]],
    *,
    attempts: int = 6,
    initial_delay_seconds: float = 1.0,
    max_delay_seconds: float = 5.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> ConnectionT:
    """Establish a connection with bounded exponential backoff.

    ``on_retry`` receives the failed one-based attempt number, the delay before
    the next attempt, and the original exception. The final error is re-raised
    unchanged so Alembic keeps its normal failure diagnostics.
    """

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if initial_delay_seconds < 0:
        raise ValueError("initial_delay_seconds cannot be negative")
    if max_delay_seconds < initial_delay_seconds:
        raise ValueError("max_delay_seconds cannot be less than the initial delay")

    delay = initial_delay_seconds
    for attempt in range(1, attempts + 1):
        try:
            return await connect()
        except Exception as exc:
            if attempt == attempts or not is_retryable_connection_error(exc):
                raise
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            await sleep(delay)
            delay = min(delay * 2, max_delay_seconds)

    raise AssertionError("connection retry loop exited unexpectedly")
