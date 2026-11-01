"""W24 ADM-05 — the project import against a real PostgreSQL and the REAL local object store (§6.3 G-C).

The source is G-A's ``seed_transfer_project`` (≥ 1 row of every exported entity),
exported through G-B's REAL runner (``run_export_job``); every import goes through
the ASGI route (``POST /api/v1/projects/import`` as an ADMIN) and the REAL runner
(``run_import_job``) on this module's engine. Proven:

1. ROUND TRIP — for EVERY exported entity ``count(new) == count(source)`` (> 0); every
   FK of every imported row points inside the NEW project; authorship maps to the
   same users by username, and to NULL for a user deactivated between export and
   import (counted ``user_not_found``); assignments cleared (counted); every inert
   value in force; exactly ONE active release; the in-review case arrived ``draft``;
   ``project.created`` + ``project.imported`` on the new project; ZERO run history /
   defects / holds / knowledge sources / digests in it; the importer is a member and
   no seeded user is.
2. The same upload + the same ``client_token`` twice → ONE job, ONE project (200 on
   the second POST); a NEW token → ``… (imported 2)``.
3. A MID-IMPORT FAILURE (the ``test_plans`` batch raises) leaves no project, no
   ``qalead-<slug>`` user, no orphan row; the job reads ``import_failed``; the upload
   object is removed.
4. Every REFUSED-BEFORE-ANY-WRITE archive: an engine listener counts ZERO INSERT
   statements during the job and ``projects`` is unchanged.
5. authz — an engineer / a QA lead POST → 403; another ADMIN's import job → the
   SAME 404 body as an unknown id.
6. W25d HYG-02 — the step-0 FIXTURE ``tests/fixtures/project_export_w24_schema1.zip`` (a REAL
   pre-W25d export: 30 entities, ``quality_gates`` 1 row) imports; ``quality_gates`` is
   reported skipped (``retired_table``, 1 row) and every exported table arrives with
   exactly the manifest's rows.

Fixture rule (§6.3): ``dispose_engine_for_loop()`` before the engine is built and
after it is disposed; the route's ``get_db`` and the runner run on THIS module's
engine (so the listener sees every statement); storage = ``LocalStorageProvider``
on a temporary directory; no Mongo; Redis is closed only if this module bound it.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0247).
"""
from __future__ import annotations

import inspect
import io
import json
import os
import uuid
import zipfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _InsertCounter:
    """An engine-level listener: every INSERT statement the engine sends, while armed."""

    def __init__(self) -> None:
        self.armed = False
        self.inserts: list[str] = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if self.armed and statement.lstrip().upper().startswith("INSERT"):
            self.inserts.append(statement.split("(")[0].strip())


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {info.filename: zf.read(info) for info in zf.infolist()}


