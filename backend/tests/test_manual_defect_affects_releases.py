"""DEF-03 (W06 §0.29): manual intake refuses a release that is not the project's.

``create_manual_defect`` stored whatever ``affects_releases`` the body named, so
a defect could claim to affect another project's release (a body-id IDOR) and a
typo was kept forever. Every id must now parse and name a release of the body's
project -- ONE statement checks them all -- or the intake is a 422 with one
text (no oracle), and nothing is staged. Stored ids are canonical lower-case,
sorted, deduplicated. The PostgreSQL half is in
``tests/integration/test_defect_escape_rate_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.services import analytics_service

PROJECT = uuid.uuid4()
MINE_A, MINE_B, THEIRS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PAYLOAD = {"title": "Refund posts twice", "severity": "P1"}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    """The project's releases are MINE_A and MINE_B; THEIRS belongs to someone else."""

    def __init__(self):
        self.added: list = []
        self.release_queries: list = []
        self.get = AsyncMock(return_value=SimpleNamespace(id=PROJECT))
        self.flush = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt, *args, **kwargs):
        tables = {t.name for t in stmt.get_final_froms()}
        if tables == {"releases"}:
            self.release_queries.append(stmt)
            params = stmt.compile(dialect=postgresql.dialect()).params
            project = next(v for k, v in params.items() if k.startswith("project_id"))
            asked = next(v for k, v in params.items() if k.startswith("id_"))
            mine = {MINE_A, MINE_B} if project == PROJECT else set()
            return _Result([rid for rid in asked if rid in mine])
        return _Result([])  # no matching test case


@pytest.mark.asyncio
async def test_ids_of_the_project_are_stored_lower_case_sorted_and_deduplicated():
    session = _Session()
    defect = await analytics_service.create_manual_defect(
        session, PROJECT, {**PAYLOAD, "affects_releases": [str(MINE_B).upper(), str(MINE_A), f" {MINE_B} "]},
    )
    assert defect.affects_releases == sorted([str(MINE_A), str(MINE_B)])
    assert len(session.release_queries) == 1, "one statement checks every id"


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [
    [str(THEIRS)],
    [str(MINE_A), str(THEIRS)],
    ["not-a-uuid"],
    [str(uuid.uuid4())],
    [""],
])
async def test_anything_else_is_one_422_and_nothing_is_staged(ids):
    session = _Session()
    with pytest.raises(HTTPException) as err:
        await analytics_service.create_manual_defect(session, PROJECT, {**PAYLOAD, "affects_releases": ids})
    assert err.value.status_code == 422
    assert err.value.detail == analytics_service.AFFECTS_RELEASES_NOT_IN_PROJECT
    assert err.value.detail == "One or more of these releases are not in this project."
    assert session.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [None, []])
async def test_no_release_asserts_nothing_and_asks_nothing(ids):
    session = _Session()
    defect = await analytics_service.create_manual_defect(session, PROJECT, {**PAYLOAD, "affects_releases": ids})
    assert defect.affects_releases is None
    assert session.release_queries == []
