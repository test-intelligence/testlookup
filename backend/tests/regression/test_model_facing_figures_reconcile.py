"""Regression: figures handed to a model must reconcile with each other.

This defect has now appeared three times in three different places:

  1. the summary agent's LLM prompt   ("6 tests, 2 failures, 60.0% pass rate")
  2. the summary agent's deterministic fallback narrative (same figures)
  3. the chat agent's run-context table ("| 10 | 4 | 44.4% |")

Each published `total`, `failed` and a `pass_rate` while omitting the skipped
and broken buckets. Pass rate is ``passed / executed``, so those three numbers
cannot all be true of the same run as a reader would combine them:
``10 - 4 = 6`` passed reads as 60%, not 44.4%.

Handed figures that do not reconcile, the model invents one that does. Observed
live on the homelab, 2026-08-16, with ground truth of 4 passed / 4 failed /
1 broken / 1 skipped:

    "In the most recent run (s2-6), 4 tests failed out of 10, resulting in a
     44.4% pass rate."

Faithful to its input, wrong to a reader. That is a product defect, not a model
one — the same prompt that omits the terms would mislead a person.

The guard parses the numbers back out of the rendered text and checks the
arithmetic, so it pins the PROPERTY that broke rather than any particular
wording, and keeps working when the table columns are reordered or renamed.
"""
from __future__ import annotations

import re

import pytest

# Ground truth mirrors the sweep fixture's final run.
RUN = {
    "build_number": "gt-6",
    "branch": "main",
    "status": "FAILED",
    "total_tests": 10,
    "passed_tests": 4,
    "failed_tests": 4,
    "skipped_tests": 1,
    "broken_tests": 1,
    "pass_rate": 44.4,
}


class _Row:
    """Stands in for the SQLAlchemy Row the builder formats."""

    def __init__(self, **kw):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.start_time = None
        for k, v in kw.items():
            setattr(self, k, v)


async def _render_async() -> str:
    """Render the chat run-context table by calling the REAL builder.

    The previous version rebuilt the row-formatting expression here and
    asserted on its own copy. Two mutations survived because of that: dropping
    the Executed column and computing it without excluding skips both left this
    mirror untouched. A guard that reimplements the thing it guards tests
    nothing.
    """
    from unittest.mock import patch

    from app.agents import conversation as conv

    row = _Row(**RUN)

    class _Result:
        def all(self):
            return [row]

    class _DB:
        async def execute(self, *_a, **_kw):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    conv._RUN_CONTEXT_CACHE.clear()
    with patch.object(conv, "AsyncSessionLocal", lambda: _DB()):
        text, _sources = await conv.ConversationAgent._fetch_run_context(
            conv.ConversationAgent(), project_id=None, limit=5,
        )
    conv._RUN_CONTEXT_CACHE.clear()
    assert text, "the real run-context builder returned nothing"
    return text


def _render() -> str:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_render_async())


# ── The chat table ──────────────────────────────────────────────────────────


def test_the_chat_table_selects_every_bucket():
    """Structural half: the query must fetch the terms the table needs. Without
    these columns the table cannot state them however it is formatted."""
    import inspect

    from app.agents.conversation import ConversationAgent

    src = inspect.getsource(ConversationAgent._fetch_run_context)
    for col in ("passed_tests", "failed_tests", "skipped_tests", "broken_tests"):
        assert f"TestRun.{col}" in src, (
            f"the chat run-context query no longer selects {col}; the table "
            f"then shows a pass rate that cannot be derived from the counts "
            f"beside it"
        )


@pytest.mark.asyncio
async def test_the_chat_table_states_the_executed_count():
    """The denominator must be a COLUMN, not something the reader computes.

    With only total and skipped, the model answered "4 tests failed out of 10
    executed" — executed is 9. The figures reconciled; the term it had to derive
    did not. `_render()` above mirrors the formatting, so this checks the real
    module rather than my copy of it.
    """
    text = await _render_async()
    assert "Executed" in text.splitlines()[0], (
        "the chat run-context table no longer states an Executed column, so the "
        "model must derive the pass-rate denominator from total - skipped — it "
        "got that wrong the last time it had to"
    )


