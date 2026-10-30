"""W24 ADM-05 — the project export against a real PostgreSQL and the REAL local object store (contract §6.3 G-B).

G-A's transfer seed builds one project carrying every exported entity (and the rows that must not
travel); the REAL runner (``run_export_job``) exports it on its read-only snapshot. Proven here:

1. the job ``succeeded``; for EVERY exported entity ``counts[t] == SELECT count(*)`` of the source and
   ``> 0`` (declaration is not emission); the manifest's ``entities`` equal the counts; no member is named
   after an excluded / not-project-scoped table;
2. no seeded secret sentinel (nor its base64 / URL-encoded form) is in ANY decompressed member, and
   ``[REDACTED]`` is where each typed one was;
3. the download through the router as a QA-lead member streams bytes whose SHA-256 is ``archive_sha256``,
   with ``Content-Length``; a non-member and a viewer get the identical 404 body;
4. the source is untouched: per exported table the row count and ``max(updated_at)`` are equal before /
   after, and the engine listener saw no INSERT / UPDATE / DELETE on an exported table during the build;
5. ``verify_archive_file`` on the downloaded bytes → ``chain: intact``, ``signature: valid``;
6. the janitor past ``expires_at`` → the object is gone, ``expired``, the download 410; a ``running`` job
   older than ``JOB_STALE_MINUTES`` → ``failed`` / ``interrupted``.

Fixture rules (§6.3): ``dispose_engine_for_loop()`` before the engine is built and after it is disposed;
the runner gets THIS module's session factory; object storage = ``LocalStorageProvider`` in a temp dir;
Redis is never bound (``get_redis`` is a no-op double for the router's membership read), Mongo is not
touched. Runs ONLY through ``pg_pytest_w24.sh b …`` on ``df_w24_b`` migrated to 0247.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import urllib.parse
import uuid
import zipfile
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_WRITE = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?([a-z_]+)\"?", re.IGNORECASE)
NOT_FOUND = {"detail": {"code": "job_not_found", "message": "Project data job not found."}}


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _NoRedis:
    async def get(self, *_: Any) -> None:
        return None

    async def set(self, *_: Any, **__: Any) -> None:
        return None

    async def delete(self, *_: Any) -> None:
        return None


def _forms(value: str) -> list[str]:
    raw = value.encode()
    return [value, base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode(),
            urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)]


async def _source_state(sessions: Any, project_id: uuid.UUID) -> dict[str, tuple[int, Any]]:
    """Per exported table: (rows of the project, max(updated_at) where the column exists)."""
    from app.models.postgres import Base
    from app.services.project_export_entities import plan
    from app.services.project_export_service import scope_clause

    state: dict[str, tuple[int, Any]] = {}
    async with sessions() as db:
        for spec in plan():
            table = Base.metadata.tables[spec.table]
            where = scope_clause(spec, project_id)
            rows = (await db.execute(select(func.count()).select_from(table).where(where))).scalar_one()
            latest = None
            if "updated_at" in table.c:
                latest = (await db.execute(select(func.max(table.c.updated_at)).where(where))).scalar_one()
            state[spec.table] = (int(rows), latest)
    return state


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env() -> Any:
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=5, max_overflow=2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sessions.engine = engine  # type: ignore[attr-defined]
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def exported(env: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Seed once, export once through the REAL runner; every check below reads this one export."""
    from app.db import storage as storage_module
    from app.models.postgres import Project, User
    from app.services import project_data_job_service as jobs
    from app.worker import project_data_runner as runner
    from tests.integration.project_transfer_seed import exported_counts, make_sentinels, seed_transfer_project

    root = tmp_path_factory.mktemp("objects")
    patch = pytest.MonkeyPatch()
    patch.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(root))
    patch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
    store = storage_module.LocalStorageProvider(default_bucket="artifacts")
    patch.setattr(storage_module, "get_storage_provider", lambda: store)
    try:
        sentinels = make_sentinels()
        async with env() as db:
            seed = await seed_transfer_project(db, sentinels=sentinels)
        async with env() as db:
            source_counts = await exported_counts(db, seed.project_id)
        before = await _source_state(env, seed.project_id)
        async with env() as db:
            project = await db.get(Project, seed.project_id)
            admin = await db.get(User, seed.admin_id)
            job = await jobs.create_export_job(db, project=project, actor=admin)
            await db.commit()
        writes: list[str] = []

        def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
            match = _WRITE.match(statement)
            if match:
                writes.append(match.group(1).lower())

        event.listen(env.engine.sync_engine, "before_cursor_execute", _record)
        try:
            status = await runner.run_export_job(job.id, storage=store, session_factory=env)
        finally:
            event.remove(env.engine.sync_engine, "before_cursor_execute", _record)
        after = await _source_state(env, seed.project_id)
        async with env() as db:
            row = await db.get(jobs.ProjectDataJob, job.id)
        chunks = [chunk async for chunk in store.stream_object(row.storage_key, bucket="compliance-packs")] \
            if row.storage_key else []
        yield SimpleNamespace(seed=seed, sentinels=sentinels, source_counts=source_counts, before=before,
                              after=after, job=row, status=status, archive=b"".join(chunks), writes=writes,
                              store=store, root=root)
    finally:
        patch.undo()


