"""E8.4 (slice 2): AI summaries in notifications and digests, and the verdict a
window report quotes, obey the human-review gate.

The AI summary email fires the moment a pipeline finishes -- exactly when its
review is still pending -- and the same text feeds both preference notifications
and event-driven digests. These tests drive the real Celery task body so the
gate is proven to sit before BOTH fan-outs, and pin the three outcomes: withheld
(enforced), drafted (project opt-in) and unchanged (reviewed, not AI-generated,
or not enforced).
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import report_distribution_policy as policy
from app.services.review_envelope import ReviewEnvelope, not_ai_generated

RUN = uuid.uuid4()
PROJECT = uuid.uuid4()
PIPELINE = uuid.uuid4()
EVIDENCE_HASH = "a" * 64
AI_TEXT = "The checkout suite regressed because the payment stub timed out."


def _envelope(state):
    if state == "not_applicable":
        return not_ai_generated()
    return ReviewEnvelope(ai_generated=True, state=state, message=state, review_id=str(uuid.uuid4()))


@pytest.fixture
def world(monkeypatch):
    from app.core.config import settings

    state = SimpleNamespace(review="pending_review", drafts=False)

    async def _envelope_for(db, run_id, workflow_type=None, ai_generated=True):
        return _envelope(state.review)

    async def _drafts(db, project_id):
        return state.drafts

    monkeypatch.setattr(policy, "review_envelope_for_run", _envelope_for)
    async def _envelope_for_subject(
        db, pipeline_run_id, *, ai_generated=True, evidence_bundle_sha256=None
    ):
        assert uuid.UUID(str(pipeline_run_id)) == PIPELINE
        assert evidence_bundle_sha256 == EVIDENCE_HASH
        return _envelope(state.review)

    monkeypatch.setattr(
        policy, "review_envelope_for_pipeline_subject", _envelope_for_subject
    )
    monkeypatch.setattr(policy, "_project_allows_drafts", _drafts)
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    state.enforce = lambda on: monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", on)
    return state


# ── gate_ai_summary_text ─────────────────────────────────────────────────────


async def _gate(text=AI_TEXT, ai_generated=True):
    return await policy.gate_ai_summary_text(
        None, run_id=RUN, project_id=PROJECT, summary_text=text,
        ai_generated=ai_generated, channel="ai_summary_notification",
    )


@pytest.mark.asyncio
async def test_an_unreviewed_summary_is_withheld_when_enforced(world):
    text, decision = await _gate()
    assert text == policy.REVIEW_PENDING_NOTICE
    assert AI_TEXT not in text
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_a_project_that_allows_drafts_gets_the_draft_line_first(world):
    world.drafts = True
    text, decision = await _gate()
    assert text.startswith(policy.DRAFT_WATERMARK)
    assert AI_TEXT in text
    assert decision.audit_action == "ai_report.distributed_unreviewed"


@pytest.mark.asyncio
async def test_a_reviewed_summary_is_unchanged(world):
    world.review = "accepted"
    text, _ = await _gate()
    assert text == AI_TEXT


@pytest.mark.asyncio
async def test_a_deterministic_summary_is_never_gated(world):
    text, decision = await _gate(ai_generated=False)
    assert text == AI_TEXT and decision is None


@pytest.mark.asyncio
async def test_not_enforced_the_summary_is_unchanged_but_audited(world):
    world.enforce(False)
    text, decision = await _gate()
    assert text == AI_TEXT
    assert decision.audit_action == "ai_report.distribution_would_refuse"


# ── the real Celery task body ────────────────────────────────────────────────


class _Session:
    def __init__(self, run, project, subs):
        self._run, self._project, self._subs = run, project, subs

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM digest_subscriptions" in sql:
            return SimpleNamespace(all=lambda: list(self._subs))
        if "FROM projects" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: self._project)
        return SimpleNamespace(scalar_one_or_none=lambda: self._run)

    async def commit(self):
        pass


def _drive(monkeypatch, *, doc, fallback_text=None):
    from app.services.notification import manager
    from app.worker import tasks

    run = SimpleNamespace(id=RUN, project_id=PROJECT, pass_rate=80.0, total_tests=10, failed_tests=2)
    sub = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=None, trigger_filter="all")

    @asynccontextmanager
    async def _factory():
        yield _Session(run, SimpleNamespace(name="Checkout"), [(sub, "qa@example.com")])

    collection = SimpleNamespace(find_one=AsyncMock(return_value=doc))
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _AnyKey(collection))
    preferences = AsyncMock()
    digests = AsyncMock()
    monkeypatch.setattr(manager, "dispatch_ai_summary_notifications", preferences)
    monkeypatch.setattr(manager, "stage_explicit_notification_deliveries", digests)
    access_audit = AsyncMock()
    monkeypatch.setattr("app.services.access_audit_service.log_access_change", access_audit)
    monkeypatch.setattr(tasks, "run_matches_digest_scope", lambda _sub, _run: True)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    if fallback_text is not None:
        monkeypatch.setattr(
            "app.services.run_summary_service.build_fallback_summary",
            AsyncMock(return_value=SimpleNamespace(executive_summary=fallback_text, executive_panel=None)),
        )
    tasks.dispatch_ai_summary_email.run(
        str(RUN), str(PROJECT), "42", str(PIPELINE), EVIDENCE_HASH
    )
    return (
        preferences.await_args.kwargs,
        digests.await_args.kwargs["deliveries"],
        access_audit,
    )


class _AnyKey(dict):
    """Mongo stand-in: any collection name returns the same fake collection."""

    def __init__(self, collection):
        super().__init__()
        self._collection = collection

    def __missing__(self, _key):
        return self._collection


def test_the_task_withholds_the_ai_summary_from_preferences_and_digests(monkeypatch, world):
    prefs, deliveries, access_audit = _drive(
        monkeypatch, doc={"executive_summary": AI_TEXT, "executive_panel": {"headline": "NO_GO"}},
    )
    assert prefs["executive_summary"] == policy.REVIEW_PENDING_NOTICE
    assert prefs["executive_panel"] is None, "the panel carries the AI verdict; it must go with the text"
    assert prefs["original_executive_summary"] == AI_TEXT
    assert prefs["summary_is_ai"] is True
    assert deliveries and deliveries[0]["body"] == policy.REVIEW_PENDING_NOTICE
    marker = deliveries[0]["metadata"]["_review_gate_v1"]
    assert marker["original_summary"] == AI_TEXT
    assert "Release signal:" not in marker["withheld_body_prefix"]
    assert marker["pipeline_run_id"] == str(PIPELINE)
    assert marker["evidence_bundle_sha256"] == EVIDENCE_HASH
    access_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_relay_refreshes_terminal_and_accepted_review_content(
    monkeypatch, world,
):
    from app.services.notification import manager

    row = SimpleNamespace(
        title="AI summary",
        body="stale draft",
        run_id=RUN,
        project_id=PROJECT,
        delivery_key="delivery-key",
        delivery_metadata={
            "executive_panel": None,
            manager._REVIEW_GATE_METADATA_KEY: {
                "original_summary": AI_TEXT,
                "accepted_body_prefix": "accepted prefix\n\n",
                "withheld_body_prefix": "withheld prefix\n\n",
                "original_executive_panel": {"headline": "NO_GO"},
                "ai_generated": True,
                "pipeline_run_id": str(PIPELINE),
                "evidence_bundle_sha256": EVIDENCE_HASH,
            },
        },
    )

    world.review = "rejected"
    _title, rejected_body, rejected_metadata, rejected_decision = (
        await manager._refresh_review_gated_delivery(object(), row)
    )
    assert rejected_body == policy.REVIEW_PENDING_NOTICE
    assert AI_TEXT not in rejected_body
    assert rejected_metadata["executive_panel"] is None
    assert manager._REVIEW_GATE_METADATA_KEY not in rejected_metadata
    assert rejected_decision is not None and rejected_decision.allowed is False

    world.review = "accepted"
    _title, accepted_body, accepted_metadata, accepted_decision = (
        await manager._refresh_review_gated_delivery(object(), row)
    )
    assert accepted_body == f"accepted prefix\n\n{AI_TEXT}"
    assert accepted_metadata["executive_panel"] == {"headline": "NO_GO"}
    assert manager._REVIEW_GATE_METADATA_KEY not in accepted_metadata
    assert accepted_decision is not None and accepted_decision.allowed is True


@pytest.mark.asyncio
async def test_notification_retry_cannot_borrow_a_newer_run_review(
    monkeypatch, world,
):
    from app.services.notification import manager

    monkeypatch.setattr(
        policy,
        "review_envelope_for_run",
        AsyncMock(return_value=_envelope("accepted")),
    )
    monkeypatch.setattr(
        policy,
        "review_envelope_for_pipeline_subject",
        AsyncMock(return_value=_envelope("rejected")),
    )
    row = SimpleNamespace(
        title="AI summary",
        body="stale draft",
        run_id=RUN,
        project_id=PROJECT,
        delivery_key="exact-subject-delivery",
        delivery_metadata={
            "failed_tests": 0,
            manager._REVIEW_GATE_METADATA_KEY: {
                "original_summary": AI_TEXT,
                "accepted_body_prefix": "",
                "original_executive_panel": {"headline": "NO_GO"},
                "ai_generated": True,
                "pipeline_run_id": str(PIPELINE),
                "evidence_bundle_sha256": EVIDENCE_HASH,
            },
        },
    )

    _title, body, _metadata, decision = await manager._refresh_review_gated_delivery(
        object(), row
    )

    assert body == policy.REVIEW_PENDING_NOTICE
    assert AI_TEXT not in body
    assert decision is not None and decision.envelope.state == "rejected"
    policy.review_envelope_for_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_withheld_notification_prefix_has_no_invented_release_signal(
    monkeypatch,
):
    from app.services.notification import manager

    staged = AsyncMock()
    monkeypatch.setattr(manager, "_load_and_notify", staged)

    await manager.dispatch_ai_summary_notifications(
        PROJECT,
        RUN,
        "42",
        "Checkout",
        policy.REVIEW_PENDING_NOTICE,
        executive_panel=None,
        pass_rate=80.0,
        total_tests=10,
        failed_tests=2,
        original_executive_summary=AI_TEXT,
        original_executive_panel={"status_signal": "NO_GO", "risk_score": 90},
        summary_is_ai=True,
        pipeline_run_id=str(PIPELINE),
        evidence_bundle_sha256=EVIDENCE_HASH,
    )

    metadata = staged.await_args.args[4]
    context = metadata[manager._REVIEW_GATE_METADATA_KEY]
    assert "Release signal: NO GO" in context["accepted_body_prefix"]
    assert "Release signal:" not in context["withheld_body_prefix"]
    assert "2 failures detected" in context["withheld_body_prefix"]


@pytest.mark.asyncio
async def test_legacy_queued_notification_is_sanitized_and_fails_closed(
    monkeypatch, world,
):
    from app.services.notification import manager

    row = SimpleNamespace(
        title="AI summary",
        body="stale draft",
        run_id=RUN,
        project_id=PROJECT,
        delivery_key="legacy-delivery",
        delivery_metadata={
            "project_name": "Checkout",
            "build_number": "42",
            "pass_rate": 80.0,
            "failed_tests": 2,
            manager._REVIEW_GATE_METADATA_KEY: {
                "original_summary": AI_TEXT,
                "accepted_body_prefix": "Release signal: CONDITIONAL GO.\n\n",
                "withheld_body_prefix": "Release signal: CONDITIONAL GO.\n\n",
                "ai_generated": True,
            },
        },
    )

    _title, body, metadata, decision = await manager._refresh_review_gated_delivery(
        object(), row
    )

    assert body.endswith(policy.REVIEW_PENDING_NOTICE)
    assert "2 failures detected in Checkout" in body
    assert "Release signal:" not in body
    assert AI_TEXT not in body
    assert metadata["executive_panel"] is None
    assert decision is None


def test_the_task_drafts_the_summary_under_a_project_opt_in(monkeypatch, world):
    world.drafts = True
    prefs, deliveries, _audit = _drive(monkeypatch, doc={"executive_summary": AI_TEXT})
    assert prefs["executive_summary"].startswith(policy.DRAFT_WATERMARK)
    assert deliveries[0]["body"].startswith(policy.DRAFT_WATERMARK)


def test_the_task_sends_a_deterministic_fallback_unchanged(monkeypatch, world):
    prefs, _, _audit = _drive(monkeypatch, doc=None, fallback_text="3 failures in checkout.")
    assert prefs["executive_summary"] == "3 failures in checkout."


def test_the_task_is_unchanged_while_the_gate_is_not_enforced(monkeypatch, world):
    world.enforce(False)
    prefs, deliveries, _audit = _drive(monkeypatch, doc={"executive_summary": AI_TEXT})
    assert prefs["executive_summary"] == AI_TEXT
    assert deliveries[0]["body"] == AI_TEXT


# ── Investigator narrative subject ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_investigator_excerpt_is_withheld_by_its_exact_subject(
    monkeypatch, world,
):
    seen = {}

    async def _subject(_db, pipeline_id, *, evidence_bundle_sha256=None):
        seen["pipeline_id"] = pipeline_id
        seen["evidence_bundle_sha256"] = evidence_bundle_sha256
        return _envelope("pending_review")

    monkeypatch.setattr(policy, "review_envelope_for_pipeline_subject", _subject)
    investigation_id = uuid.uuid4()
    evidence_hash = "d" * 64
    text, decision = await policy.gate_investigation_excerpt(
        None,
        investigation_id=investigation_id,
        project_id=PROJECT,
        excerpt=AI_TEXT,
        channel="digest_attachment",
        evidence_bundle_sha256=evidence_hash,
    )

    assert seen["pipeline_id"] == uuid.uuid5(
        uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}"
    )
    assert seen["evidence_bundle_sha256"] == evidence_hash
    assert text == policy.INVESTIGATION_REVIEW_PENDING_NOTICE
    assert AI_TEXT not in text
    assert decision is not None and decision.allowed is False


@pytest.mark.asyncio
async def test_accepted_investigator_excerpt_is_distributed(monkeypatch, world):
    monkeypatch.setattr(
        policy,
        "review_envelope_for_pipeline_subject",
        AsyncMock(return_value=_envelope("accepted")),
    )
    text, decision = await policy.gate_investigation_excerpt(
        None,
        investigation_id=uuid.uuid4(),
        project_id=PROJECT,
        excerpt=AI_TEXT,
        channel="analysis_report",
    )
    assert text == AI_TEXT
    assert decision is not None and decision.envelope.state == "accepted"


@pytest.mark.asyncio
async def test_superseded_investigator_excerpt_retains_terminal_subject(
    monkeypatch, world,
):
    subject = AsyncMock(return_value=_envelope("superseded"))
    monkeypatch.setattr(policy, "review_envelope_for_pipeline_subject", subject)

    text, decision = await policy.gate_investigation_excerpt(
        None,
        investigation_id=uuid.uuid4(),
        project_id=PROJECT,
        excerpt=AI_TEXT,
        channel="analysis_report",
        evidence_bundle_sha256="e" * 64,
    )

    assert text == policy.INVESTIGATION_REVIEW_PENDING_NOTICE
    assert decision is not None and decision.envelope.state == "superseded"
    assert subject.await_args.kwargs["evidence_bundle_sha256"] == "e" * 64


@pytest.mark.asyncio
async def test_opted_in_investigator_excerpt_is_watermarked(monkeypatch, world):
    world.drafts = True
    monkeypatch.setattr(
        policy,
        "review_envelope_for_pipeline_subject",
        AsyncMock(return_value=_envelope("pending_review")),
    )
    text, decision = await policy.gate_investigation_excerpt(
        None,
        investigation_id=uuid.uuid4(),
        project_id=PROJECT,
        excerpt=AI_TEXT,
        channel="digest_attachment",
    )
    assert text.startswith(policy.DRAFT_WATERMARK)
    assert AI_TEXT in text
    assert decision is not None
    assert decision.audit_action == "ai_report.distributed_unreviewed"


def _break_the_gate(monkeypatch):
    async def _boom(*_a, **_kw):
        raise RuntimeError("review_requests unavailable")

    monkeypatch.setattr(policy, "review_envelope_for_run", _boom)


def test_a_broken_gate_never_stops_the_notification_and_fails_closed_when_enforced(monkeypatch, world):
    """If the gate cannot decide, the event must still reach people -- and
    while enforced, without the unreviewed AI text."""
    _break_the_gate(monkeypatch)
    prefs, deliveries, _audit = _drive(
        monkeypatch, doc={"executive_summary": AI_TEXT, "executive_panel": {"headline": "NO_GO"}},
    )
    assert prefs["executive_summary"] == policy.REVIEW_PENDING_NOTICE
    assert prefs["executive_panel"] is None
    assert deliveries[0]["body"] == policy.REVIEW_PENDING_NOTICE


def test_a_broken_gate_changes_nothing_while_not_enforced(monkeypatch, world):
    world.enforce(False)
    _break_the_gate(monkeypatch)
    prefs, deliveries, _audit = _drive(monkeypatch, doc={"executive_summary": AI_TEXT})
    assert prefs["executive_summary"] == AI_TEXT
    assert deliveries[0]["body"] == AI_TEXT


# ── the verdict quoted by the window analysis report ─────────────────────────


def _verdict():
    return {"recommendation": "GO", "risk_score": 12, "project_id": str(PROJECT)}


@pytest.mark.asyncio
async def test_an_unreviewed_verdict_reads_pending_review_when_enforced(world):
    out = await policy.gate_release_verdict(None, _verdict(), test_run_id=RUN)
    assert out["recommendation"] == "PENDING_REVIEW"
    assert out["draft_recommendation"] == "GO"


@pytest.mark.asyncio
@pytest.mark.parametrize("review", ["rejected", "superseded"])
async def test_terminal_review_redacts_the_embedded_release_verdict(world, review):
    world.review = review
    verdict = {
        **_verdict(),
        "original_recommendation": "GO",
        "blocking_issues": ["rejected blocker narrative"],
        "conditions_for_go": ["rejected condition narrative"],
        "reasoning": "rejected reasoning narrative",
    }

    out = await policy.gate_release_verdict(None, verdict, test_run_id=RUN)

    assert out["recommendation"] == "PENDING_REVIEW"
    assert out.get("draft_recommendation") is None
    assert out["blocking_issues"] == []
    assert out["conditions_for_go"] == []
    assert out["reasoning"] is None
    assert out["original_recommendation"] is None


@pytest.mark.asyncio
async def test_a_project_that_allows_drafts_sees_the_value_marked_draft(world):
    world.drafts = True
    out = await policy.gate_release_verdict(None, _verdict(), test_run_id=RUN)
    assert out["recommendation"] == "GO"
    assert out["draft_watermark"] == policy.DRAFT_WATERMARK


@pytest.mark.asyncio
@pytest.mark.parametrize("review, override", [("accepted", None), ("pending_review", "QA lead overrode it")])
async def test_accepted_or_overridden_verdicts_are_unchanged(world, review, override):
    world.review = review
    out = await policy.gate_release_verdict(None, _verdict(), test_run_id=RUN, human_override=override)
    assert out["recommendation"] == "GO" and "draft_recommendation" not in out


@pytest.mark.asyncio
async def test_a_verdict_is_unchanged_while_not_enforced(world):
    world.enforce(False)
    out = await policy.gate_release_verdict(None, _verdict(), test_run_id=RUN)
    assert out["recommendation"] == "GO"
    assert out["review_state"] == "pending_review"


@pytest.mark.asyncio
async def test_the_report_collector_gates_the_verdict_it_quotes(world):
    from app.services import analysis_report_service as ars

    decision = SimpleNamespace(
        recommendation="NO_GO", risk_score=70, conditions_for_go=[], blocking_issues=["x"],
        policy_evaluation={}, created_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        test_run_id=RUN, human_override=None,
    )

    class _DB:
        async def execute(self, _stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: decision))

    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    out = await ars._collect_gate(_DB(), PROJECT, now.replace(day=6), now)

    assert out["decision"]["recommendation"] == "PENDING_REVIEW"
    assert out["decision"]["draft_recommendation"] == "NO_GO"


def test_the_report_says_the_verdict_awaits_review():
    from app.services import analysis_report_service as ars

    body = ars._status_badge("PENDING_REVIEW")
    assert "amber" in body

    data = {"sections": {"gate": {"ok": True, "data": {"decision": {
        "recommendation": "PENDING_REVIEW", "risk_score": 70, "draft_recommendation": "NO_GO",
    }}}}}
    html = _render_gate_only(ars, data)
    assert "awaiting human review" in html
    assert "NO_GO" not in html, "the withheld draft value must not appear in the report"


def test_the_report_marks_a_drafted_verdict():
    from app.services import analysis_report_service as ars

    data = {"sections": {"gate": {"ok": True, "data": {"decision": {
        "recommendation": "GO", "risk_score": 12, "draft_watermark": policy.DRAFT_WATERMARK,
    }}}}}
    assert policy.DRAFT_WATERMARK in _render_gate_only(ars, data)


def _render_gate_only(ars, data):
    """Render through the real report renderer using the shared fixture shape,
    replacing only the gate section."""
    import importlib

    fixture = importlib.import_module("tests.test_analysis_report")
    full = fixture._data()
    full["sections"]["gate"] = data["sections"]["gate"]
    return ars.render_analysis_report_html(full)
