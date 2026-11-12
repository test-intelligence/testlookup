"""W24 ADM-05 + W30 phase 2 — the project export against a real PostgreSQL, the REAL Mongo and the REAL local
object store (contract §6.3 G-B).

G-A's transfer seed builds ONE project with history (``history=True``: every catalog AND history entity, every
``MongoSpec`` collection, objects under a run's ``minio_prefix`` + the classes that must not travel); the REAL
runner (``run_export_job``) exports it twice on its read-only snapshot — body-less (phase 1) and with history.

W30 items (§6.3 G-B 1-7):
1. the body-less export is schema 1: its members and manifest keys are exactly W24's (no ``documents/``,
   ``objects/``, history entity or ``history`` block); every CATALOG entity ``counts[t] == SELECT count(*)``;
2. the history export (the seed's 30-day window — its out-of-window run is 45 days old) through the REAL runner
   on a 1 + 1 pool → ``succeeded``, schema 3 (W37 D-1); EVERY history entity ``counts[t]`` == the seed's in-window rows
   and ``> 0``; the IN_PROGRESS run, the non-terminal pipeline, the out-of-window run, the outbox row, the share
   link, the defect, the ingest-input and refused-prefix objects ABSENT (the skips counted in the manifest); per
   ``MongoSpec`` the documents' line count == the seed's in-window documents and ``> 0``; every object member's
   bytes == the stored object (SHA-256);
3. no seeded sentinel (plain / base64 / URL-encoded) in ANY decompressed member except the object binaries;
   ``[REDACTED]`` where each typed one was;
4. the source is untouched: per exported table (catalog + history) rows and ``max(updated_at)`` equal before /
   after, no INSERT / UPDATE / DELETE on a source table (engine listener), ``count_documents`` per collection
   and the object listing equal before / after;
5. ``verify_archive_file`` on each download → ``chain: intact``, ``signature: valid``;
6. the download streams with ``Content-Length`` (the history one named ``…-project-data-…``); a non-member and
   a viewer get W24's identical 404 body;
7. the estimate GET's counts equal the export's for the same window (rows, documents, objects ±0).
Then (LAST) the janitor expires the export and fails a stale job.

Fixture rules (§6.3): ``dispose_engine_for_loop()`` and ``reset_mongo_client()`` before the engines are built
and after they are disposed; the runner gets a 1 + 1 pool (the reference K3s install worker's); object storage =
``LocalStorageProvider`` in a temp dir; Redis is never bound (``get_redis`` is a no-op double for the router's
membership read); everything the seed created is removed by ``cleanup_transfer_seed`` (CI shares ONE Postgres
AND one Mongo database). Runs ONLY through ``pg_pytest_w30.sh b …`` on ``df_w30_b`` (Mongo ``df_w30_b``) at 0252.
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
    """Per exported table (catalog AND history): (rows of the project, max(updated_at) where the column exists)."""
    from app.models.postgres import Base
    from tests.integration.project_transfer_seed import _project_filter
    from app.services.project_export_entities import plan

    state: dict[str, tuple[int, Any]] = {}
    async with sessions() as db:
        for spec in plan(include_history=True):
            table = Base.metadata.tables[spec.table]
            where = _project_filter(spec, project_id)
            rows = (await db.execute(select(func.count()).select_from(table).where(where))).scalar_one()
            latest = None
            if "updated_at" in table.c:
                latest = (await db.execute(select(func.max(table.c.updated_at)).where(where))).scalar_one()
            state[spec.table] = (int(rows), latest)
    return state


async def _mongo_state(mongo: Any, seed: Any) -> dict[str, int]:
    """Per collection: the seed's documents still there (by every key value the seed wrote)."""
    out: dict[str, int] = {}
    for collection, keyed in seed.documents.items():
        from app.services.project_export_documents import MONGO_SPECS

        keys = sorted({key for key, _run in keyed})
        out[collection] = int(await mongo[collection].count_documents({MONGO_SPECS[collection].key: {"$in": keys}}))
    return out


