"""The triage flakiness tool names no history pattern from too few executions (D-0008).

GPT-6 (exploratory test, 2026-11-14) found an AI triage whose stored evidence
called a test "CONSISTENTLY FAILING — likely a genuine product bug" from ONE
failed execution, and the model repeated it ("has been consistently failing").
``check_test_flakiness`` picked its label from the failure rate alone, so one
failure read like a hundred.

Now a pattern label (flaky, consistently failing, historically stable) needs at
least ``flaky_score_service.MIN_OBSERVATIONS`` (5) recorded executions, the same
floor the Flaky score uses (owner decision P4). Below it the counts and the rate
are still reported, with "too early to tell".

The tool is called directly (no model), with the investigation context bound
and the database session replaced by one that returns the status counts.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import check_flakiness
from app.tools.check_flakiness import check_test_flakiness
from app.tools.investigation_context import (
    InvestigationContext,
    reset_investigation_context,
    set_investigation_context,
)

pytestmark = pytest.mark.regression

TEST = "test_checkout_total"


def _session_returning(rows: list[tuple[str, int]], seen: list[dict]):
    class _Result:
        def fetchall(self):
            return [SimpleNamespace(status=status, cnt=cnt) for status, cnt in rows]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, _stmt, params):
            seen.append(dict(params))
            return _Result()

    return _Session


async def _ask(monkeypatch, rows: list[tuple[str, int]]) -> tuple[str, list[dict]]:
    seen: list[dict] = []
    monkeypatch.setattr(check_flakiness, "AsyncSessionLocal", _session_returning(rows, seen))
    token = set_investigation_context(InvestigationContext(
        project_id="project-a", run_id="run-a", test_case_id="tc-a", test_name=TEST,
    ))
    try:
        out = await check_test_flakiness.ainvoke({"test_name": TEST})
    finally:
        reset_investigation_context(token)
    return out, seen


LABELS = ("CONSISTENTLY FAILING", "FLAKY", "HISTORICALLY STABLE", "product bug")


@pytest.mark.asyncio
async def test_one_failed_execution_is_too_early_to_tell(monkeypatch):
    out, _ = await _ask(monkeypatch, [("FAILED", 1)])
    assert "Total executions analysed: 1" in out
    assert "Failed/Broken: 1" in out
    assert "Failure rate: 100.0%" in out
    assert "TOO FEW EXECUTIONS" in out
    for label in LABELS:
        assert label not in out


@pytest.mark.asyncio
async def test_one_passed_execution_is_not_historically_stable(monkeypatch):
    out, _ = await _ask(monkeypatch, [("PASSED", 1)])
    assert "TOO FEW EXECUTIONS" in out
    assert "HISTORICALLY STABLE" not in out


@pytest.mark.asyncio
async def test_four_failures_are_still_too_few(monkeypatch):
    out, _ = await _ask(monkeypatch, [("FAILED", 4)])
    assert "TOO FEW EXECUTIONS — 4 recorded" in out
    assert "CONSISTENTLY FAILING" not in out


@pytest.mark.asyncio
async def test_ten_failures_are_consistently_failing(monkeypatch):
    out, _ = await _ask(monkeypatch, [("FAILED", 10)])
    assert "CONSISTENTLY FAILING" in out
    assert "TOO FEW EXECUTIONS" not in out


@pytest.mark.asyncio
async def test_five_and_five_is_flaky(monkeypatch):
    out, _ = await _ask(monkeypatch, [("FAILED", 5), ("PASSED", 5)])
    assert "FLAKY" in out and "50.0%" in out


@pytest.mark.asyncio
async def test_five_passes_are_historically_stable(monkeypatch):
    out, _ = await _ask(monkeypatch, [("PASSED", 5)])
    assert "HISTORICALLY STABLE" in out


@pytest.mark.asyncio
async def test_no_history_is_still_the_no_data_line(monkeypatch):
    out, _ = await _ask(monkeypatch, [])
    assert out.startswith("No historical data found")


@pytest.mark.asyncio
async def test_the_bound_scope_is_used_not_the_argument(monkeypatch):
    _, seen = await _ask(monkeypatch, [("FAILED", 1)])
    assert seen == [{"test_name": TEST, "project_id": "project-a"}]


def test_the_floor_is_the_flaky_scores():
    from app.services.flaky_score_service import MIN_OBSERVATIONS

    assert check_flakiness.MIN_EXECUTIONS_FOR_PATTERN == MIN_OBSERVATIONS == 5
