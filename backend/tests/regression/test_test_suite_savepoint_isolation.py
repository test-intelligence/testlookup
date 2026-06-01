"""Regression: test_suite_service used ``db.rollback()`` on its injected
session to handle expected per-row uniqueness races.

Bug pinned (review/test-suite-service, 2026-06-01):

``sync_canonical_test_cases``, ``get_or_create_default_suite``,
``get_or_create_suite_by_name`` and the ``list_test_suites`` backfill all
ran ``db.add(); await db.flush()`` inside ``try/except: await db.rollback()``
on an injected session that the caller (``ingestion_pipeline._run_isolated``
or ``get_db``) commits once at the end.  A full rollback on a concurrent-
upsert race therefore discarded EVERY row already added in the batch — the
run's ``canonical_test_cases`` were silently truncated.  ``_maybe_seed_default_owner``
was worse: it swallowed a failed owner-insert flush with no rollback at all,
poisoning the session so the next operation raised ``PendingRollbackError``.

Fix: each racy insert now runs inside a SAVEPOINT (``async with
db.begin_nested():``) so only the conflicting row unwinds; siblings and the
caller's transaction survive.

This file pins both halves:

  * Behavioural — on a REAL aiosqlite ``AsyncSession`` (mocks don't model
    transactions): a SAVEPOINT around a mid-batch unique-violation preserves
    the rows flushed before it, whereas a bare ``rollback()`` loses them.
    This is the transaction-semantics contract the fix depends on.
  * Structural — the real service functions contain no bare ``await
    db.rollback()`` and do use ``db.begin_nested()``, so the fix can't
    silently regress.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import Column, Integer, String, UniqueConstraint, func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import declarative_base  # noqa: E402

_Base = declarative_base()


class _Canon(_Base):
    __tablename__ = "canon_regr"
    __table_args__ = (UniqueConstraint("project_id", "fp", name="uq_canon_regr"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False)
    fp = Column(String, nullable=False)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    return engine, AsyncSession(engine)


async def _count(db) -> int:
    return (await db.execute(select(func.count(_Canon.id)))).scalar()


@pytest.mark.asyncio
async def test_savepoint_preserves_batch_siblings_on_midbatch_race():
    """The fix: a mid-batch unique violation handled with begin_nested()
    keeps the rows added before it (and the outer txn stays usable)."""
    engine, db = await _make_session()
    try:
        # A concurrent writer already committed fp="dup".
        db.add(_Canon(project_id="p", fp="dup"))
        await db.commit()

        # Our batch: a, b, then dup (races), then c — mirroring the per-case
        # loop in sync_canonical_test_cases after the fix.
        added = 0
        for fp in ["a", "b", "dup", "c"]:
            canonical = _Canon(project_id="p", fp=fp)
            try:
                async with db.begin_nested():
                    db.add(canonical)
                    await db.flush()
            except IntegrityError:
                # lost the race — re-select the winner (as the service does)
                canonical = (
                    await db.execute(
                        select(_Canon).where(_Canon.project_id == "p", _Canon.fp == fp)
                    )
                ).scalar_one()
            added += 1
        await db.commit()

        rows = {r.fp for r in (await db.execute(select(_Canon))).scalars().all()}
        # dup, a, b, c — nothing dropped.
        assert rows == {"dup", "a", "b", "c"}
        assert await _count(db) == 4
        assert added == 4
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_bare_rollback_would_lose_siblings():
    """Meta-test proving the bug was real (and the test above is not vacuous):
    the OLD bare-rollback pattern drops the whole batch on the same race."""
    engine, db = await _make_session()
    try:
        db.add(_Canon(project_id="p", fp="dup"))
        await db.commit()

        for fp in ["a", "b", "dup"]:
            try:
                db.add(_Canon(project_id="p", fp=fp))
                await db.flush()
            except IntegrityError:
                await db.rollback()  # the OLD pattern
        await db.commit()

        rows = {r.fp for r in (await db.execute(select(_Canon))).scalars().all()}
        # a and b were collateral damage of the full rollback.
        assert rows == {"dup"}
        assert await _count(db) == 1
    finally:
        await db.close()
        await engine.dispose()


def test_service_uses_savepoints_not_injected_rollback():
    """Structural pin: the real service no longer calls db.rollback() on its
    injected session and does use begin_nested() for the racy inserts."""
    src = Path(__file__).resolve().parents[2] / "app" / "services" / "test_suite_service.py"
    text = src.read_text(encoding="utf-8")
    # Strip comments so the explanatory "db.rollback()" mentions don't match.
    code_only = "\n".join(
        line.split("#", 1)[0] for line in text.splitlines()
    )
    assert "await db.rollback()" not in code_only, (
        "test_suite_service must not rollback its injected session — use "
        "db.begin_nested() (SAVEPOINT) so a per-row race can't discard the "
        "caller's transaction"
    )
    # The racy inserts are guarded by savepoints (one per fixed site).
    assert len(re.findall(r"async with db\.begin_nested\(\)", code_only)) >= 6
