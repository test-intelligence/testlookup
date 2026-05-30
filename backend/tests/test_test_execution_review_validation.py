"""Validation coverage for per-execution review persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def test_test_execution_review_has_database_check_constraints():
    """The ORM table must reject impossible states below the service layer."""
    from app.models.postgres import TEST_EXECUTION_REVIEW_STATES, TestExecutionReview
    from sqlalchemy import CheckConstraint

    checks = {
        constraint.name: constraint
        for constraint in TestExecutionReview.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_ter_state_valid" in checks
    assert "ck_ter_defect_link_required" in checks

    state_sql = str(checks["ck_ter_state_valid"].sqltext)
    for state in TEST_EXECUTION_REVIEW_STATES:
        assert state in state_sql

    defect_link_sql = str(checks["ck_ter_defect_link_required"].sqltext)
    assert "defect_filed" in defect_link_sql
    assert "defect_link" in defect_link_sql


def test_0081_migration_creates_same_database_constraints():
    """The Alembic migration must install the DB guards, not just the ORM."""
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0081_test_execution_reviews.py"
    ).read_text(encoding="utf-8")

    assert "ck_ter_state_valid" in migration
    assert "ck_ter_defect_link_required" in migration
    assert "sa.CheckConstraint" in migration


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _review_row(**overrides):
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


@pytest.mark.asyncio
async def test_service_rejects_pending_review_as_transition_target():
    from app.services.test_execution_review_service import upsert_review

    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await upsert_review(
            db=db,
            test_case_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            state="pending_review",
            reviewer_user_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 400
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_service_rejects_defect_filed_without_link_before_write():
    from app.services.test_execution_review_service import upsert_review

    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await upsert_review(
            db=db,
            test_case_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            state="defect_filed",
            reviewer_user_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 400
    assert "defect_link is required" in exc.value.detail
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_service_creates_review_row_for_valid_transition():
    from app.services.test_execution_review_service import upsert_review

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))
    db.add = MagicMock()
    db.flush = AsyncMock()

    test_case_id = uuid.uuid4()
    project_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()

    row = await upsert_review(
        db=db,
        test_case_id=test_case_id,
        project_id=project_id,
        state="reproducible",
        reviewer_user_id=reviewer_id,
        note="reproduced locally",
    )

    assert row.test_case_id == test_case_id
    assert row.project_id == project_id
    assert row.state == "reproducible"
    assert row.reviewed_by_user_id == reviewer_id
    assert row.note == "reproduced locally"
    db.add.assert_called_once_with(row)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_review_api_enforces_project_access_before_write():
    from app.models.schemas import TestExecutionReviewUpdate
    from app.routers.test_execution_reviews import upsert_test_case_review

    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    db = AsyncMock()
    user = SimpleNamespace(id=uuid.uuid4())

    with patch(
        "app.routers.test_execution_reviews.svc.get_test_case_project",
        new=AsyncMock(return_value=project_id),
    ), patch(
        "app.routers.test_execution_reviews._assert_project_access",
        new=AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden")),
    ), patch(
        "app.routers.test_execution_reviews.svc.upsert_review",
        new=AsyncMock(),
    ) as upsert:
        with pytest.raises(HTTPException) as exc:
            await upsert_test_case_review(
                test_case_id,
                TestExecutionReviewUpdate(state="reviewed"),
                db,
                user,
            )

    assert exc.value.status_code == 403
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_review_api_hydrates_service_result():
    from app.models.schemas import TestExecutionReviewUpdate
    from app.routers.test_execution_reviews import upsert_test_case_review

    project_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    review = _review_row(test_case_id=test_case_id, project_id=project_id)
    response = {
        "id": review.id,
        "test_case_id": test_case_id,
        "project_id": project_id,
        "state": "reviewed",
        "reviewed_by_user_id": user.id,
        "reviewed_by_username": "qa_user",
        "reviewed_by_full_name": "QA User",
        "defect_link": None,
        "note": "looks right",
        "transitioned_at": review.transitioned_at,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }

    with patch(
        "app.routers.test_execution_reviews.svc.get_test_case_project",
        new=AsyncMock(return_value=project_id),
    ), patch(
        "app.routers.test_execution_reviews._assert_project_access",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.routers.test_execution_reviews.svc.upsert_review",
        new=AsyncMock(return_value=review),
    ) as upsert, patch(
        "app.routers.test_execution_reviews.svc.hydrate_response",
        new=AsyncMock(return_value=response),
    ) as hydrate:
        result = await upsert_test_case_review(
            test_case_id,
            TestExecutionReviewUpdate(state="reviewed", note="looks right"),
            db,
            user,
        )

    upsert.assert_awaited_once_with(
        db=db,
        test_case_id=test_case_id,
        project_id=project_id,
        state="reviewed",
        reviewer_user_id=user.id,
        defect_link=None,
        note="looks right",
    )
    hydrate.assert_awaited_once_with(db, review)
    assert result == response
