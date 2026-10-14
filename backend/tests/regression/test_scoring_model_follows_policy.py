"""GATE-05: ``GET /scoring-model`` answers with the weights the verdict uses.

The route took no project and returned the module constants (``_GO_THRESHOLD``,
``_NO_GO_THRESHOLD``) and the config weights. The verdict applies the project's
active release-gate policy instead (``policy_evaluator_service.evaluate_policy``
reads ``thresholds`` and ``dimension_weights`` from it). So on a project whose
policy weighted ``user_impact`` at 0.40, Run Intelligence -> Criticality ->
"Why this score?" explained a 0.25 that never decided anything.

Now ``?project_id=`` (or ``?run_id=``) returns the policy-resolved model through
the evaluator's own extractors, names the policy, and refuses a project the
caller cannot read. Without either parameter the response is byte-for-byte the
old one (the cluster scorers call it unscoped).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

OWN = uuid.UUID("11111111-1111-4111-8111-111111111111")
FOREIGN = uuid.UUID("22222222-2222-4222-8222-222222222222")
OWN_RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
FOREIGN_RUN = uuid.UUID("44444444-4444-4444-8444-444444444444")

POLICY = SimpleNamespace(
    id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
    project_id=OWN,
    name="Checkout strict",
    version=2,
    rules={
        "thresholds": {"go_threshold": 15, "no_go_threshold": 50, "pass_rate_minimum": 95},
        "dimension_weights": {"user_impact": 0.40},
        "rules": [],
    },
)


def _member():
    return SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", is_active=True, api_key_project_id=None)


@pytest.fixture
def scoring(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core import deps
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import scoring as scoring_router

    run_projects = {OWN_RUN: OWN, FOREIGN_RUN: FOREIGN}

    async def _execute(stmt, *a, **k):
        # The only statement the route runs itself: SELECT test_runs.project_id WHERE id = :id
        run_id = stmt.whereclause.right.value
        return MagicMock(scalar_one_or_none=MagicMock(return_value=run_projects.get(run_id)))

    db = SimpleNamespace(execute=AsyncMock(side_effect=_execute))

    async def _db():
        yield db

    resolved: list = []

    async def _resolve(project_id, _db):
        resolved.append(project_id)
        return (POLICY, "project") if project_id == OWN else (None, "hardcoded")

    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value={OWN}))
    monkeypatch.setattr(scoring_router, "resolve_effective_policy", _resolve)

    app = FastAPI()
    app.include_router(scoring_router.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _member
    yield TestClient(app, raise_server_exceptions=False), resolved, db


def _weights(body: dict) -> dict[str, float]:
    return {d["name"]: d["weight"] for d in body["dimensions"]}


# ── unscoped: unchanged ─────────────────────────────────────────────────────


def test_no_parameter_keeps_the_default_response(scoring):
    from app.services.criticality_service import (
        _GO_THRESHOLD,
        _NO_GO_THRESHOLD,
        SCORE_MODEL_VERSION,
        _weights as default_weights,
    )

    http, resolved, db = scoring
    resp = http.get("/api/v1/scoring-model")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"version", "go_threshold", "no_go_threshold", "dimensions"}
    assert body["version"] == SCORE_MODEL_VERSION
    assert (body["go_threshold"], body["no_go_threshold"]) == (_GO_THRESHOLD, _NO_GO_THRESHOLD)
    assert _weights(body) == default_weights()
    assert resolved == [] and db.execute.await_count == 0


# ── scoped to a project ─────────────────────────────────────────────────────


def test_a_projects_policy_weights_and_thresholds_are_returned(scoring):
    http, resolved, _ = scoring
    resp = http.get("/api/v1/scoring-model", params={"project_id": str(OWN)})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _weights(body)["user_impact"] == 0.40
    assert (body["go_threshold"], body["no_go_threshold"]) == (15.0, 50.0)
    assert body["pass_rate_minimum"] == 95.0
    assert body["policy"] == {
        "policy_id": str(POLICY.id),
        "name": "Checkout strict",
        "version": 2,
        "level": "project",
    }
    assert resolved == [OWN]
    # Every description still travels with its weight.
    assert all(d["description"] for d in body["dimensions"])


def test_another_tenants_project_is_refused(scoring):
    """IDOR: the id in the query string is checked, not just its absence."""
    http, resolved, _ = scoring
    resp = http.get("/api/v1/scoring-model", params={"project_id": str(FOREIGN)})
    assert resp.status_code == 403
    assert "policy" not in resp.text
    assert resolved == []


def test_a_project_without_a_policy_says_the_defaults_decided(scoring, monkeypatch):
    from app.core import deps
    from app.services.criticality_service import _weights as default_weights

    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value={OWN, FOREIGN}))
    http, _, _ = scoring
    body = http.get("/api/v1/scoring-model", params={"project_id": str(FOREIGN)}).json()
    assert body["policy"] == {"policy_id": None, "name": None, "version": None, "level": "hardcoded"}
    assert _weights(body) == default_weights()


# ── scoped to a run ─────────────────────────────────────────────────────────


def test_a_run_resolves_to_its_projects_policy(scoring):
    http, resolved, _ = scoring
    body = http.get("/api/v1/scoring-model", params={"run_id": str(OWN_RUN)}).json()
    assert _weights(body)["user_impact"] == 0.40
    assert body["policy"]["name"] == "Checkout strict"
    assert resolved == [OWN]


@pytest.mark.parametrize("run_id", [FOREIGN_RUN, uuid.UUID("66666666-6666-4666-8666-666666666666")])
def test_a_run_the_caller_cannot_read_is_not_found(scoring, run_id):
    """A foreign run and a missing run answer alike: no existence oracle."""
    http, resolved, _ = scoring
    resp = http.get("/api/v1/scoring-model", params={"run_id": str(run_id)})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Test run not found"
    assert resolved == []


def test_a_run_and_a_different_project_disagree(scoring, monkeypatch):
    from app.core import deps

    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value={OWN, FOREIGN}))
    http, resolved, _ = scoring
    resp = http.get("/api/v1/scoring-model", params={"run_id": str(OWN_RUN), "project_id": str(FOREIGN)})
    assert resp.status_code == 400
    assert resolved == []


def test_malformed_ids_are_400(scoring):
    http, _, _ = scoring
    assert http.get("/api/v1/scoring-model", params={"project_id": "nope"}).status_code == 400
    assert http.get("/api/v1/scoring-model", params={"run_id": "nope"}).status_code == 400


# ── parity with the verdict ─────────────────────────────────────────────────


def test_the_model_is_what_the_evaluator_applies():
    """Same policy in, same numbers out: what "Why this score?" shows is what
    ``evaluate_policy`` computed the verdict with."""
    from app.services.criticality_service import get_scoring_model_info
    from app.services.policy_evaluator_service import evaluate_policy

    model = get_scoring_model_info(POLICY, level="project")
    result = asyncio.run(
        evaluate_policy(OWN, {"user_impact": 50.0}, 99.0, {}, db=None, policy_override=POLICY)
    )

    assert _weights(model) == result.effective_weights
    assert model["go_threshold"] == result.effective_thresholds["go_threshold"]
    assert model["no_go_threshold"] == result.effective_thresholds["no_go_threshold"]
    assert model["pass_rate_minimum"] == result.effective_thresholds["pass_rate_minimum"]
    assert model["pass_rate_hard_floor_factor"] == result.effective_thresholds["pass_rate_hard_floor_factor"]
