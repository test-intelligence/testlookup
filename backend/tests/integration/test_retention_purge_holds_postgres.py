"""W22a ACT-05 — holds inside the POLICY purge, against a real PostgreSQL (contract §2.1 P-rows, §6.3 G-C).

A hold check missed on ONE purge class is silent evidence loss. PARAMETRISED over the 16 HELD purge inputs
(W25d HYG-02: "P-12 provenance" left with the ``ai_provenance_records`` table, dropped by migration 0250):
with the policy at ``keep_last_runs = 0`` and every seeded run ``FAILED`` (so no floor arm keeps it — the
PASSED arms apply even at N = 0), seed one row of the class old enough to be purged, place a PROJECT hold
(``entity_id NULL``), then

* ``run_purge(mode="preview")`` counts the class 0 and reports ``holds.project_held``;
* ``mode="execute"`` + commit leaves the row in place;
* ``_retention_purge_sweep(project_id)`` writes a deletion job with ``holds_honoured.project_held``;
* after the hold is RELEASED the same execute deletes the row (the positive half).

Plus: a release hold keeps exactly the release's linked runs, its packs and those runs' decision reports; a
run hold keeps the run and its baseline (not the baseline's baseline); an expired hold keeps nothing; the five
not-held classes are still taken under a project hold; and, side by side, a ``NOT IN`` version of the
direct-run filter selects ZERO rows when a NULL ``entity_id`` is present while the anti-join selects exactly
the unheld runs (R-3).

Mongo and storage are in-memory doubles injected into ``run_purge``; where the test drives
``_retention_purge_sweep`` (which calls ``run_purge`` without injection) ``retention_service.get_mongo_db`` /
``get_storage_provider`` are patched to the same doubles and the analysis-cache / search-index / memory-vector
entry points to stubs, so no real Mongo, Chroma or MinIO client is bound. The fixture awaits
``dispose_engine_for_loop()`` before building its engine and after disposing it, and closes ONLY Redis (the
sweep bumps the analytics epoch). Every window is computed from a ``now`` built in the test; every row lives
under a fresh project removed at the end. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (= the app's
``DATABASE_URL``) migrated to head (0240). To be listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml`` (contract §6.4).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _MemCollection:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    def _match(self, doc: dict, filt: dict) -> bool:
        return all(doc.get(field) in cond.get("$in", ()) for field, cond in filt.items())

    async def count_documents(self, filt):
        return sum(1 for d in self.docs if self._match(d, filt))

    async def delete_many(self, filt):
        before = len(self.docs)
        self.docs[:] = [d for d in self.docs if not self._match(d, filt)]
        return type("R", (), {"deleted_count": before - len(self.docs)})()


class _MemMongo:
    """Collections keyed by name; only ``count_documents`` / ``delete_many`` with ``$in`` (what the purge uses)."""

    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def __getitem__(self, name):
        return _MemCollection(self.store.setdefault(name, []))

    def has(self, name: str, **doc) -> bool:
        return any(all(d.get(k) == v for k, v in doc.items()) for d in self.store.get(name, []))


class _MemStorage:
    def __init__(self):
        self.keys: set[tuple[str, str]] = set()   # (bucket, key)

    async def list_objects(self, prefix, bucket=None):
        return [{"Key": k} for b, k in sorted(self.keys) if b == (bucket or "default") and k.startswith(prefix)]

    async def delete_prefix(self, prefix, bucket=None):
        gone = {(b, k) for b, k in self.keys if b == (bucket or "default") and k.startswith(prefix)}
        self.keys -= gone
        return len(gone)

    async def delete_object(self, key, bucket=None):
        self.keys.discard((bucket or "default", key))


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres
    from app.db.redis_client import close_redis

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_redis()


@pytest.fixture
def stores(monkeypatch):
    """The doubles, also patched in for the sweep's un-injected ``run_purge``; the derived stores stubbed."""
    from app.services import agent_memory_service, analysis_cache_retention, retention_service, semantic_search

    mongo, storage, calls = _MemMongo(), _MemStorage(), []

    async def _caches(project_id, *, cutoff, execute):
        calls.append(("caches", execute))
        return {"redis": None, "semantic": None}

    async def _search(project_id, *, execute):
        calls.append(("search_index", execute))
        return None

    async def _vectors(project_id, ids):
        calls.append(("memory_vectors", len(ids)))
        return len(ids)

    monkeypatch.setattr(retention_service, "get_mongo_db", lambda: mongo)
    monkeypatch.setattr(retention_service, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(analysis_cache_retention, "purge_project_analysis_caches", _caches)
    monkeypatch.setattr(semantic_search, "purge_project_documents", _search)
    monkeypatch.setattr(agent_memory_service, "purge_memory_vectors", _vectors)
    return mongo, storage, calls


# ── Seeding (every timestamp from the test's own ``now``) ────────────────────


async def _project(sessions, now) -> uuid.UUID:
    """A fresh project with the purge ON, the shortest windows and the floor OFF (``keep_last_runs = 0``)."""
    from app.models.postgres import Project, ProjectRetentionPolicy

    pid = uuid.uuid4()
    async with sessions() as db:
        db.add(Project(id=pid, name=f"gc-holds-{pid.hex[:10]}", slug=f"gc-holds-{pid.hex[:10]}", is_active=True))
        await db.flush()
        db.add(ProjectRetentionPolicy(project_id=pid, enabled=True, raw_events_days=7, runs_days=30,
                                      artifacts_days=7, audit_days=365, keep_last_runs=0))
        await db.commit()
    return pid


def _run(pid, now, *, days, status="FAILED", **extra):
    from app.models.postgres import TestRun

    run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"gc-{uuid.uuid4().hex[:8]}", jenkins_job="gc",
                  status=status, ingestion_source="upload", branch="main", **extra)
    run.created_at = now - timedelta(days=days)
    return run


