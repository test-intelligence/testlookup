"""W32 F-7: ``POST /api/v1/projects/{project_id}/reset`` hands the service a
Mongo handle, and the response model keeps the ``documents`` it returns.

Without the handle the reset never visits Mongo; without the schema field the
response model silently drops the key (the half-a-fix trap).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import ProjectResetResponse


@pytest.mark.asyncio
async def test_the_route_passes_a_mongo_handle_to_the_reset(monkeypatch):
    from app.routers import projects as route

    sentinel = object()
    documents = {"raw_allure_json": 12, "run_summaries": 1}
    service = AsyncMock(
        return_value={"mode": "runs", "deleted": {"test_runs": 2}, "documents": documents}
    )
    monkeypatch.setattr(route, "get_mongo_db", lambda: sentinel)
    monkeypatch.setattr(route, "reset_project", service)
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    project_id = uuid.uuid4()

    result = await route.reset_project_data(
        project_id=project_id,
        payload=SimpleNamespace(confirmation_name="Payments", mode="runs"),
        db=db,
        current_user=SimpleNamespace(id=uuid.uuid4(), full_name="Ada", email=None),
    )

    kwargs = service.await_args.kwargs
    assert kwargs["mongo"] is sentinel
    assert kwargs["project_id"] == project_id
    assert ProjectResetResponse.model_validate(result).model_dump()["documents"] == documents


def test_the_response_model_keeps_documents_and_defaults_to_not_measured():
    kept = ProjectResetResponse.model_validate(
        {"mode": "full", "deleted": {"test_runs": 1}, "documents": {"decision_reports": 0}}
    )
    assert kept.model_dump(mode="json")["documents"] == {"decision_reports": 0}

    absent = ProjectResetResponse.model_validate({"mode": "runs", "deleted": {}})
    assert absent.model_dump(mode="json")["documents"] is None