def test_the_chat_table_states_the_basis_of_the_rate():
    import inspect

    from app.agents.conversation import ConversationAgent

    src = inspect.getsource(ConversationAgent._fetch_run_context)
    assert "skipped tests are" in src.lower() or "skipped are excluded" in src.lower(), (
        "the chat run context no longer tells the model what the pass rate is "
        "over; a rate whose denominator is unstated invites the model to assume "
        "the wrong one"
    )


@pytest.mark.asyncio
async def test_the_rendered_counts_sum_to_the_total():
    text = await _render_async()
    nums = [int(x) for x in re.findall(r"\|\s*(\d+)\s*(?=\|)", text)]
    # total, executed, passed, failed, skipped, broken — in column order.
    total, _executed, passed, failed, skipped, broken = nums[:6]
    assert passed + failed + skipped + broken == total, (
        f"rendered buckets do not sum to the total: {nums[:5]} in {text!r}"
    )


@pytest.mark.asyncio
async def test_the_rendered_rate_is_derivable_from_the_rendered_counts():
    """The exact inconsistency that made the model fabricate."""
    text = await _render_async()
    nums = [int(x) for x in re.findall(r"\|\s*(\d+)\s*(?=\|)", text)]
    total, executed, passed, _failed, skipped, _broken = nums[:6]
    rate = float(re.search(r"\*\*([\d.]+)%\*\*", text).group(1))

    # The denominator is STATED, not derived — that is the point of the column.
    assert executed == total - skipped, (
        f"stated Executed ({executed}) disagrees with total - skipped "
        f"({total - skipped}) — the column exists so nobody has to compute it"
    )
    assert abs(passed / executed * 100 - rate) < 0.15, (
        f"pass rate {rate}% is not passed/executed for the counts shown "
        f"({passed}/{executed}) — a reader combining these gets a different "
        f"number than the one printed"
    )


# ── The summary agent, guarded here too so the class is covered in one place ─


@pytest.mark.parametrize("skipped,broken", [(1, 1), (0, 0), (3, 0)])
def test_the_summary_results_line_reconciles_for_any_bucket_mix(skipped, broken):
    """The first two instances of this defect were in the summary agent. Pin it
    across bucket mixes, not just the one shape that was reported."""
    from app.agents.summary_agent import _format_results_line

    total = 12
    failed = 2
    passed = total - failed - skipped - broken
    executed = total - skipped
    line = _format_results_line({
        "total_tests": total, "passed_tests": passed, "failed_tests": failed,
        "skipped_tests": skipped, "pass_rate": passed / executed * 100,
    })

    got_passed = int(re.search(r"(\d+) passed", line).group(1))
    got_failed = int(re.search(r"(\d+) failed", line).group(1))
    got_skipped = int(re.search(r"(\d+) skipped", line).group(1))
    rate = float(re.search(r"Pass rate ([\d.]+)%", line).group(1))

    assert abs(got_passed / (total - got_skipped) * 100 - rate) < 0.15, (
        f"summary line does not reconcile: {line!r}"
    )
    assert got_failed == failed


# ── The same class on the CI-integration and chat-stub surfaces ─────────────
#
# These publish a fraction next to the pass rate. `passed/total` disagrees with
# a rate computed over EXECUTED tests whenever anything was skipped:
# "4/10 passed (44.4%)" invites the reader to compute 40%.


class _FakeRun:
    id = "22222222-2222-2222-2222-222222222222"
    build_number = "gt-6"
    branch = "main"
    project_id = "33333333-3333-3333-3333-333333333333"
    commit_hash = "abc1234"
    status = "FAILED"
    start_time = None
    end_time = None
    total_tests = 10
    passed_tests = 4
    failed_tests = 4
    skipped_tests = 1
    broken_tests = 1
    pass_rate = 44.4


def _fraction(text: str) -> tuple[int, int, float]:
    m = re.search(r"(\d+)/(\d+) passed \(([\d.]+)%\)", text)
    assert m, f"no 'x/y passed (z%)' fraction found in {text!r}"
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def test_github_check_title_fraction_matches_its_own_rate():
    from app.services.github_checks_service import _format_check_summary

    payload = _format_check_summary(_FakeRun(), "Proj")
    title = payload["output"]["title"]
    passed, denom, rate = _fraction(title)
    assert abs(passed / denom * 100 - rate) < 0.15, (
        f"GitHub check title states {passed}/{denom} beside {rate}% — a reader "
        f"computing the fraction gets {passed / denom * 100:.1f}%. Title: {title!r}"
    )


