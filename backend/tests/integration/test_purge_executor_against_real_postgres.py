"""S2a — the extracted executor, against a real database.

``run_purge``'s execute half was moved into ``execute_candidates`` so the
scheduled purge, single-run deletion and criteria deletion can share one
executor. The move is provably faithful at the source level — the body is
byte-identical once the ``plan.`` qualifiers are removed — but source identity
is not behavioural identity, and the property that matters here cannot be
observed by any mocked session:

**Materialize before cascade.** The Postgres CASCADE destroys the only mapping
from a run to its Mongo documents and MinIO keys. If the executor ever runs the
run delete before collecting those ids, the cross-store deletes silently become
no-ops against empty id lists — every unit test still passes, every count still
reports success, and the data is orphaned forever.

The existing unit suite asserts that ordering through a fake session's event
journal, which is worth having and is not the same thing: it proves the code
issues statements in an order, not that the database agrees. `_FakeDB` cannot
execute a foreign key.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``. Mongo and object storage are
recording doubles: the assertion is about *what ids reached them and when*,
which does not need those services to be real.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _RecordingMongoCollection:
    def __init__(self, journal, name):
        self._journal = journal
        self._name = name

    async def count_documents(self, query):
        return 0

    async def delete_many(self, query):
        # Record the ids the purge believed were in scope. An empty list here
        # is the failure this test exists to catch.
        ids = next(iter(query.values()), {})
        self._journal.append(("mongo", self._name, list(ids.get("$in", []))))
        return type("R", (), {"deleted_count": 0})()

    async def find(self, *_a, **_kw):
        return self

    async def to_list(self, *_a, **_kw):
        return []


class _RecordingMongo:
    def __init__(self, journal):
        self._journal = journal

    def __getitem__(self, name):
        return _RecordingMongoCollection(self._journal, name)


class _RecordingStorage:
    def __init__(self, journal):
        self._journal = journal

    async def list_objects(self, prefix, *, bucket=None):
        return []

    async def delete_prefix(self, prefix, *, bucket=None):
        self._journal.append(("minio", prefix, None))
        return 0

    async def delete_object(self, key, *, bucket=None):
        self._journal.append(("minio_object", key, None))
        return None


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_the_executor_resolves_cross_store_ids_before_the_cascade():
    """The ordering invariant, proved against a database that enforces the FK.

    Seeds one aged run with a test case, purges in execute mode, and asserts
    the Mongo delete was handed the run's id — which is only possible if the
    id was materialized while the Postgres row still existed.
    """
    from app.services import retention_service

    engine = create_async_engine(_dsn())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    aged = datetime.now(timezone.utc) - timedelta(days=4000)
    journal: list[tuple] = []

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, is_active, created_at, updated_at) "
                    "VALUES (:i, :n, :s, true, now(), now())"
                ),
                {"i": project_id, "n": f"s2a-{project_id}", "s": f"s2a-{project_id}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO project_retention_policies "
                    "(id, project_id, enabled, raw_events_days, runs_days, "
                    " artifacts_days, audit_days, created_at, updated_at) "
                    "VALUES (:i, :p, true, 7, 30, 7, 365, now(), now())"
                ),
                {"i": uuid.uuid4(), "p": project_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, build_number, status, created_at) "
                    "VALUES (:i, :p, :b, 'PASSED', :c)"
                ),
                {"i": run_id, "p": project_id, "b": f"ci-{run_id.hex[:8]}", "c": aged},
            )

        async with session_factory() as session:
            out = await retention_service.run_purge(
                session,
                project_id=project_id,
                mode="execute",
                mongo=_RecordingMongo(journal),
                storage=_RecordingStorage(journal),
            )
            await session.commit()

        mongo_calls = [entry for entry in journal if entry[0] == "mongo"]
        assert mongo_calls, "the purge never visited Mongo at all"
        assert any(str(run_id) in ids for _, _, ids in mongo_calls), (
            "no Mongo delete carried the purged run's id — the ids were "
            "resolved AFTER the Postgres cascade had already destroyed the "
            "mapping, so the cross-store delete silently did nothing"
        )

        async with engine.connect() as conn:
            still_there = (
                await conn.execute(
                    text("SELECT count(*) FROM test_runs WHERE id = :i"),
                    {"i": run_id},
                )
            ).scalar()
        assert still_there == 0, "the run should have been deleted"
        assert out["mode"] == "execute"
        assert out["counts"]["postgres"]["runs"] == 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
        await engine.dispose()


async def test_a_preview_against_a_real_database_writes_nothing():
    """Preview/execute is a contract an ADMIN authorises from.

    Worth proving against a real database rather than a fake: a preview that
    writes would be caught by no unit test whose session cannot commit.
    """
    from app.services import retention_service

    engine = create_async_engine(_dsn())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    aged = datetime.now(timezone.utc) - timedelta(days=4000)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, is_active, created_at, updated_at) "
                    "VALUES (:i, :n, :s, true, now(), now())"
                ),
                {"i": project_id, "n": f"s2a-p-{project_id}", "s": f"s2a-p-{project_id}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO test_runs (id, project_id, build_number, status, created_at) "
                    "VALUES (:i, :p, :b, 'PASSED', :c)"
                ),
                {"i": run_id, "p": project_id, "b": f"ci-{run_id.hex[:8]}", "c": aged},
            )

        journal: list[tuple] = []
        async with session_factory() as session:
            out = await retention_service.run_purge(
                session,
                project_id=project_id,
                mode="preview",
                mongo=_RecordingMongo(journal),
                storage=_RecordingStorage(journal),
            )
            await session.commit()

        assert out["mode"] == "preview"
        assert out["candidates"]["runs"] >= 1

        async with engine.connect() as conn:
            still_there = (
                await conn.execute(
                    text("SELECT count(*) FROM test_runs WHERE id = :i"),
                    {"i": run_id},
                )
            ).scalar()
        assert still_there == 1, "preview deleted a run"
        assert journal == [], "preview issued cross-store deletes"
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
        await engine.dispose()
