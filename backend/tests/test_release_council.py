"""
Unit tests for Release Council Service (Epic 9).

Tests deterministic context assembly, override audit trail, and edge cases.
All DB calls are mocked.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))

        yield


# ── Schema / model tests ──────────────────────────────────────────────────────

class TestOverrideAuditEntry:
    def test_valid_entry(self):
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-01T12:00:00Z",
            actor_id="abc-123",
            actor_name="admin_user",
            before_recommendation="NO_GO",
            before_risk_score=72,
            after_recommendation="CONDITIONAL_GO",
            reason="Hotfix deployed, blocking issues resolved",
        )
        assert entry.before_recommendation == "NO_GO"
        assert entry.after_recommendation == "CONDITIONAL_GO"
        assert entry.before_risk_score == 72

    def test_optional_fields(self):
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-01T12:00:00Z",
            before_recommendation="GO",
            before_risk_score=10,
            after_recommendation="NO_GO",
            reason="Security vulnerability found",
        )
        assert entry.actor_id is None
        assert entry.actor_name is None


class TestReleaseCouncilResponse:
    def test_default_values(self):
        from app.models.schemas import ReleaseCouncilResponse

        resp = ReleaseCouncilResponse(
            run_id="test-run-123",
            recommendation="GO",
            risk_score=15,
        )
        assert resp.blocking_issues == []
        assert resp.conditions_for_go == []
        assert resp.cluster_insights == []
        assert resp.open_defects_by_component == []
        assert resp.override_audit == []
        assert resp.human_override is None
        assert resp.original_recommendation is None

    def test_full_response(self):
        from app.models.schemas import (
            DimensionScore,
            OverrideAuditEntry,
            ReleaseCouncilResponse,
        )

        resp = ReleaseCouncilResponse(
            run_id="test-run-456",
            recommendation="CONDITIONAL_GO",
            risk_score=42,
            composite_risk=42.5,
            dimension_scores=[
                DimensionScore(name="user_impact", label="User Impact", score=60.0, weight=0.25, contribution=15.0),
            ],
            blocking_issues=["High user impact"],
            conditions_for_go=["Resolve product bugs"],
            reasoning="Elevated risk from product bugs.",
            score_model_version=1,
            open_defects_by_component=[{"component": "auth", "count": 3}],
            human_override="Approved after review",
            original_recommendation="NO_GO",
            original_risk_score=72,
            override_audit=[
                OverrideAuditEntry(
                    timestamp="2026-04-01T12:00:00Z",
                    before_recommendation="NO_GO",
                    before_risk_score=72,
                    after_recommendation="CONDITIONAL_GO",
                    reason="Hotfix deployed",
                ),
            ],
            pass_rate=85.5,
            build_number="build-123",
        )
        assert resp.dimension_scores[0].name == "user_impact"
        assert len(resp.override_audit) == 1
        assert resp.original_recommendation == "NO_GO"


class TestReleaseCouncilOverrideRequest:
    def test_valid_override(self):
        from app.models.schemas import ReleaseCouncilOverrideRequest

        req = ReleaseCouncilOverrideRequest(
            override_recommendation="GO",
            reason="All blocking issues resolved",
        )
        assert req.override_recommendation == "GO"
        assert req.reason == "All blocking issues resolved"

    def test_empty_reason_allowed_by_schema(self):
        from app.models.schemas import ReleaseCouncilOverrideRequest

        req = ReleaseCouncilOverrideRequest(
            override_recommendation="NO_GO",
            reason="",
        )
        assert req.reason == ""


class TestInputSnapshotAssembly:
    @pytest.mark.asyncio
    async def test_snapshot_from_state(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        state = {
            "pipeline_run_id": "pr-1",
            "project_id": "proj-1",
            "test_run_id": "run-1",
            "pass_rate": 85.0,
            "total_tests": 100,
            "is_regression": True,
            "regression_tests": ["t1", "t2"],
            "analyses": {"tc1": {}, "tc2": {}, "tc3": {}},
            "anomalies": [{"severity": "HIGH"}],
            "failure_clusters": [
                {"label": "Auth failures", "size": 5, "regression_classification": "new_regression"},
            ],
            "flaky_findings": [{"test_name": "flaky_test"}],
            "test_health_findings": [],
        }

        snapshot = await ReleaseRiskAgent._assemble_input_snapshot(state)

        assert snapshot["pass_rate"] == 85.0
        assert snapshot["total_tests"] == 100
        assert snapshot["is_regression"] is True
        assert snapshot["regression_test_count"] == 2
        assert snapshot["analysis_count"] == 3
        assert snapshot["anomaly_count"] == 1
        assert snapshot["cluster_count"] == 1
        assert snapshot["flaky_finding_count"] == 1
        assert snapshot["test_health_finding_count"] == 0
        assert "assembled_at" in snapshot
        from app.services.criticality_service import SCORE_MODEL_VERSION

        assert snapshot["score_model_version"] == SCORE_MODEL_VERSION

    @pytest.mark.asyncio
    async def test_snapshot_empty_state(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        state = {
            "pipeline_run_id": "pr-1",
            "project_id": "proj-1",
            "test_run_id": "run-1",
        }

        snapshot = await ReleaseRiskAgent._assemble_input_snapshot(state)

        assert snapshot["pass_rate"] == 0.0
        assert snapshot["cluster_count"] == 0
        assert snapshot["analysis_count"] == 0


class TestDeterministicScoring:
    def test_go_threshold(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(15.0, 95.0, 85.0) == "GO"

    def test_conditional_go_threshold(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(40.0, 90.0, 85.0) == "CONDITIONAL_GO"

    def test_no_go_threshold(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(60.0, 80.0, 85.0) == "NO_GO"

    def test_pass_rate_hard_floor(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(10.0, 50.0, 85.0) == "NO_GO"

    def test_compute_composite_clamped(self):
        from app.services.criticality_service import compute_composite

        scores = {k: 100.0 for k in [
            "user_impact", "env_sensitivity", "reproducibility",
            "regression_likely", "hist_recurrence", "blast_radius",
            "diagnosis_conf",
        ]}
        composite = compute_composite(scores)
        assert 0 <= composite <= 100

    def test_compute_composite_zero(self):
        from app.services.criticality_service import compute_composite

        scores = {k: 0.0 for k in [
            "user_impact", "env_sensitivity", "reproducibility",
            "regression_likely", "hist_recurrence", "blast_radius",
            "diagnosis_conf",
        ]}
        assert compute_composite(scores) == 0.0

    def test_dimension_scores_compute(self):
        from app.services.criticality_service import compute_dimension_scores

        scores = compute_dimension_scores(
            analyses={"tc1": {"failure_category": "PRODUCT_BUG", "confidence_score": 80}},
            anomalies=[],
            pass_rate=80.0,
            is_regression=False,
            regression_tests=[],
            failure_clusters=[],
            open_defects=0,
        )
        assert "user_impact" in scores
        assert "blast_radius" in scores
        assert all(0 <= v <= 100 for v in scores.values())

    def test_score_cluster_proportional(self):
        from app.services.criticality_service import score_cluster

        dim_scores = {"user_impact": 80.0, "blast_radius": 60.0}
        cluster_scores = score_cluster(
            cluster={},
            all_dim_scores=dim_scores,
            total_analyses=10,
            member_count=5,
        )
        # 50% of the run
        assert cluster_scores["user_impact"] == 40.0
        assert cluster_scores["blast_radius"] == 30.0

    def test_score_cluster_empty(self):
        from app.services.criticality_service import score_cluster

        result = score_cluster({}, {}, 0, 0)
        assert result == {}


# ── S-B-6 / D-101: an override is checked against what the gate page shows ────


class _OverrideResult:
    def __init__(self, scalar=None, row=None):
        self._scalar = scalar
        self._row = row

    def scalar_one_or_none(self):
        return self._scalar

    def one_or_none(self):
        return self._row


class _OverrideSession:
    """The decision row first (``FOR UPDATE``), then the run's project and pass rate."""

    def __init__(self, decision, project_id):
        self._results = [
            _OverrideResult(scalar=decision),
            _OverrideResult(row=(project_id, 52.0)),
        ]

    async def execute(self, _statement, *_args):
        return self._results.pop(0)

    async def flush(self):
        return None

    async def refresh(self, _row):
        return None