def test_github_check_body_still_breaks_out_every_bucket():
    """The compact title is only defensible because the body is complete."""
    from app.services.github_checks_service import _format_check_summary

    summary = _format_check_summary(_FakeRun(), "Proj")["output"]["summary"]
    for label in ("Passed:", "Failed:", "Broken:", "Skipped:"):
        assert label in summary, f"{label} missing from the check summary body"


def test_gitlab_commit_status_fraction_matches_its_own_rate():
    from app.services.gitlab_integration_service import _commit_status_payload

    desc = _commit_status_payload(_FakeRun(), "Proj")["description"]
    passed, denom, rate = _fraction(desc)
    assert abs(passed / denom * 100 - rate) < 0.15, (
        f"GitLab commit status states {passed}/{denom} beside {rate}%: {desc!r}"
    )


@pytest.mark.asyncio
async def test_chat_stub_summary_reconciles_end_to_end():
    """Behavioural, not a source grep.

    An earlier version of this guard only checked that the string
    ``passed = total - failed`` was absent from the module. That would not have
    caught a fixture drifting from the model, and it proves nothing about what
    the stub actually renders. Drive the real function and read the text.

    Ground truth: 4 passed / 4 failed / 1 broken / 1 skipped of 10, 44.4%.
    The old code derived passed as 10 - 4 = 6.
    """
    import sys
    import uuid as _uuid
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.services import chat_service

    run = SimpleNamespace(
        id=_uuid.uuid4(),
        project_id=_uuid.uuid4(),
        build_number="gt-6",
        total_tests=10,
        passed_tests=4,
        failed_tests=4,
        skipped_tests=1,
        broken_tests=1,
        pass_rate=44.4,
        start_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _Scalars(self._rows)

    class _DB:
        async def execute(self, *_a, **_kw):
            return _Result([run])

    cursor = SimpleNamespace(
        sort=lambda *a, **k: cursor,
        limit=lambda *a, **k: cursor,
        to_list=AsyncMock(return_value=[]),
    )
    mongo_db = {"run_summaries": SimpleNamespace(find=lambda *a, **k: cursor)}
    collections = SimpleNamespace(RUN_SUMMARIES="run_summaries")

    with patch.dict(
        sys.modules,
        {"app.db.mongo": SimpleNamespace(
            Collections=collections, get_mongo_db=lambda: mongo_db)},
    ):
        result = await chat_service.get_run_summaries(_DB(), str(run.project_id), 5)

    stub = next(r for r in result if r.get("is_stub"))
    text = stub["executive_summary"]

    m = re.search(r"Pass rate: ([\d.]+)% \((\d+)/(\d+) executed", text)
    assert m, f"stub summary no longer states passed/executed: {text!r}"
    rate, passed, executed = float(m.group(1)), int(m.group(2)), int(m.group(3))

    assert passed == run.passed_tests, (
        f"stub reports {passed} passed; the stored count is {run.passed_tests}. "
        f"Deriving it as total - failed counts broken and skipped as passes."
    )
    assert executed == run.total_tests - run.skipped_tests
    assert abs(passed / executed * 100 - rate) < 0.15, (
        f"stub states {passed}/{executed} beside {rate}%: {text!r}"
    )
    # A run with a broken test is not failure-free.
    assert "no failures" not in text, f"broken test not counted as a failure: {text!r}"


def test_chat_stub_reads_the_stored_passed_count():
    """`passed = total - failed` counted broken and skipped tests as passing —
    for this run it produced 6 where the stored count is 4."""
    import inspect

    from app.services import chat_service

    src = inspect.getsource(chat_service)
    assert "passed = total - failed" not in src, (
        "chat_service derives the passed count by subtraction again; broken and "
        "skipped tests get counted as passes and the number contradicts the "
        "pass rate printed beside it"
    )
    assert "run.passed_tests" in src, "chat_service no longer reads the stored count"


def test_chat_stub_does_not_call_a_broken_run_failure_free():
    """A run with 0 failed but 1 broken is not 'completed with no failures'."""
    import inspect

    from app.services import chat_service

    src = inspect.getsource(chat_service)
    assert "failures = failed + broken" in src, (
        "chat_service no longer counts broken tests as failures, so a run whose "
        "only problem was an errored test reads as clean"
    )
