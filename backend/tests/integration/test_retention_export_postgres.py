"""W22b ACT-03 — export before delete against a real PostgreSQL and the REAL local object store (§2.1, §6.4).

The chain is driven at the service level in the worker's order (the task glue
is covered by ``tests/test_deletion_export_worker.py``): the criteria preview's
estimate + freeze, execute's claim with an export, then ``start_frozen_set`` ->
``get_export_plan`` -> ``run_export`` -> ``stage_export_verified`` ->
``mark_collected`` -> ``acknowledge_collection`` -> ``start_frozen_set`` ->
``perform_run_deletion`` per run. Proven here, on real rows and real files:

* a ``collect`` export leaves the job ``awaiting_collection``, the archive file
  exists and ``verify_archive_file`` says ``verified``, and EVERY run still exists;
* the acknowledgement refuses a wrong digest (409) and queues on the right one;
  only then are the runs deleted, ``bytes_reclaimed`` equals the seeded object
  bytes, and the archive file is gone;
* two exports of the same runs are member-for-member identical except the
  manifest's timestamp and the signature; a seeded credential is absent from the ZIP;
* a job never acknowledged expires in the janitor at ``now + 73 h`` and deletes nothing;
* a storage double that corrupts the re-read fails the export and deletes nothing;
* a run held between preview and execute is not in ``runs.json``, still exists,
  and the job's ``holds_honoured`` names the hold;
* another project's job id is the unknown-id refusal.

Fixture rule (§6.4): ``dispose_engine_for_loop()`` before the engine is built
and after it is disposed; an in-memory Mongo double and a ``LocalStorageProvider``
rooted at ``tmp_path`` are injected (no ``get_mongo_db()`` / ``get_storage_provider()``
binding); nothing here bumps the analytics epoch, so Redis is never bound and
not closed; Mongo is never closed. Every window comes from a ``now`` built in
the test; every row lives under a fresh project. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` (= the app's ``DATABASE_URL``) migrated to head (0242).
"""
from __future__ import annotations

import json
import os
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

SENTINEL = "W22bSentinelValue"
SECRET = f"login failed with password={SENTINEL} at the gateway"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()

    async def to_list(self, length=None):
        return list(self._docs)