async def _add(sessions, *rows) -> None:
    async with sessions() as db:
        for row in rows:
            db.add(row)
            await db.flush()
        await db.commit()


async def _hold(sessions, pid, now, entity_type="project", entity_id=None, *, reason="Litigation sentinel",
                expires_at=None) -> uuid.UUID:
    hold_id = uuid.uuid4()
    async with sessions() as db:
        await db.execute(
            text("INSERT INTO retention_holds (id, project_id, entity_type, entity_id, entity_label, reason, "
                 "expires_at, placed_at) VALUES (:i, :p, :t, :e, 'gc label', :r, :x, :n)"),
            {"i": hold_id, "p": pid, "t": entity_type, "e": entity_id, "r": reason, "x": expires_at, "n": now},
        )
        await db.commit()
    return hold_id


async def _release_all(sessions, pid, now) -> None:
    async with sessions() as db:
        await db.execute(
            text("UPDATE retention_holds SET released_at = :n, release_reason = 'Matter closed' "
                 "WHERE project_id = :p AND released_at IS NULL"),
            {"n": now, "p": pid},
        )
        await db.commit()


async def _scalar(sessions, sql: str, **params) -> Any:
    async with sessions() as db:
        return (await db.execute(text(sql), params)).scalar()


async def _purge(sessions, pid, now, mongo, storage, *, mode: str) -> dict:
    from app.services import retention_service

    async with sessions() as db:
        out = await retention_service.run_purge(db, project_id=pid, mode=mode, now=now, mongo=mongo, storage=storage)
        await db.commit()
    return out


async def _cleanup(sessions, pid, *, audit_ids=()) -> None:
    async with sessions() as db:
        if audit_ids:
            await db.execute(text("DELETE FROM access_audit_logs WHERE id = ANY(:i)"), {"i": list(audit_ids)})
            await db.execute(text("DELETE FROM test_case_audit_logs WHERE id = ANY(:i)"), {"i": list(audit_ids)})
        await db.execute(text("DELETE FROM settings_audit_log WHERE setting_key = :k"),
                         {"k": f"retention_purge:{pid}"})
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await db.commit()


