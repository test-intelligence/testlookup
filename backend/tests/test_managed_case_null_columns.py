"""A case row with NULL ``is_automated`` lists with 200 (W13; a pre-existing 500 G-B found).

``managed_test_cases.is_automated``, ``ai_generated`` and ``automation_status``
are NULLABLE columns (migration 0007 gave them a server default and no NOT
NULL), while ``ManagedTestCaseResponse`` declared them ``bool`` / ``str``: ONE
row written without them (raw SQL, an old writer) made the whole
``GET /test-management/cases`` page answer 500 -- and W13's automation filter
("Unautomated", ``automation=not_automated`` = ``coalesce(is_automated, false)
= false``) lists exactly those rows. A NULL now reads as the column's default
(False / False / ``not_automated``), the same reading the filter and the
library health already use; the wire types stay ``bool`` / ``str``.

Driven through a FastAPI route with the list route's own ``response_model``
(pinned to be the real route's) over an ORM row holding the NULLs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.models.postgres import ManagedTestCase
from app.models.schemas import ManagedTestCaseListResponse, ManagedTestCaseResponse

PROJECT = uuid.UUID(int=11)


def _row(**overrides) -> ManagedTestCase:
    values = {
        "id": uuid.uuid4(), "project_id": PROJECT, "title": "Refund a partial order", "test_type": "functional",
        "priority": "high", "severity": "major", "status": "draft", "version": 1,
        "is_automated": None, "ai_generated": None, "automation_status": None,
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "updated_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        **overrides,
    }
    return ManagedTestCase(**values)


def test_the_real_list_route_answers_with_this_model():
    from app.routers.test_management_cases import router

    (route,) = [r for r in router.routes if isinstance(r, APIRoute) and r.path == "/cases" and "GET" in r.methods]
    assert route.response_model is ManagedTestCaseListResponse


def test_a_page_holding_a_null_row_lists_with_200():
    rows = [_row(), _row(is_automated=True, ai_generated=True, automation_status="automated", title="Login works")]
    app = FastAPI()

    @app.get("/cases", response_model=ManagedTestCaseListResponse)
    async def cases() -> dict:
        return {"items": rows, "total": 2, "page": 1, "size": 25, "pages": 1}

    response = TestClient(app).get("/cases")
    assert response.status_code == 200, response.text
    first, second = response.json()["items"]
    assert (first["is_automated"], first["ai_generated"], first["automation_status"]) == (False, False, "not_automated")
    assert (second["is_automated"], second["ai_generated"], second["automation_status"]) == (True, True, "automated")


def test_one_case_reads_the_defaults_and_keeps_what_is_stored():
    case = ManagedTestCaseResponse.model_validate(_row())
    assert case.is_automated is False and case.ai_generated is False and case.automation_status == "not_automated"
    stored = ManagedTestCaseResponse.model_validate(_row(is_automated=False, automation_status="broken"))
    assert stored.is_automated is False and stored.automation_status == "broken"