class _MemCollection:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    @staticmethod
    def _match(doc: dict, filt: dict) -> bool:
        for field, cond in filt.items():
            if isinstance(cond, dict) and "$in" in cond:
                if doc.get(field) not in cond["$in"]:
                    return False
            elif doc.get(field) != cond:
                return False
        return True

    def find(self, filt, projection=None, sort=None):
        return _Cursor(sorted((d for d in self.docs if self._match(d, filt)), key=lambda d: str(d.get("_id"))))

    async def find_one(self, filt):
        return next((d for d in self.docs if self._match(d, filt)), None)

    async def count_documents(self, filt):
        return sum(1 for d in self.docs if self._match(d, filt))

    async def delete_many(self, filt):
        before = len(self.docs)
        self.docs[:] = [d for d in self.docs if not self._match(d, filt)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class _MemMongo:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def __getitem__(self, name):
        return _MemCollection(self.store.setdefault(name, []))

    async def command(self, name, collection):
        return {"avgObjSize": 200}


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """The REAL local provider on tmp_path (true streaming, true re-read) and the Mongo double."""
    from app.db import storage as storage_module

    monkeypatch.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    return SimpleNamespace(mongo=_MemMongo(), storage=storage_module.LocalStorageProvider(default_bucket="artifacts"),
                           root=tmp_path)


@pytest.fixture(autouse=True)
def _derived_stores(monkeypatch):
    """Self-wired derived stores the per-run executor could reach: stubbed, never bound."""
    from app.services import agent_memory_service, analysis_cache_retention, semantic_search

    async def _caches(project_id, *, cutoff, execute):
        return {"redis": None, "semantic": None}

    async def _search(project_id, *, execute):
        return None

    async def _vectors(project_id, ids):
        return len(ids)

    monkeypatch.setattr(analysis_cache_retention, "purge_project_analysis_caches", _caches)
    monkeypatch.setattr(semantic_search, "purge_project_documents", _search)
    monkeypatch.setattr(agent_memory_service, "purge_memory_vectors", _vectors)


# ── Seeding (every timestamp from the test's own ``now``) ────────────────────


async def _seed(sessions, stores, now, *, runs=6) -> SimpleNamespace:
    """A fresh project: ``runs`` FAILED runs, two cases each (one carrying the
    credential sentinel), a JSON attachment (sentinel inside) and a PNG each."""
    from app.models.postgres import Project, TestCase, TestRun

    pid = uuid.uuid4()
    run_ids: list[uuid.UUID] = []
    async with sessions() as db:
        db.add(Project(id=pid, name=f"gb-export-{pid.hex[:10]}", slug=f"gb-export-{pid.hex[:10]}", is_active=True))
        await db.flush()
        for i in range(runs):
            rid = uuid.uuid4()
            run = TestRun(id=rid, project_id=pid, build_number=f"gbx-{rid.hex[:8]}", status="FAILED",
                          ingestion_source="upload", branch="main", total_tests=2, passed_tests=1, failed_tests=1)
            run.created_at = now - timedelta(days=40 + i)
            db.add(run)
            await db.flush()
            db.add(TestCase(id=uuid.uuid4(), test_run_id=rid, test_name=f"test_ok_{i}",
                            test_fingerprint=f"fpok{rid.hex[:10]}", status="PASSED"))
            db.add(TestCase(id=uuid.uuid4(), test_run_id=rid, test_name=f"test_login_{i}",
                            test_fingerprint=f"fpko{rid.hex[:10]}", status="FAILED", error_message=SECRET))
            run_ids.append(rid)
        await db.commit()
    per_run: dict[uuid.UUID, int] = {}
    for rid in run_ids:
        report = json.dumps({"password": SENTINEL, "ok": True}).encode()
        shot = b"\x89PNG" + bytes(300)
        await stores.storage.put_object(f"uploads/{pid}/{rid}/report.json", report, bucket=None)
        await stores.storage.put_object(f"uploads/{pid}/{rid}/shot.png", shot, bucket=None)
        per_run[rid] = len(report) + len(shot)
        stores.mongo.store.setdefault("raw_allure_json", []).append(
            {"_id": f"a-{rid}", "test_run_id": str(rid), "message": SECRET}
        )
    return SimpleNamespace(pid=pid, run_ids=run_ids, per_run=per_run)


async def _scalar(sessions, sql: str, **params) -> Any:
    async with sessions() as db:
        return (await db.execute(text(sql), params)).scalar()


async def _runs_left(sessions, run_ids) -> int:
    return int(await _scalar(sessions, "SELECT count(*) FROM test_runs WHERE id = ANY(:ids)", ids=list(run_ids)))


# ── The chain, at the service level, in the worker's order ──────────────────


async def _preview_and_execute(sessions, stores, seed, run_ids, choice) -> uuid.UUID:
    from app.core.config import settings
    from app.models.schemas import ExportChoice
    from app.services import deletion_job_service as jobs
    from app.services import retention_export_service as svc

    async with sessions() as db:
        estimate = await svc.estimate_export(db, project_id=seed.pid, run_ids=run_ids, mongo=stores.mongo,
                                             storage=stores.storage, max_bytes=settings.RETENTION_EXPORT_MAX_BYTES)
        job_id = jobs.stage_frozen_candidate_set(db, project_id=seed.pid, run_ids=list(run_ids),
                                                 criteria={"run_ids": [str(r) for r in run_ids]},
                                                 export_estimates=estimate)
        await db.commit()
    assert estimate["exportable"] and estimate["reclaim_object_bytes"] == sum(seed.per_run[r] for r in run_ids)
    async with sessions() as db:
        export = ExportChoice(**choice)
        svc.validate_vocabulary(export)
        claimed = await jobs.claim_frozen_set(db, job_id=job_id, project_id=seed.pid, export=export)
        job = await jobs.get_job(db, job_id)
        svc.validate_choice(export, estimate=job.export_meta["estimates"], run_count=len(claimed))
        await db.commit()
    return job_id


async def _export_phase(sessions, stores, seed, job_id, now, *, storage=None):
    """start -> plan -> run_export (own session, commit) -> stage_export_verified (own session, commit)."""
    from app.services import deletion_job_service as jobs
    from app.services import retention_export_service as svc

    async with sessions() as db:
        run_ids = await jobs.start_frozen_set(db, job_id=job_id, project_id=seed.pid)
        await db.commit()
        plan = await jobs.get_export_plan(db, job_id)

    async def _on_written(key):
        async with sessions() as wdb:
            await jobs.stage_export_written(wdb, job_id=job_id, location=key)
            await wdb.commit()

    async with sessions() as db:
        outcome = await svc.run_export(db, job_id=job_id, project_id=seed.pid, run_ids=run_ids,
                                       choice={"tier": plan.tier, "target": plan.target}, mongo=stores.mongo,
                                       storage=storage or stores.storage, now=now, on_written=_on_written)
        await db.commit()
    async with sessions() as db:
        assert await jobs.stage_export_verified(db, job_id=job_id, outcome=outcome, target=plan.target, ttl_hours=72,
                                                now=now, meta={**plan.meta, **outcome.export_meta()})
        await db.commit()
    return plan, outcome


async def _delete_loop(sessions, stores, seed, job_id, run_ids, *, holds=(), held_ids=(), meta=None):
    """The per-run loop (lock -> execution_blockers -> perform_run_deletion -> commit), then close_job
    with bytes_reclaimed from ONE listing taken before the loop."""
    from sqlalchemy import select

    from app.models.postgres import TestRun
    from app.services import deletion_job_service as jobs
    from app.services import retention_export_service as svc
    from app.services import retention_hold_service, run_deletion_service

    listing = await svc.list_project_objects(stores.storage, project_id=seed.pid)
    deleted, reclaimed = [], 0
    for rid in run_ids:
        async with sessions() as db:
            run = (await db.execute(select(TestRun).where(TestRun.id == rid).with_for_update())).scalar_one_or_none()
            if run is None or await run_deletion_service.execution_blockers(db, run=run, mongo=stores.mongo):
                continue
            nbytes = svc.bytes_under_run(listing, project_id=seed.pid, run_id=rid, minio_prefix=run.minio_prefix)
            await run_deletion_service.perform_run_deletion(
                db, run=run, mongo=stores.mongo, storage=stores.storage, search_index_documents=0,
                reason="criteria deletion", deletion_job_id=job_id,
            )
            await db.commit()
        deleted.append(rid)
        reclaimed += nbytes
    honoured = retention_hold_service.holds_honoured_record(list(holds), run_ids_kept=list(held_ids))
    extra = {"holds_honoured": honoured} if honoured is not None else {}
    assert await jobs.close_job(job_id, status=jobs.outcome_status(requested=len(run_ids), deleted=len(deleted)),
                                expected_status=jobs.RUNNING, bytes_reclaimed=reclaimed,
                                resolved_run_ids=[str(r) for r in deleted], export_meta=meta, **extra)
    return deleted, reclaimed


def _members(path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


async def _verified(sessions, path, outcome, job_id) -> dict:
    from app.services import compliance_signing
    from app.services.compliance_pack_verify import verify_archive_file

    async with sessions() as db:
        keys = await compliance_signing.public_keys(db)
    with open(path, "rb") as handle:
        return verify_archive_file(handle, recorded_manifest_sha256=outcome.manifest_sha256, public_keys=keys,
                                   pack_id=job_id, record_signature=outcome.signature,
                                   record_signing_key_id=outcome.signing_key_id)


# ── The tests ────────────────────────────────────────────────────────────────


async def test_collect_deletes_nothing_until_the_exact_digest_is_acknowledged(env, stores):
    from app.services import deletion_job_service as jobs

    now = datetime.now(timezone.utc)
    seed = await _seed(env, stores, now)
    job_id = await _preview_and_execute(env, stores, seed, seed.run_ids, {"tier": "evidence", "target": "collect"})
    plan, outcome = await _export_phase(env, stores, seed, job_id, now)

    status = await _scalar(env, "SELECT status FROM deletion_jobs WHERE id = :j", j=job_id)
    expires = await _scalar(env, "SELECT collect_expires_at FROM deletion_jobs WHERE id = :j", j=job_id)
    assert status == "awaiting_collection" and expires - now == timedelta(hours=72)
    path = stores.root / "compliance-packs" / outcome.location
    assert path.is_file() and path.stat().st_size == outcome.bytes
    assert (await _verified(env, path, outcome, job_id))["result"] == "verified"
    assert await _runs_left(env, seed.run_ids) == 6, "nothing is deleted before the acknowledgement"
    members = _members(path)
    # The builder's last member is the manifest; signature.json is appended after it (outside the chain).
    assert [name for name in members if name != "signature.json"][-1] == "manifest.json"
    assert all(SENTINEL.encode() not in data for data in members.values()), "the credential never enters the ZIP"
    assert json.loads(members["deletion.json"])["project_name"] == f"gb-export-{seed.pid.hex[:10]}"

    async with env() as db:      # another project's job id: the same refusal as an unknown id
        for project_id, wanted in ((uuid.uuid4(), job_id), (seed.pid, uuid.uuid4())):
            with pytest.raises(jobs.CollectRefused) as caught:
                await jobs.mark_collected(db, job_id=wanted, project_id=project_id, now=now)
            assert (caught.value.code, caught.value.status_code) == ("job_not_found", 404)
        await db.rollback()
    async with env() as db:
        await jobs.mark_collected(db, job_id=job_id, project_id=seed.pid, now=now)
        await db.commit()
    async with env() as db:
        with pytest.raises(jobs.CollectRefused) as caught:
            await jobs.acknowledge_collection(db, job_id=job_id, project_id=seed.pid, manifest_sha256="ef" * 32,
                                              actor_id=None, now=now)
        assert (caught.value.code, caught.value.status_code) == ("manifest_mismatch", 409)
        await db.rollback()
    async with env() as db:
        job = await jobs.acknowledge_collection(db, job_id=job_id, project_id=seed.pid,
                                                manifest_sha256=outcome.manifest_sha256.upper(), actor_id=None, now=now)
        assert job.status == "queued"
        await db.commit()

    async with env() as db:
        run_ids = await jobs.start_frozen_set(db, job_id=job_id, project_id=seed.pid)
        await db.commit()
        second = await jobs.get_export_plan(db, job_id)
    assert second.verified and second.acknowledged and second.location == outcome.location
    deleted, reclaimed = await _delete_loop(env, stores, seed, job_id, run_ids, meta=second.meta)
    from app.services import retention_export_service as svc
    assert await svc.remove_archive(stores.storage, key=second.location)
    async with env() as db:
        await jobs.stage_clear_export_location(db, job_id=job_id)
        await db.commit()

    assert await _runs_left(env, seed.run_ids) == 0 and len(deleted) == 6
    assert reclaimed == sum(seed.per_run.values())
    assert await _scalar(env, "SELECT bytes_reclaimed FROM deletion_jobs WHERE id = :j", j=job_id) == reclaimed
    assert await _scalar(env, "SELECT export_location FROM deletion_jobs WHERE id = :j", j=job_id) is None
    assert not path.exists(), "a collected archive leaves once its deletion finished"


async def test_two_exports_of_the_same_runs_differ_only_in_the_timestamp(env, stores):
    from app.services import retention_export_service as svc

    now = datetime.now(timezone.utc)
    seed = await _seed(env, stores, now, runs=3)
    job_id = await _preview_and_execute(env, stores, seed, seed.run_ids, {"tier": "full", "target": "retain"})
    first_at, second_at = now, now + timedelta(minutes=7)
    archives = []
    for at in (first_at, second_at):
        async with env() as db:
            outcome = await svc.run_export(db, job_id=job_id, project_id=seed.pid, run_ids=seed.run_ids,
                                           choice={"tier": "full", "target": "retain"}, mongo=stores.mongo,
                                           storage=stores.storage, now=at)
            await db.commit()
        archives.append(_members(stores.root / "compliance-packs" / outcome.location))
    one, two = archives
    assert one.keys() == two.keys()
    for name in one:
        if name not in ("manifest.json", "signature.json"):
            assert one[name] == two[name], name
    m1, m2 = json.loads(one["manifest.json"]), json.loads(two["manifest.json"])
    assert m1.pop("generated_at") != m2.pop("generated_at")
    assert m1 == m2
    listed = json.loads(one["objects_not_archived.json"])
    assert {entry["reason"] for entry in listed} == {"binary"} and len(listed) == 3, "the PNGs are listed, not copied"
    assert all(SENTINEL.encode() not in data for data in one.values())


async def test_an_unacknowledged_job_expires_and_deletes_nothing(env, stores):
    from app.services import retention_export_service as svc
    from app.worker.tasks import _sweep_deletion_exports

    now = datetime.now(timezone.utc)
    seed = await _seed(env, stores, now, runs=2)
    job_id = await _preview_and_execute(env, stores, seed, seed.run_ids, {"tier": "summary", "target": "collect"})
    _plan, outcome = await _export_phase(env, stores, seed, job_id, now)
    path = stores.root / "compliance-packs" / outcome.location
    assert path.is_file()

    summary = await _sweep_deletion_exports(now=now + timedelta(hours=73), storage=stores.storage)

    assert summary["expired"] >= 1 and summary["removed"] >= 1
    assert await _scalar(env, "SELECT status FROM deletion_jobs WHERE id = :j", j=job_id) == "expired"
    error = await _scalar(env, "SELECT error FROM deletion_jobs WHERE id = :j", j=job_id)
    assert error == "Not collected within 72 hours — nothing was deleted; the archive was removed."
    assert await _runs_left(env, seed.run_ids) == 2
    assert not path.exists()
    assert await _scalar(env, "SELECT export_location FROM deletion_jobs WHERE id = :j", j=job_id) is None
    audits = await _scalar(env, "SELECT count(*) FROM settings_audit_log WHERE setting_key = :k AND action IN "
                                "('export_expired', 'archive_removed')", k=f"deletion_export:{seed.pid}")
    assert audits == 2
    assert await svc.remove_archive(stores.storage, key=outcome.location), "a second removal is a no-op"


async def test_a_corrupted_re_read_fails_the_export_and_deletes_nothing(env, stores):
    from app.services import deletion_job_service as jobs
    from app.services import retention_export_service as svc

    class _Corrupting:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        async def stream_object(self, key, bucket=None):
            first = True
            async for chunk in self.inner.stream_object(key, bucket):
                if first and chunk:
                    chunk = bytes([chunk[0] ^ 0xFF]) + chunk[1:]
                    first = False
                yield chunk

    now = datetime.now(timezone.utc)
    seed = await _seed(env, stores, now, runs=2)
    job_id = await _preview_and_execute(env, stores, seed, seed.run_ids, {"tier": "summary", "target": "retain"})
    with pytest.raises(svc.ExportFailed) as caught:
        await _export_phase(env, stores, seed, job_id, now, storage=_Corrupting(stores.storage))
    failed = caught.value
    assert failed.code == "unverified" and failed.params == {"reason": "digest mismatch"}
    assert await jobs.close_job(job_id, status=jobs.FAILED, expected_status=jobs.RUNNING, error=failed.message)
    assert await _scalar(env, "SELECT status FROM deletion_jobs WHERE id = :j", j=job_id) == "failed"
    assert await _runs_left(env, seed.run_ids) == 2
    assert not list((stores.root / "compliance-packs").rglob("*.zip")), "the unproven object was removed"


async def test_a_run_held_after_the_preview_is_not_archived_and_not_deleted(env, stores):
    now = datetime.now(timezone.utc)
    seed = await _seed(env, stores, now, runs=3)
    held = seed.run_ids[1]
    job_id = await _preview_and_execute(env, stores, seed, seed.run_ids, {"tier": "evidence", "target": "retain"})
    hold_id = uuid.uuid4()
    async with env() as db:
        await db.execute(
            text("INSERT INTO retention_holds (id, project_id, entity_type, entity_id, entity_label, reason, placed_at) "
                 "VALUES (:i, :p, 'run', :e, 'run under audit', 'Audit sentinel', :n)"),
            {"i": hold_id, "p": seed.pid, "e": held, "n": now - timedelta(minutes=1)},
        )
        await db.commit()

    plan, outcome = await _export_phase(env, stores, seed, job_id, now)

    runs_json = json.loads(_members(stores.root / "compliance-packs" / outcome.location)["runs.json"])
    assert held not in {uuid.UUID(r["id"]) for r in runs_json} and len(runs_json) == 2
    assert outcome.run_ids_excluded == [held]
    await _delete_loop(env, stores, seed, job_id, outcome.run_ids_archived, holds=outcome.holds,
                       held_ids=[str(r) for r in outcome.run_ids_excluded],
                       meta={**plan.meta, **outcome.export_meta()})
    assert await _runs_left(env, [held]) == 1
    assert await _runs_left(env, seed.run_ids) == 1
    named = await _scalar(env, "SELECT holds_honoured -> 'holds' -> 0 ->> 'hold_id' FROM deletion_jobs WHERE id = :j",
                          j=job_id)
    assert named == str(hold_id)
    assert await _scalar(env, "SELECT status FROM deletion_jobs WHERE id = :j", j=job_id) == "completed"