# ── The 16 HELD purge inputs: each seeds ONE row and answers (is it there?, its preview count) ──


class _Ctx:
    def __init__(self, sessions, pid, now, mongo, storage, monkeypatch):
        self.sessions, self.pid, self.now = sessions, pid, now
        self.mongo, self.storage, self.monkeypatch = mongo, storage, monkeypatch
        self.audit_ids: list[uuid.UUID] = []

    def old(self, days=800):
        return self.now - timedelta(days=days)

    async def exists(self, table: str, row_id) -> bool:
        return bool(await _scalar(self.sessions, f"SELECT count(*) FROM {table} WHERE id = :i", i=row_id))


async def _aged_run(ctx, **extra):
    run = _run(ctx.pid, ctx.now, days=800, **extra)
    await _add(ctx.sessions, run)
    return run


async def _p01_run_row(ctx):
    run = await _aged_run(ctx)
    return (lambda: ctx.exists("test_runs", run.id)), (lambda out: out["candidates"]["runs"])


def _mongo_case(collection: str, field: str = "test_run_id"):
    async def seed(ctx):
        run = await _aged_run(ctx)
        ctx.mongo[collection].docs.append({field: str(run.id)})
        return ((lambda: _true(ctx.mongo.has(collection, **{field: str(run.id)}))),
                (lambda out: out["candidates"]["mongo_docs"][collection]))
    return seed


async def _true(value):
    return value


async def _p03_live_slug(ctx):
    from app.models.postgres import LiveSession

    slug = f"gc-slug-{uuid.uuid4().hex[:8]}"
    await _add(ctx.sessions, LiveSession(project_id=ctx.pid, run_id=slug, client_name="gc",
                                         session_token_hash=f"t{uuid.uuid4().hex}"[:64], started_at=ctx.old()))
    ctx.mongo["live_execution_events"].docs.append({"run_id": slug})
    return ((lambda: _true(ctx.mongo.has("live_execution_events", run_id=slug))),
            (lambda out: out["candidates"]["mongo_docs"]["live_execution_events"]))


async def _p04_upload_object(ctx):
    run = await _aged_run(ctx)
    key = ("default", f"uploads/{ctx.pid}/{run.id}/report.json")
    ctx.storage.keys.add(key)
    return (lambda: _true(key in ctx.storage.keys)), (lambda out: out["candidates"]["minio_objects"])


async def _p05_evidence_row(ctx):
    from app.models.postgres import EvidenceArtifact

    run = await _aged_run(ctx)
    row = EvidenceArtifact(id=uuid.uuid4(), project_id=ctx.pid, run_id=run.id, artifact_type="log",
                           source_system="gc", created_at=ctx.old())
    await _add(ctx.sessions, row)
    return (lambda: ctx.exists("evidence_artifacts", row.id)), (lambda out: out["candidates"]["evidence_artifact_rows"])


async def _p08_event_archive(ctx):
    run = _run(ctx.pid, ctx.now, days=10, event_archive=[{"event": "gc"}], event_archive_at=ctx.old(10))
    await _add(ctx.sessions, run)

    async def present():
        # The strip NULLs event_archive_at and writes JSON null into the JSONB payload column (SQLAlchemy's
        # JSON None), so "still archived" = the timestamp AND an array payload are both there.
        return bool(await _scalar(
            ctx.sessions,
            "SELECT event_archive_at IS NOT NULL AND jsonb_typeof(event_archive) = 'array' FROM test_runs WHERE id = :i",
            i=run.id,
        ))
    return present, (lambda out: out["candidates"]["event_archive_rows"])


def _row_case(table: str, build, count_key: str, *, audit=False):
    async def seed(ctx):
        row = build(ctx)
        await _add(ctx.sessions, row)
        if audit:
            ctx.audit_ids.append(row.id)
        return (lambda: ctx.exists(table, row.id)), (lambda out: out["candidates"][count_key])
    return seed


def _access_audit(ctx):
    from app.models.postgres import AccessAuditLog

    return AccessAuditLog(id=uuid.uuid4(), project_id=ctx.pid, action="view", created_at=ctx.old())


