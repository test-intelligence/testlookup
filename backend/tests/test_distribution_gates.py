"""E8.4 (first slice): unreviewed AI reports do not leave the system unmarked.

Pins the policy, the release-gate value CI reads, the report PDF export and the
share links. Also pins the rollout: with ``REVIEW_GATE_ENFORCED`` off (the
default) nothing is refused and no value changes, but every refusal that would
have happened is audited, so the impact is measurable before enforcement.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import report_distribution_policy as policy
from app.services.review_envelope import ReviewEnvelope, not_ai_generated

RUN = uuid.uuid4()
PROJECT = uuid.uuid4()
REVIEW_ID = str(uuid.uuid4())


def _envelope(state):
    if state == "not_applicable":
        return not_ai_generated()
    return ReviewEnvelope(ai_generated=True, state=state, message=state,
                          review_id=REVIEW_ID if state != "none" else None)


@pytest.fixture
def world(monkeypatch):
    """Control the review state, the project opt-in and the enforcement flag."""
    from app.core.config import settings

    state = SimpleNamespace(review="pending_review", drafts=False, enforced=True)

    async def _envelope_for(db, run_id, workflow_type=None, ai_generated=True):
        state.workflow_type = workflow_type
        return _envelope(state.review)

    async def _drafts(db, project_id):
        return state.drafts

    monkeypatch.setattr(policy, "review_envelope_for_run", _envelope_for)
    monkeypatch.setattr(policy, "_project_allows_drafts", _drafts)
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)

    def _set(**kw):
        for key, value in kw.items():
            setattr(state, key, value)
        if "enforced" in kw:
            monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", kw["enforced"])

    state.set = _set
    return state


async def _decide(**kw):
    return await policy.decide_run_distribution(None, run_id=RUN, project_id=PROJECT, channel="t", **kw)


def test_enforcement_is_off_by_default():
    from app.core.config import Settings

    assert Settings.model_fields["REVIEW_GATE_ENFORCED"].default is False


# ── the policy ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reviewed_report_goes_out_unmarked_and_unaudited(world):
    world.set(review="accepted")
    decision = await _decide()
    assert decision.allowed and decision.watermark is None and decision.audit_action is None


@pytest.mark.asyncio
async def test_a_deterministic_report_goes_out_unmarked(world):
    world.set(review="not_applicable")
    decision = await _decide()
    assert decision.allowed and decision.watermark is None and decision.audit_action is None


@pytest.mark.asyncio
async def test_an_unreviewed_report_is_refused_when_enforced(world):
    decision = await _decide()
    assert decision.allowed is False
    assert decision.audit_action == "ai_report.distribution_refused"
    detail = policy.refusal_detail(decision)
    assert detail["code"] == "report_pending_review"
    assert detail["links"]["review"].endswith(REVIEW_ID)


@pytest.mark.asyncio
async def test_a_project_that_allows_drafts_gets_a_watermarked_audited_draft(world):
    world.set(drafts=True)
    decision = await _decide()
    assert decision.allowed and decision.watermark == policy.DRAFT_WATERMARK
    assert decision.audit_action == "ai_report.distributed_unreviewed"


@pytest.mark.asyncio
async def test_include_unreviewed_gets_a_watermarked_audited_draft(world):
    decision = await _decide(include_unreviewed=True)
    assert decision.allowed and decision.watermark == policy.DRAFT_WATERMARK
    assert decision.audit_action == "ai_report.distributed_unreviewed"


@pytest.mark.asyncio
@pytest.mark.parametrize("review", ["rejected", "superseded"])
async def test_a_rejected_or_superseded_report_is_never_a_draft(world, review):
    """A report a person rejected is not awaiting review; opting in to drafts
    must not send it."""
    world.set(review=review, drafts=True)
    decision = await _decide(include_unreviewed=True)
    assert decision.allowed is False and decision.watermark is None


@pytest.mark.asyncio
async def test_not_enforced_nothing_is_refused_but_the_would_be_refusal_is_audited(world):
    world.set(enforced=False)
    decision = await _decide()
    assert decision.allowed is True
    assert decision.watermark is None, "shadow mode must not change what anyone receives"
    assert decision.audit_action == "ai_report.distribution_would_refuse"


@pytest.mark.asyncio
async def test_record_distribution_writes_the_trail_without_the_notes(world, monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr("app.services.access_audit_service.log_access_change", audit)
    decision = await _decide()

    await policy.record_distribution(None, decision, channel="report_pdf", run_id=RUN, project_id=PROJECT)

    kwargs = audit.await_args.kwargs
    assert kwargs["action"] == "ai_report.distribution_refused"
    assert kwargs["after_value"]["channel"] == "report_pdf"
    assert kwargs["after_value"]["review_id"] == REVIEW_ID


def test_every_audit_action_fits_the_audit_column():
    from app.models.postgres import AccessAuditLog

    length = AccessAuditLog.__table__.columns["action"].type.length
    for action in set(policy._AUDIT_ACTIONS.values()):
        assert len(action) <= length, action


# ── the release gate value ───────────────────────────────────────────────────


def _council(**kw):
    from app.models.schemas import ReleaseCouncilResponse

    base = dict(run_id=str(RUN), recommendation="GO", risk_score=10)
    base.update(kw)
    return ReleaseCouncilResponse(**base)


@pytest.mark.asyncio
async def test_an_unreviewed_ai_decision_reads_pending_review(world):
    out = await policy.apply_release_review_gate(None, _council(), run_id=RUN)
    assert out.recommendation == "PENDING_REVIEW"
    assert out.draft_recommendation == "GO"
    assert out.review_gate_enforced is True and out.requires_human_review is True
    assert world.workflow_type == "deep"


@pytest.mark.asyncio
async def test_allow_advisory_returns_the_marked_draft_value(world):
    out = await policy.apply_release_review_gate(None, _council(recommendation="NO_GO"), run_id=RUN, allow_advisory=True)
    assert out.recommendation == "ADVISORY_NO_GO"


@pytest.mark.asyncio
@pytest.mark.parametrize("review", ["rejected", "superseded"])
async def test_terminal_review_never_exposes_release_draft_content(world, review):
    world.set(review=review)
    out = await policy.apply_release_review_gate(
        None,
        _council(
            recommendation="NO_GO",
            blocking_issues=["rejected blocker narrative"],
            conditions_for_go=["rejected condition narrative"],
            reasoning="rejected reasoning narrative",
        ),
        run_id=RUN,
        allow_advisory=True,
    )

    assert out.recommendation == "PENDING_REVIEW"
    assert out.draft_recommendation is None
    assert out.blocking_issues == []
    assert out.conditions_for_go == []
    assert out.reasoning is None


@pytest.mark.asyncio
async def test_an_accepted_decision_is_unchanged(world):
    world.set(review="accepted")
    out = await policy.apply_release_review_gate(None, _council(), run_id=RUN)
    assert out.recommendation == "GO" and out.draft_recommendation is None


@pytest.mark.asyncio
async def test_a_synthesized_quick_look_is_not_ai_and_unchanged(world):
    out = await policy.apply_release_review_gate(None, _council(synthesized=True), run_id=RUN)
    assert out.recommendation == "GO"
    assert out.review.state == "not_applicable" and out.requires_human_review is False


@pytest.mark.asyncio
async def test_a_human_override_is_a_decision_already_made(world):
    out = await policy.apply_release_review_gate(None, _council(human_override="NO_GO"), run_id=RUN)
    assert out.recommendation == "GO"


@pytest.mark.asyncio
async def test_the_gate_value_is_unchanged_while_not_enforced(world):
    world.set(enforced=False)
    out = await policy.apply_release_review_gate(None, _council(), run_id=RUN)
    assert out.recommendation == "GO"
    assert out.review_gate_enforced is False and out.review.state == "pending_review"


# ── the report PDF export ────────────────────────────────────────────────────


class _DB:
    def __init__(self):
        self.added: list = []
        self.committed = False

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: PROJECT)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True


def _report():
    return SimpleNamespace(draft_watermark="")


@pytest.fixture
def pdf_route(world, monkeypatch):
    rendered: list = []
    monkeypatch.setattr(
        "app.services.report_composition_service.compose_report",
        AsyncMock(side_effect=lambda *a, **k: _report()),
    )

    def _render(report):
        rendered.append(report)
        return b"%PDF-fake"

    monkeypatch.setattr("app.services.report_pdf_renderer.render_report_pdf", _render)
    audit = AsyncMock()
    monkeypatch.setattr("app.services.access_audit_service.log_access_change", audit)
    return SimpleNamespace(rendered=rendered, audit=audit)


def _user(role="QA_LEAD"):
    return SimpleNamespace(id=uuid.uuid4(), username="u", role=role)


@pytest.mark.asyncio
async def test_release_advisory_override_requires_qa_lead():
    from app.routers.release_readiness import get_release_decision

    with pytest.raises(HTTPException) as exc:
        await get_release_decision(
            RUN,
            allow_advisory=True,
            current_user=_user("QA_ENGINEER"),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Advisory release values require the QA Lead role."


@pytest.mark.asyncio
async def test_pdf_export_of_an_unreviewed_report_is_409_and_the_refusal_is_kept(pdf_route):
    from app.routers.reports import export_run_report_pdf

    db = _DB()
    with pytest.raises(HTTPException) as exc:
        await export_run_report_pdf(RUN, layout="executive", include_unreviewed=False, db=db, current_user=_user())
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "report_pending_review"
    assert db.committed is True, "the refusal's audit row must survive the 409"
    assert pdf_route.rendered == []


@pytest.mark.asyncio
async def test_include_unreviewed_needs_qa_lead(pdf_route):
    from app.routers.reports import export_run_report_pdf

    with pytest.raises(HTTPException) as exc:
        await export_run_report_pdf(RUN, layout="executive", include_unreviewed=True, db=_DB(),
                                    current_user=_user("QA_ENGINEER"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_qa_lead_can_export_a_watermarked_draft(pdf_route):
    from app.routers.reports import export_run_report_pdf

    await export_run_report_pdf(RUN, layout="executive", include_unreviewed=True, db=_DB(), current_user=_user())
    assert pdf_route.rendered[0].draft_watermark == policy.DRAFT_WATERMARK
    assert pdf_route.audit.await_args.kwargs["action"] == "ai_report.distributed_unreviewed"


@pytest.mark.asyncio
async def test_pdf_export_is_unchanged_while_not_enforced(pdf_route, world):
    from app.routers.reports import export_run_report_pdf

    world.set(enforced=False)
    await export_run_report_pdf(RUN, layout="executive", include_unreviewed=False, db=_DB(), current_user=_user())
    assert pdf_route.rendered[0].draft_watermark == ""
    assert pdf_route.audit.await_args.kwargs["action"] == "ai_report.distribution_would_refuse"


# ── share links ──────────────────────────────────────────────────────────────


@pytest.fixture
def share_route(world, monkeypatch):
    link = SimpleNamespace(run_id=RUN, project_id=PROJECT, report_layout="executive",
                           created_by_name="qa", expires_at=None)
    monkeypatch.setattr("app.services.share_link_service.validate_share_link", AsyncMock(return_value=link))
    monkeypatch.setattr(
        "app.services.report_composition_service.compose_report",
        AsyncMock(side_effect=lambda *a, **k: _report()),
    )
    rendered: list = []

    def _html(report, **_kw):
        rendered.append(report)
        return "<html></html>"

    monkeypatch.setattr("app.services.report_html_renderer.render_report_html", _html)
    monkeypatch.setattr("app.services.access_audit_service.log_access_change", AsyncMock())
    return SimpleNamespace(rendered=rendered)


@pytest.mark.asyncio
async def test_a_share_link_refuses_an_unreviewed_report(share_route):
    from app.routers.shared_reports import view_shared_report

    with pytest.raises(HTTPException) as exc:
        await view_shared_report("tok", db=_DB())
    assert exc.value.status_code == 409
    assert share_route.rendered == []


@pytest.mark.asyncio
async def test_a_share_link_serves_a_watermarked_draft_when_the_project_opts_in(share_route, world):
    from app.routers.shared_reports import view_shared_report

    world.set(drafts=True)
    await view_shared_report("tok", db=_DB())
    assert share_route.rendered[0].draft_watermark == policy.DRAFT_WATERMARK


# ── the renderers ────────────────────────────────────────────────────────────


def _full_report(watermark=""):
    from app.services.report_composition_service import ReportData

    return ReportData(
        run_id=str(RUN), build_number="42", branch="main", project_name="p",
        generated_at="2026-09-13", layout="executive", pass_rate=90.0,
        total_tests=10, passed_tests=9, failed_tests=1, skipped_tests=0,
        draft_watermark=watermark,
    )


def test_the_html_report_shows_the_draft_banner_only_when_watermarked():
    from app.services.report_html_renderer import render_report_html

    assert policy.DRAFT_WATERMARK in render_report_html(_full_report(policy.DRAFT_WATERMARK))
    assert policy.DRAFT_WATERMARK not in render_report_html(_full_report())


def test_the_pdf_report_carries_the_draft_watermark(monkeypatch):
    from reportlab import rl_config

    from app.services.report_pdf_renderer import render_report_pdf

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    assert b"DRAFT" in render_report_pdf(_full_report(policy.DRAFT_WATERMARK))
    assert b"DRAFT" not in render_report_pdf(_full_report())
