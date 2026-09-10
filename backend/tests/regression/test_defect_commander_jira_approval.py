"""DefectCommander must not file a Jira ticket from unreviewed LLM output.

Re-audit finding C4. The agent's ``_promote`` called ``_create_jira_ticket``
directly, in the same call stack, immediately after generating the ticket body
with an LLM. The chain that reached it:

  raw ingested failure text (``embed_and_cluster``: ``representative_error``,
  truncated only) → embedded verbatim into the ``defect_promotion_ticket``
  prompt with no untrusted-data delimiter → LLM JSON parsed with no schema or
  content validation → straight into a live external Jira write.

Two controls the rest of the codebase already applies were both missing:

* ``check_jira_ticket_creation_policy`` — ``requires_approval`` returns True
  unconditionally for ``ActionType.JIRA_TICKET_CREATION`` ("External system
  mutation always requires approval"). The service promotion path in
  ``defect_promotion_service`` honours it; the agent did not.
* ``AI_OFFLINE_MODE`` — a hard kill switch above any integration flag, checked
  on the service path and not on the agent path.

So the agent is the one mutating agent that acted unsupervised. These tests pin
that it now proposes rather than acts.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.defect_commander import DefectCommander
from app.services.action_policy import ActionStatus


class _Result:
    """Minimal stand-in for a SQLAlchemy Result."""

    def __init__(self, value=None, items=()):
        self._value = value
        self._items = list(items)

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._items


class _Session:
    """AsyncSessionLocal() stand-in: cluster lookup, then analyses lookup."""

    def __init__(self, cluster):
        self._cluster = cluster
        self._calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, *_args, **_kwargs):
        self._calls += 1
        if self._calls == 1:
            return _Result(value=self._cluster)
        return _Result(items=[])


def _cluster():
    return SimpleNamespace(
        cluster_id="cl_a",
        test_run_id="rrrrrrrr-rrrr-rrrr-rrrr-rrrrrrrrrrrr",
        label="Timeouts in checkout",
        size=3,
        member_test_ids=["11111111-1111-1111-1111-111111111111"],
        representative_error="AssertionError: boom",
    )


@pytest.fixture
def harness(monkeypatch):
    """Drive the REAL _promote with only its collaborators stubbed.

    The policy gate, the offline flag and the Jira client stay meaningful, so a
    pass here cannot come from an unrelated early return.
    """
    cluster = _cluster()
    monkeypatch.setattr(
        "app.agents.defect_commander.AsyncSessionLocal",
        lambda: _Session(cluster),
    )
    monkeypatch.setattr(
        "app.agents.defect_commander._find_duplicate_semantic",
        AsyncMock(return_value=(None, False)),
    )
    monkeypatch.setattr(
        "app.agents.defect_commander._dim_scores_from_analyses", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        "app.agents.defect_commander.score_cluster", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        "app.agents.defect_commander.get_scoring_model_info",
        lambda *a, **k: {"dimensions": []},
    )
    monkeypatch.setattr(
        DefectCommander,
        "_jira_content_for_state",
        AsyncMock(return_value={"title": "t", "description": "d", "labels": []}),
    )
    persist = AsyncMock(return_value="dddddddd-dddd-dddd-dddd-dddddddddddd")
    monkeypatch.setattr(DefectCommander, "_persist_defect", persist)
    jira = AsyncMock(return_value=({"key": "QA-1", "ticket_url": "u"}, "u"))
    monkeypatch.setattr("app.agents.defect_commander._create_jira_ticket", jira)

    return SimpleNamespace(jira=jira, persist=persist)


STATE = {
    "cluster_id": "cl_a",
    "test_run_id": "rrrrrrrr-rrrr-rrrr-rrrr-rrrrrrrrrrrr",
    "project_id": "pppppppp-pppp-pppp-pppp-pppppppppppp",
    "project_key": "QA",
}


@pytest.mark.asyncio
async def test_no_jira_ticket_is_filed_under_the_real_policy(harness, monkeypatch):
    """The headline defect, driven end to end through the real gate.

    ``requires_approval`` returns True unconditionally for Jira creation, so a
    correctly-gated agent can never reach the external write.

    AI_OFFLINE_MODE is switched OFF deliberately. It defaults to True, and it
    blocks the write first, so leaving it on would let this test pass even if
    the approval gate were removed entirely — the offline flag would be doing
    all the work. Turning it off isolates the approval decision.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    result = await DefectCommander()._promote(dict(STATE))

    harness.jira.assert_not_awaited()
    assert result["jira_ticket"] is None
    assert result["requires_approval"] is True
    assert result["jira_blocked_reason"] == "pending_review"
    # .value, never str(): str(ActionStatus.PENDING_REVIEW) renders the
    # member name, which is the exact trap this fix removed.
    assert result["approval_status"] == ActionStatus.PENDING_REVIEW.value


