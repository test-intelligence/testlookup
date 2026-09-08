from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app.agents.run_compare_agent import (
    build_fallback_report,
    build_validated_fallback_report,
)

def test_fallback_report_uses_deterministic_diff_counts():
    payload = {
        "scope": "suite",
        "suite_name": "Checkout",
        "delta_pass_rate": -12.5,
        "new_failures": 2,
        "regressed": 1,
        "fixed": 3,
        "still_failing": 0,
        "duration_spikes": 1,
        "test_deltas": [
            {"classification": "new_failure", "test_name": "test_payment_declined"},
            {"classification": "fixed", "test_name": "test_cart_total"},
            {"classification": "duration_spike", "test_name": "test_order_history"},
        ],
    }

    report = build_fallback_report(payload)

    assert report["risk_level"] == "HIGH"
    assert "2 new failure(s)" in report["executive_summary"]
    assert "test_payment_declined" in report["new_risks"]
    assert "test_cart_total" in report["resolved_risks"]
    assert "test_order_history" in report["duration_concerns"]
    assert report["fallback_used"] is True


def test_validated_fallback_report_is_ready_without_an_llm():
    report = build_validated_fallback_report(
        {
            "scope": "suite",
            "suite_name": "Checkout",
            "new_failures": 1,
            "regressed": 0,
            "fixed": 0,
            "still_failing": 0,
            "duration_spikes": 0,
            "test_deltas": [],
        }
    )

    assert report["status"] == "ready"
    assert report["fallback_used"] is True
    assert report["markdown_report"].startswith("## Executive Summary")
    assert report["agent_contracts"]["run_compare"]["agent_name"] == "run_compare"


@pytest.mark.asyncio
async def test_deterministic_persistence_does_not_construct_llm_agent(monkeypatch):
    from app.services import run_compare_ai_service as service

    row = SimpleNamespace()
    db = SimpleNamespace(flush=AsyncMock())
    monkeypatch.setattr(service, "mark_queued", AsyncMock())
    monkeypatch.setattr(service, "_get_row", AsyncMock(return_value=row))
    monkeypatch.setattr(
        service,
        "RunCompareAgent",
        MagicMock(side_effect=AssertionError("LLM agent must not be constructed")),
    )

    report = await service.generate_and_save_report(
        db,
        project_id=uuid.uuid4(),
        left_run_id=uuid.uuid4(),
        right_run_id=uuid.uuid4(),
        suite_name="Checkout",
        compare_payload={
            "scope": "suite",
            "suite_name": "Checkout",
            "new_failures": 0,
            "regressed": 0,
            "fixed": 1,
            "still_failing": 0,
            "duration_spikes": 0,
            "test_deltas": [],
        },
        deterministic_only=True,
        cost_budget_prechecked=True,
    )

    assert report["fallback_used"] is True
    assert row.status == "ready"
    assert row.fallback_used is True
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_hard_cap_uses_fallback_before_constructing_agent(monkeypatch):
    from app.services import llm_cost_budget
    from app.services import run_compare_ai_service as service
    from app.services.llm_cost_budget import CapDecision

    row = SimpleNamespace()
    db = SimpleNamespace(flush=AsyncMock())
    monkeypatch.setattr(service, "mark_queued", AsyncMock())
    monkeypatch.setattr(service, "_get_row", AsyncMock(return_value=row))
    monkeypatch.setattr(
        llm_cost_budget,
        "check_and_apply_cap",
        AsyncMock(
            return_value=CapDecision(
                action="HARD_BLOCK",
                block=True,
                rationale="monthly cap reached",
                utilization_pct=100.0,
            )
        ),
    )
    agent = MagicMock(side_effect=AssertionError("LLM agent must remain gated"))
    monkeypatch.setattr(service, "RunCompareAgent", agent)

    report = await service.generate_and_save_report(
        db,
        project_id=uuid.uuid4(),
        left_run_id=uuid.uuid4(),
        right_run_id=uuid.uuid4(),
        suite_name=None,
        compare_payload={
            "scope": "run",
            "new_failures": 0,
            "regressed": 0,
            "fixed": 0,
            "still_failing": 0,
            "duration_spikes": 0,
            "test_deltas": [],
        },
    )

    agent.assert_not_called()
    assert report["fallback_used"] is True
    assert row.status == "ready"
