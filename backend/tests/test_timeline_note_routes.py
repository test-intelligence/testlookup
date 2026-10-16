"""W08 TRND-04: the timeline-note routes, without a database.

* every route is behind ``require_project_access()``; the three writes also
  behind ``require_role(QA_ENGINEER)``, and a VIEWER or TESTER is refused 403
  before the service runs;
* each write stages exactly one ``timeline_note.*`` ledger row (the label is
  the text's first 60 characters, ``changed`` names the fields, no user id in
  ``before`` / ``after``) and commits once; a PATCH that changes nothing
  records and commits nothing;
* the list answers ``total`` / ``truncated`` from the service's count, not
  from ``len(items)``.

The rules, the 404s and the ledger rows over real Postgres:
``tests/integration/test_timeline_notes_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.postgres import TimelineNote, UserRole
from app.models.schemas import TimelineNoteCreate, TimelineNoteUpdate
from app.routers import timeline_notes as route_module

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
NOTE = uuid.UUID("44444444-4444-4444-8444-444444444444")
BASE = f"/api/v1/projects/{PROJECT}/timeline-notes"


def _user(role="QA_ENGINEER", uid=None):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role, full_name="Sam Lee", username="sam",
                           email="sam@example.test", is_active=True, is_service_account=False,
                           api_key_project_id=None)


def _note(text="release freeze", **kw) -> TimelineNote:
    values = dict(id=NOTE, project_id=PROJECT, suite_name=None, starts_on=date(2026, 9, 10),
                  ends_on=date(2026, 9, 12), text=text, created_by_id=uuid.uuid4(),
                  created_at=datetime(2026, 9, 10, tzinfo=timezone.utc), updated_at=None)
    values.update(kw)
    return TimelineNote(**values)


def _dependency_names(route) -> list[str]:
    names, stack = [], list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        names.append(getattr(dep.call, "__qualname__", ""))
        stack.extend(dep.dependencies)
    return names


@pytest.mark.parametrize("method,path", [
    ("GET", ""), ("POST", ""), ("PATCH", "/{note_id}"), ("DELETE", "/{note_id}"),
])
def test_every_route_is_guarded_by_the_path_project(method, path):
    [route] = [r for r in route_module.router.routes
               if r.path == f"/api/v1/projects/{{project_id}}/timeline-notes{path}" and method in r.methods]
    names = _dependency_names(route)
    assert any("require_project_access" in n for n in names), names
    assert any("require_role" in n for n in names) is (method != "GET"), names


@pytest.fixture
def http(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    app.include_router(route_module.router)
    state = {"user": _user("VIEWER")}

    async def _db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    refuse = AsyncMock(side_effect=AssertionError("the service ran for a refused role"))
    for name in ("create_note", "update_note", "delete_note"):
        monkeypatch.setattr(route_module.notes, name, refuse)
    return TestClient(app, raise_server_exceptions=False), state, refuse


@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
@pytest.mark.parametrize("method,path,body", [
    ("POST", "", {"starts_on": "2026-09-10", "text": "freeze"}),
    ("PATCH", f"/{NOTE}", {"text": "freeze"}),
    ("DELETE", f"/{NOTE}", None),
])
def test_below_qa_engineer_every_write_is_refused_before_the_service(http, role, method, path, body):
    client, state, refuse = http
    state["user"] = _user(role)
    response = client.request(method, BASE + path, json=body)
    assert response.status_code == 403, response.text
    assert "Requires at least QA_ENGINEER" in response.text
    refuse.assert_not_awaited()


class _Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.fixture
def ledger(monkeypatch):
    rows: list[dict] = []

    async def record(db, **kwargs):
        rows.append(kwargs)

    monkeypatch.setattr(route_module, "record_activity", record)
    return rows


@pytest.mark.asyncio
async def test_a_pin_stages_one_created_row_and_commits_once(monkeypatch, ledger):
    note = _note(text="z" * 90, suite_name="checkout")
    monkeypatch.setattr(route_module.notes, "create_note", AsyncMock(return_value=note))
    db, user = _Session(), _user()
    body = await route_module.create_timeline_note(
        project_id=PROJECT, body=TimelineNoteCreate(starts_on=date(2026, 9, 10), text="z" * 90), db=db,
        current_user=user, _=user,
    )
    assert db.commits == 1 and body["id"] == str(NOTE)
    [row] = ledger
    assert (row["event_type"], row["entity_type"], row["entity_id"]) == ("timeline_note.created", "timeline_note", str(NOTE))
    assert row["entity_label"] == "z" * 60
    assert row["after"] == {"text": "z" * 90, "starts_on": "2026-09-10", "ends_on": "2026-09-12", "suite_name": "checkout"}
    assert str(user.id) not in repr(row["after"])


@pytest.mark.asyncio
async def test_a_change_names_its_fields_and_no_change_records_nothing(monkeypatch, ledger):
    note = _note()
    update = AsyncMock(return_value=(note, ["text", "dates"], {"text": "a"}, {"text": "b"}))
    monkeypatch.setattr(route_module.notes, "update_note", update)
    db, user = _Session(), _user()
    await route_module.update_timeline_note(
        project_id=PROJECT, note_id=NOTE, body=TimelineNoteUpdate(text="b"), db=db, current_user=user, _=user,
    )
    [row] = ledger
    assert row["event_type"] == "timeline_note.updated"
    assert row["context"] == {"changed": "text, dates"} and row["changed_fields"] == ["text", "dates"]
    assert (row["before"], row["after"]) == ({"text": "a"}, {"text": "b"})
    assert db.commits == 1

    update.return_value = (note, [], {}, {})
    body = await route_module.update_timeline_note(
        project_id=PROJECT, note_id=NOTE, body=TimelineNoteUpdate(), db=db, current_user=user, _=user,
    )
    assert len(ledger) == 1 and db.commits == 1, "no change: no ledger row, no commit"
    assert body["id"] == str(NOTE)


@pytest.mark.asyncio
async def test_a_removal_stages_one_deleted_row_with_the_notes_values(monkeypatch, ledger):
    monkeypatch.setattr(route_module.notes, "delete_note", AsyncMock(return_value=_note()))
    db, user = _Session(), _user()
    response = await route_module.delete_timeline_note(project_id=PROJECT, note_id=NOTE, db=db, current_user=user, _=user)
    assert response.status_code == 204 and db.commits == 1
    [row] = ledger
    assert row["event_type"] == "timeline_note.deleted" and row["entity_label"] == "release freeze"
    assert row["before"]["text"] == "release freeze" and row["after"] is None


@pytest.mark.asyncio
async def test_the_list_reports_the_count_not_the_page(monkeypatch):
    rows = [_note(), _note(id=uuid.uuid4())]
    listed = AsyncMock(return_value=(rows, 230))
    monkeypatch.setattr(route_module.notes, "list_notes", listed)
    reader = _user("VIEWER")
    body = await route_module.list_timeline_notes(
        project_id=PROJECT, start=date(2026, 9, 5), end=date(2026, 9, 18), suite="Checkout", db=object(),
        current_user=reader,
    )
    assert (body["total"], body["truncated"], body["start"], body["end"]) == (230, True, "2026-09-05", "2026-09-18")
    assert [item["can_edit"] for item in body["items"]] == [False, False]
    assert listed.await_args.kwargs == {"start": date(2026, 9, 5), "end": date(2026, 9, 18), "suite": "Checkout"}


def test_the_role_order_check_is_not_an_inline_admin_test():
    """``tests/regression/test_admin_routes_refuse_project_keys.py`` scan 2 flags
    ``role == ADMIN`` gates; the QA-lead rule is an index comparison."""
    import inspect

    from app.services import timeline_note_service

    source = inspect.getsource(timeline_note_service)
    assert "UserRole.ADMIN" not in source and "== \"ADMIN\"" not in source
    assert "_ROLE_ORDER.index" in source
    assert UserRole.QA_LEAD.value == "QA_LEAD"
