"""W23a FAIL-11 — the insight routes, through FastAPI's real guards.

A mini app mounts ``routers/insights.py`` alone: ``require_role`` and
``require_project_access`` run as dependencies (only ``get_current_active_user``
and ``get_db`` are overridden), so the role table, the membership check and
the project-bound key refusal are exercised for real. The session routes each
statement by its SQL and HONOURS the dismiss's ``WHERE id AND project_id``
(another project's insight is simply not found). The ledger producer is a
recorder journalling into the same list as the session's commit / refresh, so
the order is asserted, not assumed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.core.deps import (  # noqa: E402
    CREDENTIAL_KIND_API_KEY,
    CREDENTIAL_KIND_JWT,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
    get_db,
)
from app.routers import insights as route  # noqa: E402
from app.services.insight_vocab import INSIGHT_KINDS, NOT_MEASURED  # noqa: E402


class _Result:
    def __init__(self, rows: Any = (), scalar: Any = None) -> None:
        self._rows = list(rows)
        self._scalar = scalar

    def all(self) -> list:
        return list(self._rows)

    def one(self) -> Any:
        return self._rows[0]

    def scalars(self) -> "_Result":
        return self

    def scalar_one_or_none(self) -> Any:
        return self._scalar if self._scalar is not None else (self._rows[0] if self._rows else None)


class _Session:
    """Routes statements by their SQL; journals flush / commit / refresh."""

    def __init__(self, journal: list, *, member: bool = True, insights: Any = ()) -> None:
        self.journal = journal
        self.member = member
        self.insights = {row.id: row for row in insights}
        self.sql: list[str] = []

    async def execute(self, stmt: Any, *_: Any, **__: Any) -> _Result:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.sql.append(sql)
        if "FROM project_members" in sql:
            return _Result(scalar=uuid.uuid4() if self.member else None)
        if "FOR UPDATE" in sql and "FROM project_insights" in sql:
            wanted, project = compiled.params["id_1"], compiled.params["project_id_1"]
            row = self.insights.get(wanted)
            return _Result([row] if row is not None and row.project_id == project else [])
        if "count(*)" in sql:
            dismissed = sum(1 for r in self.insights.values() if r.dismissed_at is not None)
            return _Result([SimpleNamespace(total=len(self.insights), dismissed_total=dismissed)])
        if "FROM insight_detector_states" in sql:
            return _Result([])
        if "FROM project_insights" in sql:
            return _Result(list(self.insights.values()))
        raise AssertionError(f"unexpected statement: {sql}")

    async def flush(self) -> None:
        self.journal.append(("flush", ""))

    async def commit(self) -> None:
        self.journal.append(("commit", ""))

    async def refresh(self, obj: Any, *_: Any, **__: Any) -> None:
        self.journal.append(("refresh", ""))


def _user(role: str, *, credential: Any = CREDENTIAL_KIND_JWT, bound_project: Any = None) -> SimpleNamespace:
    user = SimpleNamespace(id=uuid.uuid4(), role=role, full_name="Alice Lead", username="alead",
                           email="alead@example.invalid", is_active=True)
    if bound_project is not None:
        _bind_api_key_project(user, bound_project)
    if credential is not None:
        _bind_credential_kind(user, credential)
    return user


def _insight(project_id: uuid.UUID, **over: Any) -> SimpleNamespace:
    now = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
    base = dict(id=uuid.uuid4(), project_id=project_id, kind="flake_rising", severity="medium",
                resolved_at=None, resolved_reason=None, subject_label="Payments sentinel", window_key="7d",
                evidence={"previous": 1, "current": 4}, first_seen_at=now, last_seen_at=now, detection_count=1,
                dismissed_at=None, dismissed_by_id=None, dismissed_by_name=None)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def journal() -> list:
    return []


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, journal: list) -> list:
    calls: list[dict] = []

    async def _record(db: Any, **kwargs: Any) -> None:
        calls.append({"db": db, **kwargs})
        journal.append(("activity", kwargs["event_type"]))

    monkeypatch.setattr(route, "record_activity", _record)
    return calls


@pytest.fixture
def flag(monkeypatch: pytest.MonkeyPatch) -> dict:
    from app.services import feature_flags

    state = {"on": True}

    async def _is_enabled(key: str, **_: Any) -> bool:
        assert key == "cross_run_insights"
        return state["on"]

    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)
    return state


def _client(session: _Session, user: Any) -> AsyncClient:
    app = FastAPI()
    app.include_router(route.router)

    async def _db():
        yield session

    async def _current():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def test_both_routers_are_registered_on_the_app() -> None:
    from app.bootstrap import PROTECTED_ROUTERS
    from app.routers import run_cadences

    assert route.router in PROTECTED_ROUTERS and run_cadences.router in PROTECTED_ROUTERS


# ── list: any member; a GET never writes ──────────────────────────────────────


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER", "ADMIN"])
async def test_any_member_lists(role: str, journal: list, recorded: list, flag: dict) -> None:
    project_id = uuid.uuid4()
    session = _Session(journal, insights=[_insight(project_id)])
    async with _client(session, _user(role)) as client:
        resp = await client.get(f"/api/v1/projects/{project_id}/insights")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["enabled"], body["range"], body["total"], body["dismissed_total"]) == (True, "7d", 1, 0)
    [item] = body["items"]
    assert item["title"] == "Flaky tests rising in suite Payments sentinel (1 → 4)"
    assert item["link"] == {"path": "/flaky", "label": "Flaky tests"}
    assert [d["kind"] for d in body["detectors"]] == list(INSIGHT_KINDS)
    assert {d["reason"] for d in body["detectors"]} == {NOT_MEASURED["not_evaluated"]}
    assert journal == [] and recorded == []
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in session.sql)


async def test_a_non_member_cannot_list(journal: list, flag: dict) -> None:
    project_id = uuid.uuid4()
    async with _client(_Session(journal, member=False), _user("VIEWER")) as client:
        resp = await client.get(f"/api/v1/projects/{project_id}/insights")
    assert resp.status_code == 403 and resp.json()["detail"] == "You do not have access to this project"


async def test_an_unknown_range_is_the_structured_422(journal: list, flag: dict) -> None:
    project_id = uuid.uuid4()
    session = _Session(journal)
    async with _client(session, _user("VIEWER")) as client:
        resp = await client.get(f"/api/v1/projects/{project_id}/insights", params={"range": "1h"})
        too_many = await client.get(f"/api/v1/projects/{project_id}/insights", params={"limit": 101})
        too_few = await client.get(f"/api/v1/projects/{project_id}/insights", params={"limit": 0})
    assert resp.status_code == 422
    assert resp.json() == {"detail": {"code": "unknown_range", "message": "Unknown range. Valid: 24h, 7d."}}
    assert too_many.status_code == 422 and too_few.status_code == 422
    assert not any("project_insights" in sql for sql in session.sql)


async def test_flag_off_lists_the_stored_rows_and_four_flag_off_detectors(journal: list, flag: dict) -> None:
    flag["on"] = False
    project_id = uuid.uuid4()
    async with _client(_Session(journal, insights=[_insight(project_id)]), _user("VIEWER")) as client:
        resp = await client.get(f"/api/v1/projects/{project_id}/insights", params={"range": "24h"})
    body = resp.json()
    assert resp.status_code == 200 and body["enabled"] is False and len(body["items"]) == 1
    assert [d["reason"] for d in body["detectors"]] == [NOT_MEASURED["flag_off"]] * 4
    assert all(d["measured"] is False for d in body["detectors"])


async def test_a_key_bound_to_another_project_is_refused(journal: list, flag: dict) -> None:
    project_id = uuid.uuid4()
    key = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY, bound_project=uuid.uuid4())
    row = _insight(project_id)
    session = _Session(journal, insights=[row])
    async with _client(session, key) as client:
        listed = await client.get(f"/api/v1/projects/{project_id}/insights")
        dismissed = await client.post(f"/api/v1/projects/{project_id}/insights/{row.id}/dismiss")
    assert listed.status_code == 403 and dismissed.status_code == 403
    assert listed.json()["detail"] == "This API key is restricted to a different project"
    assert row.dismissed_at is None and journal == []


# ── dismiss: QA engineer and above; scoped; one ledger row before the commit ──


@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
async def test_below_qa_engineer_cannot_dismiss(role: str, journal: list, recorded: list) -> None:
    project_id = uuid.uuid4()
    row = _insight(project_id)
    async with _client(_Session(journal, insights=[row]), _user(role)) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/insights/{row.id}/dismiss")
    assert resp.status_code == 403 and resp.json()["detail"] == "Requires at least QA_ENGINEER role"
    assert row.dismissed_at is None and recorded == [] and journal == []


async def test_a_qa_engineer_dismisses_once_with_the_ledger_row_before_the_commit(
    journal: list, recorded: list,
) -> None:
    project_id = uuid.uuid4()
    row = _insight(project_id)
    session = _Session(journal, insights=[row])
    user = _user("QA_ENGINEER")
    async with _client(session, user) as client:
        first = await client.post(f"/api/v1/projects/{project_id}/insights/{row.id}/dismiss")
        second = await client.post(f"/api/v1/projects/{project_id}/insights/{row.id}/dismiss")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["id"] == str(row.id) and body["dismissed_by_name"] == "Alice Lead"
    assert body["dismissed_at"] is not None and row.dismissed_by_id == user.id
    assert journal == [("flush", ""), ("activity", "insight.dismissed"), ("commit", ""), ("refresh", "")]
    [call] = recorded
    assert call["db"] is session and call["project_id"] == project_id
    assert (call["entity_type"], call["entity_id"], call["entity_label"]) == ("insight", str(row.id), "Payments sentinel")
    assert call["actor"].actor_type == "user"
    # The second dismissal answers the row as it is: no write, no second ledger row.
    assert second.status_code == 200 and second.json()["dismissed_by_name"] == "Alice Lead"
    assert len(recorded) == 1 and journal.count(("commit", "")) == 1


async def test_another_projects_insight_is_the_unknown_id_404(journal: list, recorded: list) -> None:
    project_id, other = uuid.uuid4(), uuid.uuid4()
    foreign = _insight(other)
    session = _Session(journal, insights=[foreign])
    async with _client(session, _user("QA_LEAD")) as client:
        theirs = await client.post(f"/api/v1/projects/{project_id}/insights/{foreign.id}/dismiss")
        unknown = await client.post(f"/api/v1/projects/{project_id}/insights/{uuid.uuid4()}/dismiss")
    expected = {"detail": {"code": "insight_not_found", "message": "Insight not found."}}
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json() == expected
    assert foreign.dismissed_at is None and recorded == [] and ("commit", "") not in journal


async def test_a_key_bound_to_this_project_may_dismiss(journal: list, recorded: list) -> None:
    project_id = uuid.uuid4()
    row = _insight(project_id)
    key = _user("QA_ENGINEER", credential=CREDENTIAL_KIND_API_KEY, bound_project=project_id)
    async with _client(_Session(journal, insights=[row]), key) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/insights/{row.id}/dismiss")
    assert resp.status_code == 200, resp.text
    assert len(recorded) == 1