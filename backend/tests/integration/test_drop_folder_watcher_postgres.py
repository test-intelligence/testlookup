"""W34 STRAT-13 against PostgreSQL at head: migration 0254, the real staged SQL and a real sweep.

What the unit tests cannot show without a database: the flag row 0254 seeded
(read by the real flag reader), the two CHECKs and the unique build prefix, the
``app_settings`` baseline written once, the multi-row upsert that never
rewrites a terminal row, the partial-index abandonment, and the whole sweep on
a ONE-connection pool (``pool_size=1, max_overflow=0``: a second session opened
while one is held would time out, so "one session at a time" is proven, not
assumed). Storage is the real ``LocalStorageProvider`` on ``tmp_path``; the
Redis lock and ``ingest_test_run.delay`` are faked (no broker), the flag cache
is bypassed (Postgres is read). Last, 0254 upgrade -> downgrade -> upgrade on a
THROWAWAY database (created and dropped here).

Cleanup: every project, row and the baseline this module writes are deleted
(the baseline row is restored if it existed): CI shares ONE Postgres database.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` at head; skips otherwise.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
#: Years in the past on purpose: the sweep's 24 h abandonment is table-wide, and a clock this old can only
#: ever reach this module's own rows, never a row a local demo (or another module) left with a real time.
T0 = datetime(2020, 1, 6, 12, 0, tzinfo=timezone.utc)
BUCKET = "dfw-it"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A one-connection session factory as the app's, two seeded projects, a local store, fakes."""
    from app.core.config import settings
    from app.db import postgres as app_postgres
    from app.db import storage as storage_module
    from app.models.postgres import AppSetting, DropFolderBuild, Project
    from app.services import drop_folder_watcher as dfw
    from app.services import feature_flags
    from app.worker import tasks

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=1, max_overflow=0, pool_timeout=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app_postgres, "AsyncSessionLocal", factory, raising=False)

    async def _no_cache(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(feature_flags, "_load_from_redis", _no_cache)
    monkeypatch.setattr(feature_flags, "_store_in_redis", _no_cache)
    held: set[str] = set()

    async def _is_duplicate(key: str, ttl: int = 3600, owner: str | None = None) -> bool:
        if key in held:
            return True
        held.add(key)
        return False

    async def _release(key: str, owner: str) -> None:
        held.discard(key)

    monkeypatch.setattr(tasks, "_is_duplicate", _is_duplicate)
    monkeypatch.setattr(tasks, "_release_duplicate_lock", _release)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tasks, "ingest_test_run", SimpleNamespace(
        delay=lambda **kw: calls.append(kw) or SimpleNamespace(id=f"it-task-{len(calls)}")))
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    store = storage_module.LocalStorageProvider(default_bucket=BUCKET)
    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: store)

    tag = uuid.uuid4().hex[:10]
    projects = [Project(id=uuid.uuid4(), name=f"DFW {tag} {n}", slug=f"dfw-{tag}-{n}", is_active=True) for n in (1, 2)]
    async with factory() as db:
        saved = (await db.execute(select(AppSetting.value).where(AppSetting.key == dfw.BASELINE_KEY))).first()
        await db.execute(delete(AppSetting).where(AppSetting.key == dfw.BASELINE_KEY))
        db.add_all(projects)
        await db.commit()
    segments = [s for p in projects for s in (str(p.id), p.slug)]
    try:
        yield SimpleNamespace(factory=factory, store=store, root=tmp_path / BUCKET, calls=calls, projects=projects,
                              tasks=tasks, dfw=dfw)
    finally:
        async with factory() as db:
            await db.execute(delete(DropFolderBuild).where(DropFolderBuild.project_segment.in_(segments)))
            await db.execute(delete(Project).where(Project.id.in_([p.id for p in projects])))
            await db.execute(delete(AppSetting).where(AppSetting.key == dfw.BASELINE_KEY))
            if saved is not None:
                db.add(AppSetting(key=dfw.BASELINE_KEY, value=saved[0], is_secret_backed=False))
            await db.commit()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _drop(root: Path, key: str, at: datetime) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"jenkins_job": "it"}')
    os.utime(path, (at.timestamp(), at.timestamp()))


async def _rows(world: Any) -> dict[str, Any]:
    from app.models.postgres import DropFolderBuild

    segments = [s for p in world.projects for s in (str(p.id), p.slug)]
    async with world.factory() as db:
        rows = (await db.execute(
            select(DropFolderBuild).where(DropFolderBuild.project_segment.in_(segments))
        )).scalars().all()
    return {row.build_prefix: row for row in rows}


