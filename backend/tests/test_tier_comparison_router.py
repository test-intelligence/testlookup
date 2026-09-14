from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import ai_evaluation as router
from app.routers import agent_configs as config_router
from app.services import agent_config_service as configs
from app.services import tier_comparison_service as svc
from app.services.agent_eval_samples import CAPABILITY_EVAL_SAMPLES, LabelKind


def _output(sample):
    if sample.label.kind == LabelKind.CLASSIFICATION:
        return {"category": sample.label.value}
    if sample.label.kind == LabelKind.STRUCTURED:
        return {**{name: "present" for name in sample.label.required_fields}, **sample.label.exact_values}
    return " ".join(sample.label.required_claims)


def _body(*, agent_id: str = "agent.summary.v1") -> router.TierComparisonRequest:
    return router.TierComparisonRequest(
        project_id=uuid.uuid4(),
        agent_id=agent_id,
        capability="summary",
        incumbent_tier="llm",
        candidate_tier="slm",
        pairs=[
            router.TierOutputPairRequest(
                sample_id=sample.sample_id,
                incumbent_output=_output(sample),
                candidate_output=_output(sample),
            )
            for sample in CAPABILITY_EVAL_SAMPLES["summary"]
        ],
    )


async def test_route_runs_paired_gate_persists_and_commits(monkeypatch):
    persisted = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(svc, "persist_tier_comparison", persisted)
    activity = AsyncMock()
    monkeypatch.setattr(router, "record_activity", activity)
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=uuid.uuid4())

    result = await router.run_tier_comparison(_body(), current_user=user, db=db)

    assert result["verdict"] == "pass" and result["sample_count"] == 20
    assert result["gate_run_id"] == str(persisted.return_value.id)
    persisted.assert_awaited_once()
    assert activity.await_args.kwargs["event_type"] == "ai_eval.tier_compared"
    assert activity.await_args.kwargs["context"]["sample_count"] == 20
    db.commit.assert_awaited_once()


async def test_route_refuses_an_agent_capability_mismatch():
    with pytest.raises(HTTPException) as exc:
        await router.run_tier_comparison(
            _body(agent_id="agent.triage.v1"),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=SimpleNamespace(),
        )
    assert exc.value.status_code == 422 and "does not match" in exc.value.detail


def test_tier_comparison_is_project_scoped():
    dependency = inspect.signature(router.run_tier_comparison).parameters["current_user"].default.dependency
    assert dependency is not None
    assert "project_id" in [cell.cell_contents for cell in (dependency.__closure__ or ())]


async def test_agent_config_put_surfaces_unmeasured_downgrade_as_422(monkeypatch):
    monkeypatch.setattr(config_router, "get_effective_ai_config", AsyncMock(return_value={"offline_mode": True}))
    monkeypatch.setattr(configs, "get_config_row", AsyncMock(return_value=None))
    put = AsyncMock()
    monkeypatch.setattr(configs, "put_config", put)
    query_result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=query_result),
        scalar=AsyncMock(return_value=None),
    )
    body = configs.default_config("agent.summary.v1").model_copy(
        update={"model": configs.ModelConfig(tier="deterministic")}
    )

    with pytest.raises(HTTPException) as exc:
        await config_router.put_agent_config(
            uuid.uuid4(),
            body.agent_id,
            body,
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
            _lead=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["verdict"] == "insufficient_samples"
    put.assert_not_awaited()
