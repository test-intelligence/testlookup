"""W30 ADM-05 phase 2 — the run-history ROUND TRIP across Postgres, MongoDB and object storage (contract §6.3 G-C).

The source is G-A's ``seed_transfer_project(..., history=True, mongo=…, storage=…)`` (≥ 1 row of EVERY history
entity, ≥ 1 document of EVERY ``MongoSpec`` collection, objects under a run's ``minio_prefix`` + the ones that
must not travel), exported WITH history through G-B's REAL runner; every import goes through the PARTED routes
(``IMPORT_PART_MAX_BYTES`` patched to 64 KiB, so the archive needs ≥ 3 parts) or the single route, and G-B's
REAL runner, on THIS module's engine (pool 1 + 1 — the reference K3s install worker's). Proven:

1. ROUND TRIP, every store — per history entity ``count(copy) == count(exported)`` (> 0); every FK of every
   imported history row points inside the copy; NO imported id equals a source id; id lists hold copy ids only;
   C-1's run pointer points at the copy's run; timestamps EQUAL the source's; per ``MongoSpec`` the copy's
   documents == the exported ones, every id field rewritten (``session_id`` / the added ``test_run_id`` = the
   copy's run); every object under ``rebuild_prefix(minio_prefix) + suffix`` with the source's bytes, every key
   under ``{copy}/``; the copy's purge paused 90 days and NOT in ``due_project_ids(now)``; the report's history
   block equals these counts.
2. NOTHING FIRES — G-2 recorders (Celery ``send_task`` / ``Task.apply_async`` bound to the real signature and
   never published, ``aiosmtplib``, ``httpx``, ``BudgetedLLM`` — patched on the CLASSES from this test) see
   exactly ONE publish, ``reindex_search`` with ``{"project_id": <copy>, "full": True}``, and zero mail / HTTP /
   LLM; the engine listener sees no INSERT into an outbox / delivery / notification / defect / quarantine table
   and exactly the two W24 activity rows; G-3 — the insights / cadence / flaky sweeps over the copy and
   ``due_project_ids`` with the recorders armed: zero, and the copy is not due.
3. IDEMPOTENCY — the same start twice → ONE job (200, its received parts); after completion → the finished
   job; ONE copy. A resumed upload (2 parts, restart, the rest) → one copy.
4. MID-IMPORT FAILURES — (a) the ``ai_analysis_versions`` batch, (b) the 2nd ``insert_many`` of
   ``rest_api_payloads``, (c) the 2nd object ``put_object_stream``, (d) the final COMMIT → ``failed`` with its
   sentence, no copy, no ``qalead-<slug>`` user, no document by the ledger's ids, no object under its root,
   ``cleanup`` NULL, the parts removed; (e) a simulated CRASH (``SystemExit`` in the objects phase, no handler)
   → the janitor (``now`` past ``HISTORY_JOB_STALE_MINUTES``) → the same emptiness, ``failed "interrupted"``.
5. REFUSED BEFORE ANY WRITE — every crafted archive: ZERO INSERTs (engine listener), ZERO Mongo writes
   (class-level recorder), ZERO storage puts outside ``project-imports/``, ``projects`` unchanged.
6. AUTHZ — an engineer / a QA lead start → 403; another ADMIN's PUT / complete → the unknown-id 404.
7. A PHASE-1 archive (a body-less export of the same seed) still imports: no history block, no pause.

Real Postgres ``df_w30_c`` + the real local Mongo ``df_w30_c`` (``MONGO_DB``) + ``LocalStorageProvider`` on
``tmp_path``, through ``pg_pytest_w30.sh c``; ``reset_mongo_client()`` before and after; ``dispose_engine_for_loop``
before and after; EVERYTHING seeded or imported is removed by ids (CI shares one Postgres AND one Mongo).
"""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PART_BYTES = 64 * 1024                       # IMPORT_PART_MAX_BYTES for this module: a small archive needs ≥ 3 parts
NEVER_INSERTED = (
    "run_downstream_outbox", "agent_child_dispatch_outbox", "webhook_deliveries", "notification_logs",
    "defects", "defect_candidates", "flaky_quarantine_requests",
)


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Statements:
    """An engine listener: every INSERT (table name) the engine sends while armed."""

    def __init__(self) -> None:
        self.armed = False
        self.inserts: list[str] = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if self.armed and statement.lstrip().upper().startswith("INSERT"):
            self.inserts.append(statement.split()[2].strip('"'))


class Recorders:
    """G-2: every outward call the import could make, recorded while armed — Celery publishes are BOUND to the
    task's real signature (the "mocking apply_async hides Celery's arg check" lesson) and never sent."""

    def __init__(self) -> None:
        self.armed = False
        self.publishes: list[tuple[str, dict[str, Any]]] = []
        self.mail: list[str] = []
        self.http: list[str] = []
        self.llm: list[str] = []
        self.mongo_writes: list[tuple[str, str]] = []

    def reset(self) -> None:
        for bucket in (self.publishes, self.mail, self.http, self.llm, self.mongo_writes):
            bucket.clear()

    def outward(self) -> dict[str, int]:
        return {"mail": len(self.mail), "http": len(self.http), "llm": len(self.llm)}


class _Refused(RuntimeError):
    """What an armed recorder raises instead of reaching the network / the model."""