def _rewrap(
    archive: bytes, *, replace: Optional[dict[str, Optional[bytes]]] = None,
    manifest: Optional[Callable[[dict[str, Any]], None]] = None, relist: bool = False, unsign: bool = False,
) -> bytes:
    """A variant of a real export: members replaced / added / removed, the manifest edited,
    its ``files`` list recomputed (``relist``), ``signature.json`` dropped (``unsign``)."""
    import hashlib

    members = _members(archive)
    for name, data in (replace or {}).items():
        if data is None:
            members.pop(name, None)
        else:
            members[name] = data
    doc = json.loads(members["manifest.json"])
    if manifest is not None:
        manifest(doc)
    if relist:
        doc["files"] = [
            {"name": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
            for n, b in sorted(members.items()) if n not in ("manifest.json", "signature.json")
        ]
    if manifest is not None or relist:
        members["manifest.json"] = json.dumps(doc, sort_keys=True).encode("utf-8")
    if unsign:
        members.pop("signature.json", None)
    return _zip(members)


async def _cleanup(sessions: Any, seeded: Any, people: list[Any]) -> None:
    """The source, every project this module imported (+ their secrets and synthetic QA leads), the people and the
    instance secrets the seed overwrote — CI shares ONE database across modules (#308: a leaked GitHub integration
    failed the integration-health sweep three modules later)."""
    from app.models.postgres import ProjectDataJob
    from tests.integration.project_transfer_seed import remove_transfer_data

    people_ids = [p.id for p in people]
    async with sessions() as db:
        imported = (await db.execute(
            select(ProjectDataJob.project_id).where(
                ProjectDataJob.requested_by_id.in_(people_ids), ProjectDataJob.project_id.is_not(None))
        )).scalars().all()
    await remove_transfer_data(sessions, seeded, project_ids=imported, user_ids=people_ids)


def _user(tag: str, role: str, label: str) -> Any:
    from app.models.postgres import User

    username = f"w24c-{label}-{tag}"
    return User(id=uuid.uuid4(), email=f"{username}@w24c.example.test", username=username, full_name=None,
                hashed_password="!integration", role=role, is_active=True)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env(tmp_path_factory):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core import deps
    from app.db import mongo as app_mongo
    from app.db import postgres as app_postgres
    from app.db import redis_client
    from app.db import storage as storage_module
    from app.models.postgres import Project, ProjectDataJob, User, UserRole
    from app.routers import project_data
    from app.services import project_data_job_service as jobs
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET
    from app.worker import project_data_runner as runner
    from app.worker import tasks
    from tests.integration.project_transfer_seed import make_sentinels, seed_transfer_project

    await app_postgres.dispose_engine_for_loop()
    mongo_was_bound, redis_was_bound = app_mongo._client is not None, redis_client._pool is not None
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=4)
    counter = _InsertCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    patcher = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("w24c-objects")
    patcher.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(root))
    storage = storage_module.LocalStorageProvider(default_bucket="artifacts")
    patcher.setattr(storage_module, "get_storage_provider", lambda: storage)
    dispatched: list[list[str]] = []

    def apply_async(*, args, **_kw):          # the REAL payload is bound, never only recorded
        inspect.signature(tasks.import_project_data.run).bind(*args)
        dispatched.append(list(args))

    patcher.setattr(tasks.import_project_data, "apply_async", apply_async)

    tag = uuid.uuid4().hex[:10].translate(str.maketrans("0123456789abcdef", "ghjkmnpqrstvwxyz"))
    admin = _user(tag, UserRole.ADMIN.value, "admin")
    other_admin = _user(tag, UserRole.ADMIN.value, "admin2")
    lead = _user(tag, UserRole.QA_LEAD.value, "lead")
    engineer = _user(tag, UserRole.QA_ENGINEER.value, "eng")
    people = [admin, other_admin, lead, engineer]
    async with sessions() as db:
        db.add_all(people)
        await db.commit()
    for person in people:
        deps._bind_credential_kind(person, deps.CREDENTIAL_KIND_JWT)
    async with sessions() as db:
        seeded = await seed_transfer_project(db, sentinels=make_sentinels())
    async with sessions() as db:
        source = await db.get(Project, seeded.project_id)
        export_job = await jobs.create_export_job(db, project=source, actor=admin)
        await db.commit()
    await runner.run_export_job(export_job.id, storage=storage, session_factory=sessions)
    async with sessions() as db:
        exported = await db.get(ProjectDataJob, export_job.id)
    assert exported.status == "succeeded", (exported.error_code, exported.error)   # G-B's real export runner
    archive = await storage.get_object_content(exported.storage_key, bucket=COMPLIANCE_BUCKET)

    state = SimpleNamespace(user=admin)
    app = FastAPI()
    app.include_router(project_data.router)
    app.dependency_overrides[deps.get_current_active_user] = lambda: state.user

    async def own_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[app_postgres.get_db] = own_db

    async def call(method: str, path: str, *, user: Any = None, **kwargs: Any):
        state.user = user or admin
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    try:
        yield SimpleNamespace(
            sessions=sessions, storage=storage, root=root, counter=counter, call=call, dispatched=dispatched,
            seeded=seeded, archive=archive, admin=admin, other_admin=other_admin, lead=lead, engineer=engineer,
            runner=runner, tag=tag, User=User,
        )
    finally:
        patcher.undo()
        await _cleanup(sessions, seeded, people)
        event.remove(engine.sync_engine, "before_cursor_execute", counter)
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        if not redis_was_bound and redis_client._pool is not None:   # close ONLY what this module bound
            await redis_client.close_redis()
        if not mongo_was_bound and app_mongo._client is not None:
            await app_mongo.close_mongo()