def _no_go_to_go_forbidden_policy():
    return types.SimpleNamespace(
        id="pol-1",
        version=3,
        rules={"rules": [{
            "type": "override_rules",
            "enabled": True,
            "params": {"allow_override_to_go_from_no_go": False},
        }]},
    )


def _stored_go_decision():
    return types.SimpleNamespace(
        recommendation="GO",
        risk_score=22,
        human_override=None,
        original_recommendation=None,
        original_risk_score=None,
        override_audit=[],
        overridden_by=None,
    )


@pytest.fixture
def red_band(monkeypatch):
    """A stored GO that the active policy's red pass-rate band shows as No-Go."""
    import uuid as _uuid
    from unittest.mock import AsyncMock

    from app.services import policy_evaluator_service
    from app.services import release_council_service as svc

    floor = AsyncMock(return_value=("NO_GO", "red", ["pass rate in red band"], ("pol-1", 3, "project")))
    monkeypatch.setattr(svc, "_apply_band_floor", floor)
    monkeypatch.setattr(
        policy_evaluator_service,
        "resolve_effective_policy",
        AsyncMock(return_value=(_no_go_to_go_forbidden_policy(), "project")),
    )
    council = AsyncMock(return_value="council")
    monkeypatch.setattr(svc, "get_release_council", council)
    return types.SimpleNamespace(svc=svc, floor=floor, project_id=_uuid.uuid4(), council=council)