def _install_recorders(patcher: pytest.MonkeyPatch, rec: Recorders) -> None:
    """Patched on the CLASSES / modules from this test (never an edit of the product files)."""
    import aiosmtplib
    import httpx
    from celery.app.task import Task
    from motor.motor_asyncio import AsyncIOMotorCollection

    from app.services.llm_factory import BudgetedLLM
    from app.worker.celery_app import celery_app

    def apply_async(self: Any, args: Any = None, kwargs: Any = None, **options: Any) -> Any:
        inspect.signature(self.run).bind(*(args or ()), **(kwargs or {}))     # Celery's own arg check, kept
        rec.publishes.append((self.name, dict(kwargs or {}) or {"args": list(args or ())}))
        return SimpleNamespace(id=str(uuid.uuid4()))                           # never published

    def send_task(name: str, args: Any = None, kwargs: Any = None, **options: Any) -> Any:
        rec.publishes.append((name, dict(kwargs or {}) or {"args": list(args or ())}))
        return SimpleNamespace(id=str(uuid.uuid4()))

    patcher.setattr(Task, "apply_async", apply_async)
    patcher.setattr(celery_app, "send_task", send_task)

    async def mail_send(*_a: Any, **_k: Any) -> Any:
        rec.mail.append("aiosmtplib.send")
        raise _Refused("mail is never sent by an import")

    async def mail_message(self: Any, *_a: Any, **_k: Any) -> Any:
        rec.mail.append("SMTP.send_message")
        raise _Refused("mail is never sent by an import")

    patcher.setattr(aiosmtplib, "send", mail_send)
    patcher.setattr(aiosmtplib.SMTP, "send_message", mail_message)
    real_async_send, real_sync_send = httpx.AsyncClient.send, httpx.Client.send

    async def async_send(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        if not rec.armed:                     # this module's own ASGI calls to the routes
            return await real_async_send(self, request, *a, **k)
        rec.http.append(request.url.host)
        raise _Refused("no HTTP from an import")

    def sync_send(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        if not rec.armed:
            return real_sync_send(self, request, *a, **k)
        rec.http.append(request.url.host)
        raise _Refused("no HTTP from an import")

    patcher.setattr(httpx.AsyncClient, "send", async_send)
    patcher.setattr(httpx.Client, "send", sync_send)

    async def llm_async(self: Any, *_a: Any, **_k: Any) -> Any:
        rec.llm.append("BudgetedLLM.ainvoke")
        raise _Refused("no model call from an import")

    def llm_sync(self: Any, *_a: Any, **_k: Any) -> Any:
        rec.llm.append("BudgetedLLM.invoke")
        raise _Refused("no model call from an import")

    patcher.setattr(BudgetedLLM, "ainvoke", llm_async)
    patcher.setattr(BudgetedLLM, "invoke", llm_sync)

    for name in ("insert_one", "insert_many", "update_one", "update_many", "replace_one", "delete_one",
                 "delete_many", "bulk_write", "find_one_and_update", "find_one_and_replace", "find_one_and_delete"):
        real = getattr(AsyncIOMotorCollection, name)

        def wrapper(self: Any, *a: Any, _real: Callable[..., Any] = real, _op: str = name, **k: Any) -> Any:
            if rec.armed:
                rec.mongo_writes.append((self.name, _op))
            return _real(self, *a, **k)

        patcher.setattr(AsyncIOMotorCollection, name, wrapper)


class RecordingStorage:
    """``LocalStorageProvider`` with every put / delete recorded (5: no put outside ``project-imports/``)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.puts: list[str] = []
        self.fail_put: Optional[Callable[[str], None]] = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def put_object(self, key: str, content: bytes, content_type: str = "application/json",
                         bucket: Optional[str] = None) -> None:
        self.puts.append(key)
        await self.inner.put_object(key, content, content_type, bucket)

    async def put_object_stream(self, key: str, fileobj: Any, *, size: int, content_type: str = "application/zip",
                                bucket: Optional[str] = None) -> None:
        self.puts.append(key)
        if self.fail_put is not None:
            self.fail_put(key)
        await self.inner.put_object_stream(key, fileobj, size=size, content_type=content_type, bucket=bucket)

    def puts_outside_imports(self) -> list[str]:
        return [k for k in self.puts if not k.startswith("project-imports/")]


async def _copy_ids(sessions: Any, project_ids: list[uuid.UUID]) -> tuple[list[str], list[str]]:
    """The run and pipeline ids of ``project_ids`` (text) — what an import's ledger names."""
    if not project_ids:
        return [], []
    async with sessions() as db:
        runs = [str(r) for r in (await db.execute(
            text("SELECT id FROM test_runs WHERE project_id = ANY(:p)"), {"p": project_ids})).scalars()]
        pipes = [str(r) for r in (await db.execute(text(
            "SELECT a.id FROM agent_pipeline_runs a JOIN test_runs r ON r.id = a.test_run_id WHERE r.project_id = ANY(:p)"
        ), {"p": project_ids})).scalars()]
    return runs, pipes


async def _cleanup(sessions: Any, mongo: Any, storage: Any, seeded: Any, people: list[Any]) -> None:
    """Every copy this module imported (its documents by its run / pipeline ids, its objects by its root), every
    ledger a failed import left, then the source through G-A's seed cleanup — CI shares ONE Postgres AND one
    Mongo database across modules. Raises on a failure: a leak fails THIS module."""
    from sqlalchemy.orm import undefer

    from app.models.postgres import ProjectDataJob
    from app.services import project_import_history as hist
    from tests.integration.project_transfer_seed import cleanup_transfer_seed

    people_ids = [p.id for p in people]
    async with sessions() as db:
        rows = (await db.execute(
            select(ProjectDataJob).options(undefer(ProjectDataJob.cleanup))
            .where(ProjectDataJob.requested_by_id.in_(people_ids), ProjectDataJob.kind == "import")
        )).scalars().all()
        copies = sorted({row.project_id for row in rows if row.project_id is not None}, key=str)
        ledgers = [dict(row.cleanup) for row in rows if row.cleanup]
    for copy in copies:
        runs, pipes = await _copy_ids(sessions, [copy])
        await hist.cleanup_outside_postgres(mongo, storage, {"project_id": str(copy), "run_ids": runs, "pipeline_ids": pipes})
    for ledger in ledgers:
        await hist.cleanup_outside_postgres(mongo, storage, ledger)
    await cleanup_transfer_seed(sessions, seeded, mongo=mongo, storage=storage, project_ids=copies, user_ids=people_ids)


def _user(tag: str, role: str, label: str) -> Any:
    from app.models.postgres import User

    username = f"w30c-{label}-{tag}"
    return User(id=uuid.uuid4(), email=f"{username}@w30c.example.test", username=username, full_name=None,
                hashed_password="!integration", role=role, is_active=True)


async def _export(sessions: Any, storage: Any, runner: Any, *, project_id: uuid.UUID, actor: Any,
                  history: bool, options: Any = None) -> tuple[Any, bytes]:
    """One export of the source through G-B's REAL runner; ``(job, archive bytes)`` — all history by default,
    or the given ``ProjectDataOptions`` (W37: a 30-day window, one release)."""
    from app.models.postgres import Project, ProjectDataJob
    from app.models.schemas import ProjectDataOptions
    from app.services import project_data_job_service as jobs
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    if options is None and history:
        options = ProjectDataOptions(include_history=True, history_days=None, max_runs=2000)
    async with sessions() as db:
        source = await db.get(Project, project_id)
        job = await jobs.create_export_job(db, project=source, actor=actor, options=options)
        await db.commit()
    await runner.run_export_job(job.id, storage=storage, session_factory=sessions)
    async with sessions() as db:
        done = await db.get(ProjectDataJob, job.id)
    assert done.status == "succeeded", (done.error_code, done.error)          # G-B's real export runner
    return done, await storage.get_object_content(done.storage_key, bucket=COMPLIANCE_BUCKET)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env(tmp_path_factory):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core import deps
    from app.db import postgres as app_postgres
    from app.db import redis_client
    from app.db import storage as storage_module
    from app.db.mongo import get_mongo_db, reset_mongo_client
    from app.models.postgres import UserRole
    from app.routers import project_data
    from app.services import project_data_job_service as jobs_module
    from app.services import project_export_vocab as vocab
    from app.worker import project_data_runner as runner
    from tests.integration.project_transfer_seed import make_sentinels, seed_transfer_project

    await app_postgres.dispose_engine_for_loop()
    reset_mongo_client()
    mongo = get_mongo_db()                                  # synchronous: the handle of MONGO_DB (df_w30_c)
    try:
        await mongo.command("ping")
    except Exception as exc:  # noqa: BLE001 - locally only; CI's postgres-integration job has the mongo service
        pytest.skip(f"MongoDB is not reachable: {type(exc).__name__}")
    redis_was_bound = redis_client._pool is not None
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=1, pool_timeout=15)   # the reference K3s install worker's pool
    listener = _Statements()
    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    patcher = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("w30c-objects")
    patcher.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(root))
    storage = RecordingStorage(storage_module.LocalStorageProvider(default_bucket="artifacts"))
    patcher.setattr(storage_module, "get_storage_provider", lambda: storage)
    rec = Recorders()
    _install_recorders(patcher, rec)
    patcher.setattr(vocab, "IMPORT_PART_MAX_BYTES", PART_BYTES)       # the route + the start validator
    patcher.setattr(jobs_module, "IMPORT_PART_MAX_BYTES", PART_BYTES)  # the job service's plan (a module copy)

    tag = uuid.uuid4().hex[:10].translate(str.maketrans("0123456789abcdef", "ghjkmnpqrstvwxyz"))
    admin, other_admin = _user(tag, UserRole.ADMIN.value, "admin"), _user(tag, UserRole.ADMIN.value, "admin2")
    lead, engineer = _user(tag, UserRole.QA_LEAD.value, "lead"), _user(tag, UserRole.QA_ENGINEER.value, "eng")
    people = [admin, other_admin, lead, engineer]
    async with sessions() as db:
        db.add_all(people)
        await db.commit()
    for person in people:
        deps._bind_credential_kind(person, deps.CREDENTIAL_KIND_JWT)
    seeded = None
    try:
        async with sessions() as db:
            seeded = await seed_transfer_project(
                db, sentinels=make_sentinels(), history=True, mongo=mongo, storage=storage.inner,
                object_bytes=200 * 1024,
            )
        history_job, archive = await _export(sessions, storage, runner, project_id=seeded.project_id, actor=admin,
                                             history=True)
        _catalog_job, catalog_archive = await _export(sessions, storage, runner, project_id=seeded.project_id,
                                                      actor=admin, history=False)
    except BaseException:                     # a failed SETUP must not leak into the shared Postgres / Mongo
        patcher.undo()
        if seeded is not None:
            await _cleanup(sessions, mongo, storage.inner, seeded, people)
        else:
            async with sessions() as db:
                await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [p.id for p in people]})
                await db.commit()
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        reset_mongo_client()
        raise

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
            sessions=sessions, storage=storage, root=root, listener=listener, rec=rec, call=call, mongo=mongo,
            seeded=seeded, archive=archive, history_job=history_job, catalog_archive=catalog_archive,
            admin=admin, other_admin=other_admin, lead=lead, engineer=engineer, runner=runner, tag=tag,
            people=people,
        )
    finally:
        patcher.undo()
        await _cleanup(sessions, mongo, storage.inner, seeded, people)
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        reset_mongo_client()
        if not redis_was_bound and redis_client._pool is not None:     # close ONLY what this module bound
            await redis_client.close_redis()