def _tc_audit(ctx):
    from app.models.postgres import TestCaseAuditLog

    return TestCaseAuditLog(id=uuid.uuid4(), project_id=ctx.pid, entity_type="test_case", entity_id=uuid.uuid4(),
                            action="update", created_at=ctx.old())


def _activity(ctx):
    from app.models.postgres import ProjectActivityEvent

    return ProjectActivityEvent(id=uuid.uuid4(), project_id=ctx.pid, category="configuration",
                                event_type="retention_policy.updated", actor_type="system", entity_type="project",
                                entity_id=str(ctx.pid), summary="gc activity", occurred_at=ctx.old())


def _deletion_job(ctx):
    from app.models.postgres import DeletionJob

    return DeletionJob(id=uuid.uuid4(), project_id=ctx.pid, job_kind="scheduled", status="completed",
                       requested_at=ctx.old())


async def _p14_revoked_link(ctx):
    from app.models.postgres import ReportShareLink

    run = _run(ctx.pid, ctx.now, days=10)   # inside the runs window: the LINK's own clock is what is held
    link = ReportShareLink(id=uuid.uuid4(), run_id=run.id, project_id=ctx.pid, token_hash=uuid.uuid4().hex * 2,
                           expires_at=ctx.now + timedelta(days=1), is_revoked=True, created_at=ctx.old(20))
    await _add(ctx.sessions, run, link)
    return (lambda: ctx.exists("report_share_links", link.id)), (lambda out: out["candidates"]["revoked_share_links"])


async def _p16_expired_pack(ctx):
    from app.models.postgres import CompliancePack

    pack = CompliancePack(id=uuid.uuid4(), project_id=ctx.pid, minio_key=f"packs/gc-{uuid.uuid4().hex[:8]}.zip",
                          manifest_sha256="a" * 64, retention_expires_at=ctx.now - timedelta(days=1))
    await _add(ctx.sessions, pack)
    ctx.storage.keys.add(("compliance-packs", pack.minio_key))
    return (lambda: ctx.exists("compliance_packs", pack.id)), (lambda out: out["candidates"]["compliance_packs_expired"])


async def _version_on(ctx, run):
    from app.models.postgres import AIAnalysisVersion, TestCase

    case = TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name="test_gc_hold", test_fingerprint="fp-gc-hold",
                    status="FAILED")
    version = AIAnalysisVersion(id=uuid.uuid4(), test_case_id=case.id, test_run_id=run.id, project_id=ctx.pid,
                                test_fingerprint="fp-gc-hold", version=1, trigger="manual", content_sha256="b" * 64)
    await _add(ctx.sessions, run, case, version)
    return version


async def _p17_cascaded_versions(ctx):
    version = await _version_on(ctx, _run(ctx.pid, ctx.now, days=800))
    return (lambda: ctx.exists("ai_analysis_versions", version.id)), (lambda out: out["candidates"]["ai_history_versions"])


async def _p18_over_cap_sweep(ctx):
    """The plan is stubbed to name one SURVIVING run's version: a project hold must skip the sweep entirely."""
    from app.services import ai_history_retention

    version = await _version_on(ctx, _run(ctx.pid, ctx.now, days=10))

    async def _plan(db, mongo, *, project_id, caps, exclude_run_ids=()):
        return ai_history_retention.OverCapPlan(analysis_version_ids=[version.id])

    ctx.monkeypatch.setattr(ai_history_retention, "plan_over_cap", _plan)
    return (lambda: ctx.exists("ai_analysis_versions", version.id)), (lambda out: out["candidates"]["ai_history_versions"])


