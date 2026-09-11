"""Re-audit N29: the summary cites flaky tests by this run's ids, not by name.

On the homelab, 226 of 490 summaries carrying a consistency report failed
``referential_integrity_flaky_ids`` -- and every one of the 226 cited test
NAMES (``"test_beta"``, ``"test_payment_timeout"``), never an id. The
evidence-pack prompt asks for ``flaky_test_ids``, but the context shows the
model each failure by name only, so the model answered with names; the check
compares with this run's analysed ids (UUIDs) and could never match.

The fixtures use the real shapes: analyses keyed by UUID, with test names
like the ones the homelab rows cited.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.consistency import check_summary_consistency  # noqa: E402
from app.agents.summary_agent import SummaryAgent  # noqa: E402

BETA = "28e49ed3-84a4-5599-a204-0bdfc973276d"      # flaky
ALPHA = "21b9cfc3-21c4-4b36-8afb-430f611c7f92"     # not flaky
GAMMA = "7f0c1a52-9b1e-4f55-8d0e-2c4b8a9e6d11"     # flaky


class _Response:
    def __init__(self, content: str):
        self.content = content


class _Model:
    def __init__(self, by_marker: dict[str, str]):
        self.by_marker = by_marker

    async def ainvoke(self, prompt, *_args, **_kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)
        for marker, payload in self.by_marker.items():
            if marker in text:
                return _Response(payload)
        return _Response("Executive summary text.")


def _analyses() -> dict:
    return {
        BETA: {"test_name": "test_beta", "confidence_score": 88, "failure_category": "FLAKY",
               "is_flaky": True, "root_cause_summary": "Race in the async waiter."},
        ALPHA: {"test_name": "test_alpha", "confidence_score": 92, "failure_category": "PRODUCT_BUG",
                "is_flaky": False, "root_cause_summary": "expected 1, got 2"},
        GAMMA: {"test_name": "test_inventory_sync", "confidence_score": 70, "failure_category": "FLAKY",
                "is_flaky": True, "root_cause_summary": "Intermittent stock drift."},
    }


async def _generate(monkeypatch, flaky_answer: list[str]):
    agent = SummaryAgent()
    agent.log_decision = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.agents.summary_agent.get_llm",
        AsyncMock(return_value=_Model({
            "evidence pack": json.dumps({
                "top_stack_traces": ["expected 1, got 2"],
                "log_anomalies": [],
                # What the homelab rows show the model answering: names.
                "flaky_test_ids": flaky_answer,
                "data_sources_used": ["flakiness_db"],
            }),
        })),
    )
    structured = await agent._generate_structured_report(
        run_data={"build_number": "102", "total_tests": 40, "failed_tests": 3, "pass_rate": 92.5},
        anomaly_summary="",
        anomalies=[],
        analyses=_analyses(),
        pipeline_run_id="pipe-n29",
    )
    return agent, structured


def _flaky_check(structured) -> object:
    report = check_summary_consistency(
        structured=structured,
        run_data={"failed_tests": 3, "pass_rate": 92.5},
        failed_test_ids=[BETA, ALPHA, GAMMA],
        analyses=_analyses(),
    )
    return next(c for c in report.checks if c.name == "referential_integrity_flaky_ids")


@pytest.mark.asyncio
async def test_a_model_that_answers_with_names_yields_run_ids(monkeypatch):
    agent, structured = await _generate(monkeypatch, ["test_beta", "test_inventory_sync"])
    layer3 = structured["layer3_evidence_pack"]
    # The ids, in the report's order (confidence descending), for the flaky tests.
    assert layer3["flaky_test_ids"] == [BETA, GAMMA]
    check = _flaky_check(structured)
    assert check.passed is True, check.offending_refs
    assert _flaky_drops(agent) == []  # both names resolved: nothing dropped


@pytest.mark.asyncio
async def test_an_entry_that_is_not_a_flaky_test_of_this_run_is_dropped_on_record(monkeypatch):
    # "test_ghost" was never analysed; "test_alpha" was, but is not flaky.
    agent, structured = await _generate(monkeypatch, ["test_beta", "test_ghost", "test_alpha"])
    assert structured["layer3_evidence_pack"]["flaky_test_ids"] == [BETA, GAMMA]
    assert ALPHA not in structured["layer3_evidence_pack"]["flaky_test_ids"]
    drops = _flaky_drops(agent)
    assert len(drops) == 1
    assert drops[0]["context"]["dropped"] == ["test_ghost", "test_alpha"]


def _flaky_drops(agent) -> list[dict]:
    """The flaky_ids_unresolved decisions (other layers log their own)."""
    return [
        call.kwargs for call in agent.log_decision.await_args_list
        if call.kwargs.get("decision_point") == "flaky_ids_unresolved"
    ]


@pytest.mark.asyncio
async def test_the_check_still_fails_what_the_model_answered(monkeypatch):
    """The check is not weakened: fed the model's raw names, it still fails."""
    _agent, structured = await _generate(monkeypatch, ["test_beta"])
    raw = dict(structured)
    raw["layer3_evidence_pack"] = {**structured["layer3_evidence_pack"], "flaky_test_ids": ["test_beta"]}
    check = _flaky_check(raw)
    assert check.passed is False
    assert check.offending_refs == ["test_beta"]


def test_the_deterministic_fallback_already_cites_ids():
    structured = SummaryAgent()._build_fallback_structured_report(
        run_data={"failed_tests": 3, "pass_rate": 92.5},
        anomaly_summary="",
        anomalies=[],
        analyses=_analyses(),
    )
    assert set(structured["layer3_evidence_pack"]["flaky_test_ids"]) == {BETA, GAMMA}
    assert _flaky_check(structured).passed is True
