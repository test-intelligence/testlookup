"""A QA lead sees AI evaluation runs of their own projects and runs with no project.

Evaluation runs are instance-level rows, but a candidate (replay) run is made
for one project and carries its ``project_id``, who asked for it, the model
and the scores. ``GET /api/v1/ai-eval/runs`` and ``/runs/{id}`` showed every
such run to any QA lead.

The rule:

* ``GET /runs``: for a caller who is not an instance admin, a run that has a
  ``project_id`` is listed only when the caller can access that project. The
  ``total`` counts the same filtered set. Runs with no project (golden-dataset
  evaluations) stay visible to every QA lead.
* ``GET /runs/{id}``: a run of a project the caller cannot access answers the
  same 404 as a run that does not exist.
* An instance admin sees everything.
* ``GET /report-cycles`` is unchanged: a report cycle has no project column
  (by design, see ``DecisionReportEvalCycle``), so there is nothing to filter
  on. A test pins that, so adding the column fails here until the route
  filters on it.

The session is scripted, so the list is checked through the SQL it runs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.deps import get_current_active_user  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.models.postgres import AIEvalRun, DecisionReportEvalCycle  # noqa: E402
from app.routers import ai_evaluation as router  # noqa: E402

pytestmark = pytest.mark.regression

PROJECT_A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
PROJECT_B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
EVALUATED = datetime(2026, 11, 19, 9, 30, tzinfo=timezone.utc)
RUNS = "/api/v1/ai-eval/runs"


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one(self):
        return self._rows[0]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each ``execute`` with the next scripted result and keeps the statements."""

    def __init__(self, *results):
        self._results = list(results)
        self.statements: list = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return self._results.pop(0) if self._results else _Rows([])


def _sql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _run(project_id: uuid.UUID | None) -> AIEvalRun:
    return AIEvalRun(
        id=uuid.uuid4(), dataset_id=uuid.uuid4(), model_name="candidate-model",
        task_type="classification", metric_kind="replay", status="completed", accuracy=0.91,
        f1_score=0.9, total_items=100, correct_items=91, scored_items=100, unscored_items=0,
        error_items=0, items_capped=False, fallback_used=False, evaluated_at=EVALUATED,
        project_id=project_id, requested_by=uuid.uuid4(),
    )


def _client(session: _Session, monkeypatch, *, accessible) -> AsyncClient:
    """The router signed in as a QA lead (or an admin when ``accessible`` is None)."""
    role = "ADMIN" if accessible is None else "QA_LEAD"
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None)
    monkeypatch.setattr(router, "get_accessible_project_ids", AsyncMock(return_value=accessible))
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_current_active_user] = lambda: user

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── GET /runs ──────────────────────────────────────────────────────────────


async def test_a_qa_leads_list_and_total_leave_out_other_projects_runs(monkeypatch):
    session = _Session(_Rows([_run(PROJECT_A), _run(None)]), _Rows([2]))
    async with _client(session, monkeypatch, accessible={PROJECT_A}) as http:
        resp = await http.get(RUNS, params={"metric_kind": "replay"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    page, count = (_sql(s) for s in session.statements)
    for sql in (page, count):  # the total counts the SAME filtered set
        assert "ai_eval_runs.project_id IS NULL OR ai_eval_runs.project_id IN" in sql
        assert str(PROJECT_A) in sql
        assert str(PROJECT_B) not in sql
        assert "metric_kind = 'replay'" in sql  # the existing filters still apply


async def test_a_qa_lead_with_no_projects_sees_only_runs_without_a_project(monkeypatch):
    session = _Session(_Rows([_run(None)]), _Rows([1]))
    async with _client(session, monkeypatch, accessible=set()) as http:
        resp = await http.get(RUNS)
    assert resp.status_code == 200
    page, count = (_sql(s) for s in session.statements)
    for sql in (page, count):
        assert "ai_eval_runs.project_id IS NULL" in sql
        assert "ai_eval_runs.project_id IN" not in sql


async def test_an_admin_lists_every_run(monkeypatch):
    session = _Session(_Rows([_run(PROJECT_A), _run(PROJECT_B), _run(None)]), _Rows([3]))
    async with _client(session, monkeypatch, accessible=None) as http:
        resp = await http.get(RUNS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert [r["project_id"] for r in body["items"]] == [str(PROJECT_A), str(PROJECT_B), None]
    for sql in (_sql(s) for s in session.statements):
        assert "WHERE" not in sql  # no project filter, no other filter


# ── GET /runs/{id} ─────────────────────────────────────────────────────────


async def test_another_projects_run_is_not_found_for_a_qa_lead(monkeypatch):
    foreign = _run(PROJECT_B)
    async with _client(_Session(_Rows([foreign])), monkeypatch, accessible={PROJECT_A}) as http:
        resp = await http.get(f"{RUNS}/{foreign.id}")
    async with _client(_Session(_Rows([])), monkeypatch, accessible={PROJECT_A}) as http:
        missing = await http.get(f"{RUNS}/{uuid.uuid4()}")
    assert resp.status_code == 404
    # The same answer as a run that does not exist: the id confirms nothing.
    assert resp.json() == missing.json()
    assert resp.json()["detail"] == {"code": "eval_run_not_found", "message": "Evaluation run not found."}
    assert "candidate-model" not in resp.text and str(PROJECT_B) not in resp.text


@pytest.mark.parametrize("project_id", [PROJECT_A, None], ids=["own project", "no project"])
async def test_a_qa_lead_reads_a_run_of_their_project_or_of_none(monkeypatch, project_id):
    run = _run(project_id)
    async with _client(_Session(_Rows([run])), monkeypatch, accessible={PROJECT_A}) as http:
        resp = await http.get(f"{RUNS}/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(run.id)
    assert resp.json()["accuracy"] == 0.91


@pytest.mark.parametrize("project_id", [PROJECT_A, PROJECT_B, None])
async def test_an_admin_reads_any_run(monkeypatch, project_id):
    run = _run(project_id)
    async with _client(_Session(_Rows([run])), monkeypatch, accessible=None) as http:
        resp = await http.get(f"{RUNS}/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["project_id"] == (str(project_id) if project_id else None)


# ── GET /report-cycles ─────────────────────────────────────────────────────


def test_a_report_cycle_has_no_project_to_filter_on():
    """If this fails, a project column was added to the report cycles: filter
    ``GET /api/v1/ai-eval/report-cycles`` on it the way ``GET /runs`` is."""
    columns = set(DecisionReportEvalCycle.__table__.columns.keys())
    assert not {"project_id", "test_run_id"} & columns
