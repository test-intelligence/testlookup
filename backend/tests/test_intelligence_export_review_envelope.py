"""E8.6: the intelligence JSON export says whether a person accepted its content.

`GET /runs/{run_id}/export` is a downloadable copy of the run's AI summary and
decision report. The review-consumer guard found it was the one report route
without the review envelope (E8.3), so a downloaded copy could not say it was
an unreviewed draft.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.review_envelope import ReviewEnvelope


@pytest.mark.asyncio
async def test_the_export_carries_the_review_envelope(monkeypatch):
    from app.routers import run_intelligence as router

    run_id = uuid.uuid4()
    intelligence = {
        "run": {"build_number": "42"},
        "structured_summary": {"executive_summary": "Checkout regressed.", "decision_report": {"recommendation": "NO_GO"}},
    }
    monkeypatch.setattr(router, "get_mongo_db", lambda: None)
    monkeypatch.setattr(router, "get_run_intelligence", AsyncMock(return_value=intelligence))
    monkeypatch.setattr(router, "get_run_mode_summary", AsyncMock(return_value=None))
    envelope_for = AsyncMock(return_value=ReviewEnvelope(
        ai_generated=True, state="pending_review", message="Human review required before use.", review_id="r1",
    ))
    monkeypatch.setattr(router, "review_envelope_for_run", envelope_for)

    response = await router.export_intelligence_report(run_id=run_id, mode="manager", db=object(), _=None)

    body = json.loads(response.body)
    assert body["requires_human_review"] is True
    assert body["review"]["state"] == "pending_review"
    assert body["ai_disclaimer"]
    assert body["summary"] == "Checkout regressed."
    assert envelope_for.await_args.args[1] == run_id
