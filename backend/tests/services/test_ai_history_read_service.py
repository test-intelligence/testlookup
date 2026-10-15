"""W03 G-B: the AI-history READ service (contract §5.3, §6, §7) on fakes.

What these pin (the same reads over real Postgres + Mongo are
``tests/integration/test_ai_history_routes_postgres.py``):

* every Mongo read carries ``test_run_id`` -- a version id of another run is
  "not found", never served; a malformed id is "not found" without a read;
* a Mongo fault is ``AIHistoryUnavailable`` (the routes' 503), never a 500;
* the list reads ``meta``/``provenance`` only (``payload`` is projected away),
  newest first, and resolves a backfill row's decision-report link in ONE call;
* review states are ONE query, and a pipeline with no review reads
  ``pending_review``;
* the D42 previous-run rule's predicates and its three "no baseline" reasons;
* the narrative reads only a ``run_compare_v2`` row and never calls an LLM;
* ``compare_brief`` never raises and never mixes projects;
* the AI timeline of a test (``history_ai_changes``) and the canonical lookup's
  chunking/truncation; the export bundle is pure JSON, oldest -> newest, with
  the N-1 consecutive diffs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.mongo import Collections
from app.services import ai_history_core as core
from app.services import ai_history_read_service as reads
from tests.fakes.fake_ai_history_store import FakeMongo

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_RUN = uuid.UUID("44444444-4444-4444-8444-444444444444")
BASELINE = uuid.UUID("55555555-5555-4555-8555-555555555555")
PIPE_1 = "66666666-6666-4666-8666-666666666661"
PIPE_2 = "66666666-6666-4666-8666-666666666662"
T0 = datetime(2026, 10, 14, 10, 0, 0)  # naive, as Motor returns it


# ── Builders ─────────────────────────────────────────────────────────────────


def _analysis(fp, *, category="PRODUCT_BUG", confidence=80, root_cause="Timeout in checkout", mode="llm",
              model="llama3.1", name=None, test_id=None):
    return {
        "test_case_id": test_id or str(uuid.uuid5(uuid.NAMESPACE_URL, fp)),
        "test_fingerprint": fp,
        "test_name": name or f"test_{fp}",
        "suite_name": "checkout",
        "status": "FAILED",
        "failure_category": category,
        "confidence_score": confidence,
        "root_cause_summary": root_cause,
        "is_flaky": False,
        "requires_human_review": False,
        "llm_provider": "ollama" if mode == "llm" else None,
        "llm_model": model if mode == "llm" else None,
        "routing_metadata": {"analysis_mode": mode},
    }


def _digest(run_id=RUN, analyses=(), *, verdict=None, pipeline=None, source="live"):
    return core.assemble_digest(
        run={"run_id": str(run_id), "project_id": str(PROJECT), "build_number": "100"},
        analyses=list(analyses),
        verdict=verdict,
        pipeline_run_id=pipeline,
        source=source,
    )


def _doc(digest, version, *, run_id=RUN, pipeline=None, trigger="pipeline", kind=core.KIND_DIGEST, meta=None):
    return {
        "version_id": str(uuid.uuid4()),
        "test_run_id": str(run_id),
        "project_id": str(PROJECT),
        "kind": kind,
        "version": version,
        "pipeline_run_id": pipeline,
        "trigger": trigger,
        "content_sha256": digest.get("content_sha256", "0" * 64),
        "generated_at": T0.replace(minute=version),
        "created_at": T0.replace(minute=version),
        "schema_version": 1,
        "provenance": core.provenance_from_digest(digest),
        "meta": meta if meta is not None else core.meta_from_digest(digest),
        "payload": digest,
    }


def _run(run_id=RUN, *, project=PROJECT, branch="main", suite="checkout", build="100", status="FAILED",
         end=None, created=None):
    return SimpleNamespace(
        id=run_id, project_id=project, branch=branch, primary_suite_name=suite, build_number=build,
        status=status, end_time=end, created_at=created or datetime(2026, 10, 14, 9, tzinfo=timezone.utc),
    )


class _Res:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = list(rows) if rows is not None else ([] if value is None else [value])

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows), first=lambda: self._rows[0] if self._rows else None)


class _Savepoint:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.savepoints += 1
        return self

    async def __aexit__(self, *exc):
        return False


class _DB:
    """Answers by the first rule whose needle is in the statement's SQL; records every statement."""

    def __init__(self, rules=()):
        self.rules = list(rules)
        self.statements: list = []
        self.savepoints = 0
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(stmt)
        for needle, result in self.rules:
            if needle in sql:
                return result(stmt) if callable(result) else result
        raise AssertionError(f"unexpected query: {sql[:300]}")

    def begin_nested(self):
        return _Savepoint(self)

    def sql(self):
        return [str(s) for s in self.statements]


