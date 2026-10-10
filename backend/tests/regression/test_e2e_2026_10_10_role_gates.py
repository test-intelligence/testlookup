"""E2E pass 2026-10-10: writes a lower role could make through the API.

Each route below checked project membership (or nothing) where the product
reserves the action for a higher role -- usually the page already hid it, and
the API did not agree:

* ``POST /ai-eval/tier-comparison`` / ``/reviewer-quality``: the project is in
  the BODY and ``require_project_access()`` reads the path, so any signed-in
  user wrote a G2 verdict into any project, and a global QA lead turned off
  another project's reviewer second-model check.
* ``POST /analytics/classify-uncategorized``: a VIEWER relabelled a year of
  failures. ``POST /deep-investigate/{run}`` (an LLM pipeline per call) and
  ``.../clusters/{id}/promote`` (an OPEN defect on the release): VIEWER.
* ``POST/PUT /feedback/{analysis_id}`` with a correction: a VIEWER rewrote the
  AI root cause and cleared ``requires_human_review``.
* ``POST /ingest``, ``/ingest/file``, ``POST/DELETE /stream/sessions``: a
  VIEWER posted results (the UI's Upload says "QA Engineer role required").
* ``POST /reports/email-trends``: any member mailed project data anywhere.
* ``PUT/DELETE /test-cases/{id}/review``: a VIEWER overwrote verdicts.
* Share links kept serving after their creator lost access.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.regression

P = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _user(role: str, **kw):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None, **kw)


@pytest.fixture
def app_as():
    """A FastAPI app with the affected routers, signed in as ``state['user']``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_api_key_context, get_current_active_user, get_current_user
    from app.db.postgres import get_db
    from app.routers import (
        ai_evaluation, analytics, deep_investigation, ingest, reports, stream,
        test_execution_reviews,
    )

    app = FastAPI()
    for r in (ai_evaluation, analytics, deep_investigation, ingest, reports, stream, test_execution_reviews):
        app.include_router(r.router)
    db = AsyncMock()

    async def _db():
        yield db

    state = {"user": _user("VIEWER")}
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    app.dependency_overrides[get_api_key_context] = lambda: (state["user"], None)
    return TestClient(app, raise_server_exceptions=False), state


ROLE_GATED = [
    ("POST", "/api/v1/ai-eval/tier-comparison", "QA_LEAD"),
    ("POST", "/api/v1/ai-eval/reviewer-quality", "QA_LEAD"),
    ("POST", "/api/v1/analytics/classify-uncategorized", "QA_ENGINEER"),
    ("POST", f"/api/v1/deep-investigate/{RUN}", "QA_ENGINEER"),
    ("POST", f"/api/v1/deep-investigate/{RUN}/clusters/c1/promote", "QA_ENGINEER"),
    ("POST", "/api/v1/reports/email-trends", "QA_LEAD"),
    ("PUT", f"/api/v1/test-cases/{RUN}/review", "QA_ENGINEER"),
    ("DELETE", f"/api/v1/test-cases/{RUN}/review", "QA_ENGINEER"),
    ("POST", "/api/v1/ingest", "QA_ENGINEER"),
    ("POST", "/api/v1/ingest/file", "QA_ENGINEER"),
    ("POST", "/api/v1/stream/sessions", "QA_ENGINEER"),
    ("DELETE", f"/api/v1/stream/sessions/{RUN}", "QA_ENGINEER"),
]
_ORDER = ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"]


@pytest.mark.parametrize("method, path, min_role", ROLE_GATED)
@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER"])
def test_a_role_below_the_gate_is_refused_before_anything_runs(app_as, method, path, min_role, role):
    if _ORDER.index(role) >= _ORDER.index(min_role):
        pytest.skip("at or above the gate")
    http, state = app_as
    state["user"] = _user(role)
    body = {"project_id": str(P), "build_number": "b", "client_name": "c",
            "results": [{"test_name": "t", "status": "PASSED"}]}
    if path.endswith("/ingest/file"):
        resp = http.post(path, files={"file": ("r.xml", b"<testsuite/>")},
                         data={"project_id": str(P), "build_number": "b"})
    else:
        resp = http.request(method, path, json=body)
    assert resp.status_code == 403, (path, resp.status_code, resp.text)
    assert "Requires at least" in resp.text