# ── 1. what 0254 put in the schema ───────────────────────────────────────────


async def test_0254_seeded_the_flag_on_and_the_reader_answers_on(world) -> None:
    from app.models.postgres import FeatureFlag
    from app.services import feature_flags

    async with world.factory() as db:
        flag = (await db.execute(select(FeatureFlag).where(FeatureFlag.key == "drop_folder_watcher"))).scalar_one()
        assert (flag.enabled_global, flag.rollout_percent, flag.enabled_projects, flag.enabled_roles) == (
            True, 100, None, None)
        assert flag.description.startswith("ON: a beat sweep polls")
        assert await feature_flags.is_enabled("drop_folder_watcher", db=db, any_scope=True, fresh=True)
        assert await feature_flags.is_enabled("drop_folder_watcher", db=db, project_id=world.projects[0].id,
                                              fresh=True)


@pytest.mark.parametrize("values, constraint", [
    ({"status": "bogus"}, "ck_dfb_status"),
    ({"status": "queued", "queued_at": T0}, "ck_dfb_queued_has_task"),
    ({"status": "queued", "task_id": "t"}, "ck_dfb_queued_has_task"),
])
async def test_the_checks_refuse_a_bad_row(world, values: dict, constraint: str) -> None:
    from app.models.postgres import DropFolderBuild

    async with world.factory() as db:
        db.add(DropFolderBuild(build_prefix=f"{world.projects[0].slug}/runs/x/",
                               project_segment=world.projects[0].slug, **values))
        with pytest.raises(IntegrityError, match=constraint):
            await db.flush()
        await db.rollback()


async def test_the_build_prefix_is_unique_and_a_terminal_row_is_never_rewritten(world) -> None:
    from app.models.postgres import DropFolderBuild

    dfw, slug = world.dfw, world.projects[0].slug
    prefix = f"{slug}/runs/5/"
    async with world.factory() as db:
        await dfw.record_decisions(db, [dfw.BuildDecision(prefix, "queued", sentinel_etag="e1",
                                                          sentinel_last_modified=T0, queued_at=T0,
                                                          task_id="first")], T0)
        await db.commit()
    async with world.factory() as db:     # a pending decision for the same folder changes nothing
        await dfw.record_decisions(db, [dfw.BuildDecision(prefix, "pending")], T0 + timedelta(minutes=5))
        await db.commit()
        db.add(DropFolderBuild(build_prefix=prefix, project_segment=slug))
        with pytest.raises(IntegrityError, match="uq_dfb_build_prefix"):
            await db.flush()
        await db.rollback()
    row = (await _rows(world))[prefix]
    assert (row.status, row.task_id, row.sentinel_etag, row.first_seen_at, row.last_checked_at) == (
        "queued", "first", "e1", T0, T0)


# ── 2. a real sweep ──────────────────────────────────────────────────────────


async def test_a_real_sweep_baselines_history_queues_new_and_abandons_stale(world) -> None:
    from app.models.postgres import AppSetting

    one, two = world.projects
    sweep = world.tasks._drop_folder_watcher_sweep
    _drop(world.root, f"{one.slug}/runs/1/upload_complete.json", T0 - timedelta(days=1))       # history
    _drop(world.root, f"{two.id}/runs/2/allure/a-result.json", T0 - timedelta(hours=1))       # no sentinel

    out = await sweep(owner="it", now=T0)
    assert out["failed"] == 0 and out["baseline"] == 1 and out["pending"] == 1 and world.calls == []
    rows = await _rows(world)
    assert {p: r.status for p, r in rows.items()} == {f"{one.slug}/runs/1/": "baseline", f"{two.id}/runs/2/": "pending"}
    assert rows[f"{one.slug}/runs/1/"].sentinel_last_modified == T0 - timedelta(days=1)
    async with world.factory() as db:
        stored = (await db.execute(select(AppSetting.value).where(AppSetting.key == world.dfw.BASELINE_KEY))).scalar()
    assert world.dfw.parse_baseline(stored) == T0

    _drop(world.root, f"{one.slug}/runs/3/upload_complete.json", T0 + timedelta(seconds=30))
    out = await sweep(owner="it", now=T0 + timedelta(minutes=1))
    assert world.calls == [{"sentinel_key": f"{one.slug}/runs/3/upload_complete.json",
                            "minio_prefix": f"{one.slug}/runs/3/"}]
    row = (await _rows(world))[f"{one.slug}/runs/3/"]
    assert (row.status, row.task_id, row.queued_at, row.project_segment) == (
        "queued", "it-task-1", T0 + timedelta(minutes=1), one.slug)
    assert row.sentinel_last_modified == T0 + timedelta(seconds=30) and row.sentinel_etag

    out = await sweep(owner="it", now=T0 + timedelta(minutes=2))
    assert len(world.calls) == 1 and out["heads"] == 1                    # only the pending folder
    pending = (await _rows(world))[f"{two.id}/runs/2/"]
    assert (pending.first_seen_at, pending.last_checked_at) == (T0, T0 + timedelta(minutes=2))

    out = await sweep(owner="it", now=T0 + timedelta(hours=24, seconds=1))
    assert out["abandoned"] == 1 and out["heads"] == 0
    assert (await _rows(world))[f"{two.id}/runs/2/"].status == "abandoned"
    async with world.factory() as db:     # the baseline did not move
        stored = (await db.execute(select(AppSetting.value).where(AppSetting.key == world.dfw.BASELINE_KEY))).scalar()
    assert world.dfw.parse_baseline(stored) == T0