# ── helpers ──────────────────────────────────────────────────────────────────


async def _post(env, archive: bytes, *, token: Optional[uuid.UUID] = None, name: Optional[str] = None,
                user: Any = None):
    data = {"client_token": str(token or uuid.uuid4())}
    if name:
        data["name"] = name
    return await env.call("POST", "/api/v1/projects/import", user=user,
                          files={"file": ("export.zip", archive, "application/zip")}, data=data)


async def _job(env, job_id: Any) -> Any:
    from app.models.postgres import ProjectDataJob

    async with env.sessions() as db:
        return await db.get(ProjectDataJob, uuid.UUID(str(job_id)))


async def _import(env, archive: bytes, **kwargs: Any) -> Any:
    """POST through the route (202), then the REAL runner on this engine; returns the job row."""
    response = await _post(env, archive, **kwargs)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert env.dispatched[-1] == [job_id]
    await env.runner.run_import_job(uuid.UUID(job_id), storage=env.storage, session_factory=env.sessions)
    return await _job(env, job_id)


async def _scalar(env, sql: str, **params: Any) -> Any:
    async with env.sessions() as db:
        return (await db.execute(text(sql), params)).scalar_one()


def _in_project(table: Any, project_id: uuid.UUID) -> Any:
    """The rows of ``table`` belonging to ``project_id`` (directly, or through the parent's scope)."""
    from app.models.postgres import Base
    from app.services.project_export_entities import spec_for

    spec = spec_for(table.name)
    if table.name == "projects":
        return table.c.id == project_id
    if "project_id" in table.c:
        return table.c.project_id == project_id
    parent_name, column = spec.parent
    parent = Base.metadata.tables[parent_name]
    return table.c[column].in_(select(parent.c.id).where(_in_project(parent, project_id)))


async def _column_values(env, table_name: str, column: str, project_id: uuid.UUID) -> list[Any]:
    from app.models.postgres import Base

    table = Base.metadata.tables[table_name]
    async with env.sessions() as db:
        return list((await db.execute(select(table.c[column]).where(_in_project(table, project_id)))).scalars())


# ── 1. the round trip ────────────────────────────────────────────────────────


