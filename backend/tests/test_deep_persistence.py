"""AI-F4 — DeepFinding reconciliation: the deep pipeline persists real
findings, seeds are tagged, and the findings endpoint can never again serve
seed-only fiction undetected.

Covers:
  * synthesis — per-cluster folding of member-test analyses (category
    majority, mean confidence, honest Nones for fields no agent computes);
  * writer idempotency — re-running persist for the same run updates the
    existing (test_run_id, cluster_id) rows instead of duplicating them;
  * origin tagging — pipeline rows carry log_evidence.origin="pipeline",
    both seed scripts tag "seed", and the endpoint surfaces the tag;
  * wiring — the deep workflow entry points actually call the persister
    (the whole bug was computed-but-never-persisted).

DB access is fully mocked; no live Postgres needed.
"""
from __future__ import annotations

import re
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.deep_persistence import (
    ORIGIN_PIPELINE,
    persist_deep_results,
    synthesize_deep_findings,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ── Fixtures ───────────────────────────────────────────────────────────────

T1, T2, T3 = (str(uuid.uuid4()) for _ in range(3))


def _final_state() -> dict:
    return {
        "failure_clusters": [
            {
                "cluster_id": "cl_001",
                "label": "Connection refused",
                "member_test_ids": [T1, T2],
                "representative_error": "ECONNREFUSED db:5432",
                "size": 2,
                "cohesion_score": 0.9,
            },
            {
                "cluster_id": "cl_002",
                "label": "Assertion drift",
                "member_test_ids": [T3],
                "representative_error": "expected 5 but was 3",
                "size": 1,
            },
        ],
        "analyses": {
            T1: {
                "root_cause_summary": "Upstream DB unreachable",
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 70,
                "evidence_references": [{"source": "stacktrace", "excerpt": "ECONNREFUSED"}],
                "recommended_actions": ["Check upstream service health"],
                "confidence_basis": "heuristic_estimate",
            },
            T2: {
                "root_cause_summary": "Connection reset mid-request",
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 60,
                "evidence_references": [],
                "recommended_actions": ["Check upstream service health", "Review deploys"],
                "confidence_basis": "heuristic_estimate",
            },
            T3: {
                "root_cause_summary": "Pagination off-by-one",
                "failure_category": "PRODUCT_BUG",
                "confidence_score": 55,
                "evidence_references": [],
                "recommended_actions": ["Review recent commits"],
                "confidence_basis": "heuristic_estimate",
            },
        },
    }


# ── Synthesis (pure) ───────────────────────────────────────────────────────


def test_synthesize_folds_member_analyses_per_cluster():
    findings = synthesize_deep_findings(_final_state())
    assert set(findings) == {"cl_001", "cl_002"}

    f1 = findings["cl_001"]
    assert f1["failure_category"] == "INFRASTRUCTURE"       # majority
    assert f1["confidence_score"] == 65                     # mean(70, 60)
    assert f1["root_cause"] == "Upstream DB unreachable"    # highest-confidence member
    assert f1["evidence"] == [{"source": "stacktrace", "excerpt": "ECONNREFUSED"}]
    assert f1["recommended_actions"] == [
        "Check upstream service health", "Review deploys",
    ]
    # Honest Nones — nothing in the deep graph computes these today.
    assert f1["causal_chain"] is None
    assert f1["affected_services"] is None
    assert f1["contract_violations"] is None
    # Origin + basis provenance
    assert f1["log_evidence"]["origin"] == ORIGIN_PIPELINE
    assert f1["log_evidence"]["confidence_basis"] == "heuristic_estimate"
    assert f1["log_evidence"]["member_count"] == 2


def test_synthesize_skips_error_only_members_and_empty_state():
    state = _final_state()
    state["analyses"][T3] = {"error": "boom", "confidence_score": 0}
    findings = synthesize_deep_findings(state)
    f2 = findings["cl_002"]
    # No usable member analyses → falls back to the representative error.
    assert f2["failure_category"] is None
    assert f2["confidence_score"] is None
    assert f2["root_cause"] == "expected 5 but was 3"
    assert f2["log_evidence"]["member_count"] == 0

    assert synthesize_deep_findings({}) == {}
    assert synthesize_deep_findings({"failure_clusters": [], "analyses": {}}) == {}


# ── Writer idempotency ─────────────────────────────────────────────────────


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _ScalarResult:
    """VIZ-212: the TestRun.project_id lookup ``persist_failure_cluster_snapshot``
    makes after its commit, to bump the right project's analytics epoch."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _fake_db(cluster_rows, finding_rows, *, project_id=None):
    db = AsyncMock()
    db.added = []
    db.add = lambda obj: db.added.append(obj)  # sync, like the real session
    db.execute = AsyncMock(
        side_effect=[
            _ScalarsResult([uuid.UUID(T1), uuid.UUID(T2), uuid.UUID(T3)]),
            _ScalarsResult(cluster_rows),
            _ScalarResult(project_id or uuid.uuid4()),
            _ScalarsResult(cluster_rows),
            _ScalarsResult(finding_rows),
        ]
    )
    db.commit = AsyncMock()
    return db


def _session_local(db):
    class _CM:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    return lambda: _CM()


@pytest.mark.asyncio
async def test_persist_inserts_on_first_run(monkeypatch):
    from app.agents import deep_persistence as dp

    db = _fake_db([], [])
    monkeypatch.setattr(dp, "AsyncSessionLocal", _session_local(db))

    run_id = str(uuid.uuid4())
    findings = await persist_deep_results(run_id, str(uuid.uuid4()), _final_state())

    assert set(findings) == {"cl_001", "cl_002"}
    # 2 clusters + 2 findings inserted
    assert len(db.added) == 4
    assert db.commit.await_count == 2
    finding_rows = [r for r in db.added if getattr(r, "root_cause", None) is not None]
    assert all(r.log_evidence["origin"] == ORIGIN_PIPELINE for r in finding_rows)


@pytest.mark.asyncio
async def test_persist_is_idempotent_per_run_and_cluster(monkeypatch):
    """An identical cluster snapshot is reused while findings stay mutable."""
    from app.agents import deep_persistence as dp

    pipeline_id = uuid.uuid4()
    existing_clusters = [
        SimpleNamespace(
            cluster_id="cl_001", label="Connection refused",
            representative_error="ECONNREFUSED db:5432",
            member_test_ids=sorted([T1, T2]), size=2,
            cohesion_score=0.9, pipeline_run_id=pipeline_id,
        ),
        SimpleNamespace(
            cluster_id="cl_002", label="Assertion drift",
            representative_error="expected 5 but was 3",
            member_test_ids=[T3], size=1,
            cohesion_score=None, pipeline_run_id=pipeline_id,
        ),
    ]
    stale_seed_finding = SimpleNamespace(
        cluster_id="cl_001", root_cause="seeded fiction", failure_category="FLAKY",
        confidence_score=99, causal_chain=[{"step": 1}], evidence=None,
        affected_services=["fake-svc"], contract_violations=None,
        recommended_actions=None, log_evidence={"origin": "seed"},
    )
    db = _fake_db(existing_clusters, [stale_seed_finding])
    monkeypatch.setattr(dp, "AsyncSessionLocal", _session_local(db))

    await persist_deep_results(
        str(uuid.uuid4()), str(pipeline_id), _final_state()
    )

    # The immutable clusters are not rewritten. The stale cl_001 finding is
    # refreshed in place and only the missing cl_002 finding is inserted.
    assert len(db.added) == 1
    assert stale_seed_finding.root_cause == "Upstream DB unreachable"
    assert stale_seed_finding.confidence_score == 65
    assert stale_seed_finding.log_evidence["origin"] == ORIGIN_PIPELINE


@pytest.mark.asyncio
async def test_persist_noops_without_clusters(monkeypatch):
    from app.agents import deep_persistence as dp

    called = False

    def _boom():
        nonlocal called
        called = True
        raise AssertionError("no session should be opened for an empty state")

    monkeypatch.setattr(dp, "AsyncSessionLocal", _boom)
    assert await persist_deep_results(str(uuid.uuid4()), None, {}) == {}
    assert called is False


# ── Endpoint origin surfacing ──────────────────────────────────────────────


def _finding_row(log_evidence):
    return SimpleNamespace(
        cluster_id="cl_001", root_cause="x", failure_category="PRODUCT_BUG",
        confidence_score=70, causal_chain=None, evidence=None,
        affected_services=None, contract_violations=None,
        recommended_actions=None, log_evidence=log_evidence,
    )


def test_endpoint_flags_seed_pipeline_and_legacy_rows(monkeypatch):
    worker_tasks = types.ModuleType("app.worker.tasks")
    worker_tasks.run_agent_pipeline = SimpleNamespace(delay=None)
    monkeypatch.setitem(sys.modules, "app.worker.tasks", worker_tasks)
    from app.routers.deep_investigation import _to_finding_response

    assert _to_finding_response(_finding_row({"origin": "seed"})).origin == "seed"
    pipeline = _to_finding_response(
        _finding_row({"origin": "pipeline", "confidence_basis": "heuristic_estimate"})
    )
    assert pipeline.origin == "pipeline"
    assert pipeline.confidence_basis == "heuristic_estimate"
    # Legacy rows (written before tagging) are visibly "unknown", never
    # silently passed off as pipeline output.
    assert _to_finding_response(_finding_row(None)).origin == "unknown"


# ── No-seed-only-data guard ────────────────────────────────────────────────


def test_seed_scripts_tag_their_deep_finding_rows():
    """If a seed script writes deep_findings rows without the origin tag,
    the endpoint would report them as 'unknown' instead of 'seed' — fail
    loudly here instead."""
    for script in ("seed_data.py", "seed_dev_data.py"):
        src = (BACKEND_ROOT / "scripts" / script).read_text(encoding="utf-8")
        writes_findings = "deep_findings" in src or "DeepFinding(" in src
        assert writes_findings, f"{script}: expected a deep_findings writer"
        assert re.search(r"[\"']origin[\"']\s*:\s*[\"']seed[\"']", src), (
            f"{script} writes deep_findings rows without log_evidence "
            "origin='seed' — the findings endpoint could serve untagged "
            "seed fiction as real data"
        )


def test_deep_workflow_wires_the_persister():
    """The original AI-F4 bug: findings-shaped output was computed but never
    persisted, so /findings served seed-only data. Pin the wiring."""
    src = (BACKEND_ROOT / "app" / "agents" / "workflow.py").read_text(encoding="utf-8")
    # Helper exists and both deep completion paths call it.
    assert "persist_deep_results" in src
    assert src.count("_persist_deep_outputs(") >= 3  # def + 2 call sites
