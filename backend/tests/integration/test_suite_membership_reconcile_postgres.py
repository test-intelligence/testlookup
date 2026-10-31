"""W25a Day-0 follow-up — the legacy-membership reconcile against a real PostgreSQL (the parity day-0 triage).

Proven here, on ``create_async_engine(dsn, pool_size=1, max_overflow=1, pool_timeout=5)`` (the production worker
pool, lesson 3):

1. A seeded divergence in this module's own project (non-hex sentinel names): canonical ``execution`` rows with
   no legacy row (two active, one ``deleted``), a default-suite row, a ``managed`` row, three
   ``first_seen_differs`` pairs (legacy earlier / canonical earlier / two runs at the SAME instant) and one
   matching key. ``compare_project`` shows ``missing_in_legacy`` 3 + ``first_seen_differs`` 3 → a DRY RUN reports
   the counts and sends no INSERT / UPDATE / DELETE (statement recorder; row counts and pointers unchanged) →
   ``apply=True`` + one commit → ``compare_project`` ``blocking == 0`` with ``default_suite`` / ``catalog_only``
   unchanged; the rows read as the legacy writer's; zero ``suite_membership_events`` → a second apply writes
   nothing.
2. A W24 import end to end: ``seed_transfer_project`` exported by the REAL export runner, accepted by
   ``accept_upload``, imported by the REAL import runner on the 1 + 1 pool → ``compare_project`` on the NEW project
   has ``blocking == 0`` and its legacy rows mirror its canonical catalog (the source itself, which never had a
   legacy row, reads ``missing_in_legacy`` 1).

Teardown deletes EVERYTHING this module wrote and asserts nothing is left (CI runs every ``*_postgres.py``
module in ONE database): the divergence project's canonical rows first (RESTRICT on suites), then the project
(suites, memberships, runs, managed cases cascade); the transfer seed, its imported copy, the jobs, the people and
the instance secrets through ``remove_transfer_data``. ``dispose_engine_for_loop()`` before the engine is built and
after it is disposed; Redis / Mongo are closed only if this module bound them. Run through
``pg_pytest_w25a.sh a …`` (``df_w25a_a`` at 0248).
"""
from __future__ import annotations

import io
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_WRITE = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?([a-z_]+)\"?", re.IGNORECASE)
_NON_HEX = "ghijkmnpqrstuvwxyz"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _tag() -> str:
    return "".join(secrets.choice(_NON_HEX) for _ in range(8))


def _written(statements: list[str]) -> set[str]:
    return {m.group(1).lower() for s in statements if (m := _WRITE.match(s))}


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env() -> Any:
    """The production worker pool: size 1 + overflow 1, 5 s to wait. Statements and checkouts are recorded."""
    from app.db import mongo as app_mongo
    from app.db import postgres as app_postgres
    from app.db import redis_client

    dsn = _dsn()
    mongo_was_bound, redis_was_bound = app_mongo._client is not None, redis_client._pool is not None
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=1, max_overflow=1, pool_timeout=5)
    probe = SimpleNamespace(out=0, peak=0, statements=[])

    @event.listens_for(engine.sync_engine, "checkout")
    def _checkout(*_: Any) -> None:
        probe.out += 1
        probe.peak = max(probe.peak, probe.out)

    @event.listens_for(engine.sync_engine, "checkin")
    def _checkin(*_: Any) -> None:
        probe.out -= 1

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        probe.statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sessions.probe = probe  # type: ignore[attr-defined]
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        if not redis_was_bound and redis_client._pool is not None:   # close ONLY what this module bound
            await redis_client.close_redis()
        if not mongo_was_bound and app_mongo._client is not None:
            await app_mongo.close_mongo()


# ── 1. the seeded divergence ─────────────────────────────────────────────────