def _members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


async def _member(env: Any, project_id: uuid.UUID, role: str, label: str) -> Any:
    from app.models.postgres import ProjectMember, User

    username = f"xfer-{label}-{uuid.uuid4().hex[:8]}g"
    user = User(id=uuid.uuid4(), username=username, email=f"{username}@xfer.example.test",
                full_name=label.title(), hashed_password="!", role=role, is_active=True)
    async with env() as db:
        db.add(user)
        await db.flush()
        if project_id is not None:
            db.add(ProjectMember(user_id=user.id, project_id=project_id, role=role))
        await db.commit()
    return user


def _client(env: Any, user: Any) -> Any:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import project_data

    app = FastAPI()
    app.include_router(project_data.router)

    async def _db() -> Any:
        async with env() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── 1. counts ───────────────────────────────────────────────────────────────

async def test_every_exported_entity_is_written_with_the_source_count(exported: Any) -> None:
    from app.services.project_export_entities import EXCLUDED, EXPORTED_TABLES, NOT_PROJECT_SCOPED

    assert exported.status == "succeeded" and exported.job.status == "succeeded", exported.job.error
    counts = exported.job.counts
    assert set(counts) == set(EXPORTED_TABLES)
    empty = sorted(t for t in EXPORTED_TABLES if counts[t] <= 0)
    assert empty == [], f"declared but not emitted: {empty}"
    assert counts == exported.source_counts
    manifest = json.loads(_members(exported.archive)["manifest.json"])
    assert {t: entry["rows"] for t, entry in manifest["entities"].items()} == counts
    assert manifest["scope"] == "project" and manifest["project"]["slug"] == exported.seed.project_slug
    names = list(_members(exported.archive))
    allowed = re.compile(r"^(entities/[a-z_]{1,63}/part-\d{4}\.jsonl|directory/users\.jsonl|README\.txt|"
                         r"manifest\.json|signature\.json)$")
    assert all(allowed.match(n) for n in names), names
    tables = {n.split("/")[1] for n in names if n.startswith("entities/")}
    assert tables == set(EXPORTED_TABLES)
    assert not tables & (set(EXCLUDED) | set(NOT_PROJECT_SCOPED))


# ── 2. secrets ──────────────────────────────────────────────────────────────

async def test_no_sentinel_in_any_member_in_any_form_and_redacted_where_typed(exported: Any) -> None:
    members = _members(exported.archive)
    secrets = dict(exported.sentinels.values())
    secrets["api_key_plaintext"] = exported.seed.api_key_plaintext
    for name, data in members.items():
        body = data.decode("utf-8")
        for label, value in secrets.items():
            for form in _forms(value):
                assert form not in body, f"{label} leaked into {name}"
    from app.services.project_export_scrub import URL_COLUMNS

    for label, (table, column) in exported.seed.placements.items():
        parts = [data.decode() for name, data in members.items() if name.startswith(f"entities/{table}/")]
        assert parts, f"{table} (where {label} was typed) has no part"
        values = [json.loads(line).get(column) for part in parts for line in part.splitlines()]
        present = [value for value in values if value is not None]
        assert present, (label, table, column)
        if label == "basic_pass" and column in URL_COLUMNS.get(table, ()):
            # S-4: a URL's userinfo is REMOVED (no marker): the host stays, the credentials do not.
            netlocs = [urllib.parse.urlsplit(value).netloc for value in present]
            assert netlocs and all("@" not in netloc for netloc in netlocs), (label, table, column)
        else:
            assert any("[REDACTED]" in json.dumps(value) for value in present), (label, table, column)


