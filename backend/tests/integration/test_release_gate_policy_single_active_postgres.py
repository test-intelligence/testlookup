"""GATE-03 on real PostgreSQL: exactly one active release-gate policy per scope.

1. Two admins publishing two DIFFERENT drafts of one scope at the same moment:
   one 200, one 409 with the race text, exactly one active row -- for a project
   scope and for the system default (``project_id IS NULL``). The same draft
   published twice: one 200, one 409 ``Policy is already published``. The race
   is made deterministic by holding the scope's advisory lock on a third
   connection until both publishes are waiting on it.
2. Direct SQL: a second active row in a project scope, and in the system scope,
   raises naming the partial unique index; so does a duplicate system version.
3. Thirty sequential publishes never raise and leave one active row: the
   demote is flushed before the promote (SQLAlchemy orders persistent UPDATEs
   by primary key, and policy ids are random uuid4).
4. Eight concurrent draft creates in one scope get versions 1..8, no 409/500.
5. Migration 0210 on a scratch table: duplicates demoted (highest version kept,
   ties by ``activated_at`` then ``id``) and logged, duplicate system versions
   renumbered, the three indexes built; the downgrade drops exactly them; a
   second upgrade is a no-op.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
Policies created here are deleted at teardown, and any system-default policy
that was active before the test is active again afterwards.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    import app.db.postgres as app_postgres
    import app.services.cache_service as cache_service

    async def _no_bump(*_a, **_kw):
        return None

    # The readiness-band cache bump is Redis; irrelevant to the race.
    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _no_bump)
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=12, max_overflow=0)
    tag = uuid.uuid4().hex[:10]
    admin_id, p1, p2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        for pid in (p1, p2):
            await conn.execute(text(
                "INSERT INTO projects (id, name, slug, is_active) VALUES (:id, :n, :n, true)"
            ), {"id": pid, "n": f"g3-{pid.hex[:10]}"})
        await conn.execute(text(
            "INSERT INTO users (id, email, username, full_name, hashed_password, role) "
            "VALUES (:id, :e, :u, 'GATE-03', '!unusable', 'ADMIN')"
        ), {"id": admin_id, "e": f"g3-{tag}@example.com", "u": f"g3_{tag}"})
        system_active = [row[0] for row in (await conn.execute(text(
            "SELECT id FROM release_gate_policies WHERE project_id IS NULL AND is_active IS TRUE"
        ))).all()]
    admin = SimpleNamespace(id=admin_id, role="ADMIN", username=f"g3_{tag}", is_active=True)
    try:
        yield SimpleNamespace(engine=engine, admin=admin, p1=p1, p2=p2, system_active=system_active)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM release_gate_policies WHERE created_by = :u"), {"u": admin_id})
            if system_active:
                await conn.execute(text(
                    "UPDATE release_gate_policies SET is_active = true WHERE id = ANY(:ids)"
                ), {"ids": system_active})
            await conn.execute(text("DELETE FROM projects WHERE id IN (:a, :b)"), {"a": p1, "b": p2})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": admin_id})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _session(world) -> AsyncSession:
    return AsyncSession(world.engine, expire_on_commit=False)


async def _create(world, project_id, name: str):
    from app.models.schemas import ReleaseGatePolicyCreate
    from app.routers.release_gate_policies import create_policy

    async with _session(world) as db:
        return await create_policy(
            ReleaseGatePolicyCreate(project_id=project_id, name=name),
            current_user=world.admin,
            db=db,
        )


async def _publish(world, policy_id, pids: list[int] | None = None):
    """(status, detail) of one publish on its own session."""
    from app.routers.release_gate_policies import publish_policy

    async with _session(world) as db:
        if pids is not None:
            pids.append((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        try:
            await publish_policy(policy_id, current_user=world.admin, db=db)
        except HTTPException as exc:
            await db.rollback()
            return exc.status_code, exc.detail
        return 200, None


async def _active_in(world, project_id) -> list[uuid.UUID]:
    async with world.engine.connect() as conn:
        if project_id is None:
            rows = await conn.execute(text(
                "SELECT id FROM release_gate_policies WHERE project_id IS NULL AND is_active IS TRUE"
            ))
        else:
            rows = await conn.execute(text(
                "SELECT id FROM release_gate_policies WHERE project_id = :p AND is_active IS TRUE"
            ), {"p": project_id})
        return [row[0] for row in rows.all()]


async def _race(world, project_id, first_id, second_id):
    """Both publishes read the scope, then wait on its lock; then both run."""
    from app.routers.release_gate_policies import _scope_lock_key

    pids: list[int] = []
    async with world.engine.connect() as blocker:
        tx = await blocker.begin()
        await blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _scope_lock_key(project_id)},
        )
        tasks = [
            asyncio.create_task(_publish(world, first_id, pids)),
            asyncio.create_task(_publish(world, second_id, pids)),
        ]
        deadline = asyncio.get_running_loop().time() + 15
        async with world.engine.connect() as observer:
            while True:
                assert not any(t.done() for t in tasks), "a publish finished without waiting for the lock"
                waiting = 0
                if len(pids) == 2:
                    waiting = (await observer.execute(text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND NOT granted AND pid = ANY(:pids)"
                    ), {"pids": pids})).scalar_one()
                    await observer.commit()
                if waiting == 2:
                    break
                assert asyncio.get_running_loop().time() < deadline, "publishes never reached the lock"
                await asyncio.sleep(0.02)
        await tx.rollback()
    return await asyncio.wait_for(asyncio.gather(*tasks), 30)


@pytest.mark.parametrize("scope", ["project", "system"])
async def test_two_drafts_published_at_once_leave_one_active(world, scope):
    from app.routers.release_gate_policies import PUBLISH_RACE_DETAIL

    project_id = world.p1 if scope == "project" else None
    a = await _create(world, project_id, f"A-{scope}")
    b = await _create(world, project_id, f"B-{scope}")
    results = await _race(world, project_id, a.id, b.id)
    assert sorted(code for code, _ in results) == [200, 409], results
    assert [detail for code, detail in results if code == 409] == [PUBLISH_RACE_DETAIL]
    active = await _active_in(world, project_id)
    assert len(active) == 1 and active[0] in {a.id, b.id}


async def test_the_same_draft_published_twice_is_published_once(world):
    from app.routers.release_gate_policies import ALREADY_PUBLISHED_DETAIL

    a = await _create(world, world.p1, "Same")
    results = await _race(world, world.p1, a.id, a.id)
    assert sorted(results, key=lambda r: r[0]) == [(200, None), (409, ALREADY_PUBLISHED_DETAIL)]
    assert await _active_in(world, world.p1) == [a.id]


_INSERT = (
    "INSERT INTO release_gate_policies (id, project_id, version, name, rules, is_active, "
    "is_draft, created_by) VALUES (gen_random_uuid(), :p, :v, 'sql', CAST('{}' AS json), "
    ":active, false, :u)"
)


async def _insert_violation(world, rows) -> str:
    async with world.engine.connect() as conn:
        tx = await conn.begin()
        try:
            with pytest.raises(IntegrityError) as exc:
                for row in rows:
                    await conn.execute(text(_INSERT), {**row, "u": world.admin.id})
            return str(exc.value.orig)
        finally:
            await tx.rollback()


async def test_the_database_refuses_a_second_active_row_in_a_project_scope(world):
    message = await _insert_violation(world, [
        {"p": world.p2, "v": 1, "active": True}, {"p": world.p2, "v": 2, "active": True},
    ])
    assert "uq_rgp_one_active_project" in message


async def test_the_database_refuses_a_second_active_system_default(world):
    # The NULL-scope proof: a plain unique index on project_id cannot see it.
    message = await _insert_violation(world, [
        {"p": None, "v": 900001, "active": True}, {"p": None, "v": 900002, "active": True},
    ])
    assert "uq_rgp_one_active_system" in message


async def test_the_database_refuses_a_duplicate_system_version(world):
    message = await _insert_violation(world, [
        {"p": None, "v": 900003, "active": False}, {"p": None, "v": 900003, "active": False},
    ])
    assert "uq_rgp_system_version" in message


async def test_thirty_sequential_publishes_never_raise(world):
    previous = None
    for i in range(30):
        draft = await _create(world, world.p2, f"seq-{i % 2}-{i}")
        assert await _publish(world, draft.id) == (200, None), f"publish {i} failed"
        if previous is not None:
            assert await _active_in(world, world.p2) == [draft.id]
        previous = draft
    assert len(await _active_in(world, world.p2)) == 1


async def test_eight_concurrent_creates_get_versions_one_to_eight(world):
    async def _one(i):
        try:
            policy = await _create(world, world.p1, f"concurrent-{i}")
        except HTTPException as exc:
            return exc.status_code
        return policy.version

    versions = await asyncio.gather(*(_one(i) for i in range(8)))
    assert sorted(versions) == list(range(1, 9)), versions


async def test_a_create_collision_is_a_409_not_a_500(world, monkeypatch):
    """The backstop for a writer that skips the lock: a version collision
    surfaces as the 409 text, never as an unhandled IntegrityError."""
    from app.routers import release_gate_policies as router

    first = await _create(world, world.p1, "first")

    async def _skip_lock(*_a, **_kw):
        return None

    real_execute = AsyncSession.execute

    async def _stale_max(self, statement, *args, **kwargs):
        result = await real_execute(self, statement, *args, **kwargs)
        sql = str(statement)
        if "max(release_gate_policies.version)" in sql:
            # Pretend the max was read before ``first`` committed.
            return SimpleNamespace(scalar=lambda: first.version - 1)
        return result

    monkeypatch.setattr(router, "_lock_scope", _skip_lock)
    monkeypatch.setattr(AsyncSession, "execute", _stale_max)
    with pytest.raises(HTTPException) as exc:
        await _create(world, world.p1, "second")
    assert exc.value.status_code == 409
    assert exc.value.detail == router.CREATE_RACE_DETAIL


# ── Migration 0210 on a scratch table ───────────────────────────────────────


def _m0210():
    spec = importlib.util.spec_from_file_location("m_0210_pg", VERSIONS / "0210_release_gate_policy_single_active.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ts(day: str | None) -> datetime | None:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc) if day else None


async def _migrate(conn, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _m0210()

    def _run(sync_conn):
        module.op = Operations(MigrationContext.configure(sync_conn))
        getattr(module, step)()

    await conn.run_sync(_run)


async def test_migration_0210_repairs_duplicates_and_round_trips(world, caplog):
    engine = world.engine
    async with engine.connect() as conn:
        tx = await conn.begin()
        try:
            base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
            scratch = f"g3_{uuid.uuid4().hex[:10]}"
            await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
            # DEFAULTS only: no indexes (the base table has 0210's), no FKs.
            await conn.execute(text(
                f'CREATE TABLE "{scratch}".release_gate_policies '
                f'(LIKE "{base}".release_gate_policies INCLUDING DEFAULTS)'
            ))
            await conn.execute(text(f'SET LOCAL search_path TO "{scratch}", "{base}"'))
            project = uuid.uuid4()
            ids = {name: uuid.uuid4() for name in ("s1", "s2", "s3", "s4", "p1", "p2")}
            rows = [
                # System scope: three actives, two of them sharing version 2.
                ("s1", None, 1, True, "2026-01-01", "2026-01-01"),
                ("s2", None, 2, True, "2026-02-01", "2026-02-01"),
                ("s3", None, 2, True, "2026-03-01", "2026-03-02"),
                ("s4", None, 1, False, "2026-04-01", None),          # a duplicate v1 draft
                # Project scope: two actives.
                ("p1", project, 4, True, "2026-01-05", "2026-01-05"),
                ("p2", project, 3, True, "2026-01-06", "2026-01-07"),
            ]
            for name, pid, version, active, created, activated in rows:
                await conn.execute(text(
                    "INSERT INTO release_gate_policies (id, project_id, version, name, rules, "
                    "is_active, is_draft, created_by, created_at, activated_at) VALUES "
                    "(:id, :p, :v, :n, CAST('{}' AS json), :a, false, :u, :c, :act)"
                ), {"id": ids[name], "p": pid, "v": version, "n": name, "a": active,
                    "u": world.admin.id, "c": _ts(created), "act": _ts(activated)})

            with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
                await _migrate(conn, "upgrade")
            state = {
                row[0]: (row[1], row[2]) for row in (await conn.execute(text(
                    "SELECT name, version, is_active FROM release_gate_policies"
                ))).all()
            }
            # One active per scope: the highest version; v2 is tied in the
            # system scope and the later activation (s3) wins.
            assert {n for n, (_, active) in state.items() if active} == {"s3", "p1"}
            system_versions = [v for n, (v, _) in state.items() if n.startswith("s")]
            assert len(system_versions) == len(set(system_versions)), state
            # Earliest-created keeps the version; the others move past the max.
            assert state["s1"][0] == 1 and state["s2"][0] == 2
            assert sorted([state["s3"][0], state["s4"][0]]) == [3, 4]
            for name in ("s1", "s2", "p2"):
                assert f"0210 demoted duplicate active policy {ids[name]}" in caplog.text, name
            assert f"(scope {project}, v3)" in caplog.text and "(scope system, v1)" in caplog.text
            indexes = {row[0] for row in (await conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = :s"
            ), {"s": scratch})).all()}
            assert indexes == {"uq_rgp_one_active_project", "uq_rgp_one_active_system", "uq_rgp_system_version"}

            await _migrate(conn, "downgrade")
            after_down = {row[0] for row in (await conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = :s"
            ), {"s": scratch})).all()}
            assert after_down == set(), "the downgrade drops exactly the three indexes"

            snapshot = (await conn.execute(text(
                "SELECT id, version, is_active, updated_at FROM release_gate_policies ORDER BY id"
            ))).all()
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
                await _migrate(conn, "upgrade")
                await _migrate(conn, "upgrade")
            assert "0210" not in caplog.text, "a re-run has nothing to repair"
            assert (await conn.execute(text(
                "SELECT id, version, is_active, updated_at FROM release_gate_policies ORDER BY id"
            ))).all() == snapshot
        finally:
            await tx.rollback()