async def _object_state(store: Any, seed: Any) -> list[tuple[str, int]]:
    roots = (f"{seed.project_id}/", f"uploads/{seed.project_id}/")
    listing = [entry for root in roots for entry in await store.list_objects(root)]
    return sorted((str(entry["Key"]), int(entry["Size"])) for entry in listing)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env() -> Any:
    from app.db import postgres as app_postgres
    from app.db.mongo import get_mongo_db, reset_mongo_client

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    reset_mongo_client()
    mongo = get_mongo_db()
    try:
        await mongo.command("ping")
    except Exception as exc:  # noqa: BLE001 - locally only; CI's postgres-integration job has the mongo service
        reset_mongo_client()
        pytest.skip(f"MongoDB is not reachable: {type(exc).__name__}")
    engine = create_async_engine(dsn, pool_size=5, max_overflow=2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sessions.engine = engine  # type: ignore[attr-defined]
    sessions.mongo = mongo  # type: ignore[attr-defined]
    try:
        yield sessions
    finally:
        await engine.dispose()
        reset_mongo_client()
        await app_postgres.dispose_engine_for_loop()


def _worker_pool() -> Any:
    """The reference K3s install worker's pool: 1 + overflow 1 (a fixture with a bigger pool hides a third connection)."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=1, pool_timeout=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sessions.engine = engine  # type: ignore[attr-defined]
    return sessions


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def world(env: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Seed ONE project WITH history (rows, documents, objects) once; remove everything at the end."""
    from app.db import storage as storage_module
    from tests.integration.project_transfer_seed import cleanup_transfer_seed, make_sentinels, seed_transfer_project

    seed = None
    root = tmp_path_factory.mktemp("objects")
    patch = pytest.MonkeyPatch()
    patch.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(root))
    patch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
    store = storage_module.LocalStorageProvider(default_bucket="artifacts")
    patch.setattr(storage_module, "get_storage_provider", lambda: store)
    try:
        sentinels = make_sentinels()
        async with env() as db:
            seed = await seed_transfer_project(db, sentinels=sentinels, history=True, mongo=env.mongo, storage=store)
        yield SimpleNamespace(seed=seed, sentinels=sentinels, store=store, root=root)
    finally:
        patch.undo()
        if seed is not None:          # CI shares ONE Postgres AND one Mongo database: leave nothing behind (#308)
            await cleanup_transfer_seed(env, seed, mongo=env.mongo, storage=store, user_ids=_CREATED_USERS)


async def _export(env: Any, world: Any, runner_sessions: Any, *, options: Any = None) -> Any:
    """ONE export of the seed through the REAL runner on ``runner_sessions``, with the source measured before /
    after and every statement of the runner's engine recorded."""
    from app.models.postgres import Project, User
    from app.services import project_data_job_service as jobs
    from app.worker import project_data_runner as runner

    seed, store = world.seed, world.store
    before = (await _source_state(env, seed.project_id), await _mongo_state(env.mongo, seed),
              await _object_state(store, seed))
    async with env() as db:
        project = await db.get(Project, seed.project_id)
        admin = await db.get(User, seed.admin_id)
        job = await jobs.create_export_job(db, project=project, actor=admin, options=options)
        await db.commit()
    writes: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        match = _WRITE.match(statement)
        if match:
            writes.append(match.group(1).lower())

    event.listen(runner_sessions.engine.sync_engine, "before_cursor_execute", _record)
    try:
        status = await runner.run_export_job(job.id, storage=store, session_factory=runner_sessions, mongo=env.mongo)
    finally:
        event.remove(runner_sessions.engine.sync_engine, "before_cursor_execute", _record)
    after = (await _source_state(env, seed.project_id), await _mongo_state(env.mongo, seed),
             await _object_state(store, seed))
    async with env() as db:
        row = await db.get(jobs.ProjectDataJob, job.id)
    chunks = [chunk async for chunk in store.stream_object(row.storage_key, bucket="compliance-packs")] \
        if row.storage_key else []
    return SimpleNamespace(seed=seed, sentinels=world.sentinels, before=before[0], after=after[0],
                           mongo_before=before[1], mongo_after=after[1], objects_before=before[2],
                           objects_after=after[2], job=row, status=status, archive=b"".join(chunks), writes=writes,
                           store=store, root=world.root, source_counts=seed.counts)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def exported(env: Any, world: Any) -> Any:
    """The body-less (phase-1) export of the history seed, through the REAL runner."""
    return await _export(env, world, env)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def history_export(env: Any, world: Any) -> Any:
    """The export WITH history (30 days) through the REAL runner on the reference K3s install worker's 1 + 1 pool."""
    from app.models.schemas import ProjectDataOptions

    small = _worker_pool()
    try:
        yield await _export(env, world, small, options=ProjectDataOptions(
            include_history=True, history_days=30, max_runs=2000))
    finally:
        await small.engine.dispose()


