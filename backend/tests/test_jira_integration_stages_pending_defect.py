"""W06 §0.34: ``POST /api/v1/integrations/jira`` stages its pending defect instead of a 500.

The handler read ``tc.project_id``, but ``TestCase`` has no ``project_id`` (its
owner is ``TestCase -> TestRun.project_id``, which the handler had just
resolved). Every call raised ``AttributeError`` before it could stage the
``pending_review`` defect the approval queue lists; the guards over this route
were source scans only. The route is driven here with a REAL ``TestCase``
instance (so the missing attribute raises exactly as it does in production)
on a recording session.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.postgres import Defect
from app.models.postgres import TestCase as CaseRow
from app.models.schemas import JiraIssueRequest
from app.routers import integrations as integrations_router

PROJECT = uuid.uuid4()


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, tc, existing=None):
        self.tc = tc
        self.existing = existing
        self.added: list = []
        self.flush = AsyncMock(side_effect=self._assign_ids)
        self.commit = AsyncMock()
        self.defect_lookups: list = []

    async def _assign_ids(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt, *args, **kwargs):
        tables = {t.name for t in stmt.get_final_froms()}
        if tables == {"test_cases"}:
            return _Result([self.tc])
        if tables == {"test_runs"}:
            return _Result([PROJECT])
        if tables == {"defects"}:
            self.defect_lookups.append(stmt)
            return _Result([self.existing] if self.existing is not None else [])
        return _Result([])  # ai_analysis: none


def _request(tc) -> JiraIssueRequest:
    return JiraIssueRequest(
        project_key="QA", test_case_id=tc.id, test_name=tc.test_name, run_id=tc.test_run_id,
        ai_summary="Checkout fails with a 500", recommended_action="Fix the gateway timeout",
    )


def _test_case() -> CaseRow:
    return CaseRow(id=uuid.uuid4(), test_run_id=uuid.uuid4(), test_name="test_checkout", status="FAILED")


async def _call(session, tc):
    with patch.object(integrations_router, "resolve_project_scope", AsyncMock()) as scope, \
            patch("app.services.cache_service.bump_analytics_epoch", AsyncMock()) as bump:
        response = await integrations_router.create_jira_defect(
            _request(tc), db=session, current_user=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER"),
        )
    return response, scope, bump


def test_a_test_case_really_has_no_project_id():
    assert not hasattr(_test_case(), "project_id")


@pytest.mark.asyncio
async def test_the_route_stages_a_pending_review_defect_in_the_owning_project():
    tc = _test_case()
    session = _Session(tc)

    response, scope, bump = await _call(session, tc)

    assert response.approval_status == "pending_review" and response.requires_approval is True
    (defect,) = [obj for obj in session.added if isinstance(obj, Defect)]
    assert defect.project_id == PROJECT
    assert defect.test_case_id == tc.id
    assert defect.resolution_status == "OPEN" and defect.approval_status == "pending_review"
    assert defect.promotion_source == "manual_jira_request"
    # What the approval queue reads (DEF-06): the requested Jira project and run.
    assert defect.policy_evaluation["requested_project_key"] == "QA"
    assert defect.policy_evaluation["requested_run_id"] == str(tc.test_run_id)
    assert defect.policy_evaluation["project_id"] == str(PROJECT)
    assert defect.id is not None and response.defect_id == defect.id
    scope.assert_awaited_once()
    assert str(scope.await_args.args[2]) == str(PROJECT)
    session.commit.assert_awaited_once()
    bump.assert_awaited_once_with(PROJECT)


@pytest.mark.asyncio
async def test_an_in_progress_defect_of_the_test_is_re_staged_not_duplicated():
    tc = _test_case()
    existing = SimpleNamespace(id=uuid.uuid4(), approval_status="approved", policy_evaluation=None, title=None)
    session = _Session(tc, existing=existing)

    response, _, _ = await _call(session, tc)

    assert not [obj for obj in session.added if isinstance(obj, Defect)]
    assert existing.approval_status == "pending_review"
    assert existing.policy_evaluation["requested_project_key"] == "QA"
    assert existing.title == "test_checkout"
    assert response.defect_id == existing.id
    # The lookup admits the live statuses and takes the newest one only.
    from sqlalchemy.dialects import postgresql

    (lookup,) = session.defect_lookups
    compiled = lookup.compile(dialect=postgresql.dialect())
    statuses = {v for k, bound in compiled.params.items() if k.startswith("resolution_status") for v in bound}
    assert statuses == {"OPEN", "IN_PROGRESS"}
    assert "ORDER BY defects.created_at DESC" in str(compiled) and "LIMIT" in str(compiled)
