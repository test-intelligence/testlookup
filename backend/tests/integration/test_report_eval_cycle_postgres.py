"""Opt-in PostgreSQL race/rollback coverage for report evaluation cycles.

Run with ``TESTLOOKUP_POSTGRES_TEST_DSN`` pointed at a disposable or approved
test database. The tests use uniquely prefixed keys and clean their rows after
each case; they never run as part of the fast hermetic suite.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

from app.models.postgres import DecisionReportEvalCycle  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _row(key: str, *, status: str = "pass") -> DecisionReportEvalCycle:
    return DecisionReportEvalCycle(
        cycle_key=key,
        corpus_version="pg-test-v1",
        corpus_sha256="a" * 64,
        report_count=1,
        status=status,
        metrics={"reports_evaluated": 1},
        checks=[],
        unavailable_metrics=[],
        consecutive_passes=1,
    )


@pytest.fixture
async def session_factory():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _cleanup(factory, key: str) -> None:
    async with factory.begin() as db:
        await db.execute(
            delete(DecisionReportEvalCycle).where(
                DecisionReportEvalCycle.cycle_key == key
            )
        )


async def test_cycle_unique_key_allows_one_concurrent_writer(session_factory):
    key = f"pg-test-{uuid.uuid4().hex}"

    async def insert_one() -> str:
        async with session_factory() as db:
            db.add(_row(key))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return "conflict"
            return "created"

    try:
        results = await asyncio.gather(insert_one(), insert_one())
        assert sorted(results) == ["conflict", "created"]
    finally:
        await _cleanup(session_factory, key)


async def test_cycle_constraint_failure_rolls_back_and_session_recovers(session_factory):
    bad_key = f"pg-test-invalid-{uuid.uuid4().hex}"
    good_key = f"pg-test-recovery-{uuid.uuid4().hex}"
    async with session_factory() as db:
        db.add(_row(bad_key, status="not-a-status"))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        db.add(_row(good_key))
        await db.commit()
    try:
        await _cleanup(session_factory, bad_key)
        await _cleanup(session_factory, good_key)
    finally:
        # Cleanup is intentionally idempotent if the constraint failure never
        # inserted the invalid row.
        await _cleanup(session_factory, bad_key)
        await _cleanup(session_factory, good_key)