_CASES = {
    "P-01 runs": _p01_run_row,
    "P-02 raw events (raw_allure_json)": _mongo_case("raw_allure_json"),
    "P-03 live events (session slug)": _p03_live_slug,
    "P-04 upload objects": _p04_upload_object,
    "P-05 evidence artifacts": _p05_evidence_row,
    "P-06 decision reports (audit clock)": _mongo_case("decision_reports"),
    "P-07 runs-clock Mongo (run_summaries)": _mongo_case("run_summaries"),
    "P-08 event archive strip": _p08_event_archive,
    "P-09 access audit": _row_case("access_audit_logs", _access_audit, "audit_rows", audit=True),
    "P-10 test-case audit": _row_case("test_case_audit_logs", _tc_audit, "audit_rows", audit=True),
    "P-11 activity ledger": _row_case("project_activity_events", _activity, "audit_rows"),
    "P-13 terminal deletion jobs": _row_case("deletion_jobs", _deletion_job, "audit_rows"),
    "P-14 revoked share links": _p14_revoked_link,
    "P-16 expired compliance packs": _p16_expired_pack,
    "P-17 cascaded AI versions": _p17_cascaded_versions,
    "P-18 AI over-cap sweep": _p18_over_cap_sweep,
}


async def _latest_job_record(sessions, pid, since):
    import json

    value = await _scalar(
        sessions,
        "SELECT holds_honoured FROM deletion_jobs WHERE project_id = :p AND job_kind = 'scheduled' "
        "AND requested_at >= :s ORDER BY requested_at DESC LIMIT 1",
        p=pid, s=since,
    )
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.parametrize("case", list(_CASES))
async def test_a_project_hold_keeps_every_held_class(env, stores, monkeypatch, case):
    from app.worker.tasks import _retention_purge_sweep

    sessions = env
    mongo, storage, _calls = stores
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, now)
    ctx = _Ctx(sessions, pid, now, mongo, storage, monkeypatch)
    try:
        present, count = await _CASES[case](ctx)
        assert await present(), "the seed did not land — the test would prove nothing"
        await _hold(sessions, pid, now)   # a PROJECT hold: entity_id NULL

        preview = await _purge(sessions, pid, now, mongo, storage, mode="preview")
        assert count(preview) == 0, f"{case}: the preview must not count a held class"
        assert preview["holds"]["project_held"] is True
        out = await _purge(sessions, pid, now, mongo, storage, mode="execute")
        assert await present(), f"{case}: a project hold must keep it through an execute"
        assert out["holds_honoured"]["project_held"] is True

        summary = await _retention_purge_sweep(str(pid))
        assert summary["errors"] == 0, summary
        assert await present(), f"{case}: the scheduled sweep must keep it too"
        record = await _latest_job_record(sessions, pid, now - timedelta(minutes=1))
        assert record and record["project_held"] is True, "the job records the hold it honoured"

        await _release_all(sessions, pid, now)
        after = await _purge(sessions, pid, now, mongo, storage, mode="preview")
        assert count(after) >= 1, f"{case}: released, the class is a candidate again"
        await _purge(sessions, pid, now, mongo, storage, mode="execute")
        assert not await present(), f"{case}: released, the same execute deletes it"
    finally:
        await _cleanup(sessions, pid, audit_ids=ctx.audit_ids)


# ── Transitive pins, expiry, the not-held classes ────────────────────────────


async def _runs_left(sessions, run_ids) -> set:
    async with sessions() as db:
        rows = (await db.execute(text("SELECT id FROM test_runs WHERE id = ANY(:i)"), {"i": list(run_ids)})).all()
    return {r[0] for r in rows}