@pytest.mark.asyncio
async def test_the_defect_row_is_written_as_pending_review(harness):
    """A proposal still persists — it is reviewable, not discarded."""
    await DefectCommander()._promote(dict(STATE))

    harness.persist.assert_awaited_once()
    kwargs = harness.persist.await_args.kwargs
    assert kwargs["approval_status"] == ActionStatus.PENDING_REVIEW.value
    assert kwargs["policy_evaluation"]["requires_approval"] is True


@pytest.mark.asyncio
async def test_offline_mode_blocks_the_write_even_if_a_policy_approved_it(
    harness, monkeypatch
):
    """AI_OFFLINE_MODE is a kill switch above the approval decision."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(
        "app.agents.defect_commander.check_jira_ticket_creation_policy",
        AsyncMock(
            return_value={
                "requires_approval": False,
                "initial_status": ActionStatus.APPROVED,
                "policy_reasons": [],
            }
        ),
    )

    result = await DefectCommander()._promote(dict(STATE))

    harness.jira.assert_not_awaited()
    assert result["jira_blocked_reason"] == "offline_mode"


@pytest.mark.asyncio
async def test_an_approved_online_promotion_still_files_the_ticket(
    harness, monkeypatch
):
    """The gate must not break the approved path it exists to protect."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(
        "app.agents.defect_commander.check_jira_ticket_creation_policy",
        AsyncMock(
            return_value={
                "requires_approval": False,
                "initial_status": ActionStatus.APPROVED,
                "policy_reasons": [],
            }
        ),
    )

    result = await DefectCommander()._promote(dict(STATE))

    harness.jira.assert_awaited_once()
    assert result["jira_ticket"] == {"key": "QA-1", "ticket_url": "u"}
    assert result["jira_blocked_reason"] is None


def test_source_gates_the_jira_call_on_approved_status():
    """The call must sit behind an APPROVED check, not run unconditionally."""
    import inspect

    from app.agents.defect_commander import DefectCommander

    source = inspect.getsource(DefectCommander._promote)
    assert "check_jira_ticket_creation_policy" in source, (
        "_promote does not evaluate the Jira creation policy"
    )
    assert "ActionStatus.APPROVED" in source, (
        "_promote does not gate the Jira write on an APPROVED decision"
    )
    assert "AI_OFFLINE_MODE" in source or "settings.AI_OFFLINE_MODE" in source, (
        "_promote does not honour the AI_OFFLINE_MODE kill switch"
    )
    # The write must be in the else branch of the gate, never before it.
    gate_at = source.index("check_jira_ticket_creation_policy")
    write_at = source.index("_create_jira_ticket(")
    assert gate_at < write_at, (
        "the Jira write appears before the policy evaluation, so the gate "
        "cannot affect it"
    )


def test_persisted_defect_records_the_approval_decision():
    """A proposal must be reviewable, so the row carries its status."""
    import inspect

    from app.agents.defect_commander import DefectCommander

    source = inspect.getsource(DefectCommander._promote)
    assert "approval_status=initial_status" in source
    assert "policy_evaluation=policy_result" in source

    signature = inspect.signature(DefectCommander._persist_defect)
    assert "approval_status" in signature.parameters
    assert "policy_evaluation" in signature.parameters


def test_persist_defaults_to_pending_review_when_no_status_supplied():
    """A caller that forgets the argument must not get an approved row."""
    import inspect

    from app.agents.defect_commander import DefectCommander

    source = inspect.getsource(DefectCommander._persist_defect)
    assert "approval_status or ActionStatus.PENDING_REVIEW" in source, (
        "the Defect model defaults approval_status to 'approved', so the agent "
        "must supply PENDING_REVIEW explicitly rather than inherit that default"
    )


def test_policy_always_requires_approval_for_jira_creation():
    """The property the gate depends on, asserted directly.

    If this ever returns False, the gate above silently becomes a no-op, so it
    is pinned here rather than assumed.
    """
    from app.services.action_policy import ActionType, requires_approval

    assert requires_approval(ActionType.JIRA_TICKET_CREATION) is True
    assert requires_approval(ActionType.JIRA_TICKET_CREATION, confidence_score=100) is True
