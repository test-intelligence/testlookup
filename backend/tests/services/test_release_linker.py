"""Unit tests for services.release_linker — race-safe upsert behavior."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.services.release_linker import (  # noqa: E402
    link_run_to_release,
    resolve_or_create_release,
)


def _execute_result(scalar_value):
    """Build a Mock that mimics the SQLAlchemy result chain."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_value)
    return result


def _make_db(*execute_returns):
    """
    Build an AsyncMock session where each ``db.execute`` call returns the
    next value from ``execute_returns``. Tracks flush/add/begin_nested calls
    so assertions can inspect them.
    """
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_execute_result(v) for v in execute_returns])
    db.flush = AsyncMock()
    db.add = MagicMock()

    # begin_nested() returns an async context manager (SAVEPOINT).
    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=savepoint)
    return db


@pytest.mark.asyncio
async def test_existing_release_returned_without_insert():
    existing = SimpleNamespace(id=uuid.uuid4(), name="Sprint 42")
    db = _make_db(existing)  # first execute → found

    release, created = await resolve_or_create_release(db, uuid.uuid4(), "Sprint 42")

    assert release is existing
    assert created is False
    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.begin_nested.assert_not_called()


@pytest.mark.asyncio
async def test_new_release_inserted_inside_savepoint():
    db = _make_db(None)  # first lookup → miss
    db.flush = AsyncMock()  # flush succeeds

    release, created = await resolve_or_create_release(db, uuid.uuid4(), "v2.0.0")

    assert created is True
    assert release.name == "v2.0.0"
    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    db.begin_nested.assert_called_once()  # SAVEPOINT used


@pytest.mark.asyncio
async def test_race_loser_returns_winners_row():
    """
    Simulate the race: our lookup misses, another transaction inserts the
    winning row, our flush raises IntegrityError, the re-lookup finds the
    winner. Must NOT raise and MUST return the winner with created=False.
    """
    winner = SimpleNamespace(id=uuid.uuid4(), name="hotfix-1.2.3")
    # execute() is called twice: first lookup (miss), second lookup (winner).
    db = _make_db(None, winner)
    db.flush = AsyncMock(
        side_effect=IntegrityError("INSERT", params={}, orig=Exception("dup"))
    )

    release, created = await resolve_or_create_release(db, uuid.uuid4(), "hotfix-1.2.3")

    assert release is winner
    assert created is False
    assert db.execute.await_count == 2
    db.begin_nested.assert_called_once()


@pytest.mark.asyncio
async def test_race_without_visible_winner_reraises():
    """
    Extremely unlikely corner: IntegrityError fires but a re-SELECT still
    can't see the winner (e.g., concurrent txn invisible under REPEATABLE
    READ). We must re-raise so the caller can retry at a higher level.
    """
    db = _make_db(None, None)  # miss, then still-missing after flush
    db.flush = AsyncMock(
        side_effect=IntegrityError("INSERT", params={}, orig=Exception("dup"))
    )

    with pytest.raises(IntegrityError):
        await resolve_or_create_release(db, uuid.uuid4(), "phantom")


@pytest.mark.asyncio
async def test_release_name_whitespace_stripped():
    db = _make_db(None)
    release, created = await resolve_or_create_release(db, uuid.uuid4(), "  v1.0  ")
    assert release.name == "v1.0"
    assert created is True


@pytest.mark.asyncio
async def test_link_race_loser_returns_false():
    """Two concurrent link_run_to_release calls: loser returns False cleanly."""
    # Two execute() calls now: the existing-link lookup, then the
    # is-this-run-already-primary lookup added by migration 0151.
    db = _make_db(None, None)
    db.flush = AsyncMock(
        side_effect=IntegrityError("INSERT", params={}, orig=Exception("uq_release_test_run"))
    )

    result = await link_run_to_release(db, uuid.uuid4(), uuid.uuid4())

    assert result is False
    db.begin_nested.assert_called_once()


@pytest.mark.asyncio
async def test_link_existing_returns_false_without_insert():
    existing = SimpleNamespace(id=uuid.uuid4())
    db = _make_db(existing)

    result = await link_run_to_release(db, uuid.uuid4(), uuid.uuid4())

    assert result is False
    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.begin_nested.assert_not_called()
