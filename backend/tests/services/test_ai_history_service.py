"""AIH-01/06: ``ai_history_service`` against in-memory fakes (no database).

The version allocation (max+1, the idempotency key, the duplicate-key retry,
the subject lock's "run is gone" mapping), the never-raise contract of the
summary hook, the capture's activity emission rules (once per NEW version,
``verdict_changed`` only on a real change, nothing on a replay or a deleted
run), the fail-closed citation lookup and the run-level prune. Real Mongo and
Postgres behaviour is pinned in ``tests/integration/test_ai_run_versions_mongo.py``
and ``tests/integration/test_ai_analysis_versions_postgres.py``.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.services import ai_history_core as core
from app.services import ai_history_service as svc
from app.services.decision_report_service import DecisionReportSubjectUnavailable

RUN = "11111111-1111-4111-8111-111111111111"
PROJECT = "22222222-2222-4222-8222-222222222222"


def _match(doc, query):
    for key, want in query.items():
        have = doc
        for part in key.split("."):
            have = have.get(part) if isinstance(have, dict) else None
        if isinstance(want, dict) and "$in" in want:
            if have not in want["$in"]:
                return False
        elif have != want:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction=None):
        if isinstance(key, list):
            key, direction = key[0]
        self.rows = sorted(self.rows, key=lambda d: d.get(key) or 0, reverse=direction == -1)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length=None):
        return [dict(r) for r in self.rows]


class FakeCollection:
    def __init__(self, unique=(), fail_inserts=0):
        self.docs: list[dict] = []
        self.unique = unique
        self.fail_inserts = fail_inserts
        self.inserts = 0

    async def find_one(self, query, projection=None, sort=None):
        rows = [d for d in self.docs if _match(d, query)]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key) or 0, reverse=direction == -1)
        return dict(rows[0]) if rows else None

    def find(self, query, projection=None):
        return _Cursor([d for d in self.docs if _match(d, query)])

    async def insert_one(self, doc):
        self.inserts += 1
        if self.fail_inserts:
            self.fail_inserts -= 1
            raise DuplicateKeyError("raced")
        for keys in self.unique:
            if any(all(d.get(k) == doc.get(k) for k in keys) for d in self.docs):
                raise DuplicateKeyError("dup")
        doc["_id"] = object()
        self.docs.append(doc)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class FakeMongo:
    def __init__(self):
        self.collections = {
            "ai_run_versions": FakeCollection(unique=[("test_run_id", "kind", "version")]),
            "decision_reports": FakeCollection(),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture
def lock(monkeypatch):
    """The subject lock without Postgres; ``state['gone']`` simulates a deleted run."""
    state = {"gone": False, "entered": 0}

    @asynccontextmanager
    async def fake_lock(test_run_id):
        if state["gone"]:
            raise DecisionReportSubjectUnavailable("decision report subject no longer exists")
        state["entered"] += 1
        yield

    monkeypatch.setattr(svc, "lock_decision_report_subject", fake_lock)
    return state


def _summary(n=1):
    return {"layer1_executive_summary": f"s{n}", "executive_summary": f"s{n}", "fallback_used": False}


async def _record(mongo, *, pipeline="pipe-1", n=1, caps=None):
    payload = _summary(n)
    return await svc.record_run_version(
        mongo, test_run_id=RUN, project_id=PROJECT, kind="run_summary", payload=payload,
        meta=core.meta_from_summary(payload), provenance=core.provenance_from_summary(payload),
        pipeline_run_id=pipeline, caps=caps,
    )


# ── snapshots ────────────────────────────────────────────────────────────────


def test_a_pipeline_snapshot_uses_the_upserts_own_defaults():
    snap = svc.snapshot_from_analysis_dict(str(uuid.UUID(int=1)), {"root_cause_summary": "x"}, {"analysis_mode": "llm"})
    assert snap.failure_category == "UNKNOWN" and snap.confidence_score == 0
    assert snap.requires_human_review is True and snap.is_flaky is False
    assert snap.recommended_actions == [] and snap.evidence_references == []
    assert snap.test_case_id == uuid.UUID(int=1)


def test_an_orm_snapshot_reads_enums_and_unflushed_nones():
    from app.models.postgres import AIAnalysis, FailureCategory

    row = AIAnalysis(test_case_id=uuid.UUID(int=2), failure_category=FailureCategory.FLAKY, confidence_score=33)
    snap = svc.snapshot_from_orm(row)
    assert snap.failure_category == "FLAKY" and snap.is_flaky is False and snap.routing_metadata is None
    assert svc.snapshot_sha256(snap) == core.analysis_content_sha256(
        failure_category="FLAKY", confidence_score=33, is_flaky=False, requires_human_review=False,
        backend_error_found=False, pod_issue_found=False, llm_provider=None, llm_model=None,
        analysis_mode=None, root_cause_summary=None,
    )


def test_the_slim_provenance_keeps_only_the_contract_keys():
    routing = {
        "analysis_mode": "llm", "mode_requested": "auto", "fallback_from": None, "fallback_reason": None,
        "prompt_versions": {"react_triage": "v2:abc"}, "eval_manifest_checksum": "sum",
        "model_config_snapshot": {"provider": "ollama", "model": "m", "temperature": 0.1},
        "confidence_basis": "llm_weighted", "input_fingerprints": {"x": "y"}, "kind_evidence": {"big": "blob"},
    }
    assert svc._slim_provenance(routing, "pipeline") == {
        "analysis_mode": "llm", "mode_requested": "auto", "prompt_versions": {"react_triage": "v2:abc"},
        "eval_manifest_checksum": "sum", "model_config_snapshot": {"provider": "ollama", "model": "m"},
        "confidence_basis": "llm_weighted", "source": "pipeline",
    }


# ── record_run_version ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_versions_are_allocated_max_plus_one_per_run_and_kind(lock):
    mongo = FakeMongo()
    first = await _record(mongo, pipeline="a", n=1)
    second = await _record(mongo, pipeline="b", n=2)
    other_kind = await svc.record_run_version(
        mongo, test_run_id=RUN, project_id=PROJECT, kind="analysis_digest", payload={"x": 1},
        meta={}, provenance={}, pipeline_run_id="a",
    )
    assert (first.version, second.version, other_kind.version) == (1, 2, 1)
    doc = second.doc
    assert set(doc) == {"version_id", "test_run_id", "project_id", "kind", "version", "pipeline_run_id", "trigger",
                        "content_sha256", "generated_at", "created_at", "schema_version", "provenance", "meta", "payload"}
    assert doc["content_sha256"] == core.summary_content_sha256(_summary(2))
    assert other_kind.doc["content_sha256"] == core.digest_content_sha256({"x": 1})
    uuid.UUID(doc["version_id"])


@pytest.mark.asyncio
async def test_a_replay_returns_the_existing_doc_without_taking_the_lock(lock):
    mongo = FakeMongo()
    first = await _record(mongo)
    entered = lock["entered"]
    again = await _record(mongo)
    assert again.created is False and again.version_id == first.version_id
    assert lock["entered"] == entered, "the idempotency pre-check answers before the lock"
    assert len(mongo["ai_run_versions"].docs) == 1


@pytest.mark.asyncio
async def test_a_lost_version_race_is_retried(lock):
    mongo = FakeMongo()
    mongo["ai_run_versions"].fail_inserts = 2
    rec = await _record(mongo)
    assert rec.created and rec.version == 1 and mongo["ai_run_versions"].inserts == 3


@pytest.mark.asyncio
async def test_a_race_lost_to_the_same_pipeline_returns_its_doc(lock, monkeypatch):
    mongo = FakeMongo()
    original = mongo["ai_run_versions"].insert_one
    winner = {}

    async def racing_insert(doc):
        if not winner:  # the other worker of the SAME pipeline lands first
            other = dict(doc, version_id="the-winner")
            winner["doc"] = other
            await original(other)
            raise DuplicateKeyError("raced")
        await original(doc)

    mongo["ai_run_versions"].insert_one = racing_insert
    rec = await _record(mongo)
    assert rec.created is False and rec.version_id == "the-winner"


@pytest.mark.asyncio
async def test_retries_are_bounded(lock):
    mongo = FakeMongo()
    mongo["ai_run_versions"].fail_inserts = 100
    with pytest.raises(DuplicateKeyError):
        await _record(mongo)
    assert mongo["ai_run_versions"].inserts == svc._ALLOCATION_ATTEMPTS


@pytest.mark.asyncio
async def test_a_run_gone_under_the_lock_raises_and_writes_nothing(lock):
    mongo = FakeMongo()
    lock["gone"] = True
    with pytest.raises(svc.AIHistorySubjectUnavailable):
        await _record(mongo)
    assert mongo["ai_run_versions"].docs == []
    assert issubclass(svc.AIHistorySubjectUnavailable, DecisionReportSubjectUnavailable)


@pytest.mark.asyncio
async def test_prune_runs_only_with_caps_and_its_failure_is_swallowed(lock, monkeypatch):
    mongo = FakeMongo()
    prune = AsyncMock(side_effect=RuntimeError("mongo blip"))
    monkeypatch.setattr(svc, "prune_run_versions", prune)
    await _record(mongo, pipeline="a")
    prune.assert_not_awaited()
    rec = await _record(mongo, pipeline="b", n=2, caps=core.AIHistoryCaps(per_run=3))
    assert rec.created and prune.await_args.kwargs == {"test_run_id": RUN, "kind": "run_summary", "cap": 3}


@pytest.mark.asyncio
async def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        await svc.record_run_version(FakeMongo(), test_run_id=RUN, project_id=PROJECT, kind="other", payload={},
                                     meta={}, provenance={}, pipeline_run_id=None)


# ── record_run_summary_version never raises ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_summary_hook_swallows_every_failure(monkeypatch):
    monkeypatch.setattr(svc, "record_run_version", AsyncMock(side_effect=ConnectionError("mongo down")))
    assert await svc.record_run_summary_version(FakeMongo(), test_run_id=RUN, project_id=PROJECT,
                                                pipeline_run_id="p", payload=_summary()) is None


@pytest.mark.asyncio
async def test_the_summary_hook_skips_a_subjectless_call(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(svc, "record_run_version", record)
    assert await svc.record_run_summary_version(FakeMongo(), test_run_id=RUN, project_id=None,
                                                pipeline_run_id="p", payload=_summary()) is None
    record.assert_not_awaited()


def test_a_backfilled_summary_never_carries_the_overwritten_markdown():
    doc = {"markdown_report": "# the DECISION report", "layer1_executive_summary": "x", "fallback_used": 1}
    payload = svc.run_summary_payload(doc, backfill=True)
    assert payload["markdown_report"] is None and payload["markdown_source"] == "overwritten_by_decision_report"
    assert payload["fallback_used"] is True
    assert svc.run_summary_payload(doc)["markdown_report"] == "# the DECISION report"


# ── citations and the run-level prune ────────────────────────────────────────


@pytest.mark.asyncio
async def test_citations_are_scoped_and_fail_closed():
    mongo = FakeMongo()
    mongo["decision_reports"].docs = [
        {"test_run_id": RUN, "pipeline_run_id": "p1", "status": "published"},
        {"test_run_id": RUN, "pipeline_run_id": "p2", "status": "rejected"},
        {"test_run_id": "other", "pipeline_run_id": "p3", "status": "published"},
    ]
    assert await svc.cited_pipeline_ids(mongo, test_run_ids=[RUN]) == {"p1"}
    assert await svc.cited_pipeline_ids(None, test_run_ids=[RUN]) is None
    assert await svc.cited_pipeline_ids(mongo) is None

    class Broken:
        def __getitem__(self, _name):
            raise RuntimeError("down")

    assert await svc.cited_pipeline_ids(Broken(), test_run_ids=[RUN]) is None


@pytest.mark.asyncio
async def test_the_run_prune_deletes_exactly_the_planned_versions(lock):
    mongo = FakeMongo()
    for i in range(1, 6):
        await _record(mongo, pipeline=f"p{i}", n=i)
    mongo["decision_reports"].docs = [{"test_run_id": RUN, "pipeline_run_id": "p3", "status": "published"}]
    removed = await svc.prune_run_versions(mongo, test_run_id=RUN, kind="run_summary", cap=2)
    left = sorted(d["version"] for d in mongo["ai_run_versions"].docs)
    assert left == [1, 3, 5] and removed == 2, "v1, the cited v3 and the newest v5 are protected; v2, v4 go"


# ── capture_analysis_digest ──────────────────────────────────────────────────


def _digest(verdict="NO_GO"):
    return core.assemble_digest(
        run={"run_id": RUN, "project_id": PROJECT, "build_number": "42"},
        analyses=[{"test_case_id": "t1", "test_fingerprint": "fp", "failure_category": "PRODUCT_BUG",
                   "routing_metadata": {"analysis_mode": "llm"}}],
        verdict={"recommendation": verdict}, source="stored", pipeline_run_id="p",
    )


@pytest.fixture
def capture_env(monkeypatch, lock):
    events = []

    async def fake_record(db, **kwargs):
        events.append(kwargs)

    digest_holder = {"verdict": "NO_GO"}

    async def fake_build(db, mongo, **kwargs):
        return _digest(digest_holder["verdict"]), svc._DigestContext(project_id=PROJECT, build_number="42")

    monkeypatch.setattr("app.services.activity.service.record", fake_record)
    monkeypatch.setattr(svc, "_build_digest", fake_build)
    monkeypatch.setattr(svc, "resolve_caps", AsyncMock(return_value=core.AIHistoryCaps()))
    return SimpleNamespace(events=events, digest=digest_holder, lock=lock)


@pytest.mark.asyncio
async def test_a_first_capture_emits_completed_only(capture_env):
    mongo = FakeMongo()
    result = await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=7))
    assert result.version.created and result.version.version == 1 and result.verdict_changed is False
    (event,) = capture_env.events
    assert event["event_type"] == "analysis.completed" and event["entity_label"] == "Build 42"
    assert event["actor"].actor_type == "system" and event["actor"].actor_name == "ai-pipeline"
    assert event["context"] == {"version": 1, "verdict": "NO_GO", "pipeline_run_id": str(uuid.UUID(int=7)),
                                "analysis_mode": "llm", "failing_tests": 1}


@pytest.mark.asyncio
async def test_a_changed_verdict_also_emits_verdict_changed(capture_env):
    mongo = FakeMongo()
    capture_env.digest["verdict"] = "GO"
    await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=1))
    capture_env.digest["verdict"] = "NO_GO"
    result = await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=2))
    assert (result.previous_verdict, result.verdict_changed) == ("GO", True)
    assert [e["event_type"] for e in capture_env.events] == [
        "analysis.completed", "analysis.completed", "analysis.verdict_changed",
    ]
    assert capture_env.events[-1]["context"] == {"from_verdict": "GO", "to_verdict": "NO_GO", "version": 2}


@pytest.mark.asyncio
async def test_a_same_verdict_or_a_replay_emits_no_change(capture_env):
    mongo = FakeMongo()
    await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=1))
    await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=2))
    replay = await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=2))
    assert replay.skipped_reason == "replay" and replay.version.created is False
    assert [e["event_type"] for e in capture_env.events] == ["analysis.completed", "analysis.completed"]


@pytest.mark.asyncio
async def test_a_run_deleted_during_capture_is_a_skip_with_no_event(capture_env):
    capture_env.lock["gone"] = True
    result = await svc.capture_analysis_digest(None, FakeMongo(), run_id=uuid.UUID(RUN), pipeline_run_id=uuid.UUID(int=1))
    assert (result.version, result.skipped_reason) == (None, "run_deleted")
    assert capture_env.events == []


@pytest.mark.asyncio
async def test_the_capture_pairs_the_summary_of_its_pipeline(capture_env):
    mongo = FakeMongo()
    pipeline = uuid.UUID(int=9)
    mongo["ai_run_versions"].docs.append({"test_run_id": RUN, "kind": "run_summary", "pipeline_run_id": str(pipeline),
                                           "version": 4, "payload": {"fallback_used": True}})
    seen = {}

    async def fake_build(db, mongo_arg, **kwargs):
        seen.update(kwargs)
        return _digest(), svc._DigestContext(project_id=PROJECT, build_number="42",
                                             report_ref={"report_id": "r", "report_version": 3})

    import app.services.ai_history_service as module

    module_build = module._build_digest
    module._build_digest = fake_build
    try:
        result = await svc.capture_analysis_digest(None, mongo, run_id=uuid.UUID(RUN), pipeline_run_id=pipeline)
    finally:
        module._build_digest = module_build
    assert seen["summary_fallback_used"] is True and seen["source"] == "stored"
    assert result.version.doc["meta"]["summary_version"] == 4
    assert result.version.doc["meta"]["decision_report_ref"] == {"report_id": "r", "report_version": 3}
    assert result.version.doc["trigger"] == "pipeline" and result.version.doc["pipeline_run_id"] == str(pipeline)