class TestOverrideUsesTheDisplayedRecommendation:
    @pytest.mark.asyncio
    async def test_a_go_shown_as_no_go_cannot_be_overridden_to_go(self, red_band):
        import uuid as _uuid

        from fastapi import HTTPException

        decision = _stored_go_decision()
        with pytest.raises(HTTPException) as refused:
            await red_band.svc.apply_override(
                _uuid.uuid4(), "GO", "shipping anyway",
                actor=types.SimpleNamespace(id=_uuid.uuid4(), username="dana.qa", full_name="Dana Quinn"),
                db=_OverrideSession(decision, red_band.project_id),
            )
        assert refused.value.status_code == 403
        assert decision.recommendation == "GO" and decision.override_audit == []
        red_band.floor.assert_awaited_once()
        assert red_band.floor.await_args.args[1:] == (red_band.project_id, "GO", 52.0)

    @pytest.mark.asyncio
    async def test_the_audit_records_the_displayed_before_and_the_full_name(self, red_band):
        import uuid as _uuid

        decision = _stored_go_decision()
        actor = types.SimpleNamespace(id=_uuid.uuid4(), username="dana.qa", full_name="Dana Quinn")
        assert await red_band.svc.apply_override(
            _uuid.uuid4(), "CONDITIONAL_GO", "fix lands in the next build",
            actor=actor, db=_OverrideSession(decision, red_band.project_id),
        ) == "council"

        (entry,) = decision.override_audit
        assert entry["before_recommendation"] == "NO_GO", "the audit's before is what the screen showed"
        assert entry["after_recommendation"] == "CONDITIONAL_GO"
        assert entry["actor_name"] == "Dana Quinn"
        assert entry["actor_id"] == str(actor.id)
        # The agent's own answer is still kept as the original.
        assert decision.original_recommendation == "GO"

    @pytest.mark.asyncio
    async def test_a_user_without_a_full_name_is_recorded_by_username(self, red_band):
        import uuid as _uuid

        decision = _stored_go_decision()
        await red_band.svc.apply_override(
            _uuid.uuid4(), "CONDITIONAL_GO", "fix lands in the next build",
            actor=types.SimpleNamespace(id=_uuid.uuid4(), username="dana.qa", full_name=None),
            db=_OverrideSession(decision, red_band.project_id),
        )
        assert decision.override_audit[0]["actor_name"] == "dana.qa"

    @pytest.mark.asyncio
    async def test_an_already_overridden_decision_skips_the_band_floor(self, red_band):
        import uuid as _uuid

        decision = _stored_go_decision()
        decision.human_override = "accepted earlier"
        await red_band.svc.apply_override(
            _uuid.uuid4(), "CONDITIONAL_GO", "re-checked",
            actor=None, db=_OverrideSession(decision, red_band.project_id),
        )
        red_band.floor.assert_not_awaited()
        assert decision.override_audit[0]["before_recommendation"] == "GO"
