"""E8.4 (slice 3): AI failure-kind labels in PR comments and MR notes obey the
human-review gate.

The labels are AI classifications of each failing test. A comment without them
still reports what failed, so the gate strips them instead of holding back the
comment -- and nothing about the gate may ever stop the comment from posting.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import report_distribution_policy as policy
from app.services.review_envelope import ReviewEnvelope

RUN = uuid.uuid4()
PROJECT = uuid.uuid4()
LABELS = {str(uuid.uuid4()): "product defect"}


@pytest.fixture
def world(monkeypatch):
    from app.core.config import settings

    state = SimpleNamespace(review="pending_review", drafts=False, broken=False)

    async def _envelope_for(db, run_id, workflow_type=None, ai_generated=True):
        if state.broken:
            raise RuntimeError("review_requests unavailable")
        return ReviewEnvelope(ai_generated=True, state=state.review, message="m", review_id=str(uuid.uuid4()))

    async def _drafts(db, project_id):
        return state.drafts

    monkeypatch.setattr(policy, "review_envelope_for_run", _envelope_for)
    monkeypatch.setattr(policy, "_project_allows_drafts", _drafts)
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    state.enforce = lambda on: monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", on)
    return state


async def _gate(labels=LABELS, channel="github_pr_comment"):
    return await policy.gate_kind_labels(
        None, run_id=RUN, project_id=PROJECT, kind_labels=dict(labels), channel=channel,
    )


# ── gate_kind_labels ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unreviewed_labels_are_stripped_when_enforced(world):
    labels, note, decision = await _gate()
    assert labels == {} and note is None
    assert decision.audit_action == "ai_report.distribution_refused"


@pytest.mark.asyncio
async def test_a_project_that_allows_drafts_keeps_the_labels_with_a_draft_note(world):
    world.drafts = True
    labels, note, decision = await _gate()
    assert labels == LABELS
    assert note == policy.KIND_LABELS_DRAFT_NOTE
    assert decision.audit_action == "ai_report.distributed_unreviewed"


@pytest.mark.asyncio
async def test_reviewed_labels_are_unchanged(world):
    world.review = "accepted"
    labels, note, decision = await _gate()
    assert labels == LABELS and note is None and decision.audit_action is None


@pytest.mark.asyncio
async def test_not_enforced_the_labels_are_unchanged_but_audited(world):
    world.enforce(False)
    labels, note, decision = await _gate()
    assert labels == LABELS and note is None
    assert decision.audit_action == "ai_report.distribution_would_refuse"


@pytest.mark.asyncio
async def test_no_labels_means_no_decision_and_no_lookup(world):
    world.broken = True  # would raise if the gate looked anything up
    labels, note, decision = await _gate(labels={})
    assert labels == {} and note is None and decision is None


@pytest.mark.asyncio
async def test_a_broken_gate_strips_the_labels_while_enforced(world):
    world.broken = True
    labels, note, decision = await _gate()
    assert labels == {} and decision is None


@pytest.mark.asyncio
async def test_a_broken_gate_changes_nothing_while_not_enforced(world):
    world.enforce(False)
    world.broken = True
    labels, _, _ = await _gate()
    assert labels == LABELS


# ── record_distribution_detached ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_detached_audit_commits_in_its_own_session(world, monkeypatch):
    committed: list[bool] = []

    class _Session:
        async def commit(self):
            committed.append(True)

    @asynccontextmanager
    async def _factory():
        yield _Session()

    audit = AsyncMock()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr("app.services.access_audit_service.log_access_change", audit)
    _, _, decision = await _gate()

    await policy.record_distribution_detached(decision, channel="github_pr_comment", run_id=RUN, project_id=PROJECT)

    assert committed == [True]
    assert audit.await_args.kwargs["after_value"]["channel"] == "github_pr_comment"


@pytest.mark.asyncio
async def test_the_detached_audit_never_raises(world, monkeypatch):
    @asynccontextmanager
    async def _broken():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _broken)
    _, _, decision = await _gate()
    await policy.record_distribution_detached(decision, channel="c", run_id=RUN, project_id=PROJECT)


@pytest.mark.asyncio
async def test_a_reviewed_decision_writes_no_audit_row(world, monkeypatch):
    world.review = "accepted"
    audit = AsyncMock()
    monkeypatch.setattr("app.services.access_audit_service.log_access_change", audit)
    _, _, decision = await _gate()
    await policy.record_distribution_detached(decision, channel="c", run_id=RUN, project_id=PROJECT)
    audit.assert_not_awaited()


# ── the rendered comment bodies ──────────────────────────────────────────────


def _run():
    return SimpleNamespace(
        id=RUN, total_tests=10, passed_tests=8, failed_tests=2, broken_tests=0,
        skipped_tests=0, pass_rate=80.0,
    )


def _part():
    from app.services import github_pr_comment_service as gh

    return gh._Partition(newly_failed=[{"name": "test_login", "message": "boom", "kind": "product defect"}])


def test_the_pr_comment_carries_the_draft_note_when_given():
    from app.services import github_pr_comment_service as gh

    body = gh._build_comment_body(_run(), PROJECT, "acme", _part(), None, draft_note=policy.KIND_LABELS_DRAFT_NOTE)
    assert policy.KIND_LABELS_DRAFT_NOTE in body
    assert body.startswith("<!-- testlookup-pr-summary:"), "the upsert marker must stay the first line"


def test_the_pr_comment_is_unchanged_without_a_draft_note():
    from app.services import github_pr_comment_service as gh

    assert "DRAFT" not in gh._build_comment_body(_run(), PROJECT, "acme", _part(), None)


def test_the_mr_note_carries_the_draft_note_through_the_shared_renderer():
    from app.services import gitlab_integration_service as gl

    body = gl._build_mr_note_body(_run(), PROJECT, "acme", _part(), None, draft_note=policy.KIND_LABELS_DRAFT_NOTE)
    assert policy.KIND_LABELS_DRAFT_NOTE in body
    assert body.startswith("<!-- testlookup-mr-summary:")


@pytest.mark.parametrize(
    "module, gather, channel",
    [
        ("app.services.github_pr_comment_service", "_gather_context", "github_pr_comment"),
        ("app.services.gitlab_integration_service", "_gather_mr_context", "gitlab_mr_note"),
    ],
)
def test_both_context_builders_gate_labels_before_partitioning_and_audit(module, gather, channel):
    """The labels feed the partition that renders the rows, so the gate has to
    run before it; and the decision is audited before the context is returned."""
    import importlib
    import inspect

    src = inspect.getsource(getattr(importlib.import_module(module), gather))
    gate_at = src.index("gate_kind_labels(")
    assert gate_at < src.index("_partition_tests(")
    assert f'channel="{channel}"' in src
    assert "draft_note=draft_note" in src
    assert src.index("record_distribution_detached(") < src.rindex("return _")