def _members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


# Every person a test below creates; the module fixture's teardown removes them with the seed (CI shares ONE database).
_CREATED_USERS: list[uuid.UUID] = []


async def _member(env: Any, project_id: uuid.UUID, role: str, label: str) -> Any:
    from app.models.postgres import ProjectMember, User

    username = f"xfer-{label}-{uuid.uuid4().hex[:8]}g"
    user = User(id=uuid.uuid4(), username=username, email=f"{username}@xfer.example.test",
                full_name=label.title(), hashed_password="!", role=role, is_active=True)
    _CREATED_USERS.append(user.id)
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

#: The manifest keys of a W24 (phase 1) project export — a body-less export still writes exactly these.
_W24_MANIFEST_KEYS = {"format_version", "generated_at", "project_id", "files", "scope", "format", "schema_version",
                      "source", "project", "entities", "directory", "excluded"}


async def test_a_body_less_export_of_a_history_seed_is_phase_1(exported: Any) -> None:
    """§6.3 G-B 1: schema 1, phase 1's 29 entities with the source counts, W24's members and manifest keys —
    no ``documents/``, ``objects/``, history entity or ``history`` block."""
    from app.services.project_export_entities import CATALOG_TABLES, EXCLUDED, HISTORY_TABLES, NOT_PROJECT_SCOPED

    assert exported.status == "succeeded" and exported.job.status == "succeeded", exported.job.error
    counts = exported.job.counts
    assert set(counts) == set(CATALOG_TABLES)
    empty = sorted(t for t in CATALOG_TABLES if counts[t] <= 0)
    assert empty == [], f"declared but not emitted: {empty}"
    assert counts == exported.source_counts
    manifest = json.loads(_members(exported.archive)["manifest.json"])
    assert set(manifest) == _W24_MANIFEST_KEYS and manifest["schema_version"] == 1
    assert {t: entry["rows"] for t, entry in manifest["entities"].items()} == counts
    assert manifest["scope"] == "project" and manifest["project"]["slug"] == exported.seed.project_slug
    names = list(_members(exported.archive))
    allowed = re.compile(r"^(entities/[a-z_]{1,63}/part-\d{4}\.jsonl|directory/users\.jsonl|README\.txt|"
                         r"manifest\.json|signature\.json)$")
    assert all(allowed.match(n) for n in names), names
    tables = {n.split("/")[1] for n in names if n.startswith("entities/")}
    assert tables == set(CATALOG_TABLES)
    assert not tables & (set(EXCLUDED) | set(NOT_PROJECT_SCOPED) | set(HISTORY_TABLES))
    assert exported.job.options == {} and exported.store is not None


# ── 2. secrets ──────────────────────────────────────────────────────────────

async def test_no_sentinel_in_any_member_in_any_form_and_redacted_where_typed(exported: Any) -> None:
    _assert_no_sentinel(exported, catalog_only=True)


async def test_no_sentinel_in_any_history_member_in_any_form_and_redacted_where_typed(history_export: Any) -> None:
    _assert_no_sentinel(history_export, catalog_only=False)


