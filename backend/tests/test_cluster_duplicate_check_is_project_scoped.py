"""Security (W06 §0.30): the cluster duplicate check lists only the RUN's project's defects.

``GET /api/v1/deep-investigate/{run_id}/clusters/{cluster_id}/duplicate-check``
is guarded by the run only, and ``cluster_service.check_duplicate`` selected the
open defects of EVERY project and returned their titles, severities, components
and Jira keys: a member of project A read project B's defect titles by asking
about one of A's clusters. The candidates are now the run's project's live
(OPEN or IN_PROGRESS) defects only.

A session fake that filters its defects by the project and statuses the
statement actually binds, so dropping the project filter lets B's defect
through and fails the test.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import cluster_service

PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
RUN = str(uuid.uuid4())


def _params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


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
    def __init__(self, defects, run_project=PROJECT_A):
        self.defects = defects
        self.run_project = run_project
        self.defect_queries = 0

    async def execute(self, stmt, *args, **kwargs):
        tables = {t.name for t in stmt.get_final_froms()}
        if "failure_clusters" in tables:
            return _Result([SimpleNamespace(label="Checkout payment gateway timeout")])
        if "test_runs" in tables:
            return _Result([self.run_project] if self.run_project is not None else [])
        assert tables == {"defects"}, tables
        self.defect_queries += 1
        params = _params(stmt)
        projects = {v for k, v in params.items() if k.startswith("project_id")}
        statuses = set()
        for key, value in params.items():
            if key.startswith("resolution_status"):
                statuses |= set(value) if isinstance(value, (list, tuple)) else {value}
        return _Result([
            d for d in self.defects
            if (not projects or d.project_id in projects) and d.resolution_status in statuses
        ])


def _defect(project_id, title, status="OPEN", jira=None):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, title=title, severity="HIGH",
        component="payments", jira_ticket_id=jira, resolution_status=status,
    )


@pytest.mark.asyncio
async def test_another_projects_defect_titles_are_never_listed():
    mine = _defect(PROJECT_A, "Checkout payment gateway timeout", jira="A-1")
    theirs = _defect(PROJECT_B, "Checkout payment gateway timeout in the secret project", jira="B-9")
    session = _Session([mine, theirs])

    result = await cluster_service.check_duplicate(RUN, "c1", session)

    listed = result["potential_duplicates"]
    assert [d["defect_id"] for d in listed] == [str(mine.id)]
    assert "secret project" not in repr(result) and "B-9" not in repr(result)


@pytest.mark.asyncio
async def test_an_in_progress_defect_of_the_project_is_a_candidate():
    in_progress = _defect(PROJECT_A, "Checkout payment gateway timeout", status="IN_PROGRESS")
    closed = _defect(PROJECT_A, "Checkout payment gateway timeout", status="RESOLVED")
    result = await cluster_service.check_duplicate(RUN, "c1", _Session([in_progress, closed]))
    assert [d["defect_id"] for d in result["potential_duplicates"]] == [str(in_progress.id)]
    assert result["has_likely_duplicate"] is True


@pytest.mark.asyncio
async def test_a_run_without_a_project_compares_against_nothing():
    session = _Session([_defect(PROJECT_B, "Checkout payment gateway timeout")], run_project=None)
    result = await cluster_service.check_duplicate(RUN, "c1", session)
    assert result["potential_duplicates"] == [] and result["has_likely_duplicate"] is False
    assert session.defect_queries == 0