async def test_a_release_hold_keeps_its_linked_runs_their_packs_and_their_decision_reports(env, stores):
    from app.models.postgres import CompliancePack, Release, ReleaseTestRunLink

    sessions = env
    mongo, storage, _calls = stores
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, now)
    try:
        linked, loose = _run(pid, now, days=800), _run(pid, now, days=800)
        release = Release(id=uuid.uuid4(), project_id=pid, name=f"gc-rel-{uuid.uuid4().hex[:6]}")
        packs = [CompliancePack(id=uuid.uuid4(), project_id=pid, release_id=rel, minio_key=f"packs/gc-{i}.zip",
                                manifest_sha256="c" * 64, retention_expires_at=now - timedelta(days=1))
                 for i, rel in enumerate((release.id, None))]
        await _add(sessions, linked, loose, release,
                   ReleaseTestRunLink(id=uuid.uuid4(), release_id=release.id, test_run_id=linked.id), *packs)
        for run in (linked, loose):
            mongo["decision_reports"].docs.append({"test_run_id": str(run.id)})
        hold_id = await _hold(sessions, pid, now, "release", release.id, reason="Audit Q4 sentinel")

        preview = await _purge(sessions, pid, now, mongo, storage, mode="preview")
        (active,) = preview["holds"]["active"]
        assert (active["hold_id"], active["runs_kept"], active["packs_kept"]) == (str(hold_id), 1, 1)
        out = await _purge(sessions, pid, now, mongo, storage, mode="execute")

        assert await _runs_left(sessions, [linked.id, loose.id]) == {linked.id}
        left_packs = {r for r in [await _scalar(sessions, "SELECT id FROM compliance_packs WHERE id = :i", i=p.id)
                                  for p in packs] if r}
        assert left_packs == {packs[0].id}, "the release's pack stays, the unpinned one goes"
        assert mongo.has("decision_reports", test_run_id=str(linked.id))
        assert not mongo.has("decision_reports", test_run_id=str(loose.id))
        honoured = out["holds_honoured"]
        assert (honoured["project_held"], honoured["runs_kept"], honoured["packs_kept"]) == (False, 1, 1)
        assert honoured["run_ids_kept"] == [str(linked.id)]
    finally:
        await _cleanup(sessions, pid)


async def test_a_run_hold_keeps_the_run_and_its_baseline_but_not_the_baselines_baseline(env, stores):
    from app.models.postgres import RunBaseline

    sessions = env
    mongo, storage, _calls = stores
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, now)
    try:
        held, baseline, grand = (_run(pid, now, days=d) for d in (800, 850, 900))
        await _add(sessions, grand, baseline, held,
                   RunBaseline(id=uuid.uuid4(), run_id=held.id, baseline_run_id=baseline.id,
                               selection_reason="newest_passed", classification="same"),
                   RunBaseline(id=uuid.uuid4(), run_id=baseline.id, baseline_run_id=grand.id,
                               selection_reason="newest_passed", classification="same"))
        await _hold(sessions, pid, now, "run", held.id, reason="Run matter sentinel")
        await _purge(sessions, pid, now, mongo, storage, mode="execute")
        assert await _runs_left(sessions, [held.id, baseline.id, grand.id]) == {held.id, baseline.id}, (
            "one hop (D-2): the held run's baseline is pinned, the baseline's baseline is not"
        )
    finally:
        await _cleanup(sessions, pid)


async def test_an_expired_hold_keeps_nothing(env, stores):
    sessions = env
    mongo, storage, _calls = stores
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, now)
    try:
        run = _run(pid, now, days=800)
        await _add(sessions, run)
        await _hold(sessions, pid, now - timedelta(days=2), expires_at=now - timedelta(minutes=1))
        preview = await _purge(sessions, pid, now, mongo, storage, mode="preview")
        assert preview["holds"] == {"project_held": False, "runs_kept": 0, "packs_kept": 0, "active": []}
        out = await _purge(sessions, pid, now, mongo, storage, mode="execute")
        assert out["holds_honoured"] is None
        assert await _runs_left(sessions, [run.id]) == set(), "the predicate reads now: an expired hold stops at once"
    finally:
        await _cleanup(sessions, pid)


