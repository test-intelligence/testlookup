"""Regression coverage for transient database failure during pod startup."""

from __future__ import annotations

import errno
import socket

import pytest

from app.db.migration_retry import (
    connect_with_retry,
    is_retryable_connection_error,
)


class _WrappedDatabaseError(Exception):
    def __init__(self, original: BaseException) -> None:
        super().__init__("database connection failed")
        self.orig = original


class _SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"database error {sqlstate}")
        self.sqlstate = sqlstate


@pytest.mark.asyncio
async def test_connect_retries_refused_connections_with_bounded_backoff():
    connection = object()
    calls = 0
    delays: list[float] = []
    retries: list[tuple[int, float, type[BaseException]]] = []

    async def connect():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "not ready")
        return connection

    async def sleep(delay: float) -> None:
        delays.append(delay)

    result = await connect_with_retry(
        connect,
        attempts=4,
        initial_delay_seconds=0.5,
        max_delay_seconds=1.0,
        sleep=sleep,
        on_retry=lambda attempt, delay, exc: retries.append(
            (attempt, delay, type(exc))
        ),
    )

    assert result is connection
    assert calls == 3
    assert delays == [0.5, 1.0]
    assert retries == [
        (1, 0.5, ConnectionRefusedError),
        (2, 1.0, ConnectionRefusedError),
    ]


@pytest.mark.asyncio
async def test_connect_reraises_the_final_transient_error_unchanged():
    error = ConnectionResetError(errno.ECONNRESET, "connection reset")
    calls = 0

    async def connect():
        nonlocal calls
        calls += 1
        raise error

    async def no_wait(_delay: float) -> None:
        return None

    with pytest.raises(ConnectionResetError) as captured:
        await connect_with_retry(connect, attempts=3, sleep=no_wait)

    assert captured.value is error
    assert calls == 3


@pytest.mark.asyncio
async def test_connect_does_not_retry_non_transport_failures():
    calls = 0

    async def connect():
        nonlocal calls
        calls += 1
        raise ValueError("invalid database configuration")

    with pytest.raises(ValueError, match="invalid database configuration"):
        await connect_with_retry(connect)

    assert calls == 1


def test_retry_classifier_follows_database_wrapper_original_error():
    wrapped = _WrappedDatabaseError(
        OSError(errno.EHOSTUNREACH, "database host unreachable")
    )

    assert is_retryable_connection_error(wrapped) is True
    assert is_retryable_connection_error(ValueError("bad migration")) is False


def test_retry_classifier_covers_database_startup_and_temporary_dns():
    starting_up = _WrappedDatabaseError(_SqlStateError("57P03"))
    temporary_dns = _WrappedDatabaseError(
        socket.gaierror(socket.EAI_AGAIN, "temporary name resolution failure")
    )

    assert is_retryable_connection_error(starting_up) is True
    assert is_retryable_connection_error(temporary_dns) is True
    assert is_retryable_connection_error(_SqlStateError("28P01")) is False
    assert is_retryable_connection_error(
        socket.gaierror(socket.EAI_NONAME, "host does not exist")
    ) is False


@pytest.mark.asyncio
async def test_connect_retry_rejects_invalid_bounds():
    async def connect():
        return object()

    with pytest.raises(ValueError, match="attempts"):
        await connect_with_retry(connect, attempts=0)
    with pytest.raises(ValueError, match="initial_delay_seconds"):
        await connect_with_retry(connect, initial_delay_seconds=-1)
    with pytest.raises(ValueError, match="max_delay_seconds"):
        await connect_with_retry(
            connect,
            initial_delay_seconds=2,
            max_delay_seconds=1,
        )