async def test_the_round_trip_copies_every_entity_into_a_new_project_and_nothing_else(env):
    from app.models.postgres import Base, User
    from app.services import project_export_vocab as vocab
    from app.services.project_export_entities import plan
    from tests.integration.project_transfer_seed import exported_counts

    seeded = env.seeded
    async with env.sessions() as db:
        source_counts = await exported_counts(db, seeded.project_id)
    assert all(count > 0 for count in source_counts.values()), source_counts       # declaration is not emission
    source_users = {
        (spec.table, column): Counter(await _column_values(env, spec.table, column, seeded.project_id))
        for spec in plan() for column in spec.user_refs
    }
    source_cleared = {
        (spec.table, column): sum(v is not None for v in await _column_values(env, spec.table, column, seeded.project_id))
        for spec in plan() for column in spec.cleared
    }
    in_review = sum(
        status in vocab.REVIEW_RESET_STATES
        for status in await _column_values(env, "managed_test_cases", "status", seeded.project_id)
    )
    assert in_review >= 1

    # The author is deactivated BETWEEN the export and the import: their authorship cannot travel.
    async with env.sessions() as db:
        await db.execute(update(User).where(User.id == seeded.author_id).values(is_active=False))
        await db.commit()
    try:
        job = await _import(env, env.archive)
    finally:
        async with env.sessions() as db:
            await db.execute(update(User).where(User.id == seeded.author_id).values(is_active=True))
            await db.commit()
    assert job.status == "succeeded", (job.error_code, job.error)
    new_id = job.project_id
    report = job.report
    assert report["project_name"] == f"{seeded.project_name} (imported)"
    assert report["project_slug"] == f"{seeded.project_slug}-imported"
    assert report["signature"] == "valid" and job.signature == "valid"

    async with env.sessions() as db:
        new_counts = await exported_counts(db, new_id)
    assert new_counts == source_counts                                    # every entity, equal counts
    assert report["inserted"] == source_counts == job.counts
    assert env.root.exists() and job.storage_key is None                  # the upload was removed

    # Every FK of every imported row points inside the NEW project.
    async with env.sessions() as db:
        for spec in plan():
            table = Base.metadata.tables[spec.table]
            edges = {**spec.fks, **{column: spec.table for column in spec.self_fks}}
            for column, parent_name in edges.items():
                parent = Base.metadata.tables[parent_name]
                outside = (await db.execute(
                    select(func.count()).select_from(table).where(
                        _in_project(table, new_id), table.c[column].is_not(None),
                        table.c[column].not_in(select(parent.c.id).where(_in_project(parent, new_id))),
                    )
                )).scalar_one()
                assert outside == 0, f"{spec.table}.{column} points outside the new project"
        chained = (await db.execute(text(
            "SELECT count(*) FROM releases WHERE project_id = :p AND baseline_release_id IS NOT NULL"), {"p": new_id}
        )).scalar_one()
    assert chained >= 1                                                   # the baseline chain survived the remap

    # Authorship by username (the deactivated author → NULL, or the importer where NOT NULL); assignments NULL.
    author_misses = 0
    for (table_name, column), before in source_users.items():
        after = Counter(await _column_values(env, table_name, column, new_id))
        expected = Counter({k: v for k, v in before.items() if k not in (None, seeded.author_id)})
        nullable = Base.metadata.tables[table_name].c[column].nullable
        misses = before.get(seeded.author_id, 0)
        author_misses += misses
        if not nullable and misses:
            expected[env.admin.id] += misses
        assert Counter({k: v for k, v in after.items() if k is not None}) == expected, (table_name, column)
    cleared = {(c["entity"], c["column"], c["reason"]): c["count"] for c in report["cleared"]}
    assert author_misses >= 1
    assert sum(n for (_e, _c, reason), n in cleared.items() if reason == "user_not_found") == author_misses
    for (table_name, column), was_set in source_cleared.items():
        assert cleared.get((table_name, column, "assignment"), 0) == was_set
        if (table_name, column) == ("projects", "default_qa_lead_user_id"):
            continue                       # cleared from the archive, then set by provisioning (below)
        assert set(await _column_values(env, table_name, column, new_id)) <= {None}, (table_name, column)
    # The copy's default QA lead is its OWN synthetic account (create_project's provisioning), never the source's.
    async with env.sessions() as db:
        lead = (await db.execute(text(
            "SELECT u.username FROM projects p JOIN users u ON u.id = p.default_qa_lead_user_id WHERE p.id = :p"),
            {"p": new_id})).scalar_one()
    assert lead == f"qalead-{report['project_slug']}"

    await _assert_inert(env, new_id, report)
    await _assert_isolated(env, new_id, in_review, report)