async def _remove_divergence(env: Any, w: SimpleNamespace) -> None:
    """Everything the divergence seed wrote, in FK order; raises on a leak (a leak must fail THIS module)."""
    from app.models import postgres as m

    async with env() as db:
        await db.execute(delete(m.CanonicalTestCase).where(m.CanonicalTestCase.project_id == w.pid))
        await db.execute(delete(m.Project).where(m.Project.id == w.pid))   # suites, memberships, runs, managed cascade
        await db.commit()
    async with env() as db:
        left = 0
        for model in (m.SuiteMembership, m.SuiteMembershipEvent, m.TestSuite, m.TestRun, m.CanonicalTestCase,
                      m.ManagedTestCase, m.Project):
            column = model.id if model is m.Project else model.project_id
            left += int((await db.execute(select(func.count()).select_from(model).where(column == w.pid))).scalar_one())
    assert left == 0, f"{left} rows of this module's divergence project survived its teardown"


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def world(env: Any) -> Any:
    from app.models import postgres as m

    tag = _tag()
    w = SimpleNamespace(tag=tag, pid=uuid.uuid4())
    fp = {name: f"rq{name}{tag}" for name in (
        "missa", "missb", "missgone", "default", "managed", "match", "canonlate", "legacylate", "tie")}
    w.fp = fp
    w.default_suite = f"Default Suite (Reconcile {tag})"
    try:
        async with env() as db:
            db.add(m.Project(id=w.pid, name=f"Reconcile {tag}", slug=f"reconcile-{tag}", is_active=True))
            await db.flush()
            smoke, regression = m.TestSuite(project_id=w.pid, name="Smoke"), m.TestSuite(project_id=w.pid, name="Regression")
            default = m.TestSuite(project_id=w.pid, name=w.default_suite, is_default=True)
            db.add_all([smoke, regression, default])
            base = datetime.now(timezone.utc) - timedelta(hours=3)

            def run(label: str, at: datetime) -> Any:
                row = m.TestRun(id=uuid.uuid4(), project_id=w.pid, build_number=f"reconcile-{tag}-{label}",
                                status="PASSED", created_at=at)
                db.add(row)
                return row

            early, late = run("early", base), run("late", base + timedelta(milliseconds=400))   # the same second
            last = run("last", base + timedelta(hours=1))
            tie_a, tie_b = sorted((run("tie1", base + timedelta(minutes=5)), run("tie2", base + timedelta(minutes=5))),
                                  key=lambda r: str(r.id))                                   # the SAME instant
            await db.flush()
            w.early, w.late, w.last, w.tie_a, w.tie_b = early.id, late.id, last.id, tie_a.id, tie_b.id
            linked = m.ManagedTestCase(project_id=w.pid, title=f"Linked {tag}", test_fingerprint=fp["missa"])
            db.add(linked)
            await db.flush()
            w.linked_case = linked.id

            def canon(key: str, suite: Any, first: Any, *, status: str = "active", source: str = "execution",
                      deleted_at: Any = None) -> None:
                db.add(m.CanonicalTestCase(project_id=w.pid, test_suite_id=suite.id, test_fingerprint=fp[key],
                                           test_name=f"test_{key}", class_name="ReconcileTest", status=status,
                                           source=source, first_seen_run_id=first, last_seen_run_id=last.id,
                                           deleted_at_run_id=deleted_at))

            def legacy(key: str, suite: str, first: Any) -> None:
                db.add(m.SuiteMembership(project_id=w.pid, suite_name=suite, test_fingerprint=fp[key],
                                         test_name=f"test_{key}", class_name="ReconcileTest", status="active",
                                         source="execution", first_seen_run_id=first, last_seen_run_id=last.id))

            canon("missa", smoke, early.id)                                     # missing_in_legacy (a managed case links it)
            canon("missb", regression, early.id)                                # missing_in_legacy
            canon("missgone", regression, early.id, status="deleted", deleted_at=last.id)   # missing_in_legacy, retired
            canon("default", default, early.id)                                 # EXPECTED default_suite
            canon("managed", smoke, None, source="managed")                     # EXPECTED catalog_only
            canon("match", smoke, early.id)
            legacy("match", "Smoke", early.id)
            canon("canonlate", smoke, late.id)                                  # first_seen_differs: canonical later
            legacy("canonlate", "Smoke", early.id)
            canon("legacylate", regression, early.id)                           # first_seen_differs: legacy later
            legacy("legacylate", "Regression", late.id)
            canon("tie", smoke, tie_a.id)                                       # first_seen_differs: same instant
            legacy("tie", "Smoke", tie_b.id)
            await db.commit()
        yield w
    finally:
        await _remove_divergence(env, w)


async def _snapshot(env: Any, pid: uuid.UUID) -> tuple[int, int, int, list[Any], list[Any]]:
    from app.models import postgres as m

    async with env() as db:
        legacy = int((await db.execute(select(func.count()).select_from(m.SuiteMembership)
                                       .where(m.SuiteMembership.project_id == pid))).scalar_one())
        canonical = int((await db.execute(select(func.count()).select_from(m.CanonicalTestCase)
                                          .where(m.CanonicalTestCase.project_id == pid))).scalar_one())
        events = int((await db.execute(select(func.count()).select_from(m.SuiteMembershipEvent)
                                       .where(m.SuiteMembershipEvent.project_id == pid))).scalar_one())
        legacy_first = sorted((await db.execute(select(m.SuiteMembership.test_fingerprint, m.SuiteMembership.first_seen_run_id)
                                                .where(m.SuiteMembership.project_id == pid))).all(), key=str)
        canonical_first = sorted((await db.execute(select(m.CanonicalTestCase.test_fingerprint,
                                                          m.CanonicalTestCase.first_seen_run_id)
                                                   .where(m.CanonicalTestCase.project_id == pid))).all(), key=str)
    return legacy, canonical, events, legacy_first, canonical_first


