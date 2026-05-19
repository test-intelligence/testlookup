"""API coverage for /api/v1/test-cases/{id}/review."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402
from tests.integration.conftest import fake_execute_result  # noqa: E402

pytestmark = pytest.mark.asyncio


def _review(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid.uuid4(),
        "test_case_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "state": "reviewed",
        "reviewed_by_user_id": uuid.uuid4(),
        "defect_link": None,
        "note": None,
        "transitioned_at": now,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


async def test_get_review_returns_hydrated_reviewer(
    client, auth_as, fake_db
):
    user = auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    review = _review(
        test_case_id=test_case_id,
        project_id=project_id,
        reviewed_by_user_id=user.id,
        note="verified locally",
    )
    fake_db.set_execute_results([
        fake_execute_result(first=(project_id,)),
        fake_execute_result(scalar=review),
        fake_execute_result(scalar=user),
    ])

    with patch(
        "app.routers.test_execution_reviews.get_accessible_project_ids",
        new=AsyncMock(return_value={project_id}),
    ):
        resp = await client.get(f"/api/v1/test-cases/{test_case_id}/review")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["test_case_id"] == str(test_case_id)
    assert body["state"] == "reviewed"
    assert body["reviewed_by_username"] == user.username
    assert body["note"] == "verified locally"


async def test_get_review_returns_404_for_implicit_pending_review(
    client, auth_as, fake_db
):
    auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    fake_db.set_execute_results([
        fake_execute_result(first=(project_id,)),
        fake_execute_result(scalar=None),
    ])

    with patch(
        "app.routers.test_execution_reviews.get_accessible_project_ids",
        new=AsyncMock(return_value={project_id}),
    ):
        resp = await client.get(f"/api/v1/test-cases/{test_case_id}/review")

    assert resp.status_code == 404
    assert "No review recorded" in resp.json()["detail"]


async def test_put_review_creates_transition_row(
    client, auth_as, fake_db
):
    user = auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    fake_db.set_execute_results([
        fake_execute_result(first=(project_id,)),
        fake_execute_result(scalar=None),
        fake_execute_result(scalar=user),
    ])

    with patch(
        "app.routers.test_execution_reviews.get_accessible_project_ids",
        new=AsyncMock(return_value={project_id}),
    ):
        resp = await client.put(
            f"/api/v1/test-cases/{test_case_id}/review",
            json={"state": "reproducible", "note": "reproduced on staging"},
        )

    assert resp.status_code == 200, resp.text
    assert fake_db.flushed == 1
    assert len(fake_db.added) == 1
    added = fake_db.added[0]
    assert added.test_case_id == test_case_id
    assert added.project_id == project_id
    assert added.state == "reproducible"
    assert added.reviewed_by_user_id == user.id
    assert resp.json()["state"] == "reproducible"


async def test_put_review_rejects_defect_without_link(
    client, auth_as, fake_db
):
    auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    fake_db.set_execute_results([fake_execute_result(first=(project_id,))])

    with patch(
        "app.routers.test_execution_reviews.get_accessible_project_ids",
        new=AsyncMock(return_value={project_id}),
    ):
        resp = await client.put(
            f"/api/v1/test-cases/{test_case_id}/review",
            json={"state": "defect_filed"},
        )

    assert resp.status_code == 400
    assert "defect_link is required" in resp.json()["detail"]
    assert fake_db.added == []


async def test_put_review_rejects_unknown_state_at_api_boundary(
    client, auth_as
):
    auth_as(role=UserRole.QA_ENGINEER)

    resp = await client.put(
        f"/api/v1/test-cases/{uuid.uuid4()}/review",
        json={"state": "not_a_state"},
    )

    assert resp.status_code == 422


async def test_delete_review_clears_existing_row(
    client, auth_as, fake_db
):
    auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    review = _review(test_case_id=test_case_id, project_id=project_id)
    fake_db.set_execute_results([
        fake_execute_result(first=(project_id,)),
        fake_execute_result(scalar=review),
    ])

    with patch(
        "app.routers.test_execution_reviews.get_accessible_project_ids",
        new=AsyncMock(return_value={project_id}),
    ):
        resp = await client.delete(f"/api/v1/test-cases/{test_case_id}/review")

    assert resp.status_code == 204
    assert fake_db.deleted == [review]