async def _assert_inert(env, new_id: uuid.UUID, report: dict[str, Any]) -> None:
    """Every sender arrives switched off; cadences paused 90 days with the reason."""
    from app.services import project_export_vocab as vocab
    from app.services.project_export_entities import spec_for

    async with env.sessions() as db:
        async def rows(sql: str) -> list[Any]:
            return list((await db.execute(text(sql), {"p": new_id})).all())

        hooks = await rows("SELECT enabled, has_secret, disabled_reason, disabled_at FROM webhook_subscriptions WHERE project_id = :p")
        assert hooks and all(
            (h.enabled, h.has_secret, h.disabled_reason) == (False, False, spec_for("webhook_subscriptions").inert["disabled_reason"])
            and h.disabled_at is not None for h in hooks
        )
        channels = await rows("SELECT channel_type, target, is_active FROM team_notification_channels WHERE project_id = :p")
        assert channels and not any(c.is_active for c in channels)
        assert {c.target for c in channels if c.channel_type in ("slack", "teams")} == {vocab.REDACTED}
        assert any(c.channel_type == "email" and c.target != vocab.REDACTED for c in channels)
        for policy in await rows("SELECT transitions_enabled, per_run_events_enabled FROM notification_transition_policies WHERE project_id = :p"):
            assert (policy.transitions_enabled, policy.per_run_events_enabled) == (False, False)
        assert {r.auto_create_defect for r in await rows(
            "SELECT auto_create_defect FROM quarantine_lifecycle_policies WHERE project_id = :p")} == {False}
        assert {(r.enabled, r.has_pat) for r in await rows(
            "SELECT enabled, has_pat FROM github_integrations WHERE project_id = :p")} == {(False, False)}
        assert {(r.enabled, r.has_pat, r.commit_status_enabled) for r in await rows(
            "SELECT enabled, has_pat, commit_status_enabled FROM gitlab_integrations WHERE project_id = :p")} == {(False, False, False)}
        cadences = await rows("SELECT paused_at, paused_until, paused_reason, paused_by_id FROM run_cadences WHERE project_id = :p")
        assert cadences and all(
            c.paused_reason == vocab.IMPORTED_PAUSE_REASON and c.paused_by_id == env.admin.id
            and c.paused_until - c.paused_at == timedelta(days=vocab.IMPORTED_PAUSE_DAYS) for c in cadences
        )
        assert (await db.execute(text(
            "SELECT count(*) FROM releases WHERE project_id = :p AND is_active"), {"p": new_id})).scalar_one() == 1
    for entity in ("webhook_subscriptions", "team_notification_channels", "run_cadences", "github_integrations",
                   "gitlab_integrations", "notification_transition_policies", "quarantine_lifecycle_policies"):
        assert report["switched_off"].get(entity, 0) >= 1, entity


async def _assert_isolated(env, new_id: uuid.UUID, in_review: int, report: dict[str, Any]) -> None:
    """No run history / defect / hold / knowledge source / digest travelled; no access was granted."""
    from app.services import project_export_vocab as vocab

    async with env.sessions() as db:
        async def count(sql: str) -> int:
            return int((await db.execute(text(sql), {"p": new_id})).scalar_one())

        for table in ("test_runs", "defects", "retention_holds", "knowledge_sources", "digest_subscriptions", "api_keys"):
            assert await count(f"SELECT count(*) FROM {table} WHERE project_id = :p") == 0, table   # noqa: S608
        assert await count(
            "SELECT count(*) FROM managed_test_cases WHERE project_id = :p AND status IN ('review_requested', 'under_review')"
        ) == 0
        events = {row[0] for row in (await db.execute(text(
            "SELECT event_type FROM project_activity_events WHERE project_id = :p"), {"p": new_id})).all()}
        members = {row[0] for row in (await db.execute(text(
            "SELECT user_id FROM project_members WHERE project_id = :p"), {"p": new_id})).all()}
    assert report["switched_off"].get("managed_test_cases.review_reset") == in_review
    assert {"project.created", "project.imported"} <= events
    assert env.admin.id in members
    async with env.sessions() as db:
        seeded_people = set((await db.execute(
            select(env.User.id).where(env.User.username.in_(list(env.seeded.usernames.values())))
        )).scalars())
    seeded_people |= {env.seeded.admin_id, env.seeded.author_id, env.seeded.assignee_id}
    assert not members & seeded_people                                    # an archive never grants access
    assert not set(vocab.REVIEW_RESET_STATES) & set(await _column_values(env, "managed_test_cases", "status", new_id))


