"""E8.6: the intelligence JSON export says whether a person accepted its content.

`GET /runs/{run_id}/export` is a downloadable copy of the run's AI summary and
decision report. The review-consumer guard found it was the one report route
without the review envelope (E8.3), so a downloaded copy could not say it was
an unreviewed draft.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.review_envelope import ReviewEnvelope


@pytest.mark.asyncio
async def test_the_export_carries_the_review_envelope(monkeypatch):
    from app.routers import run_intelligence as router

    run_id = uuid.uuid4()
    intelligence = {
        "run": {"build_number": "42", "project_id": str(uuid.uuid4())},
        "structured_summary": {"executive_summary": "Checkout regressed.", "decision_report": {"recommendation": "NO_GO"}},
    }
    monkeypatch.setattr(router, "get_mongo_db", lambda: None)
    monkeypatch.setattr(router, "get_run_intelligence", AsyncMock(return_value=intelligence))
    monkeypatch.setattr(router, "get_run_mode_summary", AsyncMock(return_value=None))
    envelope_for = AsyncMock(return_value=ReviewEnvelope(
        ai_generated=True, state="pending_review", message="Human review required before use.", review_id="r1",
    ))
    monkeypatch.setattr(router, "review_envelope_for_run", envelope_for)
    monkeypatch.setattr(
        router,
        "decide_run_distribution",
        AsyncMock(return_value=SimpleNamespace(allowed=True)),
    )
    monkeypatch.setattr(router, "record_distribution", AsyncMock())

    response = await router.export_intelligence_report(run_id=run_id, mode="manager", db=object(), _=None)

    body = json.loads(response.body)
    assert body["requires_human_review"] is True
    assert body["review"]["state"] == "pending_review"
    assert body["ai_disclaimer"]
    assert body["summary"] == "Checkout regressed."
    assert envelope_for.await_args.args[1] == run_id


@pytest.mark.asyncio
async def test_enforced_pending_intelligence_export_is_refused_and_audited(monkeypatch):
    from app.routers import run_intelligence as router
    from app.services.report_distribution_policy import DistributionDecision, REFUSED

    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    intelligence = {
        "run": {"build_number": "42", "project_id": str(project_id)},
        "structured_summary": {"executive_summary": "Unreviewed AI text."},
    }
    envelope = ReviewEnvelope(
        ai_generated=True,
        state="pending_review",
        message="Human review required before use.",
        review_id="review-1",
    )
    decision = DistributionDecision(
        allowed=False,
        reason=REFUSED,
        envelope=envelope,
        enforced=True,
    )
    db = SimpleNamespace(commit=AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(router, "get_mongo_db", lambda: None)
    monkeypatch.setattr(router, "get_run_intelligence", AsyncMock(return_value=intelligence))
    monkeypatch.setattr(router, "get_run_mode_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "decide_run_distribution", AsyncMock(return_value=decision))
    monkeypatch.setattr(router, "record_distribution", record)

    with pytest.raises(router.HTTPException) as refused:
        await router.export_intelligence_report(run_id=run_id, mode="manager", db=db, _=None)

    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "report_pending_review"
    record.assert_awaited_once()
    assert record.await_args.kwargs["channel"] == "intelligence_export"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_allowed_pending_intelligence_export_carries_the_draft_watermark(monkeypatch):
    from app.routers import run_intelligence as router
    from app.services.report_distribution_policy import (
        DRAFT_WATERMARK,
        DistributionDecision,
        PROJECT_ALLOWS_DRAFTS,
    )

    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    intelligence = {
        "run": {"build_number": "42", "project_id": str(project_id)},
        "structured_summary": {
            "executive_summary": "Unreviewed AI text.",
            "decision_report": {"recommendation": "NO_GO"},
        },
    }
    envelope = ReviewEnvelope(
        ai_generated=True,
        state="pending_review",
        message="Human review required before use.",
        review_id="review-1",
    )
    decision = DistributionDecision(
        allowed=True,
        reason=PROJECT_ALLOWS_DRAFTS,
        envelope=envelope,
        watermark=DRAFT_WATERMARK,
        enforced=True,
    )
    monkeypatch.setattr(router, "get_mongo_db", lambda: None)
    monkeypatch.setattr(
        router, "get_run_intelligence", AsyncMock(return_value=intelligence)
    )
    monkeypatch.setattr(router, "get_run_mode_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(
        router, "decide_run_distribution", AsyncMock(return_value=decision)
    )
    monkeypatch.setattr(router, "record_distribution", AsyncMock())
    monkeypatch.setattr(
        router, "review_envelope_for_run", AsyncMock(return_value=envelope)
    )

    response = await router.export_intelligence_report(
        run_id=run_id, mode="manager", db=object(), _=None
    )

    body = json.loads(response.body)
    assert body["draft_watermark"] == DRAFT_WATERMARK
    assert body["summary"] == "Unreviewed AI text."
    assert body["decision_report"]["recommendation"] == "NO_GO"
