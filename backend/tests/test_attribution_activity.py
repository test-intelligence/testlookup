"""S-D-6 / D-56: the two attribution writes leave an activity row.

* ``POST /runs/{id}/release`` (set_run_release) re-attributes a run and
  demotes the previous primary link: one ``run.release_changed`` row naming
  the actor, the old release and the new one, staged before the commit.
* ``POST /canonical-test-cases/{id}/link`` and ``/bulk-link`` move tests
  between suites: one ``suite.cases_moved`` row per request naming the actor,
  the old suite(s), the new suite and how many tests moved.

The routes run for real over a recording session; the real ledger writer
builds the rows.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


class _Session:
    def __init__(self, *, run=None, previous_release_name=None, move_rows=()):
        self.journal: list[str] = []
        self.added: list = []
        self._run = run
        self._previous = previous_release_name
        self._move_rows = list(move_rows)

    async def execute(self, stmt, *_a, **_k):
        sql = str(stmt)
        result = MagicMock()
        if "releases.name" in sql:
            result.scalar_one_or_none.return_value = self._previous
        else:
            result.scalar_one_or_none.return_value = self._run
        result.all.return_value = list(self._move_rows)
        self.journal.append("execute")
        return result

    def add(self, obj):
        self.added.append(obj)
        self.journal.append("add")

    async def flush(self):
        self.journal.append("flush")

    def begin_nested(self):
        @asynccontextmanager
        async def _savepoint():
            yield

        return _savepoint()

    async def commit(self):
        self.journal.append("commit")

    async def refresh(self, *_a, **_k):
        return None


def _events(session):
    return [row for row in session.added if type(row).__name__ == "ProjectActivityEvent"]


def _lead():
    return SimpleNamespace(id=uuid.uuid4(), full_name="Lena Lead", username="lena", role="QA_LEAD")


@pytest.fixture(autouse=True)
def _no_epoch(monkeypatch):
    from app.services import cache_service

    monkeypatch.setattr(cache_service, "bump_analytics_epoch", AsyncMock())


# -- run release change -------------------------------------------------------


def _release_route(monkeypatch, release):
    from app.services import release_linker

    monkeypatch.setattr(release_linker, "auto_link_release", AsyncMock(return_value=(release, False)))
    monkeypatch.setattr(release_linker, "sync_primary_release", AsyncMock())
    from app.routers.runs import set_run_release

    return set_run_release


@pytest.mark.asyncio
async def test_a_run_release_change_records_who_and_from_which_release(monkeypatch):
    old_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="812", primary_release_id=old_id)
    release = SimpleNamespace(id=uuid.uuid4(), name="2.5", status="active")
    route = _release_route(monkeypatch, release)
    db = _Session(run=run, previous_release_name="2.4")
    actor = _lead()

    await route(run.id, {"release_name": "2.5"}, db=db, current_user=actor, _=None)

    (event,) = _events(db)
    assert event.event_type == "run.release_changed"
    assert event.actor_id == actor.id and event.actor_name == "Lena Lead"
    assert event.entity_id == str(run.id)
    assert event.context["release_name"] == "2.5"
    assert event.context["from_release"] == "release 2.4"
    assert event.summary == "Build 812 moved from release 2.4 to release 2.5"
    # Outcome mode: staged before the commit, so it dies with a rollback.
    assert db.journal.index("add") < db.journal.index("commit")


@pytest.mark.asyncio
async def test_a_first_release_reads_from_no_release(monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number=None, primary_release_id=None)
    release = SimpleNamespace(id=uuid.uuid4(), name="2.5", status="active")
    route = _release_route(monkeypatch, release)
    db = _Session(run=run)

    await route(run.id, {"release_name": "2.5"}, db=db, current_user=_lead(), _=None)

    (event,) = _events(db)
    assert event.context["from_release"] == "no release"


@pytest.mark.asyncio
async def test_setting_the_same_release_again_records_nothing(monkeypatch):
    release = SimpleNamespace(id=uuid.uuid4(), name="2.5", status="active")
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="1", primary_release_id=release.id)
    route = _release_route(monkeypatch, release)
    db = _Session(run=run)

    await route(run.id, {"release_name": "2.5"}, db=db, current_user=_lead(), _=None)

    assert _events(db) == []


# -- suite move ----------------------------------------------------------------


def _suite_routes(monkeypatch, *, canonical=None, target=None):
    from app.routers import suites
    from app.services import test_suite_service as svc

    monkeypatch.setattr(suites, "_enforce_project_access", AsyncMock())
    if canonical is not None:
        monkeypatch.setattr(svc, "get_canonical_or_404", AsyncMock(return_value=canonical))
    monkeypatch.setattr(svc, "get_suite_or_404", AsyncMock(return_value=target))
    monkeypatch.setattr(suites, "_canonical_to_response", lambda c: {"id": str(c.id)})
    return suites


@pytest.mark.asyncio
async def test_a_single_move_records_the_old_and_new_suite(monkeypatch):
    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Payments")
    canonical = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, test_suite_id=uuid.uuid4())
    suites = _suite_routes(monkeypatch, canonical=canonical, target=target)
    db = _Session(move_rows=[("test_refund", "Checkout")])
    actor = _lead()

    await suites.link_canonical_to_suite(
        canonical.id, SimpleNamespace(test_suite_id=target.id), db=db, current_user=actor,
    )

    assert canonical.test_suite_id == target.id
    (event,) = _events(db)
    assert event.event_type == "suite.cases_moved"
    assert event.actor_id == actor.id
    assert event.entity_id == str(target.id) and event.entity_label == "Payments"
    assert event.context == {"count": 1, "from_suites": "Checkout", "tests": "test_refund"}
    assert event.summary == "1 test(s) moved to suite Payments from Checkout"
    assert db.journal.index("add") < db.journal.index("commit")


@pytest.mark.asyncio
async def test_a_bulk_move_records_one_summary_row(monkeypatch):
    from app.services import test_suite_service as svc

    project_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, name="Payments")
    suites = _suite_routes(monkeypatch, target=target)
    monkeypatch.setattr(
        svc, "bulk_link_canonicals_to_suite",
        AsyncMock(return_value={"moved": 3, "skipped_already_in_target": 0, "missing_ids": []}),
    )
    db = _Session(move_rows=[("a", "Checkout"), ("b", "Cart"), ("c", "Checkout")])

    await suites.bulk_link_canonicals_to_suite(
        SimpleNamespace(target_test_suite_id=target.id, canonical_ids=[uuid.uuid4() for _ in range(3)]),
        db=db, current_user=_lead(),
    )

    (event,) = _events(db)
    assert event.event_type == "suite.cases_moved"
    assert event.context["count"] == 3
    assert event.context["from_suites"] == "Cart, Checkout"


@pytest.mark.asyncio
async def test_a_bulk_move_that_moves_nothing_records_nothing(monkeypatch):
    from app.services import test_suite_service as svc

    target = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), name="Payments")
    suites = _suite_routes(monkeypatch, target=target)
    monkeypatch.setattr(
        svc, "bulk_link_canonicals_to_suite",
        AsyncMock(return_value={"moved": 0, "skipped_already_in_target": 1, "missing_ids": []}),
    )
    db = _Session(move_rows=[])

    await suites.bulk_link_canonicals_to_suite(
        SimpleNamespace(target_test_suite_id=target.id, canonical_ids=[uuid.uuid4()]),
        db=db, current_user=_lead(),
    )

    assert _events(db) == []