async def _compare(env: Any, pid: uuid.UUID) -> dict[str, int]:
    from app.core.config import settings
    from app.services import suite_membership_parity as parity

    async with env() as db:
        result = await parity.compare_project(db, pid, window_runs=settings.CANONICAL_DELETION_WINDOW_RUNS)
        await db.rollback()
    counts: dict[str, int] = {}
    for d in result.divergences:
        counts[d.reason] = counts.get(d.reason, 0) + 1
    counts["blocking"] = sum(1 for d in result.divergences if d.blocking)
    return counts


PLAN = {
    "legacy_inserted": 3, "legacy_inserted_removed": 1, "first_seen_aligned_legacy": 2,
    "first_seen_aligned_canonical": 1, "left_default_suite": 1, "left_catalog_only": 1, "left_unfit": 0,
}


async def test_a_dry_run_reports_the_plan_and_writes_nothing(env: Any, world: Any) -> None:
    from app.services.suite_membership_reconcile import reconcile_project

    before = await _compare(env, world.pid)
    assert before == {"missing_in_legacy": 3, "first_seen_differs": 3, "default_suite": 1, "catalog_only": 1,
                      "blocking": 6}
    snapshot = await _snapshot(env, world.pid)
    env.probe.statements.clear()
    env.probe.peak = 0
    async with env() as db:
        result = await reconcile_project(db, world.pid, apply=False)
        await db.commit()                     # even a commit after a dry run has nothing to write
    assert result.counts() == PLAN and not result.applied
    assert _written(env.probe.statements) == set(), env.probe.statements
    assert env.probe.peak == 1
    assert await _snapshot(env, world.pid) == snapshot
    assert await _compare(env, world.pid) == before


async def test_apply_makes_the_project_clean_and_a_second_apply_changes_nothing(env: Any, world: Any) -> None:
    from app.models import postgres as m
    from app.services.suite_membership_reconcile import reconcile_project

    fp = world.fp
    canonical_before = (await _snapshot(env, world.pid))[1]
    env.probe.statements.clear()
    env.probe.peak = 0
    async with env() as db:
        result = await reconcile_project(db, world.pid, apply=True)
        await db.commit()
    assert result.counts() == PLAN and result.applied
    assert _written(env.probe.statements) == {"suite_memberships", "canonical_test_cases"}
    assert env.probe.peak == 1

    after = await _compare(env, world.pid)
    assert after == {"default_suite": 1, "catalog_only": 1, "blocking": 0}     # EXPECTED keys unchanged, never touched
    legacy, canonical, events, _l, _c = await _snapshot(env, world.pid)
    assert (legacy, canonical, events) == (4 + 3, canonical_before, 0)        # no event row: not a membership change

    async with env() as db:
        rows = {r.test_fingerprint: r for r in (await db.execute(
            select(m.SuiteMembership).where(m.SuiteMembership.project_id == world.pid))).scalars().all()}
        firsts = dict((await db.execute(select(m.CanonicalTestCase.test_fingerprint, m.CanonicalTestCase.first_seen_run_id)
                                        .where(m.CanonicalTestCase.project_id == world.pid))).all())
    missa, missb, gone = rows[fp["missa"]], rows[fp["missb"]], rows[fp["missgone"]]
    assert (missa.suite_name, missa.status, missa.source, missa.managed_test_case_id, missa.review_tag) == (
        "Smoke", "active", "linked", world.linked_case, None)
    assert (missb.suite_name, missb.status, missb.source, missb.managed_test_case_id) == (
        "Regression", "active", "execution", None)
    assert (missa.test_name, missa.class_name, missa.first_seen_run_id, missa.last_seen_run_id) == (
        "test_missa", "ReconcileTest", world.early, world.last)
    assert (gone.suite_name, gone.status, gone.review_tag, gone.deleted_at_run_id) == (
        "Regression-deleted", "needs_review", "needs_review", world.last)
    assert missa.tags is None and missa.created_at is not None
    for key in ("default", "managed"):
        assert fp[key] not in rows, key                                        # left alone
    assert rows[fp["canonlate"]].first_seen_run_id == firsts[fp["canonlate"]] == world.early
    assert rows[fp["legacylate"]].first_seen_run_id == firsts[fp["legacylate"]] == world.early
    assert rows[fp["tie"]].first_seen_run_id == firsts[fp["tie"]] == world.tie_a   # the smaller id string
    assert rows[fp["match"]].first_seen_run_id == firsts[fp["match"]] == world.early

    snapshot = await _snapshot(env, world.pid)
    env.probe.statements.clear()
    async with env() as db:
        again = await reconcile_project(db, world.pid, apply=True)
        await db.commit()
    assert again.changes == 0 and (again.left_default_suite, again.left_catalog_only) == (1, 1)
    assert _written(env.probe.statements) == set()
    assert await _snapshot(env, world.pid) == snapshot