# ── 2. idempotency ───────────────────────────────────────────────────────────


async def test_the_same_upload_and_token_make_one_project_and_a_new_token_the_next_name(env):
    from app.services.project_export_entities import plan
    from app.services.project_import_service import name_candidates

    token = uuid.uuid4()
    dispatched_before = len(env.dispatched)
    first = await _post(env, env.archive, token=token)
    again = await _post(env, env.archive, token=token)            # before the worker ran
    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json()["id"] == first.json()["id"] and len(env.dispatched) == dispatched_before + 1
    job_id = first.json()["id"]
    await env.runner.run_import_job(uuid.UUID(job_id), storage=env.storage, session_factory=env.sessions)
    after = await _post(env, env.archive, token=token)            # after it succeeded
    assert after.status_code == 200 and after.json()["id"] == job_id and after.json()["status"] == "succeeded"
    assert len(env.dispatched) == dispatched_before + 1
    job = await _job(env, job_id)
    assert await _scalar(env, "SELECT count(*) FROM project_data_jobs WHERE client_token = :t", t=token) == 1
    assert await _scalar(env, "SELECT count(*) FROM projects WHERE id = :p", p=job.project_id) == 1

    # Every author is active here: authorship maps to the SAME users, by username.
    for spec in plan():
        for column in spec.user_refs:
            before = Counter(await _column_values(env, spec.table, column, env.seeded.project_id))
            assert Counter(await _column_values(env, spec.table, column, job.project_id)) == before, (spec.table, column)

    second = await _import(env, env.archive)                       # a NEW token: a deliberate second copy
    ladder = name_candidates(env.seeded.project_name)
    assert second.status == "succeeded" and second.project_id != job.project_id
    assert ladder.index(second.report["project_name"]) == ladder.index(job.report["project_name"]) + 1


# ── 3. a failure mid-import ──────────────────────────────────────────────────


async def test_a_failure_mid_import_leaves_no_project_no_user_no_orphan_and_no_upload(env, monkeypatch):
    from app.models.postgres import Base
    from app.services import project_export_vocab as vocab
    from app.services import project_import_service as svc
    from app.services.project_export_entities import plan

    real_insert = svc._insert_batch

    async def failing_insert(db, table, rows):
        if table.name == "test_plans":
            raise RuntimeError("injected mid-import failure")
        await real_insert(db, table, rows)

    monkeypatch.setattr(svc, "_insert_batch", failing_insert)
    slugs = svc.slug_candidates(env.seeded.project_slug)
    async with env.sessions() as db:
        taken = set((await db.execute(text("SELECT slug FROM projects WHERE slug = ANY(:s)"), {"s": slugs})).scalars())
    doomed_slug = svc.pick_free(slugs, taken)
    doomed_name = f"W24c doomed {env.tag}"
    projects_before = await _scalar(env, "SELECT count(*) FROM projects")

    job = await _import(env, env.archive, name=doomed_name)

    assert job.status == "failed" and job.error_code == "import_failed"
    assert job.error == vocab.ERROR_SENTENCES["import_failed"].format(error_type="RuntimeError")
    assert job.project_id is None and job.storage_key is None
    assert await _scalar(env, "SELECT count(*) FROM projects") == projects_before
    assert await _scalar(env, "SELECT count(*) FROM projects WHERE lower(name) = lower(:n)", n=doomed_name) == 0
    assert await _scalar(env, "SELECT count(*) FROM projects WHERE slug = :s", s=doomed_slug) == 0
    assert await _scalar(env, "SELECT count(*) FROM users WHERE username = :u", u=f"qalead-{doomed_slug}") == 0
    for spec in plan():
        table = Base.metadata.tables[spec.table]
        if "project_id" in table.c and spec.table != "projects":
            orphans = await _scalar(env, (
                f"SELECT count(*) FROM {spec.table} t WHERE t.project_id IS NOT NULL "   # noqa: S608 - metadata names
                "AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = t.project_id)"
            ))
            assert orphans == 0, spec.table
    uploads = [p for p in env.root.rglob("*.zip") if str(job.id) in p.name]
    assert uploads == []                                                   # the upload object was removed


