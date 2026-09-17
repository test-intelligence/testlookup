"""E8.3: every AI report response says whether a human has reviewed it.

Pins the envelope (``requires_human_review``, ``review``, the disclaimer) and the
two headers on each report route. Also pins the two cases that could quietly
lie: AI content with no review request must read as unreviewed, not settled, and
a deterministic fallback must not be labelled AI-generated. Throughout, the
reviewer's identity must never appear (section 8.2).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response

from app.services import review_envelope as env
from app.services.review_request_service import AI_DISCLAIMER_VERSION

RUN = uuid.uuid4()
REVIEWER = uuid.uuid4()


def _review(state="pending_review", reviewed_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(), state=state, reviewed_at=reviewed_at, reviewed_by=REVIEWER,
    )


class _DB:
    def __init__(self, row=None):
        self.row = row
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: self.row))


# ── the envelope ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pending_review_points_the_reader_at_it():
    review = _review()
    envelope = await env.review_envelope_for_run(_DB(review), RUN)

    assert envelope.ai_generated is True
    assert envelope.state == "pending_review"
    assert envelope.review_id == str(review.id)
    assert f"/api/v1/reviews/{review.id}" in envelope.message


@pytest.mark.asyncio
async def test_an_accepted_review_carries_when_but_never_who():
    reviewed_at = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
    envelope = await env.review_envelope_for_run(_DB(_review("accepted", reviewed_at)), RUN)
    fields = envelope.fields()

    assert fields["review"]["state"] == "accepted"
    assert fields["review"]["reviewed_at"] == reviewed_at.isoformat()
    assert str(REVIEWER) not in str(fields), "reviewer identity leaked into the payload"
    assert "reviewed_by" not in str(fields)


@pytest.mark.asyncio
async def test_ai_content_with_no_review_request_fails_closed_to_unreviewed():
    """A report from before E8.1 has no request. Absence is not a review."""
    envelope = await env.review_envelope_for_run(_DB(None), RUN)
    assert envelope.state == "pending_review"
    assert envelope.review_id is None
    assert envelope.fields()["requires_human_review"] is True


@pytest.mark.asyncio
async def test_a_deterministic_fallback_is_not_labelled_ai_generated():
    db = _DB(_review("pending_review"))
    envelope = await env.review_envelope_for_run(db, RUN, ai_generated=False)

    assert envelope.state == "not_applicable"
    assert envelope.fields()["requires_human_review"] is False
    assert envelope.fields()["ai_disclaimer"] is None
    assert db.statements == [], "no review lookup for content no model wrote"


@pytest.mark.asyncio
async def test_a_non_uuid_run_id_is_unreviewed_without_a_query():
    db = _DB(_review("accepted"))
    envelope = await env.review_envelope_for_run(db, "not-a-uuid")
    assert envelope.state == "pending_review" and db.statements == []


@pytest.mark.asyncio
async def test_the_lookup_ignores_superseded_reviews_and_can_filter_the_workflow():
    db = _DB(_review())
    await env.review_envelope_for_run(db, RUN, workflow_type="deep")
    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": False}))
    params = db.statements[0].compile().params
    assert "review_requests.state !=" in sql
    assert "superseded" in params.values()
    assert "deep" in params.values()


@pytest.mark.asyncio
async def test_generic_parent_report_lookup_excludes_investigator_subjects():
    db = _DB(_review("accepted"))

    await env.review_envelope_for_run(db, RUN)

    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": False}))
    params = db.statements[0].compile().params
    assert "review_requests.workflow_type !=" in sql
    assert "investigation" in params.values()


@pytest.mark.asyncio
async def test_investigator_lookup_is_bound_to_the_exact_pipeline_subject():
    db = _DB(_review("accepted"))

    await env.review_envelope_for_pipeline(db, RUN)

    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": False}))
    params = db.statements[0].compile().params
    assert "review_requests.pipeline_run_id =" in sql
    assert RUN in params.values()


@pytest.mark.asyncio
async def test_immutable_pipeline_subject_lookup_retains_superseded_state():
    db = _DB(_review("superseded"))

    envelope = await env.review_envelope_for_pipeline_subject(db, RUN)

    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": False}))
    assert "review_requests.pipeline_run_id =" in sql
    assert "review_requests.state !=" not in sql
    assert envelope.state == "superseded"


def test_the_disclaimer_is_versioned_in_the_payload():
    fields = env._unreviewed().fields()
    assert fields["ai_disclaimer_version"] == AI_DISCLAIMER_VERSION
    assert fields["ai_disclaimer"]


def test_headers_are_set_for_ai_and_non_ai_content():
    response = Response()
    env._unreviewed().apply_headers(response)
    assert response.headers["X-TestLookup-AI-Generated"] == "true"
    assert response.headers["X-TestLookup-Review-State"] == "pending_review"

    response = Response()
    env.not_ai_generated().apply_headers(response)
    assert response.headers["X-TestLookup-AI-Generated"] == "false"
    assert response.headers["X-TestLookup-Review-State"] == "not_applicable"


def test_the_review_headers_are_exposed_through_cors():
    """A browser cannot read a custom header the server does not expose."""
    from starlette.middleware.cors import CORSMiddleware

    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    exposed = set(cors.kwargs["expose_headers"])
    assert set(env.EXPOSED_HEADERS) <= exposed


def test_the_block_schema_matches_the_architecture_vocabulary():
    from typing import get_args

    from app.models.schemas import ReviewBlock

    states = set(get_args(ReviewBlock.model_fields["state"].annotation))
    assert states == {"pending_review", "accepted", "rejected", "superseded", "not_applicable"}
    assert "reviewed_by" not in ReviewBlock.model_fields


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_agents_run_summary_carries_the_envelope(monkeypatch):
    from app.routers import agents

    doc = {"test_run_id": str(RUN), "executive_summary": "AI summary", "markdown_report": "# r"}
    collection = SimpleNamespace(find_one=AsyncMock(return_value=dict(doc)))
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _Mongo(collection))
    review = _review()
    response = Response()

    out = await agents.get_run_summary(str(RUN), response, db=_DB(review))

    assert out.requires_human_review is True
    assert out.review.state == "pending_review" and out.review.review_id == str(review.id)
    assert response.headers["X-TestLookup-AI-Generated"] == "true"
    assert str(REVIEWER) not in out.model_dump_json()


@pytest.mark.asyncio
async def test_the_agents_fallback_summary_is_not_applicable(monkeypatch):
    from app.models.schemas import AgentRunSummaryResponse
    from app.routers import agents

    collection = SimpleNamespace(find_one=AsyncMock(return_value=None))
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _Mongo(collection))
    fallback = AgentRunSummaryResponse(test_run_id=str(RUN), executive_summary="deterministic", markdown_report="")
    monkeypatch.setattr(agents, "build_fallback_summary", AsyncMock(return_value=fallback))
    response = Response()

    out = await agents.get_run_summary(str(RUN), response, db=_DB(_review()))

    assert out.review.state == "not_applicable"
    assert out.requires_human_review is False
    assert response.headers["X-TestLookup-AI-Generated"] == "false"


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_used, state, ai_header", [(False, "pending_review", "true"), (True, "not_applicable", "false")])
async def test_the_mode_summary_follows_its_fallback_flag(monkeypatch, fallback_used, state, ai_header):
    from app.routers import run_intelligence

    monkeypatch.setattr(run_intelligence, "get_mongo_db", lambda: object())
    monkeypatch.setattr(
        run_intelligence, "get_run_mode_summary",
        AsyncMock(return_value={"summary": "x", "fallback_used": fallback_used}),
    )
    response = Response()

    out = await run_intelligence.get_run_summary_by_mode(RUN, response, mode="executive", db=_DB(None))

    assert out["review"]["state"] == state
    assert response.headers["X-TestLookup-AI-Generated"] == ai_header
    assert out["summary"] == "x", "the envelope adds keys; it must not replace the report"


@pytest.mark.asyncio
async def test_decision_report_versions_use_their_exact_pipeline_reviews(monkeypatch):
    from app.routers import run_intelligence

    monkeypatch.setattr(run_intelligence, "get_mongo_db", lambda: object())
    pipeline_1 = uuid.uuid4()
    pipeline_2 = uuid.uuid4()
    monkeypatch.setattr(
        "app.services.decision_report_service.list_decision_report_versions",
        AsyncMock(return_value=[
            {"report_id": "r2", "report_version": 2, "pipeline_run_id": str(pipeline_2)},
            {"report_id": "r1", "report_version": 1, "pipeline_run_id": str(pipeline_1)},
        ]),
    )
    exact = AsyncMock(side_effect=[
        env.ReviewEnvelope(ai_generated=True, state="accepted", message="accepted"),
        env.ReviewEnvelope(ai_generated=True, state="superseded", message="superseded"),
    ])
    monkeypatch.setattr(run_intelligence, "review_envelope_for_pipeline_subject", exact)
    response = Response()

    out = await run_intelligence.list_run_decision_reports(RUN, response, limit=5, db=object())

    assert [row["review"]["state"] for row in out] == ["accepted", "superseded"]
    assert [call.args[1] for call in exact.await_args_list] == [
        str(pipeline_2), str(pipeline_1)
    ]
    assert all("pipeline_run_id" not in row for row in out)
    assert response.headers["X-TestLookup-Review-State"] == "accepted"


@pytest.mark.asyncio
async def test_selected_decision_report_uses_its_exact_pipeline_review(monkeypatch):
    from app.routers import run_intelligence

    pipeline_id = uuid.uuid4()
    result = {
        "summary": "immutable version",
        "_decision_report_pipeline_run_id": str(pipeline_id),
    }
    exact = AsyncMock(return_value=env.ReviewEnvelope(
        ai_generated=True, state="superseded", message="superseded"
    ))
    monkeypatch.setattr(run_intelligence, "get_mongo_db", lambda: object())
    monkeypatch.setattr(run_intelligence, "get_run_intelligence", AsyncMock(return_value=result))
    monkeypatch.setattr(run_intelligence, "review_envelope_for_pipeline_subject", exact)
    response = Response()
    db = object()

    out = await run_intelligence.get_run_intelligence_endpoint(
        RUN, response, include="", report_version=1, db=db
    )

    assert out["review"]["state"] == "superseded"
    assert "_decision_report_pipeline_run_id" not in out
    exact.assert_awaited_once_with(db, str(pipeline_id))


@pytest.mark.asyncio
async def test_a_cached_intelligence_snapshot_gets_a_live_review_state(monkeypatch):
    """The envelope is applied per response; a cache must not freeze it."""
    from app.routers import run_intelligence

    cached = {"summary": "cached snapshot"}
    monkeypatch.setattr(run_intelligence, "get_mongo_db", lambda: object())
    monkeypatch.setattr(run_intelligence, "get_cached_snapshot", AsyncMock(return_value=cached))
    response = Response()

    out = await run_intelligence.get_run_intelligence_endpoint(
        RUN, response, include="", report_version=None, db=_DB(_review("rejected", datetime.now(timezone.utc))),
    )

    assert out["review"]["state"] == "rejected"
    assert "review" not in cached, "the review state was written into the cached snapshot"


class _Mongo:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, _name):
        return self._collection
