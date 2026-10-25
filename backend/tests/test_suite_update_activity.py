"""W13 TCL-07: ``PATCH /api/v1/suites/{suite_id}`` records ``suite.updated``.

The route drives the REAL stage-only service (``update_test_suite``), the REAL
``copy_suite_owner`` and the REAL ledger writer over a recording session:

* a field whose value CHANGED records one ``suite.updated`` row (context
  ``changed``; before / after of the changed fields only), staged BEFORE the
  route's commit; an identical PATCH records nothing;
* a rename copies an explicit owner row to the new name (the owner is keyed by
  NAME) and keeps the old one; the analytics epoch is bumped AFTER the commit
  (the existing order); a description-only change bumps nothing;
* the role floor (QA_ENGINEER, a decorator dependency) and the project check
  (``_enforce_project_access``: 403 ``Forbidden``) come first.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")


class _Session:
    """Records adds, flushes, commits and statements, in order."""

    def __init__(self, journal, *, owner_rows=()):
        self.journal = journal
        self.added: list = []
        self.statements: list = []
        self._owner_rows = list(owner_rows)

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        self.journal.append(("execute", ""))
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._owner_rows)
        return result

    def add(self, obj):
        self.added.append(obj)
        self.journal.append(("add", type(obj).__name__))

    async def flush(self):
        self.journal.append(("flush", ""))

    def begin_nested(self):
        @asynccontextmanager
        async def _savepoint():
            yield

        return _savepoint()

    async def commit(self):
        self.journal.append(("commit", ""))

    async def refresh(self, *_a, **_k):
        return None


@pytest.fixture
def journal(monkeypatch):
    from app.services import cache_service

    events: list[tuple[str, str]] = []

    async def _bump(project_id):
        events.append(("bump", str(project_id)))

    monkeypatch.setattr(cache_service, "bump_analytics_epoch", _bump)
    return events


def _suite(project_id):
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, name="Checkout API", description=None,
        tags=None, is_default=False, created_at=now, updated_at=now,
    )


def _engineer():
    return SimpleNamespace(id=uuid.uuid4(), full_name="Priya QA", username="priya", role="QA_ENGINEER")


def _routes(monkeypatch, suite):
    from app.routers import suites
    from app.services import test_suite_service as svc

    monkeypatch.setattr(suites, "_enforce_project_access", AsyncMock())
    monkeypatch.setattr(svc, "get_suite_or_404", AsyncMock(return_value=suite))
    return suites


def _payload(**change):
    return SimpleNamespace(**{"name": None, "description": None, "tags": None, **change})


def _events(session):
    return [row for row in session.added if type(row).__name__ == "ProjectActivityEvent"]


def _owner_rows(session):
    return [row for row in session.added if type(row).__name__ == "TestSuiteOwner"]


@pytest.mark.asyncio
async def test_a_rename_records_one_event_copies_the_owner_and_bumps_after_the_commit(journal, monkeypatch):
    project_id, lead_id = uuid.uuid4(), uuid.uuid4()
    suite = _suite(project_id)
    suites = _routes(monkeypatch, suite)
    old_owner = SimpleNamespace(suite_name="Checkout API", owner_user_id=lead_id)
    db = _Session(journal, owner_rows=[old_owner])

    await suites.update_suite(suite.id, _payload(name="  Checkout API v2 "), db=db, current_user=_engineer())

    assert suite.name == "Checkout API v2"
    (event,) = _events(db)
    assert event.event_type == "suite.updated"
    assert event.entity_type == "suite" and event.entity_id == str(suite.id)
    assert event.entity_label == "Checkout API v2"
    assert event.context == {"changed": "name"}
    assert event.diff["before"] == {"name": "Checkout API"}
    assert event.diff["after"] == {"name": "Checkout API v2"}
    assert event.summary == "Suite Checkout API v2 changed: name"
    (copied,) = _owner_rows(db)
    assert (copied.suite_name, copied.owner_user_id, copied.project_id) == ("Checkout API v2", lead_id, project_id)
    kinds = [kind for kind, _ in journal]
    # Staged before the commit; the bump after it (the existing order).
    assert kinds.index("commit") > max(i for i, k in enumerate(kinds) if k == "add")
    assert journal[-1] == ("bump", str(project_id))
    assert kinds.index("bump") > kinds.index("commit")
    assert "delete" not in kinds  # the old name keeps its owner row


@pytest.mark.asyncio
async def test_a_description_change_records_the_event_and_bumps_nothing(journal, monkeypatch):
    suite = _suite(uuid.uuid4())
    suites = _routes(monkeypatch, suite)
    db = _Session(journal)

    await suites.update_suite(suite.id, _payload(description="Cards and wallets"), db=db, current_user=_engineer())

    (event,) = _events(db)
    assert event.context == {"changed": "description"}
    assert event.diff["before"] == {"description": None}
    assert event.diff["after"] == {"description": "Cards and wallets"}
    assert db.statements == []  # no owner read without a rename
    assert _owner_rows(db) == []
    assert ("commit", "") in journal and not any(kind == "bump" for kind, _ in journal)


@pytest.mark.asyncio
async def test_two_changed_fields_are_named_in_order_and_an_identical_patch_records_nothing(journal, monkeypatch):
    suite = _suite(uuid.uuid4())
    suites = _routes(monkeypatch, suite)
    db = _Session(journal)
    await suites.update_suite(
        suite.id, _payload(description="d", tags=["smoke"]), db=db, current_user=_engineer(),
    )
    (event,) = _events(db)
    assert event.context == {"changed": "description, tags"}
    assert event.diff["after"] == {"description": "d", "tags": ["smoke"]}

    same = _Session(journal)
    await suites.update_suite(
        suite.id, _payload(name="Checkout API ", description="d", tags=["smoke"]), db=same,
        current_user=_engineer(),
    )
    assert _events(same) == []
    assert same.statements == []


@pytest.mark.asyncio
async def test_the_role_floor_and_the_project_check_refuse_first(journal, monkeypatch):
    from app.routers import suites
    from app.services import test_suite_service as svc

    route = next(
        r for r in suites.router.routes
        if getattr(r, "path", "") == "/api/v1/suites/{suite_id}" and "PATCH" in getattr(r, "methods", set())
    )
    (role_check,) = [dep.dependency for dep in route.dependencies]
    with pytest.raises(HTTPException) as refused:
        await role_check(current_user=SimpleNamespace(id=uuid.uuid4(), role="TESTER"))
    assert refused.value.status_code == 403
    assert refused.value.detail == "Requires at least QA_ENGINEER role"

    # A QA engineer of ANOTHER project: the real project check answers 403.
    suite = _suite(uuid.uuid4())
    monkeypatch.setattr(svc, "get_suite_or_404", AsyncMock(return_value=suite))
    monkeypatch.setattr(suites, "get_accessible_project_ids", AsyncMock(return_value={uuid.uuid4()}))
    db = _Session(journal)
    with pytest.raises(HTTPException) as forbidden:
        await suites.update_suite(suite.id, _payload(name="Stolen"), db=db, current_user=_engineer())
    assert (forbidden.value.status_code, forbidden.value.detail) == (403, "Forbidden")
    assert suite.name == "Checkout API" and db.added == [] and journal == []


@pytest.mark.asyncio
async def test_copy_suite_owner_adds_nothing_when_the_new_name_already_has_an_owner(journal):
    from app.services.suite_review_service import copy_suite_owner

    project_id = uuid.uuid4()
    rows = [
        SimpleNamespace(suite_name="Old", owner_user_id=uuid.uuid4()),
        SimpleNamespace(suite_name="New", owner_user_id=uuid.uuid4()),
    ]
    db = _Session(journal, owner_rows=rows)
    assert await copy_suite_owner(db, project_id=project_id, from_name="Old", to_name="New") is False
    assert db.added == []

    only_old = _Session(journal, owner_rows=rows[:1])
    assert await copy_suite_owner(only_old, project_id=project_id, from_name="Old", to_name="New") is True
    assert [(row.suite_name, row.owner_user_id) for row in only_old.added] == [("New", rows[0].owner_user_id)]

    nobody = _Session(journal, owner_rows=[])
    assert await copy_suite_owner(nobody, project_id=project_id, from_name="Old", to_name="New") is False
    assert nobody.added == []
