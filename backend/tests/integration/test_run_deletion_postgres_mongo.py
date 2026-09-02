"""S2c — a single-run delete, against a database that enforces its constraints.

Three properties here cannot be observed by any mocked session, and each of
them is a way the delete can look correct while being wrong:

1. **Materialize before cascade.** The Postgres CASCADE destroys the only
   mapping from a run to its Mongo documents and MinIO keys. If the executor
   resolves those ids after the run delete, every cross-store delete silently
   becomes a no-op against an empty list — the counts still look plausible.

2. **The scope cannot widen.** ``resolve_run_candidates`` pins five
   project-wide clocks to ``false()``. A ``_FakeDB`` returns whatever it was
   handed regardless of the WHERE clause, so only a real database can show
   that the project's OTHER runs and its audit rows are still there afterwards.

3. **The tombstone shares the deletion's transaction.** A rollback must take
   both or neither. A fake session that cannot roll back proves nothing.

**Store coverage is honest about its limits.** CI supplies real PostgreSQL and
real MongoDB; it does not run MinIO, so object storage is a recording double.
That is sufficient for what is asserted about it — *which prefixes were handed
to delete_prefix, and when* — and insufficient for anything about MinIO's own
behaviour, which this file therefore does not claim.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _RecordingStorage:
    """Records the prefixes handed to it. CI runs no MinIO."""

    def __init__(self):
        self.deleted_prefixes: list[str] = []
        self.deleted_objects: list[str] = []

    async def list_objects(self, prefix, *, bucket=None):
        return []

    async def delete_prefix(self, prefix, *, bucket=None):
        self.deleted_prefixes.append(prefix)
        return 0

    async def delete_object(self, key, *, bucket=None):
        self.deleted_objects.append(key)
        return None


class _RecordingMongoCollection:
    def __init__(self, journal, name):
        self._journal = journal
        self._name = name

    async def count_documents(self, query):
        return 0

    async def delete_many(self, query):
        ids = next(iter(query.values()), {})
        self._journal.append((self._name, list(ids.get("$in", []))))
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


async def _seed(conn, *, project_id, runs):
    await conn.execute(
        text(
            "INSERT INTO projects (id, name, slug, is_active, created_at, updated_at) "
            "VALUES (:i, :n, :s, true, now(), now())"
        ),
        {"i": project_id, "n": f"s2c-{project_id}", "s": f"s2c-{project_id}"},
    )
    for run_id in runs:
        await conn.execute(
            text(
                "INSERT INTO test_runs "
                "(id, project_id, build_number, status, created_at) "
                "VALUES (:i, :p, :b, 'PASSED', now())"
            ),
            {"i": run_id, "p": project_id, "b": f"ci-{run_id.hex[:8]}"},
        )
        await conn.execute(
            text(
                "INSERT INTO test_cases "
                "(id, test_run_id, test_fingerprint, test_name, status) "
                "VALUES (:i, :r, :f, :n, 'PASSED')"
            ),
            {
                "i": uuid.uuid4(),
                "r": run_id,
                "f": f"fp-{run_id.hex[:12]}",
                "n": f"case-{run_id.hex[:6]}",
            },
        )


async def _delete_one_run(
    session, *, project_id, run_id, minio_prefix, mongo, storage, commit=True
):
    """Calls the PRODUCTION transaction, not a copy of it.

    The first version of this file re-implemented the sequence here. Its
    rollback assertion then proved a property of SQLAlchemy rather than
    anything about the ordering in ``perform_run_deletion`` — a mutation that
    split the tombstone into a separate commit survived it.
    """
    from app.services.run_deletion_service import perform_run_deletion

    counts = await perform_run_deletion(
        session,
        run=SimpleNamespace(
            id=run_id, project_id=project_id, minio_prefix=minio_prefix
        ),
        mongo=mongo,
        storage=storage,
        search_index_documents=None,
        reason="integration test",
    )
    if commit:
        await session.commit()
    else:
        await session.rollback()
    return counts


async def test_deleting_one_run_leaves_the_projects_other_runs_alone():
    """The scope-widening failure, against a database that would show it.

    ``resolve_run_candidates`` pins the project-wide clocks to ``false()``. If
    any of them inherited the nightly purge's filters, this project's second
    run — and its audit rows — would go too, and the counts would still read
    like a single-run delete.
    """
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    doomed, survivor = uuid.uuid4(), uuid.uuid4()
    journal: list[tuple] = []

    try:
        async with engine.begin() as conn:
            await _seed(conn, project_id=project_id, runs=[doomed, survivor])
            await conn.execute(
                text(
                    "INSERT INTO access_audit_logs (id, project_id, action) "
                    "VALUES (:i, :p, 'read')"
                ),
                {"i": uuid.uuid4(), "p": project_id},
            )

        async with factory() as session:
            counts = await _delete_one_run(
                session,
                project_id=project_id,
                run_id=doomed,
                minio_prefix=None,
                mongo=_RecordingMongo(journal),
                storage=_RecordingStorage(),
            )

        async with engine.connect() as conn:
            remaining = (
                await conn.execute(
                    text("SELECT count(*) FROM test_runs WHERE project_id = :p"),
                    {"p": project_id},
                )
            ).scalar()
            audit_rows = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM access_audit_logs WHERE project_id = :p"
                    ),
                    {"p": project_id},
                )
            ).scalar()
            gone = (
                await conn.execute(
                    text("SELECT count(*) FROM test_runs WHERE id = :i"),
                    {"i": doomed},
                )
            ).scalar()

        assert gone == 0, "the targeted run survived"
        assert remaining == 1, (
            "a single-run delete removed the project's other runs — the "
            "project-wide clocks leaked into the run-scoped plan"
        )
        assert audit_rows == 1, (
            "a single-run delete took the project's audit trail with it"
        )
        assert counts["postgres"]["runs"] == 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
        await engine.dispose()


async def test_the_cross_store_ids_are_resolved_before_the_cascade():
    """Materialize-before-cascade, proved where the FK is real.

    Asserts the Mongo delete was handed the run's id, which is only possible
    if the id was collected while the Postgres row still existed.
    """
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    journal: list[tuple] = []
    storage = _RecordingStorage()

    try:
        async with engine.begin() as conn:
            await _seed(conn, project_id=project_id, runs=[run_id])

        async with factory() as session:
            await _delete_one_run(
                session,
                project_id=project_id,
                run_id=run_id,
                minio_prefix=None,
                mongo=_RecordingMongo(journal),
                storage=storage,
            )

        assert journal, "the delete never visited Mongo at all"

        by_collection = {name: ids for name, ids in journal}
        # The run id reaches Mongo through THREE independent candidate lists,
        # so "some collection got it" is satisfied while one of them is empty.
        # Each collection is asserted against the list that actually feeds it.
        for collection in ("raw_allure_json", "execution_logs", "run_summaries",
                           "live_execution_events"):
            assert collection in by_collection, (
                f"{collection} was never visited by the delete"
            )
            assert str(run_id) in by_collection[collection], (
                f"{collection} was handed an id set without this run — its "
                "candidate list was resolved after the Postgres cascade "
                "destroyed the mapping, so the delete silently did nothing"
            )

        assert f"uploads/{project_id}/{run_id}/" in storage.deleted_prefixes
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
        await engine.dispose()


async def test_a_prefix_outside_the_project_never_reaches_object_storage():
    """RET-D9 end to end.

    ``minio_prefix`` is uploader-derived. A sentinel at
    ``uploads/shared/upload_complete.json`` yields the prefix
    ``uploads/shared/`` — honouring it wipes a shared area for every project.
    """
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    storage = _RecordingStorage()

    try:
        async with engine.begin() as conn:
            await _seed(conn, project_id=project_id, runs=[run_id])
            await conn.execute(
                text("UPDATE test_runs SET minio_prefix = :p WHERE id = :i"),
                {"p": "uploads/shared/", "i": run_id},
            )

        async with factory() as session:
            await _delete_one_run(
                session,
                project_id=project_id,
                run_id=run_id,
                minio_prefix="uploads/shared/",
                mongo=_RecordingMongo([]),
                storage=storage,
            )

        assert "uploads/shared/" not in storage.deleted_prefixes, (
            "a crafted prefix reached delete_prefix and would have wiped a "
            "shared area belonging to every project"
        )
        assert storage.deleted_prefixes == [f"uploads/{project_id}/{run_id}/"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
        await engine.dispose()


async def test_the_tombstone_lands_in_the_same_transaction_as_the_delete():
    """Both or neither, through the production transaction.

    A rollback that took the deletion but left the tombstone would block live
    ingestion into a run that still exists. The reverse leaves the run gone
    with nothing stopping the five re-creation paths bringing it back.
    """
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    try:
        async with engine.begin() as conn:
            await _seed(conn, project_id=project_id, runs=[run_id])

        async with factory() as session:
            await _delete_one_run(
                session,
                project_id=project_id,
                run_id=run_id,
                minio_prefix=None,
                mongo=_RecordingMongo([]),
                storage=_RecordingStorage(),
                commit=False,
            )

        async with engine.connect() as conn:
            run_rows = (
                await conn.execute(
                    text("SELECT count(*) FROM test_runs WHERE id = :i"),
                    {"i": run_id},
                )
            ).scalar()
            tombstones = (
                await conn.execute(
                    text("SELECT count(*) FROM run_tombstones WHERE run_id = :i"),
                    {"i": run_id},
                )
            ).scalar()

        assert run_rows == 1, "the rollback did not restore the run"
        assert tombstones == 0, (
            "the tombstone survived a rollback that restored the run — it "
            "would block live ingestion into a run that exists"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
        await engine.dispose()


async def test_the_tombstone_survives_the_run_it_names():
    """``run_tombstones.run_id`` must NOT be a foreign key to ``test_runs``.

    Asserted against the live catalog rather than the ORM: a FK added in a
    later migration would make the tombstone impossible to write, and the
    delete would fail at the last step with everything else already gone.
    """
    engine = create_async_engine(_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    try:
        async with engine.begin() as conn:
            await _seed(conn, project_id=project_id, runs=[run_id])

        async with factory() as session:
            await _delete_one_run(
                session,
                project_id=project_id,
                run_id=run_id,
                minio_prefix=None,
                mongo=_RecordingMongo([]),
                storage=_RecordingStorage(),
            )

        async with engine.connect() as conn:
            tombstones = (
                await conn.execute(
                    text("SELECT count(*) FROM run_tombstones WHERE run_id = :i"),
                    {"i": run_id},
                )
            ).scalar()
            fks = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.table_constraints "
                        "WHERE table_name = 'run_tombstones' "
                        "AND constraint_type = 'FOREIGN KEY' "
                        "AND constraint_name LIKE '%run_id%'"
                    )
                )
            ).scalar()

        assert tombstones == 1, "no tombstone was written for the deleted run"
        assert fks == 0, (
            "run_tombstones.run_id has a foreign key; the row it names is "
            "deleted, so the tombstone could never be written"
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM projects WHERE id = :i"), {"i": project_id}
            )
            await conn.execute(
                text("DELETE FROM run_tombstones WHERE run_id = :i"), {"i": run_id}
            )
        await engine.dispose()
