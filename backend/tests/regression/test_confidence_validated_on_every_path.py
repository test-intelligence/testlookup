"""Regression: every path that publishes a confidence must apply the caps.

Measured on the homelab, 2026-08-16, one test case, one body of evidence:

    pipeline-stored : confidence=50  evidence=0  requires_human_review=True
    POST /analyze   : confidence=95  evidence=0  requires_human_review=False
    stored AFTER    : confidence=95  evidence=0  requires_human_review=False

``POST /api/v1/analyze`` upserts the same ``AIAnalysis`` row, so the third line
is the damage: opening a finding in the UI rewrote the persisted figure, cleared
its human-review flag and moved it above the confidence gate — with no new
evidence and no re-run of anything that could have earned the higher number.

Cause: the caps lived as ``AnalysisAgent._validate_confidence``, reachable only
from the batch pipeline. The router called the same ``classify_test`` and
persisted the engine's raw self-assessment. This is the class this repo keeps
finding — a validation stage that exists but that one of its callers cannot
reach — so these guards are about the CLASS, not the endpoint:

  1. the rules live somewhere every caller can reach, and behave;
  2. the router applies them before persisting;
  3. applying them twice does not inflate a score.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.confidence_validation import validate_confidence

# The engine's raw output in the live repro: a confident claim with nothing
# backing it. Deliberately not a fixture — every test here starts from a fresh
# dict because validate_confidence mutates in place.
def _raw(**over):
    base = {
        "root_cause_summary": (
            "Database connection refused — the service could not reach "
            "db.internal:5432 during the run."
        ),
        "failure_category": "INFRASTRUCTURE",
        "confidence_score": 95,
        "evidence_references": [],
        "tools_used": [],
        "is_flaky": False,
        "backend_error_found": True,
        "pod_issue_found": False,
        "recommended_actions": ["Check pod health"],
    }
    base.update(over)
    return base


def _evidence(n: int) -> list[dict]:
    """Response-model-shaped evidence (`AnalysisResponse` validates these)."""
    return [
        {"source": "stacktrace", "reference_id": f"ref-{i}", "excerpt": f"line {i}"}
        for i in range(n)
    ]


# ── 1. The rules themselves ─────────────────────────────────────────────────


def test_a_confident_claim_with_no_evidence_is_capped():
    """The exact live number: 95 with zero evidence references becomes 50."""
    out = validate_confidence(_raw())
    assert out["confidence_score"] == 50, (
        "an evidence-free claim kept its raw confidence — the cap that makes "
        "the published number mean something is not being applied"
    )


def test_the_cap_drives_the_human_review_flag():
    """`requires_human_review` is what analytics counts as needs-review and what
    the gate renders. It must follow the FINAL number, not the raw one."""
    out = validate_confidence(_raw())
    assert out["requires_human_review"] is True
    assert out["confidence_gate_status"] == "below_threshold"


def test_the_adjustment_is_recorded_not_silent():
    """A number that was quietly lowered is indistinguishable from one the
    engine produced. Ops must be able to see 'said 95, capped to 50, because'."""
    out = validate_confidence(_raw())
    rules = {a["rule"] for a in out.get("_confidence_adjustments") or []}
    assert "no_evidence_references" in rules
    entry = next(a for a in out["_confidence_adjustments"]
                 if a["rule"] == "no_evidence_references")
    assert entry["from"] == 95 and entry["to"] == 50


def test_validation_is_idempotent():
    """`evidence_multiplier_bonus` adds +5. If two callers both validate — the
    pipeline and then the router on the same dict — a second pass would raise a
    score nothing re-earned.

    `tools_used` matters here and the guard is worthless without it: without
    tools the `no_tools_no_cache` cap pulls the score back to 60 and the +5
    bonus pushes it to 65 again, so repeated validation is accidentally a fixed
    point and this test passes even with the idempotence check deleted. That is
    how the first version of this guard survived mutation.
    """
    first = validate_confidence(_raw(
        evidence_references=_evidence(3), confidence_score=70,
        tools_used=["search_logs"],
    ))
    score = first["confidence_score"]
    assert score == 75, f"expected one +5 bonus over 70, got {score}"
    second = validate_confidence(first)
    assert second["confidence_score"] == score, (
        "re-validating changed the score, so the number now depends on how "
        "many times it happened to pass through validation"
    )


def test_an_errored_analysis_cannot_claim_confidence():
    out = validate_confidence(_raw(error="LLM call failed"))
    assert out["confidence_score"] == 0


def test_unknown_category_cannot_be_confident():
    out = validate_confidence(_raw(
        failure_category="UNKNOWN", evidence_references=_evidence(4),
    ))
    assert out["confidence_score"] <= 40


# ── 2. The router applies them BEFORE persisting ────────────────────────────


def _authorized_admin():
    from app.models.postgres import UserRole

    return SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, api_key_project_id=None)


@pytest.mark.asyncio
async def test_post_analyze_persists_the_validated_number(monkeypatch):
    """The live defect, pinned end to end: the engine returns 95 with no
    evidence; the row that gets written must carry 50.

    Behavioural on purpose. An earlier draft of this guard asserted that the
    router's source contained the string "validate_confidence" — which its own
    explanatory comment satisfied.
    """
    from app.routers import analyze as analyze_router
    from app.models.postgres import TestCase as TestCaseModel

    run_id, project_id, tc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def _fake_resolve(db, user, requested_id):
        return SimpleNamespace(
            test_case=SimpleNamespace(
                id=tc_id, test_name="t_infra_fail", suite_name="GroundTruthSuite",
                error_message="java.net.ConnectException: Connection refused",
                stack_trace=None, duration_ms=12, severity="HIGH",
                test_fingerprint="fp",
            ),
            test_run=SimpleNamespace(
                id=run_id, end_time=None, start_time=None,
                ocp_pod_name=None, ocp_namespace=None,
            ),
            run_id=run_id,
            project_id=project_id,
        )

    async def _fake_classify(test_case, run_context=None, **kw):
        return _raw(llm_provider="openrouter", llm_model="mistralai/mistral-nemo",
                    recommended_actions=[], role_actions={})

    monkeypatch.setattr(analyze_router, "resolve_authorized_test_case", _fake_resolve)
    monkeypatch.setattr(analyze_router.analysis_router, "classify_test", _fake_classify)

    created: list = []

    db = AsyncMock()
    # 1st execute: the existing-AIAnalysis lookup (none) — 2nd: the TestCase update.
    none_result = MagicMock()
    none_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(side_effect=[none_result, MagicMock()])
    db.add = MagicMock(side_effect=created.append)
    db.commit = AsyncMock()

    resp = await analyze_router.analyze_test_case(
        request=SimpleNamespace(test_case_id=tc_id),
        db=db,
        current_user=_authorized_admin(),
    )

    assert created, "no AIAnalysis row was written"
    row = created[0]
    assert row.confidence_score == 50, (
        f"POST /analyze persisted {row.confidence_score} for an evidence-free "
        "claim — the engine's raw self-assessment is reaching the database "
        "unvalidated, which is exactly the live defect"
    )
    assert row.requires_human_review is True, (
        "the persisted human-review flag was cleared by an unvalidated score"
    )
    assert resp.confidence_score == 50, (
        "the response disagrees with the row it just wrote"
    )
    assert isinstance(TestCaseModel, type)  # import kept meaningful


@pytest.mark.asyncio
async def test_post_analyze_does_not_downgrade_a_well_evidenced_score(monkeypatch):
    """The cap must not become a blanket ceiling — a claim WITH evidence and
    tools keeps its number. Otherwise the fix would just move the inaccuracy."""
    from app.routers import analyze as analyze_router

    run_id, project_id, tc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def _fake_resolve(db, user, requested_id):
        return SimpleNamespace(
            test_case=SimpleNamespace(
                id=tc_id, test_name="t", suite_name="s", error_message="boom",
                stack_trace=None, duration_ms=1, severity="HIGH", test_fingerprint="fp",
            ),
            test_run=SimpleNamespace(id=run_id, end_time=None, start_time=None,
                                     ocp_pod_name=None, ocp_namespace=None),
            run_id=run_id, project_id=project_id,
        )

    async def _fake_classify(test_case, run_context=None, **kw):
        return _raw(
            confidence_score=92,
            evidence_references=_evidence(3),
            tools_used=["search_logs", "check_pod_health"],
        )

    monkeypatch.setattr(analyze_router, "resolve_authorized_test_case", _fake_resolve)
    monkeypatch.setattr(analyze_router.analysis_router, "classify_test", _fake_classify)

    created: list = []
    none_result = MagicMock()
    none_result.scalar_one_or_none = MagicMock(return_value=None)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[none_result, MagicMock()])
    db.add = MagicMock(side_effect=created.append)
    db.commit = AsyncMock()

    await analyze_router.analyze_test_case(
        request=SimpleNamespace(test_case_id=tc_id),
        db=db,
        current_user=_authorized_admin(),
    )
    assert created[0].confidence_score >= 90, (
        "an evidenced, tool-backed claim was capped — validation has become a "
        "ceiling rather than a check"
    )


# ── 3. The agent still routes through the same rules ────────────────────────


def test_the_agent_delegates_rather_than_keeping_its_own_copy():
    """Two copies of a policy drift; the one that drifts is the one nobody is
    looking at. Assert on the CALL, not on prose about it."""
    import ast
    import inspect

    from app.agents.analysis_agent import AnalysisAgent

    tree = ast.parse(inspect.getsource(AnalysisAgent._validate_confidence).strip())
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "validate_confidence" in called, (
        "the agent no longer delegates to the shared confidence policy, so the "
        "pipeline and the endpoint can once again publish different numbers"
    )
