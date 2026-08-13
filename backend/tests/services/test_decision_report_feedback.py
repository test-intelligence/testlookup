from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException

from app.models.postgres import DecisionReportFeedback
from app.routers.feedback import DecisionReportFeedbackRequest
from app.services import feedback_service


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, values):
        self.values = list(values)
        self.added = []
        self.flush = AsyncMock()

    async def execute(self, _statement):
        return _Result(self.values.pop(0))

    def add(self, value):
        self.added.append(value)


class _Collection:
    def __init__(self, report):
        self.report = report
        self.query = None

    async def find_one(self, query, _projection=None):
        self.query = query
        if self.report is None:
            return None
        expected = {
            "project_id": "project-1",
            "test_run_id": str(RUN_ID),
            "report_id": "report-1",
            "report_version": 2,
            "status": "published",
        }
        return self.report if query == expected else None


class _Mongo:
    def __init__(self, report):
        self.collection = _Collection(report)

    def __getitem__(self, name):
        return self.collection


RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _report():
    return {
        "project_id": "project-1",
        "test_run_id": str(RUN_ID),
        "report_id": "report-1",
        "report_version": 2,
        "status": "published",
        "evidence_bundle_sha256": "a" * 64,
        "decision_intelligence": {
            "claims": [
                {
                    "claim_id": "claim-1",
                    "kind": "inference",
                    "evidence": [{
                        "type": "artifact",
                        "id": "artifact-1",
                        "source": "stacktrace",
                        "kind": "tool_observation",
                        "checksum_sha256": "b" * 64,
                        "excerpt": "secret must never be persisted to feedback",
                    }],
                    "counter_evidence": [],
                },
            ],
        },
    }


def _user():
    return SimpleNamespace(id=USER_ID)


@pytest.mark.asyncio
async def test_report_feedback_binds_exact_published_version_and_hash():
    body = DecisionReportFeedbackRequest(
        report_version=2,
        feedback_kind="utility",
        utility_rating="useful",
    )
    db = _DB(["project-1", None])
    result = await feedback_service.submit_decision_report_feedback(
        db, _Mongo(_report()), run_id=RUN_ID, report_id="report-1", body=body, current_user=_user(),
    )

    assert result["status"] == "recorded"
    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, DecisionReportFeedback)
    assert row.report_version == 2
    assert row.report_evidence_sha256 == "a" * 64
    assert row.feedback_kind == "utility"


@pytest.mark.asyncio
async def test_claim_correction_accepts_only_bound_evidence_projection():
    body = DecisionReportFeedbackRequest(
        report_version=2,
        feedback_kind="claim_correction",
        claim_id="claim-1",
        correction_type="cause",
        corrected_value="database timeout",
        reason="The linked stack trace identifies the timeout boundary.",
        evidence_ids=["artifact-1"],
    )
    db = _DB(["project-1", None])
    await feedback_service.submit_decision_report_feedback(
        db, _Mongo(_report()), run_id=RUN_ID, report_id="report-1", body=body, current_user=_user(),
    )
    row = db.added[0]
    assert row.claim_kind == "inference"
    assert row.evidence_refs == [{
        "id": "artifact-1",
        "type": "artifact",
        "source": "stacktrace",
        "kind": "tool_observation",
        "checksum_sha256": "b" * 64,
    }]
    assert all("excerpt" not in ref for ref in row.evidence_refs)


@pytest.mark.asyncio
async def test_claim_correction_rejects_unbound_evidence_and_does_not_write():
    body = DecisionReportFeedbackRequest(
        report_version=2,
        feedback_kind="claim_correction",
        claim_id="claim-1",
        correction_type="category",
        corrected_value="infra",
        reason="The evidence points to infrastructure.",
        evidence_ids=["foreign-evidence"],
    )
    db = _DB(["project-1", None])
    with pytest.raises(HTTPException) as exc:
        await feedback_service.submit_decision_report_feedback(
            db, _Mongo(_report()), run_id=RUN_ID, report_id="report-1", body=body, current_user=_user(),
        )
    assert exc.value.status_code == 422
    assert db.added == []


def test_feedback_request_requires_evidence_for_corrections():
    with pytest.raises(ValueError):
        DecisionReportFeedbackRequest(
            report_version=2,
            feedback_kind="claim_correction",
            claim_id="claim-1",
            correction_type="flaky",
        )


@pytest.mark.asyncio
async def test_idempotency_returns_existing_feedback_without_second_write():
    existing = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        test_run_id=RUN_ID,
        report_id="report-1",
        report_version=2,
    )
    body = DecisionReportFeedbackRequest(
        report_version=2,
        feedback_kind="utility",
        utility_rating="not_useful",
        idempotency_key="retry-1",
    )
    db = _DB(["project-1", existing])
    result = await feedback_service.submit_decision_report_feedback(
        db, _Mongo(_report()), run_id=RUN_ID, report_id="report-1", body=body, current_user=_user(),
    )
    assert result["status"] == "already_recorded"
    assert db.added == []