def _assert_no_sentinel(exported: Any, *, catalog_only: bool) -> None:
    """§6.3 G-B 3: EVERY decompressed member (documents and the object index included; the object BINARIES are
    copied unscanned by design — R-3) is free of every seeded sentinel in every form."""
    from app.services.project_export_entities import CATALOG_TABLES

    assert exported.status == "succeeded", (exported.job.error_code, exported.job.error)
    members = _members(exported.archive)
    secrets = dict(exported.sentinels.values())
    secrets["api_key_plaintext"] = exported.seed.api_key_plaintext
    for name, data in members.items():
        if re.match(r"^objects/[0-9a-f-]{36}/\d{6}\.bin$", name):
            continue
        body = data.decode("utf-8")
        for label, value in secrets.items():
            for form in _forms(value):
                assert form not in body, f"{label} leaked into {name}"
    from app.services.project_export_scrub import URL_COLUMNS

    for label, (table, column) in exported.seed.placements.items():
        if catalog_only and table not in CATALOG_TABLES:
            continue                  # a history carrier is not in a catalog export at all
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
    if not catalog_only:              # the history carriers: a failed case, a stage result, two documents
        for prefix in ("entities/test_cases/", "entities/agent_stage_results/", "documents/raw_allure_json/",
                       "documents/rest_api_payloads/"):
            text = "".join(data.decode() for name, data in members.items() if name.startswith(prefix))
            assert text and "[REDACTED]" in text, prefix
        runs = [json.loads(line) for name, data in members.items() if name.startswith("entities/test_runs/")
                for line in data.decode().splitlines()]
        urls = [run["ci_run_url"] for run in runs if run.get("ci_run_url")]
        assert urls and all("@" not in urllib.parse.urlsplit(url).netloc for url in urls)   # S-8 userinfo removed


# ── W30 2: the history export ───────────────────────────────────────────────

def _lines_of(members: dict[str, bytes], prefix: str) -> list[str]:
    return [line for name, data in sorted(members.items()) if name.startswith(prefix)
            for line in data.decode("utf-8").splitlines()]


async def test_a_history_export_carries_every_history_entity_of_its_window(history_export: Any) -> None:
    from app.services.project_export_entities import CATALOG_TABLES, HISTORY_TABLES
    from tests.integration.project_transfer_seed import expected_history_counts

    job, seed = history_export.job, history_export.seed
    assert history_export.status == "succeeded", (job.error_code, job.error)
    members = _members(history_export.archive)
    manifest = json.loads(members["manifest.json"])
    assert manifest["schema_version"] == 3 and set(manifest) == _W24_MANIFEST_KEYS | {"history"}   # W37 D-1
    assert job.options == {"include_history": True, "history_days": 30, "max_runs": 2000}
    expected = expected_history_counts(seed, history_days=30)
    thin = sorted(t for t in HISTORY_TABLES if job.counts.get(t, 0) <= 0)
    assert thin == [], f"declared but not emitted: {thin}"
    assert {t: job.counts[t] for t in HISTORY_TABLES} == expected
    assert {t: job.counts[t] for t in CATALOG_TABLES} == seed.counts
    assert {t: entry["rows"] for t, entry in manifest["entities"].items()} == {
        t: job.counts[t] for t in (*CATALOG_TABLES, *HISTORY_TABLES)}
    allowed = re.compile(r"^(entities/[a-z_]{1,63}/part-\d{4}\.jsonl|documents/[a-z_]{1,63}/part-\d{4}\.jsonl|"
                         r"objects/index/part-\d{4}\.jsonl|objects/[0-9a-f-]{36}/\d{6}\.bin|directory/users\.jsonl|"
                         r"README\.txt|manifest\.json|signature\.json)$")
    assert all(allowed.match(n) for n in members), [n for n in members if not allowed.match(n)]
    history = manifest["history"]
    assert history["window_days"] == 30 and history["runs"] == job.counts["test_runs"]
    assert history["runs_in_progress_skipped"] >= 1
    skipped = {(entry["entity"], entry["reason"]) for entry in history["skipped"]}
    assert ("agent_pipeline_runs", "pipeline_not_terminal") in skipped


