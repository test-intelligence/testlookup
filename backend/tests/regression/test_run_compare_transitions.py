"""VIZ-507: the status-flow Sankey's data. ``compare_runs`` counts every
test's (before -> after) status, unchanged tests included, so the flows
CONSERVE: the transitions leaving the left run add up to its test count and
those reaching the right run to its. A renamed (fuzzy-paired) test is one
flow, not a removal plus an addition.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.schemas import RunCompareTransition
from app.services import run_compare_service as svc


def _tc(fp: str, name: str, status: str, suite: str = "auth", duration: int = 100):
    return SimpleNamespace(test_fingerprint=fp, test_name=name, suite_name=suite, status=status, duration_ms=duration)


def _summary(tests: dict) -> dict:
    statuses = [t.status for t in tests.values()]
    return {
        "run_id": str(uuid.uuid4()), "build_number": "1", "branch": None, "environment": None,
        "status": "COMPLETED", "started_at": None, "finished_at": None, "duration_ms": None,
        "total_tests": len(statuses),
        "passed_tests": statuses.count("PASSED"), "failed_tests": statuses.count("FAILED"),
        "broken_tests": statuses.count("BROKEN"), "skipped_tests": statuses.count("SKIPPED"),
        "pass_rate": None,
    }


async def _compare(monkeypatch, left: dict, right: dict) -> dict:
    left_id, right_id = uuid.uuid4(), uuid.uuid4()
    runs = {left_id: SimpleNamespace(id=left_id), right_id: SimpleNamespace(id=right_id)}
    tests = {left_id: left, right_id: right}

    async def load_summary(_db, run_id):
        return runs[run_id]

    async def load_rows(_db, run_id, suite_name=None):
        return tests[run_id]

    monkeypatch.setattr(svc, "_load_summary", load_summary)
    monkeypatch.setattr(svc, "_load_test_rows", load_rows)
    monkeypatch.setattr(svc, "_summary_dict", lambda run, **_: _summary(tests[run.id]))
    return await svc.compare_runs(None, left_id, right_id)


def _flows(result: dict) -> dict[tuple[str, str], int]:
    return {(t["before"], t["after"]): t["count"] for t in result["transitions"]}


@pytest.mark.asyncio
async def test_every_test_is_one_flow_and_the_flows_conserve(monkeypatch):
    left = {
        "a": _tc("a", "test_login", "PASSED"),
        "b": _tc("b", "test_logout", "PASSED"),
        "c": _tc("c", "test_refund", "FAILED"),
        "d": _tc("d", "test_old_thing", "PASSED"),
        "e": _tc("e", "test_flaky", "BROKEN"),
    }
    right = {
        "a": _tc("a", "test_login", "PASSED"),       # unchanged: counted, unlike deltas
        "b": _tc("b", "test_logout", "FAILED"),      # passed -> failed
        "c": _tc("c", "test_refund", "PASSED"),      # failed -> passed
        "e": _tc("e", "test_flaky", "BROKEN"),       # unchanged broken
        "z": _tc("z", "test_brand_new_feature", "SKIPPED"),  # new
    }
    result = await _compare(monkeypatch, left, right)
    flows = _flows(result)
    assert flows == {
        ("passed", "passed"): 1,
        ("passed", "failed"): 1,
        ("passed", "absent"): 1,
        ("failed", "passed"): 1,
        ("broken", "broken"): 1,
        ("absent", "skipped"): 1,
    }
    out_of_left = sum(n for (before, _), n in flows.items() if before != "absent")
    into_right = sum(n for (_, after), n in flows.items() if after != "absent")
    assert out_of_left == len(left) and into_right == len(right)
    # Each entry is a valid RunCompareTransition (the response's additive field).
    for transition in result["transitions"]:
        RunCompareTransition(**transition)


@pytest.mark.asyncio
async def test_a_renamed_test_is_one_flow_not_a_removal_plus_an_addition(monkeypatch):
    # The fuzzy pass pairs a removed test with a new PASSING one (a new failing
    # test is a new_failure, never paired), so: failed, renamed, now passing.
    left = {"old": _tc("old", "test_login", "FAILED")}
    right = {"new": _tc("new", "test_login_with_valid_credentials", "PASSED")}
    result = await _compare(monkeypatch, left, right)
    assert [d["paired_by"] for d in result["test_deltas"]] == ["fuzzy_name_match"], "the fixture must fuzzy-pair"
    assert _flows(result) == {("failed", "passed"): 1}


@pytest.mark.asyncio
async def test_transitions_are_ordered_and_carry_no_zero_flows(monkeypatch):
    left = {"a": _tc("a", "test_a", "FAILED"), "b": _tc("b", "test_b", "PASSED")}
    right = {"a": _tc("a", "test_a", "FAILED"), "b": _tc("b", "test_b", "PASSED")}
    result = await _compare(monkeypatch, left, right)
    assert [(t["before"], t["after"]) for t in result["transitions"]] == [("passed", "passed"), ("failed", "failed")]
    assert all(t["count"] > 0 for t in result["transitions"])