# ── 4. refused before any write ──────────────────────────────────────────────

_PART = "entities/test_suites/part-0001.jsonl"


def _flip_a_byte(archive: bytes) -> bytes:
    part = _members(archive)[_PART]
    return _rewrap(archive, replace={_PART: part[:-2] + bytes([part[-2] ^ 0x01]) + part[-1:]})


def _edit_after_signing(archive: bytes) -> bytes:
    return _rewrap(archive, manifest=lambda m: m["project"].update(name="Someone Else's Service"))


def _wrong_type(archive: bytes) -> bytes:
    lines = _members(archive)[_PART].decode("utf-8").splitlines()
    first = json.loads(lines[0])
    first["name"] = ["SENTINELwrongTypeValue"]
    body = "\n".join([json.dumps(first, sort_keys=True), *lines[1:]]) + "\n"
    return _rewrap(archive, replace={_PART: body.encode("utf-8")}, relist=True, unsign=True)


def _unknown_entity(archive: bytes) -> bytes:
    row = json.dumps({"id": str(uuid.uuid4()), "status": "PASSED"}).encode("utf-8") + b"\n"
    return _rewrap(archive, replace={"entities/test_runs/part-0001.jsonl": row},
                   manifest=lambda m: m["entities"].update(test_runs={"rows": 1, "parts": 1}), relist=True, unsign=True)


def _unknown_column(archive: bytes) -> bytes:
    def add(m: dict[str, Any]) -> None:
        entry = m["entities"]["test_suites"]
        entry["columns"] = [*(entry.get("columns") or ["name"]), "added_later"]
    return _rewrap(archive, manifest=add, unsign=True)


_REFUSED: list[tuple[str, Callable[[bytes], bytes], str, str]] = [
    ("byte-flipped", _flip_a_byte, "tampered", "a member was changed"),
    ("manifest-edited-after-signing", _edit_after_signing, "signature_invalid", "signature is not valid"),
    ("zip-slip", lambda a: _rewrap(a, replace={"../evil": b"x"}), "unsafe_archive", "unsafe path"),
    ("ratio-bomb", lambda a: _rewrap(a, replace={"bomb.bin": b"\x00" * (4 * 1024 * 1024)}),
     "unsafe_archive", "compression ratio"),
    ("schema-2", lambda a: _rewrap(a, manifest=lambda m: m.update(schema_version=2)), "schema_newer",
     "(schema 2); this deployment reads schema 1"),
    ("unknown-entity", _unknown_entity, "unknown_entity", "contains test_runs"),
    ("unknown-column", _unknown_column, "unknown_column", "test_suites.added_later"),
    ("wrong-type", _wrong_type, "invalid_row",
     "test_suites row 1 in entities/test_suites/part-0001.jsonl is not valid (name); nothing was imported."),
    ("retention-archive", lambda a: _rewrap(a, manifest=lambda m: m.update(scope="runs", format=None), unsign=True),
     "not_a_project_export", "not a TestLookup project export"),
]


@pytest.mark.parametrize(("variant", "code", "fragment"), [r[1:] for r in _REFUSED], ids=[r[0] for r in _REFUSED])
async def test_a_refused_archive_writes_nothing(env, variant, code, fragment):
    archive = variant(env.archive)
    projects_before = await _scalar(env, "SELECT count(*) FROM projects")
    response = await _post(env, archive)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    env.counter.inserts.clear()
    env.counter.armed = True
    try:
        await env.runner.run_import_job(uuid.UUID(job_id), storage=env.storage, session_factory=env.sessions)
    finally:
        env.counter.armed = False
    job = await _job(env, job_id)
    assert (job.status, job.error_code) == ("failed", code)
    assert fragment in job.error and "SENTINEL" not in job.error
    assert env.counter.inserts == [], f"the refused import wrote: {env.counter.inserts}"
    assert await _scalar(env, "SELECT count(*) FROM projects") == projects_before
    assert job.project_id is None and job.storage_key is None


