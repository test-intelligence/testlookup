"""Regression coverage for slug-safe ingestion project resolution."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.models.schemas import SentinelFile  # noqa: E402
from app.services.ingestion import _upsert_test_run  # noqa: E402


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_slug_identifier_never_builds_uuid_or_predicate():
    project = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(project), _Result(None)])
    db.add = MagicMock()
    db.flush = AsyncMock()

    run = await _upsert_test_run(
        db,
        SentinelFile(build_number="slug-build", project_id="auth-service"),
        "reports/slug-build",
    )

    assert run.project_id == project.id
    first_query = str(db.execute.await_args_list[0].args[0])
    assert "projects.slug" in first_query
    assert "WHERE projects.id" not in first_query
