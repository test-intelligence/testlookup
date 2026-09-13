import json
import uuid
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_export_contains_immutable_report_identity(monkeypatch):
    from app.routers import run_intelligence

    run_id = uuid.uuid4()
    intelligence = {
        "run": {"build_number": "42"},
        "structured_summary": {
            "executive_summary": "verified",
            "decision_report": {
                "report_id": "report-2",
                "report_version": 2,
                "supersedes_report_id": "report-1",
                "status": "published",
            },
            "decision_report_verification": {"status": "passed"},
        },
        "what_changed_since_last_good_run": None,
        "release_decision": None,
        "failure_clusters": [],
        "dimension_scores": [],
        "defect_candidates": [],
        "role_actions": {},
        "provenance": {},
        "category_breakdown": {},
    }
    monkeypatch.setattr(run_intelligence, "get_mongo_db", lambda: object())
    monkeypatch.setattr(run_intelligence, "get_run_intelligence", AsyncMock(return_value=intelligence))
    monkeypatch.setattr(run_intelligence, "get_run_mode_summary", AsyncMock(return_value=None))
    from app.services.review_envelope import ReviewEnvelope

    monkeypatch.setattr(run_intelligence, "review_envelope_for_run", AsyncMock(return_value=ReviewEnvelope(
        ai_generated=True, state="accepted", message="Reviewed and accepted.",
    )))

    response = await run_intelligence.export_intelligence_report(run_id, db=object())
    payload = json.loads(response.body)

    assert payload["decision_report"]["report_id"] == "report-2"
    assert payload["decision_report"]["report_version"] == 2
    assert payload["decision_report_verification"]["status"] == "passed"