# ── the parted upload, the runner ────────────────────────────────────────────


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parts(archive: bytes) -> list[bytes]:
    return [archive[i:i + PART_BYTES] for i in range(0, len(archive), PART_BYTES)]


async def _start(env: Any, archive: bytes, *, token: uuid.UUID, name: Optional[str] = None,
                 sha: Optional[str] = None, user: Any = None) -> Any:
    body: dict[str, Any] = {"client_token": str(token), "bytes": len(archive), "parts": len(_parts(archive)),
                            "sha256": sha or _sha(archive)}
    if name:
        body["name"] = name
    return await env.call("POST", "/api/v1/project-imports/uploads", json=body, user=user)


async def _put(env: Any, job_id: str, part: int, data: bytes, *, user: Any = None) -> Any:
    return await env.call("PUT", f"/api/v1/project-imports/uploads/{job_id}/parts/{part}", content=data, user=user,
                          headers={"Content-Type": "application/octet-stream", "X-Part-Sha256": _sha(data)})


async def _complete(env: Any, job_id: str, *, user: Any = None) -> Any:
    return await env.call("POST", f"/api/v1/project-imports/uploads/{job_id}/complete", user=user)


async def _job(env: Any, job_id: Any) -> Any:
    from sqlalchemy.orm import undefer

    from app.models.postgres import ProjectDataJob

    async with env.sessions() as db:
        return (await db.execute(select(ProjectDataJob).options(undefer(ProjectDataJob.cleanup))
                                 .where(ProjectDataJob.id == uuid.UUID(str(job_id))))).scalar_one()


async def _run(env: Any, job_id: Any) -> str:
    """G-B's REAL import runner on this module's engine (pool 1 + 1), the recorders and the listener ARMED."""
    env.rec.armed = env.listener.armed = True
    try:
        return await env.runner.run_import_job(uuid.UUID(str(job_id)), storage=env.storage, session_factory=env.sessions)
    finally:
        env.rec.armed = env.listener.armed = False


async def _upload(env: Any, archive: bytes, *, token: Optional[uuid.UUID] = None, name: Optional[str] = None,
                  sha: Optional[str] = None) -> str:
    """Start → every part → complete (202); returns the job id (not run yet)."""
    started = await _start(env, archive, token=token or uuid.uuid4(), name=name, sha=sha)
    assert started.status_code == 201, started.text
    job_id = started.json()["id"]
    for number, data in enumerate(_parts(archive), start=1):
        put = await _put(env, job_id, number, data)
        assert put.status_code == 200, put.text
    done = await _complete(env, job_id)
    assert done.status_code == 202, done.text
    return str(job_id)


async def _import_parted(env: Any, archive: bytes, **kwargs: Any) -> Any:
    job_id = await _upload(env, archive, **kwargs)
    assert env.rec.publishes[-1] == ("app.worker.tasks.import_project_data", {"args": [job_id]})   # the route's enqueue
    env.rec.reset()                                   # from here on: only what the IMPORT itself does
    env.listener.inserts.clear()
    await _run(env, job_id)
    return await _job(env, job_id)


async def _scalar(env: Any, sql: str, **params: Any) -> Any:
    async with env.sessions() as db:
        return (await db.execute(text(sql), params)).scalar_one()


async def _ledger_is_sql_null(env: Any, job_id: Any) -> bool:
    """SQL NULL, not a JSON ``null`` (which ``WHERE cleanup IS NOT NULL`` — the janitor's sweep — still sees)."""
    return bool(await _scalar(env, "SELECT cleanup IS NULL FROM project_data_jobs WHERE id = :j", j=uuid.UUID(str(job_id))))


def _members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {info.filename: zf.read(info) for info in zf.infolist()}