async def test_the_five_not_held_classes_are_still_taken_under_a_project_hold(env, stores):
    """D-7: expired agent memory, analysis caches, search-index documents, search usage rows and unlabelled
    shadow pairs are derived, rebuildable or fixed-window telemetry — a project hold does not keep them."""
    from app.models.postgres import AgentMemoryEntry, AIEvalShadowPair, SearchQueryEvent

    sessions = env
    mongo, storage, calls = stores
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, now)
    try:
        run = _run(pid, now, days=10)
        memory = AgentMemoryEntry(id=uuid.uuid4(), project_id=pid, run_id=run.id, entity_type="test_case",
                                  entity_id="gc-entity", expires_at=now - timedelta(days=1))
        usage = SearchQueryEvent(id=uuid.uuid4(), project_id=pid, q_hash="d" * 64, q_length=5, entity_scope="tests",
                                 mode_requested="auto", mode_used="keyword", result_count=1, latency_ms=3,
                                 created_at=now - timedelta(days=40))
        shadow = AIEvalShadowPair(id=uuid.uuid4(), project_id=pid, agent_id="gc", sample_key=f"root_cause:{uuid.uuid4()}",
                                  incumbent_tier="small", candidate_tier="large", incumbent_output={"a": 1},
                                  candidate_output={"b": 2}, label_status="pending", created_at=now - timedelta(days=40))
        await _add(sessions, run, memory, usage, shadow)
        await _hold(sessions, pid, now)

        from app.services import retention_service

        async with sessions() as db:   # NOT injected: the derived-store entry points (stubbed) are visited
            await retention_service.run_purge(db, project_id=pid, mode="execute", now=now)
            await db.commit()
        assert await _scalar(sessions, "SELECT count(*) FROM search_query_events WHERE id = :i", i=usage.id) == 0
        assert await _scalar(sessions, "SELECT count(*) FROM ai_eval_shadow_pairs WHERE id = :i", i=shadow.id) == 0
        assert await _scalar(sessions, "SELECT lifecycle_status FROM agent_memory_entries WHERE id = :i",
                             i=memory.id) == "expired"
        assert ("caches", True) in calls and ("search_index", True) in calls and ("memory_vectors", 1) in calls
        assert await _runs_left(sessions, [run.id]) == {run.id}
    finally:
        await _cleanup(sessions, pid)


async def test_not_in_selects_nothing_beside_a_null_entity_id_while_the_anti_join_selects_the_unheld(env):
    """R-3, side by side. With a live run hold on A and a live PROJECT hold (``entity_id NULL``) present, the
    direct-run filter written as ``NOT IN`` selects ZERO rows (``x NOT IN (…, NULL)`` is never true — a purge
    built on it would silently delete nothing and report success), while the same filter as the hold
    service's ``NOT EXISTS`` arm selects exactly the runs without a run hold. The whole ``run_not_held``
    rightly selects nothing then: the project hold pins every run."""
    from app.services import retention_hold_service as holds

    sessions = env
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, now)
    try:
        held, free_1, free_2 = (_run(pid, now, days=800) for _ in range(3))
        await _add(sessions, held, free_1, free_2)
        await _hold(sessions, pid, now, "run", held.id, reason="Run matter sentinel")
        await _hold(sessions, pid, now)
        not_in = await _scalar(
            sessions,
            "SELECT count(*) FROM test_runs WHERE project_id = :p AND id NOT IN "
            "(SELECT entity_id FROM retention_holds WHERE project_id = :p "
            " AND released_at IS NULL AND (expires_at IS NULL OR expires_at > :n))",
            p=pid, n=now,
        )
        anti = await _scalar(
            sessions,
            "SELECT count(*) FROM test_runs t WHERE t.project_id = :p AND NOT EXISTS "
            "(SELECT 1 FROM retention_holds h WHERE h.project_id = :p AND h.entity_id = t.id "
            " AND h.released_at IS NULL AND (h.expires_at IS NULL OR h.expires_at > :n))",
            p=pid, n=now,
        )
        assert not_in == 0, "NOT IN beside a NULL entity_id matches nothing"
        assert anti == 2, "the anti-join keeps exactly the run-held run out"
        from sqlalchemy import select

        from app.models.postgres import TestRun

        async with sessions() as db:
            whole = (await db.execute(select(TestRun.id).where(
                TestRun.project_id == pid, holds.run_not_held(TestRun.id, pid, now=now)))).all()
        assert whole == [], "with a live project hold every run is pinned"
    finally:
        await _cleanup(sessions, pid)