@pytest.mark.asyncio
async def test_the_ai_eval_routes_check_the_body_project_membership():
    """A QA lead who is not a member of the body's project is refused before
    the verdict is computed or persisted."""
    from app.routers import ai_evaluation

    refused = AsyncMock(side_effect=HTTPException(403, "You do not have access to this project"))
    with patch("app.core.deps.resolve_project_scope", refused):
        with pytest.raises(HTTPException) as exc:
            await ai_evaluation._authorize_body_project(AsyncMock(), _user("QA_LEAD"), P)
    assert exc.value.status_code == 403
    refused.assert_awaited_once()
    assert str(refused.await_args.args[2]) == str(P)


@pytest.mark.parametrize("role, correction, allowed", [
    ("VIEWER", True, False),
    ("TESTER", True, False),
    ("QA_ENGINEER", True, True),
    ("VIEWER", False, True),   # a plain rating stays open to every member
])
def test_only_qa_engineers_may_apply_a_correction(role, correction, allowed):
    from app.models.postgres import FeedbackRating
    from app.services.feedback_service import _require_correction_role

    body = SimpleNamespace(
        rating=FeedbackRating.INCORRECT if correction else FeedbackRating.CORRECT,
        corrected_category="INFRASTRUCTURE" if correction else None,
    )
    if allowed:
        _require_correction_role(body, _user(role))
    else:
        with pytest.raises(HTTPException) as exc:
            _require_correction_role(body, _user(role))
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_submit_feedback_refuses_a_viewer_correction_before_touching_the_analysis():
    from app.models.postgres import FeedbackRating
    from app.services import feedback_service

    analysis = SimpleNamespace(id=uuid.uuid4(), test_case_id=uuid.uuid4(), failure_category="PRODUCT_BUG",
                               root_cause_summary="orig", requires_human_review=True)
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=analysis)))
    body = SimpleNamespace(rating=FeedbackRating.INCORRECT, corrected_category="INFRASTRUCTURE",
                           corrected_root_cause="planted", comment=None)
    with patch.object(feedback_service, "_require_analysis_access", AsyncMock()):
        with pytest.raises(HTTPException):
            await feedback_service.submit_feedback(db, analysis.id, body, _user("VIEWER"))
    assert analysis.root_cause_summary == "orig" and analysis.requires_human_review is True
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("creator, accessible, ok", [
    (None, None, False),                                              # creator deleted
    (SimpleNamespace(id=uuid.uuid4(), is_active=False), None, False),  # deactivated
    (SimpleNamespace(id=uuid.uuid4(), is_active=True), set(), False),  # removed from the project
    (SimpleNamespace(id=uuid.uuid4(), is_active=True), {P}, True),     # still a member
    (SimpleNamespace(id=uuid.uuid4(), is_active=True), None, True),    # admin
])
async def test_a_share_link_lasts_only_as_long_as_its_creators_access(creator, accessible, ok):
    from datetime import datetime, timedelta, timezone

    from app.services import share_link_service

    link = SimpleNamespace(
        project_id=P, created_by_id=creator.id if creator else None, is_revoked=False,
        expires_at=datetime.now(timezone.utc) + timedelta(days=3), access_count=0, last_accessed_at=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=link)))
    db.get = AsyncMock(return_value=creator)
    with patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)):
        if ok:
            assert await share_link_service.validate_share_link(db, "tok") is link
        else:
            with pytest.raises(ValueError, match="no longer valid"):
                await share_link_service.validate_share_link(db, "tok")