def _store(*docs):
    mongo = FakeMongo()
    mongo[Collections.AI_RUN_VERSIONS].docs.extend(docs)
    return mongo


def _review(pipeline, state, created):
    return SimpleNamespace(id=uuid.uuid4(), pipeline_run_id=uuid.UUID(pipeline), state=state,
                           reviewed_at=created if state in ("accepted", "rejected") else None, created_at=created)


# ── Summaries and review states ──────────────────────────────────────────────


def test_a_version_summary_reads_meta_and_provenance_and_makes_instants_explicit():
    digest = _digest(analyses=[_analysis("fp1"), _analysis("fp2", category="INFRASTRUCTURE")],
                     verdict={"recommendation": "NO_GO", "risk_score": 72})
    doc = _doc(digest, 2, pipeline=PIPE_2)
    summary = reads.version_summary(doc, latest_version=2, review={"state": "pending_review", "message": "m"})
    assert summary["verdict"] == "NO_GO" and summary["risk_score"] == 72
    assert summary["failing_tests"] == 2 and summary["categories"] == {"INFRASTRUCTURE": 1, "PRODUCT_BUG": 1}
    assert summary["analysis_mode"] == "llm" and summary["llm_model"] == "llama3.1"
    assert summary["is_latest"] is True and summary["pipeline_run_id"] == PIPE_2
    assert summary["generated_at"].tzinfo is timezone.utc
    assert "payload" not in summary
    assert reads.version_summary(doc, latest_version=3)["is_latest"] is False


def test_an_older_stored_shape_never_breaks_a_summary():
    summary = reads.version_summary({"version_id": "x", "version": "2", "meta": "garbage", "provenance": None})
    assert summary["version"] == 2 and summary["verdict"] is None and summary["categories"] is None
    assert summary["decision_report_ref"] is None and summary["content_sha256"] == ""