# ── 2. a W24 import end to end ───────────────────────────────────────────────


class _Upload:
    """The route's ``UploadFile.read(size)``, over bytes."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def transfer(env: Any, tmp_path_factory: Any) -> Any:
    """The W24 transfer seed exported by the REAL export runner (on a roomier engine, as the W24 module does); the
    import itself runs later on ``env`` (1 + 1). Cleanup: ``remove_transfer_data`` (source, copies, jobs, people,
    instance secrets)."""
    from app.db import storage as storage_module
    from app.models.postgres import Project, ProjectDataJob, User
    from app.services import project_data_job_service as jobs
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET
    from app.worker import project_data_runner as runner
    from tests.integration.project_transfer_seed import make_sentinels, remove_transfer_data, seed_transfer_project

    roomy = create_async_engine(_dsn(), pool_size=4, max_overflow=4)
    seed_sessions = async_sessionmaker(roomy, expire_on_commit=False)
    patcher = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("w25a-reconcile-objects")
    patcher.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(root))
    storage = storage_module.LocalStorageProvider(default_bucket="artifacts")
    patcher.setattr(storage_module, "get_storage_provider", lambda: storage)
    state = SimpleNamespace(seeded=None, imported=[])
    try:
        async with seed_sessions() as db:
            state.seeded = await seed_transfer_project(db, sentinels=make_sentinels())
        async with seed_sessions() as db:
            source = await db.get(Project, state.seeded.project_id)
            admin = await db.get(User, state.seeded.admin_id)
            export_job = await jobs.create_export_job(db, project=source, actor=admin)
            await db.commit()
        await runner.run_export_job(export_job.id, storage=storage, session_factory=seed_sessions)
        async with seed_sessions() as db:
            exported = await db.get(ProjectDataJob, export_job.id)
        assert exported.status == "succeeded", (exported.error_code, exported.error)
        archive = await storage.get_object_content(exported.storage_key, bucket=COMPLIANCE_BUCKET)
        yield SimpleNamespace(seeded=state.seeded, archive=archive, storage=storage, runner=runner, state=state)
    finally:
        patcher.undo()
        try:
            if state.seeded is not None:
                await remove_transfer_data(seed_sessions, state.seeded, project_ids=state.imported)
        finally:
            await roomy.dispose()


async def test_an_imported_project_is_clean_on_arrival(env: Any, transfer: Any) -> None:
    from app.models import postgres as m
    from app.services.project_import_service import accept_upload

    seeded = transfer.seeded
    source = await _compare(env, seeded.project_id)
    assert source.get("missing_in_legacy") == 1        # the seed's canonical row; the source never had a legacy row

    async with env() as db:
        admin = await db.get(m.User, seeded.admin_id)
        job, created = await accept_upload(db, transfer.storage, file=_Upload(transfer.archive),
                                           name=f"Reconcile import {_tag()}", client_token=uuid.uuid4(), actor=admin)
        await db.commit()
    assert created
    status = await transfer.runner.run_import_job(job.id, storage=transfer.storage, session_factory=env)
    async with env() as db:
        done = await db.get(m.ProjectDataJob, job.id)
    if done.project_id is not None:
        transfer.state.imported.append(done.project_id)
    assert status == "succeeded", (done.error_code, done.error)
    new_id = done.project_id

    assert (await _compare(env, new_id))["blocking"] == 0
    async with env() as db:
        legacy = (await db.execute(select(m.SuiteMembership).where(m.SuiteMembership.project_id == new_id))).scalars().all()
        mirrored = (await db.execute(
            select(m.CanonicalTestCase.test_fingerprint, m.CanonicalTestCase.test_name, m.TestSuite.name)
            .join(m.TestSuite, m.TestSuite.id == m.CanonicalTestCase.test_suite_id)
            .where(m.CanonicalTestCase.project_id == new_id, m.CanonicalTestCase.source == "execution",
                   m.TestSuite.is_default.is_(False))
        )).all()
        events = int((await db.execute(select(func.count()).select_from(m.SuiteMembershipEvent)
                                       .where(m.SuiteMembershipEvent.project_id == new_id))).scalar_one())
    assert len(mirrored) >= 1                          # declaration is not emission: the copy has a catalog to mirror
    assert sorted((r.test_fingerprint, r.test_name, r.suite_name) for r in legacy) == sorted(tuple(r) for r in mirrored)
    assert all(r.status == "active" and r.first_seen_run_id is None and r.last_seen_run_id is None for r in legacy)
    assert events == 0