# ── 5. authorization ─────────────────────────────────────────────────────────


async def test_only_an_admin_imports_and_another_admins_job_is_the_unknown_404(env):
    for user in (env.engineer, env.lead):
        refused = await _post(env, env.archive, user=user)
        assert refused.status_code == 403, refused.text
    mine = await _post(env, env.archive)
    assert mine.status_code == 202
    job_path = f"/api/v1/project-data-jobs/{mine.json()['id']}"
    theirs = await env.call("GET", job_path, user=env.other_admin)
    unknown = await env.call("GET", f"/api/v1/project-data-jobs/{uuid.uuid4()}", user=env.other_admin)
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json()
    own = await env.call("GET", job_path)
    assert own.status_code == 200 and own.json()["kind"] == "import"


async def test_the_import_runs_inside_a_two_connection_worker_pool(env):
    """The homelab's Celery worker pool is size 1 + overflow 1 (the export failed export_write there by holding a
    third connection): the import's ONE transaction plus its progress / manifest writes must fit in two."""
    response = await _post(env, env.archive, name=f"W24 pool check {env.tag}")
    assert response.status_code == 202, response.text
    job_id = uuid.UUID(response.json()["id"])
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=1, pool_timeout=5)
    small = async_sessionmaker(engine, expire_on_commit=False)
    try:
        status = await env.runner.run_import_job(job_id, storage=env.storage, session_factory=small)
    finally:
        await engine.dispose()
    job = await _job(env, job_id)
    assert status == "succeeded", (job.error_code, job.error)
    assert job.project_id is not None


# ── 6. W25d HYG-02: a REAL pre-W25d archive (it lists the dropped quality_gates) still imports ──

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "project_export_w24_schema1.zip"


async def test_a_pre_w25d_archive_imports_with_quality_gates_skipped_and_nothing_else_lost(env):
    """The step-0 FIXTURE (written by the W24 exporter on the pre-W25d tree; signed by a key that exists only in
    the database it came from). Every W24 export lists all its tables, so without the RETIRED_ENTITIES tolerance
    it would be refused ``unknown_entity`` for ``quality_gates``."""
    from app.services import project_export_vocab as vocab
    from app.services.project_export_entities import EXPORTED_TABLES
    from tests.integration.project_transfer_seed import exported_counts

    archive = FIXTURE.read_bytes()
    members = _members(archive)
    manifest = json.loads(members["manifest.json"])
    listed = {table: entry["rows"] for table, entry in manifest["entities"].items()}
    assert manifest["schema_version"] == 1 and len(listed) == 30 and listed["quality_gates"] == 1
    assert set(listed) == set(EXPORTED_TABLES) | set(vocab.RETIRED_ENTITIES)
    assert "entities/quality_gates/part-0001.jsonl" in members and "signature.json" in members

    job = await _import(env, archive)

    assert job.status == "succeeded", (job.error_code, job.error)
    report = job.report
    assert report["skipped"] == [{"entity": "quality_gates", "column": None, "reason": "retired_table", "count": 1}]
    expected = {table: listed[table] for table in EXPORTED_TABLES}
    assert all(rows > 0 for rows in expected.values()), expected               # declaration is not emission
    assert report["inserted"] == expected == job.counts                         # nothing else was lost
    async with env.sessions() as db:
        assert await exported_counts(db, job.project_id) == expected
    # Signed by the key of the database step 0 ran in: "unknown_key" everywhere else (CI), "valid" only there.
    async with env.sessions() as db:
        from app.services import compliance_signing

        known = json.loads(members["signature.json"])["key_id"] in await compliance_signing.public_keys(db)
    assert report["signature"] == ("valid" if known else "unknown_key") == job.signature
    assert report["source_project_name"] == manifest["project"]["name"]
    assert await _scalar(env, "SELECT to_regclass('quality_gates') IS NULL")    # the table is gone (0250)