@pytest.mark.asyncio
async def test_review_states_are_one_query_newest_wins_and_absence_reads_pending():
    newer = _review(PIPE_1, "accepted", datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    older = _review(PIPE_1, "pending_review", datetime(2026, 10, 14, 11, tzinfo=timezone.utc))
    db = _DB([("FROM review_requests", _Res(rows=[newer, older]))])
    states = await reads.review_states(db, [PIPE_1, PIPE_2, None, "not-a-uuid"])
    assert len(db.statements) == 1
    assert states[PIPE_1]["state"] == "accepted"
    assert states[PIPE_2]["state"] == "pending_review" and states[PIPE_2]["review_id"] is None
    assert set(states) == {PIPE_1, PIPE_2}


@pytest.mark.asyncio
async def test_review_states_without_a_pipeline_run_no_query():
    db = _DB()
    assert await reads.review_states(db, [None, ""]) == {}
    assert db.statements == []


# ── get / list (R1, R4) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_version_of_another_run_is_not_found_because_every_read_carries_the_run():
    mine = _doc(_digest(analyses=[_analysis("fp1")]), 1)
    theirs = _doc(_digest(OTHER_RUN, [_analysis("fp1")]), 1, run_id=OTHER_RUN)
    mongo = _store(mine, theirs)
    assert (await reads.get_version(None, mongo, run_id=RUN, version_id=mine["version_id"]))["version"] == 1
    with pytest.raises(reads.AIVersionNotFound) as exc:
        await reads.get_version(None, mongo, run_id=RUN, version_id=theirs["version_id"])
    assert exc.value.detail == "AI version not found"
    assert all(q.get("test_run_id") == str(RUN) for _c, _op, q in mongo.reads)


@pytest.mark.asyncio
async def test_a_malformed_version_id_is_not_found_without_a_read():
    mongo = _store()
    with pytest.raises(reads.AIVersionNotFound):
        await reads.get_version(None, mongo, run_id=RUN, version_id="v1'; drop")
    assert mongo.reads == []


@pytest.mark.asyncio
async def test_a_mongo_fault_is_unavailable_not_a_crash():
    mongo = _store()
    mongo.fail = True
    db = _DB([("FROM review_requests", _Res(rows=[]))])
    with pytest.raises(reads.AIHistoryUnavailable):
        await reads.list_versions(db, mongo, run=_run(), kind=core.KIND_DIGEST, limit=50)
    with pytest.raises(reads.AIHistoryUnavailable):
        await reads.get_version(db, mongo, run_id=RUN, version_id=str(uuid.uuid4()))
    with pytest.raises(reads.AIHistoryUnavailable):
        await reads.get_version(db, None, run_id=RUN, version_id=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_the_list_is_newest_first_never_reads_a_payload_and_links_backfill_rows_in_one_call():
    d1 = _digest(analyses=[_analysis("fp1", mode="rules")])
    d2 = _digest(analyses=[_analysis("fp1")], verdict={"recommendation": "GO"})
    v1 = _doc(d1, 1, pipeline=PIPE_1, trigger="backfill")
    v2 = _doc(d2, 2, pipeline=PIPE_2,
              meta={**core.meta_from_digest(d2), "decision_report_ref": {"report_id": "r2", "report_version": 4}})
    mongo = _store(v1, v2, _doc(d1, 1, run_id=OTHER_RUN))
    reports = mongo[Collections.DECISION_REPORTS].docs
    reports.extend([
        {"test_run_id": str(RUN), "status": "published", "report_id": "r1a", "report_version": 1,
         "pipeline_run_id": PIPE_1},
        {"test_run_id": str(RUN), "status": "published", "report_id": "r1b", "report_version": 3,
         "pipeline_run_id": PIPE_1},
    ])
    db = _DB([
        ("FROM review_requests", _Res(rows=[_review(PIPE_2, "accepted", datetime(2026, 10, 14, tzinfo=timezone.utc))])),
        ("FROM ai_analysis JOIN test_cases", _Res(value=uuid.uuid4())),
    ])
    body = await reads.list_versions(db, mongo, run=_run(), kind=core.KIND_DIGEST, limit=1)
    assert body["total"] == 2 and body["truncated"] is True and body["latest_version"] == 2
    assert [v["version"] for v in body["versions"]] == [2]
    assert body["live"] == {"available": True}
    assert body["versions"][0]["review"]["state"] == "accepted"
    assert body["versions"][0]["decision_report_ref"] == {"report_id": "r2", "report_version": 4}

    full = await reads.list_versions(db, mongo, run=_run(), kind=core.KIND_DIGEST, limit=100)
    assert [v["version"] for v in full["versions"]] == [2, 1]
    assert full["versions"][1]["decision_report_ref"] == {"report_id": "r1b", "report_version": 3}
    assert full["versions"][1]["trigger"] == "backfill" and full["versions"][1]["analysis_mode"] == "rules"
    finds = [q for c, op, q in mongo.reads if c == Collections.DECISION_REPORTS]
    assert len(finds) == 1, "the backfill links resolve in ONE decision-report read"


@pytest.mark.asyncio
async def test_an_empty_store_is_an_empty_list_not_an_error():
    db = _DB([("FROM review_requests", _Res(rows=[])), ("FROM ai_analysis JOIN test_cases", _Res(value=None))])
    body = await reads.list_versions(db, _store(), run=_run(), kind=core.KIND_DIGEST, limit=50)
    assert body["versions"] == [] and body["latest_version"] is None and body["total"] == 0
    assert body["live"] == {"available": False}


# ── Diff (R2) ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_diff_of_two_versions_names_the_change(monkeypatch):
    d1 = _digest(analyses=[_analysis("fp1", category="INFRASTRUCTURE", confidence=40, mode="rules")],
                 verdict={"recommendation": "CONDITIONAL_GO", "risk_score": 55})
    d2 = _digest(analyses=[_analysis("fp1", category="PRODUCT_BUG", confidence=85)],
                 verdict={"recommendation": "NO_GO", "risk_score": 72})
    v1, v2 = _doc(d1, 1, pipeline=PIPE_1), _doc(d2, 2, pipeline=PIPE_2)
    mongo = _store(v1, v2)
    body = await reads.diff_versions(None, mongo, run=_run(), from_id=v1["version_id"], to_ref="latest")
    assert body["from"]["version"] == 1 and body["to"]["version"] == 2 and body["to"]["source"] == "stored"
    diff = body["diff"]
    assert diff["mode"] == "versions" and diff["verdict"]["direction"] == "worse"
    assert diff["provenance"]["changed"] is True
    assert diff["tests"]["changed"][0]["category"] == {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"}

    same = await reads.diff_versions(None, mongo, run=_run(), from_id=v2["version_id"], to_ref=v2["version_id"])
    assert same["diff"]["identical"] is True and same["diff"]["has_changes"] is False

    monkeypatch.setattr(reads, "build_digest", AsyncMock(return_value=d2))
    live = await reads.diff_versions(None, mongo, run=_run(), from_id=v1["version_id"], to_ref="live")
    assert live["to"]["ref"] == "live" and live["to"]["source"] == "live" and live["to"]["version"] is None


@pytest.mark.asyncio
async def test_from_must_be_a_digest_of_this_run_and_latest_needs_a_stored_version():
    summary = _doc({}, 1, kind=core.KIND_SUMMARY)
    mongo = _store(summary)
    with pytest.raises(reads.AIVersionNotFound):
        await reads.diff_versions(None, mongo, run=_run(), from_id=summary["version_id"], to_ref="latest")
    with pytest.raises(reads.AIHistoryEmpty) as exc:
        await reads.resolve_digest_ref(None, _store(), run=_run(), ref="latest")
    assert exc.value.detail == "AI history is empty for this run"


# ── Previous run (D42) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_suite_means_no_baseline_and_no_query():
    db = _DB()
    prev = await reads.resolve_previous_run(db, run=_run(suite=None))
    assert (prev.baseline, prev.reason) == (None, "no_primary_suite")
    assert prev.message == reads.NO_SUITE_MESSAGE and db.statements == []


@pytest.mark.asyncio
async def test_a_suite_the_run_lacks_is_its_own_reason(monkeypatch):
    monkeypatch.setattr(reads, "ensure_run_has_suite", AsyncMock(return_value=False))
    prev = await reads.resolve_previous_run(_DB(), run=_run(), suite_name="payments")
    assert prev.reason == "run_lacks_suite" and "payments" in prev.message


@pytest.mark.asyncio
async def test_the_previous_run_query_is_the_d42_rule():
    found = _run(BASELINE)
    db = _DB([("FROM test_runs", _Res(value=found))])
    prev = await reads.resolve_previous_run(db, run=_run(branch=None))
    assert prev.baseline is found and prev.reason is None and prev.suite_name == "checkout"
    sql = db.sql()[0]
    assert "test_runs.project_id = " in sql and "test_runs.id != " in sql
    assert "test_runs.status != " in sql
    assert "test_runs.branch IS NULL" in sql
    assert "lower(trim(test_runs.primary_suite_name))" in sql and "EXISTS" in sql
    assert "coalesce(test_runs.end_time, test_runs.created_at) <" in sql
    assert "ORDER BY coalesce(test_runs.end_time, test_runs.created_at) DESC, test_runs.id" in sql

    db2 = _DB([("FROM test_runs", _Res(value=None))])
    nothing = await reads.resolve_previous_run(db2, run=_run(branch="main"))
    assert nothing.reason == "no_previous_run"
    assert nothing.message == 'No earlier completed run of "checkout" on branch "main" found.'
    assert "test_runs.branch = " in db2.sql()[0]


@pytest.mark.asyncio
async def test_the_previous_run_body_names_the_rule_and_both_runs(monkeypatch):
    found = _run(BASELINE, build="99")
    monkeypatch.setattr("app.services.runs_service.fetch_run_seq_map",
                        AsyncMock(return_value={str(RUN): 12, str(BASELINE): 11}))
    body = await reads.previous_run_body(_DB([("FROM test_runs", _Res(value=found))]), run=_run())
    assert body["baseline"]["run_seq"] == 11 and body["current"]["run_seq"] == 12
    assert body["rule"]["scope"] == "project+primary_suite+branch" and body["rule"]["branch"] == "main"
    assert body["baseline"]["finished_at"] == found.created_at


# ── Narrative (D43) ──────────────────────────────────────────────────────────


def _diff_with_changes():
    a = _digest(BASELINE, [_analysis("fp1", category="INFRASTRUCTURE", confidence=40)],
                verdict={"recommendation": "GO", "risk_score": 35})
    b = _digest(RUN, [_analysis("fp1", category="PRODUCT_BUG", confidence=85)],
                verdict={"recommendation": "NO_GO", "risk_score": 72})
    return core.diff_digests(a, b, mode="cross_run")


@pytest.mark.asyncio
async def test_the_narrative_reads_only_a_v2_row_and_labels_its_source():
    diff = _diff_with_changes()
    llm_row = SimpleNamespace(status="ready", fallback_used=False, model_name="llama3.1",
                              updated_at=datetime(2026, 10, 14, tzinfo=timezone.utc), created_at=None,
                              ai_report={"executive_summary": "The gateway regressed.", "risk_level": "HIGH",
                                         "ai_changes": ["Verdict GO -> NO_GO (risk 35 -> 72)"], "fallback_used": False})
    db = _DB([("FROM run_comparison_reports", _Res(value=llm_row))])
    ready = await reads.narrative_for(db, project_id=PROJECT, baseline_id=BASELINE, run_id=RUN,
                                      suite_name="Checkout ", diff=diff)
    assert (ready["status"], ready["source"], ready["label"]) == ("ready", "llm", "AI narrative")
    assert ready["ai_changes"] == ["Verdict GO -> NO_GO (risk 35 -> 72)"] and ready["model_name"] == "llama3.1"
    compiled = db.statements[0].compile()
    assert "run_compare_v2" in compiled.params.values(), "a v1 row must never be served as v2"
    assert "checkout" in compiled.params.values(), "the suite key is normalised"

    fallback_row = SimpleNamespace(**{**llm_row.__dict__, "fallback_used": True,
                                      "ai_report": {**llm_row.ai_report, "fallback_used": True}})
    fb = await reads.narrative_for(_DB([("FROM run_comparison_reports", _Res(value=fallback_row))]),
                                   project_id=PROJECT, baseline_id=BASELINE, run_id=RUN, suite_name=None, diff=diff)
    assert (fb["source"], fb["label"], fb["fallback_used"]) == ("deterministic", "deterministic summary", True)

    queued = SimpleNamespace(status="queued", ai_report=None, fallback_used=False)
    q = await reads.narrative_for(_DB([("FROM run_comparison_reports", _Res(value=queued))]),
                                  project_id=PROJECT, baseline_id=BASELINE, run_id=RUN, suite_name=None, diff=diff)
    assert (q["status"], q["report_status"], q["label"]) == ("deterministic", "queued", "deterministic summary")
    assert q["ai_changes"] == core.build_ai_changes(diff)
    assert q["executive_summary"].startswith("Compared with the baseline run: verdict GO -> NO_GO")

    absent = await reads.narrative_for(_DB([("FROM run_comparison_reports", _Res(value=None))]),
                                       project_id=PROJECT, baseline_id=BASELINE, run_id=RUN, suite_name=None,
                                       diff=diff)
    assert absent["report_status"] == "absent" and absent["generated_at"] is None


def test_the_deterministic_sentence_says_when_nothing_changed():
    same = core.diff_digests(_digest(), _digest(), mode="cross_run")
    assert reads.deterministic_sentence(same) == "No AI analysis changes since the baseline run."
    assert reads.deterministic_sentence(_diff_with_changes()).isascii()


# ── compare_brief (the Run Compare payload) ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_brief_never_mixes_projects_and_never_raises(monkeypatch):
    builder = AsyncMock()
    monkeypatch.setattr(reads, "build_digest", builder)
    other = _run(BASELINE, project=OTHER_PROJECT)
    assert await reads.compare_brief(_DB(), None, baseline=other, run=_run()) == {
        "available": False, "reason": "different_projects",
    }
    builder.assert_not_awaited()

    builder.side_effect = RuntimeError("relation does not exist")
    db = _DB()
    assert await reads.compare_brief(db, None, baseline=_run(BASELINE), run=_run()) == {
        "available": False, "reason": "ai_history_unavailable",
    }
    assert db.savepoints == 1, "the digests are read inside a SAVEPOINT"


@pytest.mark.asyncio
async def test_the_brief_says_which_side_was_not_analysed_and_carries_the_ai_changes(monkeypatch):
    analysed = _digest(RUN, [_analysis("fp1")], verdict={"recommendation": "NO_GO", "risk_score": 72})
    empty = _digest(BASELINE)

    async def build(db, mongo, *, run_id, **_):
        return {RUN: analysed, BASELINE: empty}[run_id]

    monkeypatch.setattr(reads, "build_digest", build)
    assert (await reads.compare_brief(_DB(), None, baseline=_run(BASELINE), run=_run()))["reason"] == (
        "baseline_not_analysed"
    )
    baseline = _digest(BASELINE, [_analysis("fp1", category="INFRASTRUCTURE", confidence=40)],
                       verdict={"recommendation": "GO", "risk_score": 35})

    async def build2(db, mongo, *, run_id, **_):
        return {RUN: analysed, BASELINE: baseline}[run_id]

    monkeypatch.setattr(reads, "build_digest", build2)
    brief = await reads.compare_brief(_DB(), None, baseline=_run(BASELINE), run=_run())
    assert brief["available"] is True and brief["left_ref"] == "live" and brief["right_ref"] == "live"
    assert brief["verdict"]["from"] == "GO" and brief["verdict"]["to"] == "NO_GO"
    assert brief["ai_changes"][0].startswith("Verdict GO -> NO_GO")


# ── compare_runs_ai (R7) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_explicit_baseline_must_be_a_run_of_the_same_project():
    db = _DB([("FROM test_runs", _Res(value=None))])
    with pytest.raises(reads.BaselineRunNotFound):
        await reads.compare_runs_ai(db, _store(), run=_run(), baseline_run_id=OTHER_RUN, suite_name=None)
    compiled = db.statements[0].compile()
    assert PROJECT in compiled.params.values(), "the baseline is loaded WITH the run's project"


@pytest.mark.asyncio
async def test_no_baseline_is_an_answer_with_no_diff(monkeypatch):
    db = _DB([("FROM test_runs", _Res(value=None))])
    body = await reads.compare_runs_ai(db, _store(), run=_run(), baseline_run_id=None, suite_name=None)
    assert body["reason"] == "no_previous_run" and body["diff"] is None and body["narrative"] is None
    assert body["sides"] is None and body["baseline"] is None


@pytest.mark.asyncio
async def test_a_pinned_version_of_another_run_is_not_found(monkeypatch):
    theirs = _doc(_digest(OTHER_RUN, [_analysis("fp1")]), 1, run_id=OTHER_RUN)
    db = _DB([("FROM test_runs", _Res(value=_run(BASELINE)))])
    with pytest.raises(reads.AIVersionNotFound):
        await reads.compare_runs_ai(db, _store(theirs), run=_run(), baseline_run_id=BASELINE, suite_name=None,
                                    version_id=theirs["version_id"])
    with pytest.raises(reads.AIVersionNotFound):
        await reads.compare_runs_ai(db, _store(theirs), run=_run(), baseline_run_id=BASELINE, suite_name=None,
                                    baseline_version_id=theirs["version_id"])


@pytest.mark.asyncio
async def test_live_against_live_compares_and_labels_the_narrative(monkeypatch):
    current = _digest(RUN, [_analysis("fp1", category="PRODUCT_BUG", confidence=85)],
                      verdict={"recommendation": "NO_GO", "risk_score": 72})
    baseline = _digest(BASELINE, [_analysis("fp1", category="INFRASTRUCTURE", confidence=40, mode="rules")],
                       verdict={"recommendation": "GO", "risk_score": 35})

    async def build(db, mongo, *, run_id, **_):
        return {RUN: current, BASELINE: baseline}[run_id]

    monkeypatch.setattr(reads, "build_digest", build)
    monkeypatch.setattr("app.services.runs_service.fetch_run_seq_map", AsyncMock(return_value={str(BASELINE): 7}))
    db = _DB([
        ("FROM run_comparison_reports", _Res(value=None)),
        ("FROM test_runs", _Res(value=_run(BASELINE, branch="release"))),
    ])
    body = await reads.compare_runs_ai(db, _store(), run=_run(), baseline_run_id=BASELINE, suite_name=None)
    assert body["baseline"]["selection"] == "explicit" and body["baseline"]["branch_mismatch"] is True
    assert body["current_ref"]["source"] == "live" and body["baseline_ref"]["run_id"] == str(BASELINE)
    assert body["sides"]["current"]["verdict"] == "NO_GO" and body["sides"]["baseline"]["generated_at"] is None
    assert body["diff"]["mode"] == "cross_run" and body["diff"]["verdict"]["direction"] == "worse"
    assert body["diff"]["provenance"]["note"].startswith("Baseline analysed by rules")
    assert body["narrative"]["label"] == "deterministic summary"
    assert body["reason"] is None


@pytest.mark.asyncio
async def test_an_unanalysed_baseline_is_named(monkeypatch):
    current = _digest(RUN, [_analysis("fp1")])

    async def build(db, mongo, *, run_id, **_):
        return current if run_id == RUN else _digest(BASELINE)

    monkeypatch.setattr(reads, "build_digest", build)
    monkeypatch.setattr("app.services.runs_service.fetch_run_seq_map", AsyncMock(return_value={str(BASELINE): 7}))
    db = _DB([("FROM test_runs", _Res(value=_run(BASELINE)))])
    body = await reads.compare_runs_ai(db, _store(), run=_run(), baseline_run_id=None, suite_name=None)
    assert body["reason"] == "baseline_not_analysed" and body["message"] == "#7 has no AI analysis to compare."
    assert body["diff"] is None and body["sides"]["current"]["analysed_tests"] == 1
    assert body["baseline"]["selection"] == "default"


# ── AI in test history (AIH-05) ──────────────────────────────────────────────


def _point(run, label, ai):
    return {"run_id": run, "run_label": label, "ai": ai}


def test_the_ai_changes_walk_oldest_to_newest_and_say_what_changed():
    infra = {"category": "INFRASTRUCTURE", "confidence": 40, "root_cause_sha": "a" * 16}
    bug = {"category": "PRODUCT_BUG", "confidence": 85, "root_cause_sha": "b" * 16}
    reworded = {**bug, "root_cause_sha": "c" * 16}
    points = [  # newest first, as the timeline serves them
        _point("r4", "Run #4", reworded),
        _point("r3", "Run #3", None),  # a passed run in between
        _point("r2", "Run #2", bug),
        _point("r1", "Run #1", infra),
    ]
    changes = reads.history_ai_changes(points)
    assert [c["run_label"] for c in changes] == ["Run #2", "Run #4"]
    assert changes[0]["kinds"] == ["category", "root_cause"]
    assert (changes[0]["from_category"], changes[0]["to_category"]) == ("INFRASTRUCTURE", "PRODUCT_BUG")
    assert (changes[0]["from_confidence"], changes[0]["to_confidence"]) == (40, 85)
    assert changes[1]["kinds"] == ["root_cause"]
    assert reads.history_ai_changes(points[:2]) == []
    assert reads.history_ai_changes([]) == []


@pytest.mark.asyncio
async def test_the_test_history_lookup_is_two_keyed_queries_and_none_without_ids():
    db = _DB()
    assert await reads.test_history_ai(db, [None]) == {}
    assert db.statements == []

    case = uuid.uuid4()
    row = SimpleNamespace(test_case_id=case, failure_category="PRODUCT_BUG", confidence_score=85,
                          root_cause_summary="  <img src=x onerror=alert(1)>\n timed   out ",
                          is_flaky=False, requires_human_review=True, llm_model="llama3.1", analysis_mode="llm")
    db = _DB([
        ("FROM ai_analysis_versions", _Res(rows=[(case, 3)])),
        ("FROM ai_analysis", _Res(rows=[row])),
    ])
    ai = (await reads.test_history_ai(db, [case, case]))[case]
    assert len(db.statements) == 2
    assert ai == {
        "test_id": str(case), "category": "PRODUCT_BUG", "confidence": 85,
        "root_cause_excerpt": "<img src=x onerror=alert(1)> timed out",
        "root_cause_sha": core.root_cause_hash(row.root_cause_summary),
        "analysis_mode": "llm", "model": "llama3.1", "is_flaky": False, "requires_human_review": True,
        "version_count": 3,
    }
    assert "ai_analysis.test_case_id IN" in db.sql()[0]


@pytest.mark.asyncio
async def test_the_canonical_lookup_reads_the_newest_2000_in_chunks_of_500(monkeypatch):
    calls = []

    async def lookup(db, ids):
        calls.append(len(ids))
        return {i: {"category": "PRODUCT_BUG", "test_id": str(i), "root_cause_sha": "x"} for i in ids}

    monkeypatch.setattr(reads, "test_history_ai", lookup)
    ids = [uuid.uuid4() for _ in range(2101)]
    ai, truncated = await reads.canonical_runs_ai(None, ids)
    assert calls == [500, 500, 500, 500] and truncated is True
    assert ids[0] in ai and ids[2000] not in ai
    assert set(ai[ids[0]]) == set(reads._CANONICAL_AI_KEYS), "no test_id / hash on the canonical page"
    assert (await reads.canonical_runs_ai(None, ids[:3]))[1] is False


# ── Per-test versions (R5) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_test_of_another_run_has_no_versions():
    db = _DB([("FROM test_cases", _Res(rows=[]))])
    assert await reads.list_test_versions(db, run_id=RUN, test_id=uuid.uuid4()) is None
    assert len(db.statements) == 1 and "test_cases.test_run_id = " in db.sql()[0]


# ── Export bundle ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_export_bundle_is_pure_json_oldest_first_with_the_consecutive_diffs(monkeypatch):
    d1 = _digest(analyses=[_analysis("fp1", mode="rules")])
    d2 = _digest(analyses=[_analysis("fp1", category="INFRASTRUCTURE")])
    d3 = _digest(analyses=[_analysis("fp1", category="INFRASTRUCTURE"), _analysis("fp2")])
    summary = {"executive_summary": "Seven failures.", "fallback_used": False, "analysis_count": 1}
    mongo = _store(
        _doc(d3, 3, pipeline=PIPE_2), _doc(d1, 1, pipeline=PIPE_1), _doc(d2, 2, pipeline=PIPE_1),
        {**_doc({}, 1, kind=core.KIND_SUMMARY, pipeline=PIPE_1, meta=core.meta_from_summary(summary)),
         "payload": summary},
    )
    actor = uuid.uuid4()
    version_row = SimpleNamespace(
        id=uuid.uuid4(), version=2, trigger="human_correction", pipeline_run_id=None, actor_user_id=actor,
        failure_category="PRODUCT_BUG", confidence_score=90, analysis_mode="llm", llm_provider="ollama",
        llm_model="llama3.1", is_flaky=False, requires_human_review=False, root_cause_summary="Gateway timeout",
        content_sha256="f" * 64, created_at=datetime(2026, 10, 14, tzinfo=timezone.utc), test_fingerprint="fp1",
    )
    db = _DB([
        ("FROM review_requests", _Res(rows=[])),
        ("count(*)", _Res(value=1)),
        ("FROM ai_analysis_versions JOIN test_cases", _Res(rows=[(version_row, "test_pay")])),
        ("FROM users", _Res(rows=[SimpleNamespace(id=actor, full_name=None, username="anna")])),
    ])
    bundle = await reads.export_bundle(db, mongo, run=_run(), max_versions=100, max_test_rows=1000)
    json.dumps(bundle)  # pure JSON: no default= needed
    assert bundle["report_type"] == "ai_history" and bundle["schema_version"] == 1
    assert [v["version"] for v in bundle["versions"]] == [1, 2, 3]
    assert bundle["versions"][-1]["is_latest"] is True and bundle["versions"][0]["payload"]["kind"] == core.KIND_DIGEST
    assert [(d["from_version"], d["to_version"]) for d in bundle["diffs"]] == [(1, 2), (2, 3)]
    assert bundle["diffs"][1]["diff"]["tests"]["counts"]["added"] == 1
    assert [s["version"] for s in bundle["run_summaries"]] == [1]
    item = bundle["per_test_versions"]["items"][0]
    assert item["actor"] == {"user_id": str(actor), "name": "anna"} and item["test_name"] == "test_pay"
    assert bundle["per_test_versions"] == {**bundle["per_test_versions"], "total": 1, "truncated": False}
    assert bundle["requires_human_review"] is True and bundle["review"]["state"] == "pending_review"
    assert bundle["exported_at"].endswith("+00:00")
    assert mongo.writes == [] and db.commit.await_count == 0 and not db.add.called