# ── 3. download ─────────────────────────────────────────────────────────────

async def test_the_download_streams_the_verified_bytes_and_refuses_alike(env: Any, exported: Any) -> None:
    from app.models.postgres import UserRole

    pid = exported.seed.project_id
    lead = await _member(env, pid, UserRole.QA_LEAD.value, "lead")
    viewer = await _member(env, pid, UserRole.VIEWER.value, "viewer")
    stranger = await _member(env, None, UserRole.QA_LEAD.value, "stranger")
    path = f"/api/v1/project-data-jobs/{exported.job.id}/download"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
        patch.setattr("app.db.storage.get_storage_provider", lambda: exported.store)
        async with _client(env, lead) as client:
            ok = await client.get(path)
        async with _client(env, viewer) as client:
            as_viewer = await client.get(path)
        async with _client(env, stranger) as client:
            as_stranger = await client.get(path)
    assert ok.status_code == 200
    assert hashlib.sha256(ok.content).hexdigest() == exported.job.archive_sha256
    assert int(ok.headers["content-length"]) == len(ok.content) == exported.job.bytes
    assert ok.content == exported.archive
    assert as_viewer.status_code == as_stranger.status_code == 404
    assert as_viewer.json() == as_stranger.json() == NOT_FOUND


# ── 4. the source is untouched ──────────────────────────────────────────────

async def test_the_source_is_untouched(exported: Any) -> None:
    from app.services.project_export_entities import EXPORTED_TABLES

    assert exported.after == exported.before
    touched = sorted(set(exported.writes) & set(EXPORTED_TABLES))
    assert touched == [], f"the export wrote to source tables: {touched}"
    assert "project_data_jobs" in exported.writes      # the job row is the export's own write


# ── 5. the stored archive verifies ──────────────────────────────────────────

async def test_the_downloaded_bytes_verify(env: Any, exported: Any) -> None:
    from app.services import compliance_signing
    from app.services.compliance_pack_verify import verify_archive_file

    async with env() as db:
        keys = await compliance_signing.public_keys(db)
    # The client path (the import's): the job row records "signed", not the raw signature, so the
    # server path's row-vs-file comparison does not apply; the chain and the Ed25519 check do.
    result = verify_archive_file(io.BytesIO(exported.archive), recorded_manifest_sha256=exported.job.manifest_sha256,
                                 public_keys=keys, pack_id=None)
    assert result["chain"] == "intact" and result["signature"] == "valid", result
    assert exported.job.signature == "signed"


# ── 6. the janitor (LAST: it expires the shared export) ─────────────────────

async def test_the_janitor_expires_the_export_and_fails_a_stale_job(env: Any, exported: Any) -> None:
    from app.models.postgres import ProjectDataJob, UserRole
    from app.worker import project_data_runner as runner

    pid = exported.seed.project_id
    now = exported.job.expires_at + timedelta(minutes=1)
    stale = ProjectDataJob(id=uuid.uuid4(), kind="export", status="running", project_id=pid, counts={}, attempts=1,
                           requested_at=now - timedelta(hours=2),
                           started_at=now - timedelta(minutes=41))
    async with env() as db:
        db.add(stale)
        await db.commit()
    key = exported.job.storage_key
    assert (exported.root / "compliance-packs" / key).exists()
    await runner.run_sweep(now=now, storage=exported.store, session_factory=env)
    async with env() as db:
        job = await db.get(ProjectDataJob, exported.job.id)
        failed = await db.get(ProjectDataJob, stale.id)
    assert job.status == "expired" and job.storage_key is None
    assert not (exported.root / "compliance-packs" / key).exists()
    assert failed.status == "failed" and failed.error_code == "interrupted"
    lead = await _member(env, pid, UserRole.QA_LEAD.value, "late")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
        async with _client(env, lead) as client:
            gone = await client.get(f"/api/v1/project-data-jobs/{exported.job.id}/download")
    assert gone.status_code == 410
    assert gone.json()["detail"] == {"code": "export_expired",
                                     "message": "This export was removed after 72 hours; start a new one."}