async def test_what_must_not_travel_is_absent_from_a_history_export(history_export: Any) -> None:
    """The IN_PROGRESS run, the non-terminal pipeline (+ its stage results), the out-of-window run, the outbox
    row, the share link, the live session, the defect, the hold — none of their ids is in any member."""
    members = _members(history_export.archive)
    text = "\n".join(data.decode("utf-8") for name, data in members.items() if not name.endswith(".bin"))
    seed = history_export.seed
    # (an id LIST naming a run outside the window — release_gate_decisions.run_ids — is narrowed by the export too)
    leaked = sorted(f"{table}:{row_id} in " + ",".join(name for name, data in members.items()
                                                       if not name.endswith(".bin") and str(row_id) in data.decode())
                    for table, ids in seed.history_excluded.items() for row_id in ids if str(row_id) in text)
    assert leaked == [], f"rows that must not travel are in the archive: {leaked}"
    manifest = json.loads(members["manifest.json"])
    skipped = {(entry["entity"], entry["reason"]): entry["count"] for entry in manifest["history"]["skipped"]}
    assert skipped.get(("release_gate_decisions", "run_not_exported"), 0) >= 1
    assert seed.live_slug and seed.live_slug not in "\n".join(_lines_of(members, "entities/"))


async def test_every_collection_travels_with_its_window_documents(history_export: Any) -> None:
    from app.services.project_export_documents import MONGO_COLLECTIONS
    from tests.integration.project_transfer_seed import expected_document_counts

    members = _members(history_export.archive)
    manifest = json.loads(members["manifest.json"])
    expected = expected_document_counts(history_export.seed, history_days=30)
    for spec in MONGO_COLLECTIONS:
        lines = _lines_of(members, f"documents/{spec.collection}/")
        assert len(lines) == expected[spec.collection] > 0, spec.collection
        assert manifest["history"]["documents"][spec.collection]["docs"] == len(lines)
        assert history_export.job.counts[f"documents.{spec.collection}"] == len(lines)
        assert all('"_id"' not in line for line in lines), spec.collection


async def test_every_object_member_is_the_stored_object_and_nothing_else_travels(
    env: Any, history_export: Any,
) -> None:
    from app.models.postgres import TestRun

    members = _members(history_export.archive)
    seed, store = history_export.seed, history_export.store
    index = [json.loads(line) for line in _lines_of(members, "objects/index/")]
    exported_keys = set()
    async with env() as db:
        prefixes = dict((await db.execute(select(TestRun.id, TestRun.minio_prefix)
                                          .where(TestRun.project_id == seed.project_id))).all())
    for entry in index:
        key = prefixes[uuid.UUID(entry["run_id"])] + entry["suffix"]
        stored = b"".join([chunk async for chunk in store.stream_object(key)])
        assert hashlib.sha256(members[entry["member"]]).hexdigest() == entry["sha256"] \
            == hashlib.sha256(stored).hexdigest(), key
        assert entry["bytes"] == len(stored) and entry["root"] == "minio_prefix"
        exported_keys.add(key)
    travelled = {key for key, klass in seed.objects.items() if klass == "minio_prefix"
                 and key in exported_keys}
    assert len(travelled) >= 2
    assert not {key for key, klass in seed.objects.items() if klass != "minio_prefix"} & exported_keys
    assert history_export.job.counts["objects"] == len(index)


async def test_a_history_export_leaves_the_source_untouched_in_every_store(history_export: Any) -> None:
    """§6.3 G-B 4: rows + max(updated_at) per table (catalog AND history), documents per collection, the object
    listing — equal before / after; the runner's engine wrote no source table."""
    from app.services.project_export_entities import EXPORTED_TABLES

    assert history_export.after == history_export.before
    assert history_export.mongo_after == history_export.mongo_before and sum(history_export.mongo_before.values())
    assert history_export.objects_after == history_export.objects_before and history_export.objects_before
    touched = sorted(set(history_export.writes) & set(EXPORTED_TABLES))
    assert touched == [], f"the history export wrote to source tables: {touched}"
    assert set(history_export.writes) <= {"project_data_jobs", "project_activity_events", "compliance_signing_keys",
                                          "settings_audit_logs"}, set(history_export.writes)


