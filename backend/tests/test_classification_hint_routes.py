"""FAIL-10: the classification-hint routes' wiring and handler rules (no database).

The role matrix, the 404 parity, the duplicate 409 and the ledger rows are
proven through the real app on Postgres
(``integration/test_classification_hints_postgres.py``). Here: every route is
mounted under the project, guarded by ``require_project_access``, the writes
also by ``require_role(QA_LEAD)``; a refused write stages no ledger row and
commits nothing; a PATCH that changes nothing records nothing; the duplicate
mapping keys on the constraint name only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.schemas import ClassificationHintCreate, ClassificationHintUpdate
from app.routers import classification_hints as router_module

PREFIX = "/api/v1/projects/{project_id}/classification-hints"
PID, HID = uuid.uuid4(), uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4(), username="lead", full_name="Priya Nair", role="QA_LEAD")
PAYLOAD = {"id": str(HID), "project_id": str(PID), "match_field": "error_message", "pattern": "ECONNREFUSED",
           "category": "INFRASTRUCTURE", "note": None, "enabled": True, "created_by_name": "Priya Nair",
           "created_at": None, "updated_at": None}


def _deps(route: APIRoute) -> list[str]:
    out, stack = [], [route.dependant]
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            out.append(getattr(dep.call, "__qualname__", ""))
        stack.extend(dep.dependencies)
    return out


def _routes() -> dict[tuple[str, str], APIRoute]:
    from app.main import app

    return {
        (method, route.path): route
        for route in app.routes if isinstance(route, APIRoute) and route.path.startswith(PREFIX)
        for method in route.methods
    }


def test_five_routes_are_mounted_under_the_project_each_guarded():
    routes = _routes()
    assert set(routes) == {
        ("GET", PREFIX), ("GET", f"{PREFIX}/preview"), ("POST", PREFIX),
        ("PATCH", f"{PREFIX}/{{hint_id}}"), ("DELETE", f"{PREFIX}/{{hint_id}}"),
    }
    for (method, path), route in routes.items():
        names = _deps(route)
        assert any("require_project_access" in n for n in names), (method, path)
        assert any("get_current_active_user" in n for n in names), (method, path)
        writes = method in ("POST", "PATCH", "DELETE")
        assert any(n.startswith("require_role") for n in names) is writes, (method, path)


def test_the_bodies_forbid_extra_fields():
    with pytest.raises(ValidationError):
        ClassificationHintCreate(match_field="error_message", pattern="abc", category="FLAKY", project_id="x")
    with pytest.raises(ValidationError):
        ClassificationHintUpdate(project_id="x")
    assert ClassificationHintUpdate(category="FLAKY").model_fields_set == {"category"}


def _integrity(message: str) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception(message))


def test_only_the_rule_index_is_a_duplicate():
    assert router_module._is_duplicate(_integrity(
        'duplicate key value violates unique constraint "uq_classification_hints_project_rule"'))
    assert not router_module._is_duplicate(_integrity(
        'insert or update on table "classification_hints" violates foreign key constraint "classification_hints_created_by_fkey"'))


class _Db:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def ledger(monkeypatch):
    calls: list[dict] = []

    async def _record(db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(router_module, "record_activity", _record)
    return calls


@pytest.mark.asyncio
async def test_a_refused_create_stages_nothing_and_commits_nothing(monkeypatch, ledger):
    async def _refuse(*_a, **_k):
        raise HTTPException(status_code=422, detail="pattern must be 3-200 characters.")

    monkeypatch.setattr(router_module.hints, "create_hint", _refuse)
    db = _Db()
    with pytest.raises(HTTPException):
        await router_module.create_classification_hint(
            PID, ClassificationHintCreate(match_field="error_message", pattern="ab", category="FLAKY"),
            db=db, current_user=USER, _=USER,
        )
    assert ledger == [] and db.commits == 0


@pytest.mark.asyncio
async def test_a_duplicate_is_409_after_a_rollback_and_another_integrity_error_re_raises(monkeypatch, ledger):
    body = ClassificationHintCreate(match_field="error_message", pattern="abc", category="FLAKY")
    for message, expected in (
        ('violates unique constraint "uq_classification_hints_project_rule"', 409),
        ('violates check constraint "ck_classification_hints_category"', None),
    ):
        async def _boom(*_a, _message=message, **_k):
            raise _integrity(_message)

        monkeypatch.setattr(router_module.hints, "create_hint", _boom)
        db = _Db()
        if expected:
            with pytest.raises(HTTPException) as caught:
                await router_module.create_classification_hint(PID, body, db=db, current_user=USER, _=USER)
            assert caught.value.status_code == 409
            assert caught.value.detail == "A hint with this field and text already exists."
        else:
            with pytest.raises(IntegrityError):
                await router_module.create_classification_hint(PID, body, db=db, current_user=USER, _=USER)
        assert db.rollbacks == 1 and db.commits == 0 and ledger == []


@pytest.mark.asyncio
async def test_a_created_hint_stages_one_row_then_commits(monkeypatch, ledger):
    async def _create(*_a, **_k):
        return dict(PAYLOAD)

    monkeypatch.setattr(router_module.hints, "create_hint", _create)
    db = _Db()
    out = await router_module.create_classification_hint(
        PID, ClassificationHintCreate(**{k: PAYLOAD[k] for k in ("match_field", "pattern", "category")}),
        db=db, current_user=USER, _=USER,
    )
    assert out["id"] == str(HID) and db.commits == 1
    [row] = ledger
    assert row["event_type"] == "classification_hint.created"
    assert row["entity_type"] == "classification_hint" and row["entity_id"] == str(HID)
    assert row["entity_label"] == 'error message contains "ECONNREFUSED" → Infrastructure'
    assert row["project_id"] == PID


@pytest.mark.asyncio
async def test_a_patch_that_changes_nothing_records_and_commits_nothing(monkeypatch, ledger):
    async def _unchanged(*_a, **_k):
        return dict(PAYLOAD), {}, {}

    monkeypatch.setattr(router_module.hints, "update_hint", _unchanged)
    db = _Db()
    await router_module.update_classification_hint(
        PID, HID, ClassificationHintUpdate(category="INFRASTRUCTURE"), db=db, current_user=USER, _=USER,
    )
    assert ledger == [] and db.commits == 0

    async def _changed(*_a, **_k):
        return dict(PAYLOAD, category="FLAKY"), {"category": "INFRASTRUCTURE"}, {"category": "FLAKY"}

    monkeypatch.setattr(router_module.hints, "update_hint", _changed)
    await router_module.update_classification_hint(
        PID, HID, ClassificationHintUpdate(category="FLAKY"), db=db, current_user=USER, _=USER,
    )
    [row] = ledger
    assert row["event_type"] == "classification_hint.updated"
    assert row["context"] == {"changed": "category"}
    assert (row["before"], row["after"]) == ({"category": "INFRASTRUCTURE"}, {"category": "FLAKY"})
    assert db.commits == 1