async def test_the_flag_off_writes_nothing(world) -> None:
    from app.models.postgres import FeatureFlag

    _drop(world.root, f"{world.projects[0].slug}/runs/8/upload_complete.json", T0)
    async with world.factory() as db:
        flag = (await db.execute(select(FeatureFlag).where(FeatureFlag.key == "drop_folder_watcher"))).scalar_one()
        before = (flag.enabled_global, flag.updated_at)
        flag.enabled_global = False
        await db.commit()
    try:
        assert await world.tasks._drop_folder_watcher_sweep(owner="it", now=T0) == {"skipped": "flag_off"}
        assert await _rows(world) == {} and world.calls == []
    finally:
        async with world.factory() as db:
            await db.execute(text("UPDATE feature_flags SET enabled_global = :on, updated_at = :at WHERE key = :key"),
                             {"on": before[0], "at": before[1], "key": "drop_folder_watcher"})
            await db.commit()


# ── 3. 0254 on a throwaway database ──────────────────────────────────────────


async def _alembic(dsn: str, direction: str, revision: str) -> None:
    def invoke() -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = dsn
        return subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", direction, revision],
                              cwd=BACKEND_ROOT, env=environment, capture_output=True, text=True, check=False)

    result = await asyncio.to_thread(invoke)
    assert result.returncode == 0, f"alembic {direction} {revision} failed\n{result.stdout}\n{result.stderr[-4000:]}"


async def _shape(dsn: str) -> tuple[bool, bool, bool]:
    engine = create_async_engine(dsn, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            table = await conn.scalar(text("SELECT to_regclass('drop_folder_builds') IS NOT NULL"))
            index = await conn.scalar(text("SELECT to_regclass('ix_dfb_pending_first_seen') IS NOT NULL"))
            flag = await conn.scalar(text(
                "SELECT count(*) > 0 FROM feature_flags WHERE key = 'drop_folder_watcher' AND enabled_global"))
            return bool(table), bool(index), bool(flag)
    finally:
        await engine.dispose()


async def test_migration_0254_upgrades_downgrades_and_upgrades_again() -> None:
    import asyncpg
    from sqlalchemy.engine import make_url

    source = make_url(_dsn())
    name = f"w34_dfw_{uuid.uuid4().hex[:12]}"
    connect = {"user": source.username, "password": source.password, "host": source.host or "localhost",
               "port": source.port or 5432, "database": "postgres"}
    admin = await asyncpg.connect(**connect)
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    finally:
        await admin.close()
    dsn = source.set(database=name).render_as_string(hide_password=False)
    try:
        await _alembic(dsn, "upgrade", "head")
        assert await _shape(dsn) == (True, True, True)
        await _alembic(dsn, "downgrade", "0253")
        assert await _shape(dsn) == (False, False, False)
        await _alembic(dsn, "upgrade", "head")
        assert await _shape(dsn) == (True, True, True)
    finally:
        admin = await asyncpg.connect(**connect)
        try:
            await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                "WHERE datname = $1 AND pid <> pg_backend_pid()", name)
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()