async def test_the_history_download_verifies_and_is_named_project_data(env: Any, history_export: Any) -> None:
    """§6.3 G-B 5 + 6: the stored history archive verifies offline; the download streams it with its length and
    the history file name; a viewer and a non-member read W24's ONE 404."""
    from app.models.postgres import UserRole
    from app.services import compliance_signing
    from app.services.compliance_pack_verify import verify_archive_file

    async with env() as db:
        keys = await compliance_signing.public_keys(db)
    result = verify_archive_file(io.BytesIO(history_export.archive), public_keys=keys, pack_id=None,
                                 recorded_manifest_sha256=history_export.job.manifest_sha256)
    assert result["chain"] == "intact" and result["signature"] == "valid", result
    pid = history_export.seed.project_id
    lead = await _member(env, pid, UserRole.QA_LEAD.value, "hlead")
    viewer = await _member(env, pid, UserRole.VIEWER.value, "hviewer")
    stranger = await _member(env, None, UserRole.QA_LEAD.value, "hstranger")
    path = f"/api/v1/project-data-jobs/{history_export.job.id}/download"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
        patch.setattr("app.db.storage.get_storage_provider", lambda: history_export.store)
        async with _client(env, lead) as client:
            ok = await client.get(path)
        async with _client(env, viewer) as client:
            as_viewer = await client.get(path)
        async with _client(env, stranger) as client:
            as_stranger = await client.get(path)
    assert ok.status_code == 200 and ok.content == history_export.archive
    assert int(ok.headers["content-length"]) == len(ok.content) == history_export.job.bytes
    assert "-project-data-" in ok.headers["content-disposition"]
    assert as_viewer.json() == as_stranger.json() == NOT_FOUND


async def test_the_estimate_counts_what_the_export_wrote(env: Any, history_export: Any) -> None:
    """§6.3 G-B 7: the estimate GET (the router, a QA-lead member) for the export's window equals its counts
    (runs, test cases, documents per collection, objects); a wider window counts the older run too. A GET:
    nothing written."""
    from app.models.postgres import UserRole

    job = history_export.job
    manifest = json.loads(_members(history_export.archive)["manifest.json"])
    lead = await _member(env, history_export.seed.project_id, UserRole.QA_LEAD.value, "elead")
    path = f"/api/v1/projects/{history_export.seed.project_id}/export-estimate"
    writes: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        if _WRITE.match(statement):
            writes.append(statement)

    event.listen(env.engine.sync_engine, "before_cursor_execute", _record)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
            patch.setattr("app.db.storage.get_storage_provider", lambda: history_export.store)
            async with _client(env, lead) as client:
                same = await client.get(path, params={"include_history": "true", "history_days": 30})
                wider = await client.get(path, params={"include_history": "true", "history_days": 90})
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", _record)
    assert writes == [], writes
    assert same.status_code == 200, same.text
    body = same.json()
    assert body["measured"] == {"postgres": True, "documents": True, "objects": True}
    assert body["runs"] == job.counts["test_runs"] == manifest["history"]["runs"]
    assert body["test_cases"] == job.counts["test_cases"]
    assert body["documents"] == {name: entry["docs"] for name, entry in manifest["history"]["documents"].items()}
    assert body["objects"] == job.counts["objects"] == manifest["history"]["objects"]["count"]
    assert body["object_bytes"] == manifest["history"]["objects"]["bytes"]
    assert body["over_limit"] is False and body["estimated_bytes"] > 0
    assert wider.json()["runs"] == body["runs"] + 1        # the 45-day-old run is inside 90 days


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


async def test_the_export_runs_inside_a_two_connection_worker_pool(exported: Any) -> None:
    """The reference K3s install's Celery worker pool is size 1 + overflow 1. Its first live export held the snapshot AND the
    signing session through the write, needed a third connection to record the storage key, and failed
    ``export_write`` (TimeoutError) after two 30 s pool timeouts. The same export through a 1 + 1 pool succeeds."""
    from app.models.postgres import Project, User
    from app.services import project_data_job_service as jobs
    from app.worker import project_data_runner as runner

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=1, pool_timeout=5)
    small = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with small() as db:
            project = await db.get(Project, exported.seed.project_id)
            admin = await db.get(User, exported.seed.admin_id)
            job = await jobs.create_export_job(db, project=project, actor=admin)
            await db.commit()
        status = await runner.run_export_job(job.id, storage=exported.store, session_factory=small)
        async with small() as db:
            row = await db.get(jobs.ProjectDataJob, job.id)
        assert status == "succeeded", (row.error_code, row.error)
        assert row.status == "succeeded" and row.storage_key and row.archive_sha256
    finally:
        await engine.dispose()