def _rezip(members: dict[str, bytes], *, relist: bool = True, unsign: bool = True,
           manifest: Optional[Callable[[dict[str, Any]], None]] = None) -> bytes:
    """A variant of the real export: the manifest edited / its ``files`` list recomputed / unsigned."""
    members = dict(members)
    doc = json.loads(members["manifest.json"])
    if manifest is not None:
        manifest(doc)
    if relist:
        doc["files"] = [{"name": n, "bytes": len(b), "sha256": _sha(b)} for n, b in sorted(members.items())
                        if n not in ("manifest.json", "signature.json")]
    members["manifest.json"] = json.dumps(doc, sort_keys=True).encode("utf-8")
    if unsign:
        members.pop("signature.json", None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ── reading a project's rows, documents and objects ──────────────────────────

_WINDOW_PARENT = {"run": "test_runs", "case": "test_cases", "pipeline": "agent_pipeline_runs",
                  "canonical": "canonical_test_cases", "release": "releases"}


def _in(table_name: str, project_id: uuid.UUID) -> Any:
    """``table_name``'s rows inside ``project_id`` — directly, or up its parent / window chain."""
    from app.models.postgres import Base
    from app.services.project_export_entities import spec_for

    table = Base.metadata.tables[table_name]
    if table_name == "projects":
        return table.c.id == project_id
    if "project_id" in table.c:
        return table.c.project_id == project_id
    spec = spec_for(table_name)
    if spec.parent is not None:
        parent_name, column = spec.parent
    else:
        kind, column = spec.window_parts or ("", None)
        parent_name = _WINDOW_PARENT[kind]
    parent = Base.metadata.tables[parent_name]
    return table.c[column].in_(select(parent.c.id).where(_in(parent_name, project_id)))


async def _ids(env: Any, table_name: str, project_id: uuid.UUID) -> set[uuid.UUID]:
    from app.models.postgres import Base

    table = Base.metadata.tables[table_name]
    async with env.sessions() as db:
        return set((await db.execute(select(table.c.id).where(_in(table_name, project_id)))).scalars())


async def _values(env: Any, table_name: str, column: str, project_id: uuid.UUID) -> dict[uuid.UUID, Any]:
    from app.models.postgres import Base

    table = Base.metadata.tables[table_name]
    async with env.sessions() as db:
        rows = (await db.execute(select(table.c.id, table.c[column]).where(_in(table_name, project_id)))).all()
    return {row[0]: row[1] for row in rows}


async def _documents(env: Any, project_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """Per ``MongoSpec`` the documents of ``project_id``'s runs / pipelines (case-keyed: by its cases)."""
    from app.services.project_export_documents import MONGO_COLLECTIONS

    runs = [str(i) for i in await _ids(env, "test_runs", project_id)]
    pipes = [str(i) for i in await _ids(env, "agent_pipeline_runs", project_id)]
    cases = [str(i) for i in await _ids(env, "test_cases", project_id)]
    keyed = {"test_runs": runs, "agent_pipeline_runs": pipes, "test_cases": cases}
    out: dict[str, list[dict[str, Any]]] = {}
    for spec in MONGO_COLLECTIONS:
        ids = keyed[spec.key_map]
        found: list[dict[str, Any]] = []
        for start in range(0, len(ids), 500):
            cursor = env.mongo[spec.collection].find({spec.key: {"$in": ids[start:start + 500]}})
            found += [doc async for doc in cursor]
        out[spec.collection] = found
    return out


def _index_lines(archive: bytes) -> list[dict[str, Any]]:
    members = _members(archive)
    lines: list[dict[str, Any]] = []
    for name in sorted(n for n in members if n.startswith("objects/index/")):
        lines += [json.loads(line) for line in members[name].decode("utf-8").splitlines() if line.strip()]
    return lines


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def imported(env):
    """THE history import of the module: the archive through the PARTED routes (≥ 3 parts of 64 KiB) and the
    real runner, the recorders and the INSERT listener armed — what they saw is kept for item 2."""
    parts = len(_parts(env.archive))
    assert parts >= 3, f"the history archive is {len(env.archive)} bytes: {parts} part(s) of {PART_BYTES}"
    job = await _import_parted(env, env.archive)
    assert job.status == "succeeded", (job.error_code, job.error)
    return SimpleNamespace(job=job, copy=job.project_id, publishes=list(env.rec.publishes),
                           outward=env.rec.outward(), inserts=list(env.listener.inserts), parts=parts)


# ── 1. the round trip, every store ───────────────────────────────────────────


async def test_the_history_round_trip_copies_every_entity_with_new_ids_and_kept_times(env, imported):
    from app.models.postgres import Base
    from app.services.project_export_entities import HISTORY_TABLES, spec_for
    from app.services.project_import_history import history_id
    from tests.integration.project_transfer_seed import expected_history_counts

    source, copy, exported = env.seeded.project_id, imported.copy, env.history_job.counts
    oracle = expected_history_counts(env.seeded, history_days=None)  # G-A's, independent of the export code
    for table in HISTORY_TABLES:                                      # declaration is not emission: > 0 each
        copied = await _ids(env, table, copy)
        assert len(copied) == exported[table] == oracle[table] > 0, (table, len(copied), exported.get(table))
        assert not copied & await _ids(env, table, source), f"{table}: an imported id equals a source id"
    async with env.sessions() as db:                                  # every FK inside the copy
        for table_name in HISTORY_TABLES:
            spec, table = spec_for(table_name), Base.metadata.tables[table_name]
            edges = {**spec.fks, **spec.null_unless, **{c: table_name for c in spec.self_fks}}
            for column, parent_name in edges.items():
                parent = Base.metadata.tables[parent_name]
                outside = (await db.execute(select(table.c.id).where(
                    _in(table_name, copy), table.c[column].is_not(None),
                    table.c[column].not_in(select(parent.c.id).where(_in(parent_name, copy))),
                ).limit(1))).first()
                assert outside is None, f"{table_name}.{column} points outside the copy"
    runs, cases = await _ids(env, "test_runs", copy), await _ids(env, "test_cases", copy)
    for members in (await _values(env, "failure_clusters", "member_test_ids", copy)).values():
        assert {uuid.UUID(str(m)) for m in members or []} <= cases
    for listed in (await _values(env, "release_gate_decisions", "run_ids", copy)).values():
        assert {uuid.UUID(str(r)) for r in listed or []} <= runs
    pointers = [p for p in (await _values(env, "canonical_test_cases", "first_seen_run_id", copy)).values() if p]
    assert pointers and set(pointers) <= runs                         # C-1: the copy's own runs
    job_id = imported.job.id
    for table_name, column in (("test_runs", "created_at"), ("test_runs", "exec_started_at"),
                               ("test_cases", "created_at"), ("ai_analysis", "created_at"),
                               ("agent_pipeline_runs", "completed_at")):
        before, after = await _values(env, table_name, column, source), await _values(env, table_name, column, copy)
        kept = {history_id(job_id, src): when for src, when in before.items() if history_id(job_id, src) in after}
        assert kept and all(after[new] == when for new, when in kept.items()), (table_name, column)


async def test_every_document_is_copied_with_every_id_rewritten(env, imported):
    from app.services.project_export_documents import MONGO_COLLECTIONS
    from tests.integration.project_transfer_seed import expected_document_counts

    copy, exported = imported.copy, env.history_job.counts
    oracle = expected_document_counts(env.seeded, history_days=None)
    runs = {str(i) for i in await _ids(env, "test_runs", copy)}
    pipes = {str(i) for i in await _ids(env, "agent_pipeline_runs", copy)}
    cases = {str(i) for i in await _ids(env, "test_cases", copy)}
    copies = await _documents(env, copy)
    targets = {"test_runs": runs, "agent_pipeline_runs": pipes, "test_cases": cases}
    for spec in MONGO_COLLECTIONS:
        docs = copies[spec.collection]
        assert len(docs) == exported[f"documents.{spec.collection}"] == oracle[spec.collection] > 0, spec.collection
        for doc in docs:
            assert doc[spec.key] in targets[spec.key_map]
            for field, table in spec.id_fields.items():
                assert doc.get(field) is None or doc[field] in targets[table], (spec.collection, field)
            if spec.project_field and spec.project_field in doc:
                assert doc[spec.project_field] == str(copy)
            for field in (*spec.run_fields, *spec.added_fields):
                assert doc[field] in runs, (spec.collection, field)   # session_id / the added test_run_id
    assert imported.job.report["history"]["documents"] == {c: len(d) for c, d in copies.items()}


async def test_the_copys_report_cites_the_copys_artifact_so_its_retention_keeps_it(env, imported):
    """W32 F-W30-8: the evidence refs nested in the imported report are the COPY's ids, so the retention
    purge's real reader protects the copy's cited artifact once the copy's purge pause ends."""
    from app.db.mongo import Collections
    from app.services import retention_service
    from app.services.project_import_history import history_id

    source_artifact = env.seeded.ids["evidence"]
    copy_artifact = history_id(imported.job.id, source_artifact)
    assert copy_artifact in await _ids(env, "evidence_artifacts", imported.copy)
    (report,) = (await _documents(env, imported.copy))[Collections.DECISION_REPORTS]
    body = report["decision_intelligence"]
    assert body["evidence"][0]["artifact_id"] == str(copy_artifact)
    assert body["claims"][0]["evidence"][0]["id"] == str(copy_artifact)
    assert body["evidence"][0]["scope"]["project_id"] == str(imported.copy)
    assert str(source_artifact) not in json.dumps(report, default=str), "no source artifact id in the copy"

    # A second, uncited candidate: the reader's fail-closed path would return BOTH, a real scan one.
    protected = await retention_service._published_report_artifact_ids(
        env.mongo, imported.copy, [copy_artifact, uuid.uuid4(), source_artifact]
    )
    assert protected == {copy_artifact}


async def test_every_object_is_under_the_copys_root_with_the_sources_bytes(env, imported):
    from app.services.project_export_objects import rebuild_prefix

    source, copy = env.seeded.project_id, imported.copy
    prefixes = await _values(env, "test_runs", "minio_prefix", source)
    lines = _index_lines(env.archive)
    assert lines and len(lines) == imported.job.report["history"]["objects"]
    for line in lines:
        src_prefix = prefixes[uuid.UUID(line["run_id"])]
        new_prefix = rebuild_prefix(src_prefix, source_project_id=source, new_project_id=copy)
        assert new_prefix and new_prefix.startswith(f"{copy}/")
        original = await env.storage.get_object_content(src_prefix + line["suffix"])
        assert await env.storage.get_object_content(new_prefix + line["suffix"]) == original
    keys = [o["Key"] for o in await env.storage.list_objects(f"{copy}/")]
    assert len(keys) == len(lines) and all(k.startswith(f"{copy}/") for k in keys)
    for prefix in (await _values(env, "test_runs", "minio_prefix", copy)).values():
        assert prefix is None or prefix.startswith(f"{copy}/")
    for prefix in (await _values(env, "test_cases", "minio_s3_prefix", copy)).values():
        assert prefix is None or prefix.startswith(f"{copy}/")


async def _purge_hour(env: Any, project_id: uuid.UUID) -> datetime:
    """The next ``HH:00`` of the project's EFFECTIVE purge window — the instant the hourly beat would select it
    (``due_project_ids`` matches the current UTC hour), asserted enabled so "not due" means "paused"."""
    from app.services import retention_service

    async with env.sessions() as db:
        policy = await retention_service.get_effective_policy(db, project_id)
    assert policy.enabled, "the copy's retention must be ON, or 'not due' proves nothing"
    now = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0)
    at = now.replace(hour=int(policy.purge_window_start_utc))
    return at if at > datetime.now(timezone.utc) else at + timedelta(days=1)


async def test_the_copys_purge_is_paused_and_the_report_says_what_was_written(env, imported):
    from app.services import retention_service
    from app.services.project_export_vocab import IMPORTED_PURGE_PAUSE_DAYS

    copy, report = imported.copy, imported.job.report["history"]
    paused = await _scalar(env, "SELECT purge_paused_until FROM project_retention_policies WHERE project_id = :p", p=copy)
    assert abs(paused - (imported.job.finished_at + timedelta(days=IMPORTED_PURGE_PAUSE_DAYS))) < timedelta(minutes=5)
    at = await _purge_hour(env, copy)
    async with env.sessions() as db:
        assert copy not in await retention_service.due_project_ids(db, now=at)          # the beat's own select
    assert report["runs"] == len(await _ids(env, "test_runs", copy))
    assert report["test_cases"] == len(await _ids(env, "test_cases", copy))
    created = list((await _values(env, "test_runs", "created_at", copy)).values())
    assert (datetime.fromisoformat(report["oldest_run_at"]), datetime.fromisoformat(report["newest_run_at"])) == (
        min(created), max(created))
    assert datetime.fromisoformat(report["retention_paused_until"]) == paused
    assert report["search_reindex"] == "queued"
    # what the EXPORT left out is in the report too, every reason with a sentence (pipeline_scratch included)
    from app.services.project_export_vocab import SKIP_REASONS

    manifest_skips = json.loads(_members(env.archive)["manifest.json"])["history"]["skipped"]
    reported = {(s["entity"], s["reason"]): s["count"] for s in report["skipped"]}
    assert all(s["reason"] in SKIP_REASONS for s in report["skipped"])
    for entry in manifest_skips:
        assert reported.get((entry["entity"], entry["reason"]), 0) >= entry["count"], entry
    assert ("evidence_artifacts", "pipeline_scratch") in reported
    assert ("agent_pipeline_runs", "pipeline_not_terminal") in reported
    assert await _ledger_is_sql_null(env, imported.job.id) and imported.job.storage_key is None
    # The parted upload's state is cleared at completion to SQL NULL (none_as_null), not to a JSON null.
    assert await _scalar(env, "SELECT upload IS NULL FROM project_data_jobs WHERE id = :j", j=uuid.UUID(str(imported.job.id)))
    assert imported.job.options.get("schema_version") == 3          # W37 D-1: a history export is schema 3
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    assert await env.storage.list_objects(f"project-imports/parts/{imported.job.id}/", bucket=COMPLIANCE_BUCKET) == []


# ── 2. nothing an ingest does is triggered ───────────────────────────────────


async def test_exactly_one_publish_and_no_mail_http_or_model_call(env, imported):
    copy = str(imported.copy)
    assert imported.publishes == [("app.worker.tasks.reindex_search", {"project_id": copy, "full": True})]
    assert imported.outward == {"mail": 0, "http": 0, "llm": 0}
    assert not set(imported.inserts) & set(NEVER_INSERTED), imported.inserts
    events = (await _scalar(env, "SELECT array_agg(event_type ORDER BY event_type) FROM project_activity_events "
                                 "WHERE project_id = :p", p=imported.copy))
    assert sorted(events) == ["project.created", "project.imported"]
    pipelines = await _values(env, "agent_pipeline_runs", "status", imported.copy)
    assert pipelines and set(pipelines.values()) <= {"completed", "passed", "failed"}   # nothing a reaper claims
    async with env.sessions() as db:
        leased = (await db.execute(text(
            "SELECT count(*) FROM agent_pipeline_runs a JOIN test_runs r ON r.id = a.test_run_id "
            "WHERE r.project_id = :p AND (a.lease_owner IS NOT NULL OR a.lease_expires_at IS NOT NULL "
            "OR a.next_retry_at IS NOT NULL OR a.cancel_requested)"), {"p": imported.copy})).scalar_one()
    assert leased == 0


async def test_the_sweeps_over_the_copy_send_nothing_and_its_purge_is_not_due(env, imported):
    """G-3: the sweeps that visit projects on their own schedule, run now over a database holding the copy."""
    from app.db.postgres import AsyncSessionLocal
    from app.services import retention_service
    from app.services.flaky_detection_timing_service import sweep_project
    from app.worker import tasks

    at = await _purge_hour(env, imported.copy)
    env.rec.reset()
    env.rec.armed = True
    try:
        await tasks._cross_run_insights_sweep(owner=f"w30c-{env.tag}")
        await tasks._run_cadence_check_sweep(owner=f"w30c-{env.tag}")
        async with AsyncSessionLocal() as db:
            await sweep_project(db, imported.copy)
            await db.rollback()
            due = await retention_service.due_project_ids(db, now=at)
    finally:
        env.rec.armed = False
    assert env.rec.outward() == {"mail": 0, "http": 0, "llm": 0}
    assert [p for p in env.rec.publishes if p[0] != "app.worker.tasks.reindex_search"] == []
    assert imported.copy not in due


# ── 3. idempotency through the parted routes ─────────────────────────────────


async def _copies_named(env: Any, name: str) -> int:
    return int(await _scalar(env, "SELECT count(*) FROM projects WHERE name = :n", n=name))


async def test_the_same_start_twice_is_one_job_and_one_copy(env):
    token, name = uuid.uuid4(), f"W30c same token {env.tag}"
    parts = _parts(env.archive)
    first = await _start(env, env.archive, token=token, name=name)
    assert first.status_code == 201, first.text
    job_id = first.json()["id"]
    assert (await _put(env, job_id, 1, parts[0])).status_code == 200
    again = await _start(env, env.archive, token=token, name=name)          # a re-sent start: the open job
    assert again.status_code == 200 and again.json()["id"] == job_id
    assert again.json()["upload"]["received"] == [1]
    for number, data in enumerate(parts[1:], start=2):
        assert (await _put(env, job_id, number, data)).status_code == 200
    assert (await _complete(env, job_id)).status_code == 202
    assert (await _complete(env, job_id)).status_code == 200                # an idempotent repeat, no 2nd enqueue
    assert await _run(env, job_id) == "succeeded"
    finished = await _start(env, env.archive, token=token, name=name)       # after completion: the finished job
    assert finished.status_code == 200 and finished.json()["id"] == job_id
    assert finished.json()["status"] == "succeeded"
    assert await _scalar(env, "SELECT count(*) FROM project_data_jobs WHERE client_token = :t", t=token) == 1
    assert await _copies_named(env, name) == 1


async def test_a_resumed_upload_sends_only_the_missing_parts_and_makes_one_copy(env):
    token, name = uuid.uuid4(), f"W30c resumed {env.tag}"
    parts = _parts(env.archive)
    first = await _start(env, env.archive, token=token, name=name)
    job_id = first.json()["id"]
    for number in (1, 2):                                                    # the client is interrupted here
        assert (await _put(env, job_id, number, parts[number - 1])).status_code == 200
    resumed = await _start(env, env.archive, token=token, name=name)
    assert resumed.status_code == 200 and resumed.json()["upload"]["received"] == [1, 2]
    for number, data in enumerate(parts, start=1):
        if number not in resumed.json()["upload"]["received"]:
            assert (await _put(env, job_id, number, data)).status_code == 200
    assert (await _complete(env, job_id)).status_code == 202
    assert await _run(env, job_id) == "succeeded"
    assert await _copies_named(env, name) == 1


# ── 6. authorization ─────────────────────────────────────────────────────────


async def test_only_an_admin_starts_and_another_admins_upload_is_the_unknown_404(env):
    for user in (env.engineer, env.lead):
        refused = await _start(env, env.archive, token=uuid.uuid4(), user=user)
        assert refused.status_code == 403, refused.text
    mine = await _start(env, env.archive, token=uuid.uuid4())
    job_id = mine.json()["id"]
    part = _parts(env.archive)[0]
    theirs_put = await _put(env, job_id, 1, part, user=env.other_admin)
    unknown_put = await _put(env, str(uuid.uuid4()), 1, part, user=env.other_admin)
    theirs_done = await _complete(env, job_id, user=env.other_admin)
    unknown_done = await _complete(env, str(uuid.uuid4()), user=env.other_admin)
    assert theirs_put.status_code == unknown_put.status_code == theirs_done.status_code == unknown_done.status_code == 404
    assert theirs_put.json() == unknown_put.json() == theirs_done.json() == unknown_done.json()
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    assert await env.storage.list_objects(f"project-imports/parts/{job_id}/", bucket=COMPLIANCE_BUCKET) == []


# ── 7. a phase-1 archive still imports ───────────────────────────────────────


async def test_a_catalog_archive_still_imports_with_no_history_and_no_pause(env):
    from app.services.project_export_entities import CATALOG_TABLES

    assert json.loads(_members(env.catalog_archive)["manifest.json"])["schema_version"] == 1
    job = await _import_parted(env, env.catalog_archive)       # the parted route takes a schema-1 file the same
    assert job.status == "succeeded", (job.error_code, job.error)
    assert job.report.get("history") is None and set(job.counts) == set(CATALOG_TABLES)
    assert await _scalar(env, "SELECT count(*) FROM test_runs WHERE project_id = :p", p=job.project_id) == 0
    assert await _scalar(env, "SELECT count(*) FROM project_retention_policies WHERE project_id = :p "
                              "AND purge_paused_until IS NOT NULL", p=job.project_id) == 0
    assert env.rec.publishes == [] and env.rec.outward() == {"mail": 0, "http": 0, "llm": 0}   # phase 1, unchanged


# ── W37 F-W30-3: plan execution results travel with history (schema 3) ───────

CHARGED, OTHER = "Checkout charges the saved card", "Refund reverses the ledger entries"   # the seed's two items
_PLAN_EXECUTION = ("execution_status", "executed_by_id", "executed_at", "execution_notes", "actual_duration_minutes",
                   "executed_case_version", "test_case_result_id")


async def _plan_items(env: Any, project_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """The project's plan items keyed by their managed case's title."""
    async with env.sessions() as db:
        rows = (await db.execute(text(
            "SELECT c.title, " + ", ".join(f"i.{c}" for c in _PLAN_EXECUTION) + " FROM test_plan_items i "
            "JOIN test_plans p ON p.id = i.plan_id JOIN managed_test_cases c ON c.id = i.test_case_id "
            "WHERE p.project_id = :p"), {"p": project_id})).mappings().all()
    return {row["title"]: dict(row) for row in rows}


async def _plan_counters(env: Any, project_id: uuid.UUID) -> list[tuple[int, ...]]:
    async with env.sessions() as db:
        return [tuple(r) for r in (await db.execute(text(
            "SELECT total_cases, executed_cases, passed_cases, failed_cases, blocked_cases FROM test_plans "
            "WHERE project_id = :p"), {"p": project_id})).all()]


def _archive_rows(archive: bytes, table: str) -> dict[str, dict[str, Any]]:
    members = _members(archive)
    rows: dict[str, dict[str, Any]] = {}
    for name in sorted(n for n in members if n.startswith(f"entities/{table}/")):
        for line in members[name].decode("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["id"]] = row
    return rows


async def test_plan_execution_results_travel_with_history_and_point_at_the_copys_case(env, imported):
    """Status, time, author (by USERNAME), version, duration, notes (S-6 scrubbed) arrive; ``test_case_result_id``
    is the copy's case of the same run (uuid5 of the job); the copy's counters are recomputed from its items."""
    from app.services.project_import_history import history_id

    seed, job = env.seeded, imported.job
    source, copy = await _plan_items(env, seed.project_id), await _plan_items(env, imported.copy)
    assert set(copy) == {CHARGED, OTHER}
    for title in (CHARGED, OTHER):
        for column in ("execution_status", "executed_at", "executed_case_version", "actual_duration_minutes"):
            assert copy[title][column] == source[title][column], (title, column)
    assert (copy[CHARGED]["execution_status"], copy[OTHER]["execution_status"]) == ("passed", "failed")
    assert copy[CHARGED]["executed_by_id"] == seed.assignee_id and copy[OTHER]["executed_by_id"] == seed.author_id
    notes = copy[CHARGED]["execution_notes"]
    assert notes.startswith("Passed after re-auth") and seed.sentinels.jwt not in notes and notes != source[CHARGED][
        "execution_notes"]
    assert copy[CHARGED]["test_case_result_id"] == history_id(job.id, seed.ids["run_case_charge"])
    assert copy[OTHER]["test_case_result_id"] == history_id(job.id, seed.ids["case_old"])     # all history: it travelled
    assert await _plan_counters(env, imported.copy) == [(2, 2, 1, 1, 0)]                      # recompute_plan_counts' formula
    rows = _archive_rows(env.archive, "test_plan_items")
    assert json.loads(_members(env.archive)["manifest.json"])["schema_version"] == 3
    assert set(_PLAN_EXECUTION) <= set(rows[str(seed.ids["plan_item"])])


async def test_a_catalog_round_trip_still_drops_the_plan_execution_results(env):
    manifest = json.loads(_members(env.catalog_archive)["manifest.json"])
    assert manifest["schema_version"] == 1
    assert not set(_PLAN_EXECUTION) & set(manifest["entities"]["test_plan_items"]["columns"])
    assert all(not set(_PLAN_EXECUTION) & set(row) for row in _archive_rows(env.catalog_archive, "test_plan_items").values())
    job = await _import_parted(env, env.catalog_archive)
    assert job.status == "succeeded", (job.error_code, job.error)
    copy = await _plan_items(env, job.project_id)
    assert {row["execution_status"] for row in copy.values()} == {"not_run"}
    assert all(row[c] is None for row in copy.values() for c in _PLAN_EXECUTION if c != "execution_status")
    assert await _plan_counters(env, job.project_id) == [(1, 0, 0, 0, 0)]     # total_cases as exported, the rest 0


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def windowed(env):
    """A 30-DAY history export of the seed (the 45-day-old run stays behind) and its import."""
    from app.models.schemas import ProjectDataOptions

    options = ProjectDataOptions(include_history=True, history_days=30, max_runs=2000)
    export, archive = await _export(env.sessions, env.storage, env.runner, project_id=env.seeded.project_id,
                                    actor=env.admin, history=True, options=options)
    job = await _import_parted(env, archive)
    assert job.status == "succeeded", (job.error_code, job.error)
    return SimpleNamespace(export=export, archive=archive, job=job, copy=job.project_id)


async def test_a_plan_result_whose_case_is_outside_the_window_arrives_null_with_its_manual_result(env, windowed):
    from app.services.project_import_history import history_id

    seed = env.seeded
    written = _archive_rows(windowed.archive, "test_plan_items")
    assert written[str(seed.ids["plan_item_old"])]["test_case_result_id"] is None    # the EXPORT wrote it NULL
    assert written[str(seed.ids["plan_item"])]["test_case_result_id"] == str(seed.ids["run_case_charge"])
    source, copy = await _plan_items(env, seed.project_id), await _plan_items(env, windowed.copy)
    assert copy[OTHER]["test_case_result_id"] is None
    for column in ("execution_status", "executed_at", "executed_by_id", "executed_case_version"):
        assert copy[OTHER][column] == source[OTHER][column], column                  # the manual result is kept
    assert copy[CHARGED]["test_case_result_id"] == history_id(windowed.job.id, seed.ids["run_case_charge"])
    assert not any(e["entity"] == "test_plan_items" for e in windowed.job.report["cleared"])   # nothing to clear
    assert await _plan_counters(env, windowed.copy) == [(2, 2, 1, 1, 0)]


# ── W37 F-W30-1: a release-scoped history export (D-2: primary AND secondary links) ──


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def release_scoped(env):
    """A history export scoped to the seed's release "2026.11" — linked: the FAILED run (primary; it is also linked
    to another release), the OLD run (SECONDARY) and the IN_PROGRESS run; unlinked: the W24 run — plus a CROSS-
    PROJECT link row (another project's run under "2026.11", its link even claiming the source project); its
    import through the parted routes. The extra project / run / link are removed here."""
    from app.models import postgres as m
    from app.models.schemas import ProjectDataOptions

    seed = env.seeded
    other = m.Project(id=uuid.uuid4(), name=f"W37 other {env.tag}", slug=f"w37-other-{env.tag}", is_active=True)
    stray = m.TestRun(id=uuid.uuid4(), project_id=other.id, build_number=f"w37-stray-{env.tag}", status="PASSED",
                      ingestion_source="file", created_at=datetime.now(timezone.utc) - timedelta(days=1))
    link = m.ReleaseTestRunLink(id=uuid.uuid4(), release_id=seed.ids["release_child"], test_run_id=stray.id,
                                is_primary=False, link_source="manual_ui", project_id=seed.project_id)
    async with env.sessions() as db:
        db.add(other)
        await db.flush()
        db.add(stray)
        await db.flush()
        db.add(link)
        await db.commit()
    try:
        options = ProjectDataOptions(include_history=True, history_days=None, max_runs=2000,
                                     history_release_id=seed.ids["release_child"])
        export, archive = await _export(env.sessions, env.storage, env.runner, project_id=seed.project_id,
                                        actor=env.admin, history=True, options=options)
        job = await _import_parted(env, archive)
        assert job.status == "succeeded", (job.error_code, job.error)
        yield SimpleNamespace(export=export, archive=archive, job=job, copy=job.project_id, stray=stray.id)
    finally:
        async with env.sessions() as db:
            await db.execute(text("DELETE FROM release_test_run_links WHERE id = :l"), {"l": link.id})
            await db.execute(text("DELETE FROM test_runs WHERE id = :r"), {"r": stray.id})
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": other.id})
            await db.commit()


def _run_lines(archive: bytes) -> list[str]:
    members = _members(archive)
    return [json.loads(line)["id"] for name in sorted(n for n in members if n.startswith("entities/test_runs/"))
            for line in members[name].decode("utf-8").splitlines() if line.strip()]


async def test_a_release_scoped_export_holds_exactly_the_releases_linked_terminal_runs(env, release_scoped):
    from app.services.project_import_history import history_id

    seed, release = env.seeded, str(env.seeded.ids["release_child"])
    lines = _run_lines(release_scoped.archive)
    assert sorted(lines) == sorted([str(seed.ids["run_failed"]), str(seed.ids["run_old"])])   # each ONCE
    assert str(release_scoped.stray) not in lines and str(seed.ids["run"]) not in lines         # cross-project / unlinked
    manifest = json.loads(_members(release_scoped.archive)["manifest.json"])
    block = manifest["history"]
    assert manifest["schema_version"] == 3 and block["window_days"] is None
    assert block["release"] == {"id": release, "name": "2026.11"}
    assert (block["runs"], block["runs_in_progress_skipped"], block["runs_over_cap"]) == (2, 1, 0)
    assert release_scoped.export.options == {"include_history": True, "history_days": None, "max_runs": 2000,
                                             "history_release_id": release}
    copied = await _ids(env, "test_runs", release_scoped.copy)
    assert copied == {history_id(release_scoped.job.id, seed.ids["run_failed"]),
                      history_id(release_scoped.job.id, seed.ids["run_old"])}


async def test_the_estimate_of_a_release_counts_its_runs_and_refuses_another_projects_release(env, release_scoped):
    seed = env.seeded
    path = f"/api/v1/projects/{seed.project_id}/export-estimate"
    before = await _scalar(env, "SELECT count(*) FROM project_data_jobs")
    answer = await env.call("GET", path, params={"history_release_id": str(seed.ids["release_child"])})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["runs"], body["runs_in_progress"], body["runs_over_cap"]) == (2, 1, 0)
    assert body["history_days"] is None and body["history_release_id"] == str(seed.ids["release_child"])
    async with env.sessions() as db:
        foreign = (await db.execute(text("SELECT id FROM releases WHERE project_id <> :p LIMIT 1"),
                                    {"p": seed.project_id})).scalar()
    for release in [r for r in (foreign, uuid.uuid4()) if r is not None]:
        refused = await env.call("GET", path, params={"history_release_id": str(release)})
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "history_release_invalid"
    assert await _scalar(env, "SELECT count(*) FROM project_data_jobs") == before


# ── 4. a failure mid-import: three stores, one outcome ───────────────────────


def _archive_ids(archive: bytes, table: str) -> list[str]:
    members = _members(archive)
    ids: list[str] = []
    for name in sorted(n for n in members if n.startswith(f"entities/{table}/")):
        ids += [json.loads(line)["id"] for line in members[name].decode("utf-8").splitlines() if line.strip()]
    return ids


async def _qa_leads(env: Any) -> int:
    """The synthetic QA leads an import of THIS source can create (``qalead-<source slug>-imported…``)."""
    from app.services.project_import_service import slug_candidates

    return int(await _scalar(env, "SELECT count(*) FROM users WHERE username = ANY(:u)",
                             u=[f"qalead-{s}" for s in slug_candidates(env.seeded.project_slug)]))


async def _assert_nothing_left(env: Any, job: Any, *, name: str, leads_before: int, objects_before: set[str]) -> None:
    """No copy, no synthetic QA lead, no document by the job's (uuid5) run / pipeline ids, no new object, no
    ledger, no part — whatever store the failure happened in."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET
    from app.services.project_export_documents import MONGO_COLLECTIONS
    from app.services.project_import_history import history_id

    assert job.status == "failed" and job.project_id is None and await _ledger_is_sql_null(env, job.id)
    assert await _copies_named(env, name) == 0
    assert await _qa_leads(env) == leads_before
    runs = [str(history_id(job.id, s)) for s in _archive_ids(env.archive, "test_runs")]
    pipes = [str(history_id(job.id, s)) for s in _archive_ids(env.archive, "agent_pipeline_runs")]
    for spec in MONGO_COLLECTIONS:
        field, ids = (("test_run_id", runs) if spec.key_map == "test_cases"
                      else (spec.key, pipes if spec.key_map == "agent_pipeline_runs" else runs))
        assert await env.mongo[spec.collection].count_documents({field: {"$in": ids}}) == 0, spec.collection
    assert {o["Key"] for o in await env.storage.list_objects("")} == objects_before          # no object kept
    assert await env.storage.list_objects(f"project-imports/parts/{job.id}/", bucket=COMPLIANCE_BUCKET) == []


def _fail_ai_versions(monkeypatch: Any, env: Any) -> None:
    from app.services import project_import_service as svc

    real = svc._insert_batch

    async def failing(db: Any, table: Any, rows: Any) -> None:
        if table.name == "ai_analysis_versions":
            raise RuntimeError("injected: the ai_analysis_versions batch")
        await real(db, table, rows)

    monkeypatch.setattr(svc, "_insert_batch", failing)


def _fail_second_rest_insert(monkeypatch: Any, env: Any) -> None:
    from app.services import project_export_vocab as vocab
    from app.services import project_import_history as hist

    monkeypatch.setattr(vocab, "MONGO_BATCH_DOCS", 1)            # one document per insert_many
    real, calls = hist._insert, {"n": 0}
    second = min(2, int(env.history_job.counts["documents.rest_api_payloads"]))

    class _LostPrimary:                                          # the DRIVER call fails, under the import's own wrapper
        def __init__(self, inner: Any) -> None:
            self.name = inner.name

        async def insert_many(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("injected: an insert_many of rest_api_payloads")

    async def failing(collection: Any, batch: Any) -> None:
        if collection.name == "rest_api_payloads":
            calls["n"] += 1
            if calls["n"] == second:
                await real(_LostPrimary(collection), batch)
                return
        await real(collection, batch)

    monkeypatch.setattr(hist, "_insert", failing)


def _fail_second_object(monkeypatch: Any, env: Any) -> None:
    seen = {"n": 0}

    def failing(key: str) -> None:
        if not key.startswith("project-imports/"):
            seen["n"] += 1
            if seen["n"] == 2:
                raise RuntimeError("injected: the 2nd object put")

    monkeypatch.setattr(env.storage, "fail_put", failing)


def _fail_commit(monkeypatch: Any, env: Any) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.postgres import ProjectDataJob

    real = AsyncSession.commit

    async def failing(self: Any) -> None:
        if any(isinstance(o, ProjectDataJob) and o.kind == "import" and o.status == "succeeded" and o.project_id
               for o in list(self.sync_session.identity_map.values())):
            raise RuntimeError("injected: the import's COMMIT")
        await real(self)

    monkeypatch.setattr(AsyncSession, "commit", failing)


@pytest.mark.parametrize(("inject", "code", "error_type"), [
    (_fail_ai_versions, "import_failed", "RuntimeError"),
    (_fail_second_rest_insert, "documents_unwritable", "RuntimeError"),
    (_fail_second_object, "object_unreadable", "RuntimeError"),
    (_fail_commit, "import_failed", "RuntimeError"),
], ids=["a-ai-versions-batch", "b-second-insert-many", "c-second-object", "d-final-commit"])
async def test_a_failure_mid_import_leaves_nothing_in_any_store(env, monkeypatch, inject, code, error_type):
    from app.services import project_export_vocab as vocab

    name = f"W30c doomed {code} {uuid.uuid4().hex[:4].translate(str.maketrans('0123456789abcdef', 'ghjkmnpqrstvwxyz'))}"
    objects_before = {o["Key"] for o in await env.storage.list_objects("")}
    leads_before = await _qa_leads(env)
    job_id = await _upload(env, env.archive, name=name)
    inject(monkeypatch, env)
    await _run(env, job_id)
    job = await _job(env, job_id)
    assert job.error_code == code, (job.error_code, job.error)
    assert job.error == vocab.ERROR_SENTENCES[code].format(error_type=error_type)
    await _assert_nothing_left(env, job, name=name, leads_before=leads_before, objects_before=objects_before)


async def test_a_crash_mid_import_is_cleaned_by_the_janitor(env, monkeypatch):
    """(e) No handler ran (OOM, eviction): ``SystemExit`` in the objects phase with the import's own cleanup
    disabled → the job stays ``running`` with its LEDGER; the janitor, ``now`` past ``HISTORY_JOB_STALE_MINUTES``,
    finds a ledger whose project never committed → the same cleanup → ``failed`` ``interrupted``."""
    from app.services import project_export_vocab as vocab
    from app.services import project_import_history as hist

    name = f"W30c crashed {env.tag}"
    objects_before = {o["Key"] for o in await env.storage.list_objects("")}
    leads_before = await _qa_leads(env)
    job_id = await _upload(env, env.archive, name=name)
    real_cleanup = hist.cleanup_outside_postgres

    async def no_cleanup(*_a: Any, **_k: Any) -> dict[str, int]:
        return {}

    def crash(key: str) -> None:
        if not key.startswith("project-imports/"):
            raise SystemExit("simulated worker crash")

    monkeypatch.setattr(hist, "cleanup_outside_postgres", no_cleanup)
    monkeypatch.setattr(env.storage, "fail_put", crash)
    with pytest.raises(SystemExit):
        await _run(env, job_id)
    crashed = await _job(env, job_id)
    assert crashed.status == "running" and crashed.cleanup and crashed.cleanup["run_ids"]
    monkeypatch.setattr(hist, "cleanup_outside_postgres", real_cleanup)
    monkeypatch.setattr(env.storage, "fail_put", None)
    later = datetime.now(timezone.utc) + timedelta(minutes=vocab.HISTORY_JOB_STALE_MINUTES + 5)
    await env.runner.run_sweep(now=later, storage=env.storage, session_factory=env.sessions)
    job = await _job(env, job_id)
    assert job.error_code == "interrupted" and job.error == vocab.ERROR_SENTENCES["interrupted"]
    await _assert_nothing_left(env, job, name=name, leads_before=leads_before, objects_before=objects_before)


# ── 5. refused before ANY write, in every store ──────────────────────────────

_OPERATOR = "$" + "where"                     # built at run time
_UP = ".." + "/"


def _first(members: dict[str, bytes], prefix: str) -> str:
    if prefix == "objects/<run>/":                       # an object member, never the index
        return sorted(n for n in members if n.startswith("objects/") and not n.startswith("objects/index/"))[0]
    return sorted(n for n in members if n.startswith(prefix))[0]


def _flip(members: dict[str, bytes], name: str) -> bytes:
    data = members[name]
    return _rezip({**members, name: data[:-2] + bytes([data[-2] ^ 0x01]) + data[-1:]}, relist=False, unsign=False)


def _with_document(archive: bytes, doc: dict[str, Any]) -> bytes:
    """One crafted line appended to the run_summaries part, its count and the files list made consistent."""
    from bson import json_util

    members = _members(archive)
    name = _first(members, "documents/run_summaries/")
    line = json_util.dumps(doc, sort_keys=True).encode("utf-8") + b"\n"

    def count(m: dict[str, Any]) -> None:
        m["history"]["documents"]["run_summaries"]["docs"] += 1
    return _rezip({**members, name: members[name] + line}, manifest=count)


def _with_suffix(archive: bytes, suffix: str) -> bytes:
    members = _members(archive)
    name = _first(members, "objects/index/")
    lines = [json.loads(x) for x in members[name].decode("utf-8").splitlines() if x.strip()]
    lines[0]["suffix"] = suffix
    return _rezip({**members, name: b"".join(json.dumps(x, sort_keys=True).encode() + b"\n" for x in lines)})


def _unknown_collection(archive: bytes) -> bytes:
    members = _members(archive)

    def listed(m: dict[str, Any]) -> None:
        m["history"]["documents"]["secret_stash"] = {"docs": 1, "parts": 1}
    return _rezip({**members, "documents/secret_stash/part-0001.jsonl": b'{"x": 1}\n'}, manifest=listed)


def _too_many(archive: bytes) -> bytes:
    members = _members(archive)
    members.update({f"pad/{n:05d}": b"" for n in range(40_001)})
    return _rezip(members)


def _deep(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"leaf": "SENTINELdeepValueQuokka"}
    for _ in range(depth):
        node = {"n": node}
    return node


def _run_key(archive: bytes) -> str:
    return _archive_ids(archive, "test_runs")[0]


_CRAFTED: list[tuple[str, Callable[[bytes], bytes], str, str]] = [
    ("document-byte-flipped", lambda a: _flip(_members(a), _first(_members(a), "documents/")), "tampered",
     "a member was changed"),
    ("object-byte-flipped", lambda a: _flip(_members(a), _first(_members(a), "objects/<run>/")), "tampered",
     "a member was changed"),
    ("manifest-edited-after-signing", lambda a: _rezip(_members(a), relist=False, unsign=False,
                                                       manifest=lambda m: m["project"].update(name="Someone Else")),
     "signature_invalid", "signature is not valid"),
    ("traversal-member", lambda a: _rezip({**_members(a), "objects/" + _UP + "x.bin": b"x"}), "tampered",
     "an unexpected member"),
    ("unsafe-index-suffix", lambda a: _with_suffix(a, _UP + _UP + "outside/k.json"), "tampered", "an unsafe object key"),
    ("ratio-bomb", lambda a: _rezip({**_members(a), "bomb.bin": b"\x00" * (4 * 1024 * 1024)}), "unsafe_archive",
     "compression ratio"),
    ("forty-thousand-and-one-members", _too_many, "unsafe_archive", "the limit is 40000"),
    ("schema-4", lambda a: _rezip(_members(a), manifest=lambda m: m.update(schema_version=4)), "schema_newer",
     "(schema 4); this deployment reads schema 1, 2, 3"),
    ("unknown-collection", _unknown_collection, "unknown_entity", "contains secret_stash"),
    ("operator-key", lambda a: _with_document(a, {"test_run_id": _run_key(a), _OPERATOR: "SENTINELquokka"}),
     "invalid_document", "documents/run_summaries/part-0001.jsonl"),
    ("dotted-key", lambda a: _with_document(a, {"test_run_id": str(uuid.uuid4()), "a.b": "SENTINELquokka"}),
     "invalid_document", "a key containing a dot"),
    ("depth-33", lambda a: _with_document(a, {"test_run_id": str(uuid.uuid4()), **_deep(33)}), "invalid_document",
     "nested deeper than 32"),
    ("an-id-field", lambda a: _with_document(a, {"test_run_id": str(uuid.uuid4()), "_id": "SENTINELquokka"}),
     "invalid_document", "an _id field"),
]


async def _refused_run(env: Any, job_id: str) -> Any:
    projects_before = await _scalar(env, "SELECT count(*) FROM projects")
    env.rec.reset()
    env.listener.inserts.clear()
    env.storage.puts.clear()
    await _run(env, job_id)
    job = await _job(env, job_id)
    assert env.listener.inserts == [], f"the refused import wrote: {env.listener.inserts}"
    assert env.rec.mongo_writes == [], f"the refused import wrote documents: {env.rec.mongo_writes}"
    assert env.storage.puts_outside_imports() == [], env.storage.puts_outside_imports()
    assert await _scalar(env, "SELECT count(*) FROM projects") == projects_before
    assert job.project_id is None and await _ledger_is_sql_null(env, job.id)
    return job


@pytest.mark.parametrize(("craft", "code", "fragment"), [c[1:] for c in _CRAFTED], ids=[c[0] for c in _CRAFTED])
async def test_a_crafted_archive_is_refused_before_any_write_in_any_store(env, craft, code, fragment):
    archive = craft(env.archive)
    posted = await env.call("POST", "/api/v1/projects/import", data={"client_token": str(uuid.uuid4())},
                            files={"file": ("history.zip", archive, "application/zip")})
    assert posted.status_code == 202, posted.text
    job = await _refused_run(env, posted.json()["id"])
    assert (job.status, job.error_code) == ("failed", code), job.error
    assert fragment in job.error and "SENTINEL" not in job.error


async def test_an_assembled_upload_that_is_not_the_starts_file_is_refused(env):
    job_id = await _upload(env, env.archive, sha="0" * 64)          # every part right, the whole-file digest wrong
    job = await _refused_run(env, job_id)
    assert (job.status, job.error_code) == ("failed", "upload_digest_mismatch")
