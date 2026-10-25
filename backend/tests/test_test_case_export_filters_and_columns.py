"""W13 TCL-01 + TCL-04: the Excel export honours the list's filters and appends ID / Suite / Description.

``GET /api/v1/test-management/cases/export/excel`` used to take one status,
type and priority and search the title alone, so the workbook was not what the
filtered page showed. It now runs the list's own predicates
(``apply_case_list_filters``: status IN, owner = assignee else author,
automation) and the list's search, keeps its own default of every status, and
APPENDS columns 19-21 ``ID`` / ``Suite`` / ``Description`` (columns 1-18 keep
their numbers: ``tests/regression/test_export_rendering_helpers.py`` pins them).
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

openpyxl = pytest.importorskip("openpyxl")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ExportSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        return _Rows(self.rows)


def _row(**overrides):
    base = dict(
        id=uuid.uuid4(), title="Pay by card", status="draft", test_type="functional", priority="high",
        severity="major", feature_area=None, objective=None, preconditions=None, expected_result=None,
        is_automated=False, automation_status="not_automated", ai_generated=False, ai_quality_score=None,
        last_execution_status=None, tags=["smoke"], steps=None, version=1,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), suite_name="Checkout", description="=1+1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def scoped(monkeypatch):
    from app.core import deps

    project_id = uuid.uuid4()

    async def _scope(*_a, **_k):
        return project_id, None

    monkeypatch.setattr(deps, "resolve_project_scope", _scope)
    return project_id


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


@pytest.mark.asyncio
async def test_the_export_runs_the_lists_filters(scoped):
    from app.routers import test_management_exports as exports

    me = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")
    db = _ExportSession([_row()])
    await exports.export_test_cases_excel(
        project_id=scoped, status=["draft", "review_requested", "draft"], owner="me",
        automation="not_automated", db=db, current_user=me,
    )

    (stmt,) = db.statements
    sql = _sql(stmt)
    assert "managed_test_cases.status IN (__[POSTCOMPILE_status_1])" in sql
    assert "coalesce(managed_test_cases.assignee_id, managed_test_cases.author_id) = " in sql
    assert "coalesce(managed_test_cases.is_automated, false) = false" in sql
    params = stmt.compile().params
    assert ["draft", "review_requested"] in params.values()
    assert me.id in params.values()
    # The export keeps its own default: no retired exclusion was added.
    assert "NOT IN" not in sql


@pytest.mark.asyncio
async def test_a_direct_call_without_filters_builds_todays_predicates_only(scoped):
    from app.routers import test_management_exports as exports

    db = _ExportSession([])
    # The regression tests call it with no filter at all: the Query(None)
    # defaults are normalised away and nothing but the project is filtered.
    await exports.export_test_cases_excel(project_id=scoped, db=db, current_user=object())
    sql = _sql(db.statements[0])
    where = sql.split(" WHERE ", 1)[1]
    assert where.startswith("managed_test_cases.project_id = ")
    assert " AND " not in where.split(" ORDER BY ")[0]

    await exports.export_test_cases_excel(
        project_id=scoped, automation="automated", search="pay-07", db=db, current_user=object(),
    )
    sql = _sql(db.statements[1])
    assert "managed_test_cases.is_automated IS true" in sql
    assert "managed_test_cases.title ILIKE" in sql and "test_case_requirement_links" in sql


@pytest.mark.asyncio
async def test_columns_19_to_21_are_id_suite_and_description(scoped):
    from app.routers import test_management_exports as exports

    row = _row()
    response = await exports.export_test_cases_excel(
        project_id=scoped, db=_ExportSession([row]), current_user=object(),
    )
    sheet = openpyxl.load_workbook(io.BytesIO(response.body), data_only=False)["Test Cases"]
    assert [sheet.cell(row=1, column=c).value for c in (1, 18, 19, 20, 21)] == [
        "Title", "Created At", "ID", "Suite", "Description",
    ]
    assert sheet.max_column == 21
    assert sheet.cell(row=2, column=19).value == str(row.id)
    assert sheet.cell(row=2, column=20).value == "Checkout"
    # Stored as typed, escaped on the way out like every other text cell.
    description = sheet.cell(row=2, column=21)
    assert description.value == "'=1+1" and description.data_type != "f"


@pytest.mark.asyncio
async def test_an_invalid_filter_is_refused_before_the_workbook(scoped):
    from fastapi import HTTPException

    from app.routers import test_management_exports as exports

    db = _ExportSession([])
    with pytest.raises(HTTPException) as refused:
        await exports.export_test_cases_excel(project_id=scoped, automation="maybe", db=db, current_user=object())
    assert refused.value.status_code == 422
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_export_aliases_p0_and_refuses_an_unknown_priority_or_status(scoped):
    """The export takes plain strings, so the list's strictness is applied here
    too: an unknown value used to export an empty workbook that looked real."""
    from fastapi import HTTPException

    from app.core.test_library_vocab import PRIORITY_INVALID_SENTENCE, STATUS_INVALID_SENTENCE
    from app.routers import test_management_exports as exports

    db = _ExportSession([])
    await exports.export_test_cases_excel(project_id=scoped, priority=["P0", "p1"], db=db, current_user=object())
    assert ["critical", "high"] in db.statements[0].compile().params.values()

    for kwargs, sentence in (
        ({"priority": ["urgent"]}, PRIORITY_INVALID_SENTENCE),
        ({"status": ["draft", "bogus"]}, STATUS_INVALID_SENTENCE),
    ):
        with pytest.raises(HTTPException) as refused:
            await exports.export_test_cases_excel(project_id=scoped, db=db, current_user=object(), **kwargs)
        assert (refused.value.status_code, refused.value.detail) == (422, sentence)
    assert len(db.statements) == 1
