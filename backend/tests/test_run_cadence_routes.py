"""W23a TRND-03 — the expected-run-cadence routes, through FastAPI's real guards.

A mini app mounts ``routers/run_cadences.py`` alone: the role and project
guards run as dependencies (only ``get_current_active_user`` and ``get_db``
are overridden), so the QA_LEAD rule, the project-bound key refusal (N26) and
the membership check are exercised for real. The REAL service runs on a
statement-routing fake session; the ledger producer is patched with a
recorder that journals into the same list as ``commit`` / ``refresh``, so the
order "stage, commit, refresh" is asserted, not assumed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.core.deps import (  # noqa: E402
    CREDENTIAL_KIND_API_KEY,
    CREDENTIAL_KIND_JWT,
    PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
    get_db,
)
from app.models.postgres import RunCadence  # noqa: E402
from app.routers import run_cadences as route  # noqa: E402
from app.services.run_cadence_vocab import CADENCE_ERRORS  # noqa: E402

PROJECT = uuid.UUID("22222222-2222-4222-8222-222222222222")
BASE = f"/api/v1/projects/{PROJECT}/run-cadences"
XSS = "<img src=x onerror=alert(1)>"


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows), first=lambda: rows[0] if rows else None)


class _Session:
    """Routes statements by their SQL; journals flush / commit / refresh."""

    def __init__(self, journal, *, cadences=(), member=True):
        self.journal = journal
        self.cadences = list(cadences)
        self.member = member
        self.sql: list[str] = []
        self.deleted: list = []

    def _matching(self, sql, params):
        rows = [c for c in self.cadences if c.project_id == params.get("project_id_1", c.project_id)]
        if "run_cadences.id = " in sql:
            rows = [c for c in rows if c.id == params["id_1"]]
        if "run_cadences.id != " in sql:
            rows = [c for c in rows if c.id != params["id_1"]]
        if "suite_key IS NULL" in sql:
            rows = [c for c in rows if c.suite_key is None]
        elif "run_cadences.suite_key = " in sql:
            rows = [c for c in rows if c.suite_key == params["suite_key_1"]]
        return rows

    async def execute(self, stmt, *_a, **_kw):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        self.sql.append(sql)
        if "FROM project_members" in sql:
            return _Result(scalar=uuid.uuid4() if self.member else None)
        if "FROM test_runs" in sql or "FROM run_cadence_windows" in sql or "FROM users" in sql:
            return _Result()
        if "count(*)" in sql and "FROM run_cadences" in sql:
            return _Result(scalar=len(self._matching(sql, params)))
        if sql.startswith("SELECT run_cadences.id \nFROM run_cadences"):
            return _Result([(c.id,) for c in self._matching(sql, params)])
        if "FROM run_cadences" in sql:
            return _Result(self._matching(sql, params))
        return _Result()

    def add(self, obj):
        self.cadences.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)
        self.cadences.remove(obj)

    async def flush(self):
        self.journal.append(("flush", ""))

    async def commit(self):
        self.journal.append(("commit", ""))

    async def refresh(self, obj, *_a, **_kw):
        now = datetime.now(timezone.utc)
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        obj.updated_at = now
        self.journal.append(("refresh", type(obj).__name__))

    async def rollback(self):
        self.journal.append(("rollback", ""))


def _user(role, *, credential=CREDENTIAL_KIND_JWT, bound_project=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, full_name="Alice Lead", username="alice",
                           email="alice@example.invalid", is_active=True)
    if bound_project is not None:
        _bind_api_key_project(user, bound_project)
    if credential is not None:
        _bind_credential_kind(user, credential)
    return user


def _cadence(project_id=PROJECT, **kw) -> RunCadence:
    created = datetime.now(timezone.utc) - timedelta(days=3)
    values = dict(id=uuid.uuid4(), project_id=project_id, suite_name="nightly", suite_key="nightly",
                  mode="weekly", weekdays=31, expected_by=datetime(2026, 1, 1, 6, 0).time(), cron=None,
                  timezone="Europe/Berlin", grace_minutes=60, effective_from=created, checked_through=created,
                  paused_at=None, paused_until=None, paused_reason=None, paused_by_id=None, created_by_id=None,
                  created_at=created, updated_at=created)
    values.update(kw)
    return RunCadence(**values)


def _body(**kw):
    body = {"suite_name": "nightly", "mode": "weekly", "weekdays": ["mon", "tue", "wed", "thu", "fri"],
            "expected_by": "06:00", "timezone": "Europe/Berlin", "grace_minutes": 60}
    body.update(kw)
    return body


@pytest.fixture
def journal():
    return []


@pytest.fixture
def recorded(monkeypatch, journal):
    calls: list[dict] = []

    async def _record(db, **kwargs):
        calls.append({"db": db, **kwargs})
        journal.append(("activity", kwargs["event_type"]))

    monkeypatch.setattr(route, "record_activity", _record)
    return calls


def _client(session, user) -> AsyncClient:
    app = FastAPI()
    app.include_router(route.router)

    async def _db():
        yield session

    async def _current():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _handler_steps(journal) -> list[tuple[str, str]]:
    """The router's own steps: flushes and the service's refresh (just before each staged row) dropped."""
    steps = [e for e in journal if e[0] != "flush"]
    return [e for i, e in enumerate(steps)
            if not (e[0] == "refresh" and i + 1 < len(steps) and steps[i + 1][0] == "activity")]


def _dependency_names(r) -> list[str]:
    names, stack = [], list(r.dependant.dependencies)
    while stack:
        dep = stack.pop()
        names.append(getattr(dep.call, "__qualname__", ""))
        stack.extend(dep.dependencies)
    return names


def test_every_route_is_guarded_and_status_precedes_the_id_routes():
    paths = [(sorted(r.methods)[0], r.path) for r in route.router.routes]
    assert paths.index(("GET", "/api/v1/projects/{project_id}/run-cadences/status")) < min(
        i for i, (_m, p) in enumerate(paths) if "{cadence_id}" in p)
    for r in route.router.routes:
        names = _dependency_names(r)
        assert any("require_project_access" in n for n in names), r.path
        assert any("require_role" in n for n in names) is ("GET" not in r.methods), r.path
    assert not any("GET" in r.methods and "{cadence_id}" in r.path for r in route.router.routes)


# ── reads: any member; a bound key of THIS project ───────────────────────────


@pytest.mark.parametrize("user_kwargs", [
    {"role": "VIEWER"},
    {"role": "ADMIN", "credential": CREDENTIAL_KIND_API_KEY, "bound_project": PROJECT},
])
async def test_any_member_and_its_own_bound_key_read(user_kwargs, journal, recorded):
    session = _Session(journal, cadences=[_cadence()])
    async with _client(session, _user(**user_kwargs)) as client:
        listed = await client.get(BASE)
        status = await client.get(f"{BASE}/status?days=7")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1 and listed.json()["items"][0]["suite_name"] == "nightly"
    assert status.status_code == 200, status.text
    assert status.json()["configured"] is True and status.json()["days"] == 7
    assert journal == [] and recorded == []


async def test_status_without_a_cadence_and_its_day_bounds(journal):
    session = _Session(journal)
    async with _client(session, _user("VIEWER")) as client:
        empty = await client.get(f"{BASE}/status")
        low = await client.get(f"{BASE}/status?days=0")
        high = await client.get(f"{BASE}/status?days=91")
    assert empty.status_code == 200 and empty.json()["configured"] is False and empty.json()["items"] == []
    assert empty.json()["days"] == 14
    assert (low.status_code, high.status_code) == (422, 422)


async def test_a_non_member_reads_nothing(journal):
    session = _Session(journal, cadences=[_cadence()], member=False)
    async with _client(session, _user("VIEWER")) as client:
        resp = await client.get(BASE)
    assert resp.status_code == 403


# ── writes: QA_LEAD and above, never a project-bound key ─────────────────────


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER"])
async def test_below_qa_lead_every_write_is_refused(role, journal, recorded):
    row = _cadence()
    session = _Session(journal, cadences=[row])
    async with _client(session, _user(role)) as client:
        responses = [
            await client.post(BASE, json=_body(suite_name="other")),
            await client.patch(f"{BASE}/{row.id}", json={"grace_minutes": 5}),
            await client.delete(f"{BASE}/{row.id}"),
            await client.post(f"{BASE}/{row.id}/pause", json={
                "until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "reason": "x"}),
            await client.post(f"{BASE}/{row.id}/resume"),
        ]
    for resp in responses:
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "Requires at least QA_LEAD role"
    assert journal == [] and recorded == []


async def test_a_project_bound_key_is_refused_on_every_write(journal, recorded):
    row = _cadence(paused_at=datetime.now(timezone.utc), paused_until=datetime.now(timezone.utc) + timedelta(days=1),
                   paused_reason="freeze")
    session = _Session(journal, cadences=[row])
    key = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY, bound_project=PROJECT)
    async with _client(session, key) as client:
        responses = [
            await client.post(BASE, json=_body(suite_name="other")),
            await client.patch(f"{BASE}/{row.id}", json={"grace_minutes": 5}),
            await client.delete(f"{BASE}/{row.id}"),
            await client.post(f"{BASE}/{row.id}/pause", json={
                "until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "reason": "x"}),
            await client.post(f"{BASE}/{row.id}/resume"),
        ]
    for resp in responses:
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL
    assert journal == [] and recorded == [] and session.deleted == []


async def test_qa_lead_creates_and_the_ledger_row_is_staged_before_the_commit(journal, recorded):
    session = _Session(journal)
    lead = _user("QA_LEAD")
    async with _client(session, lead) as client:
        resp = await client.post(BASE, json=_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["summary"] == "Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min"
    assert (body["weekdays"], body["expected_by"], body["paused"]) == (["mon", "tue", "wed", "thu", "fri"], "06:00", False)
    assert body["next_deadline"] is not None
    assert _handler_steps(journal) == [
        ("activity", "run_cadence.created"), ("commit", ""), ("refresh", "RunCadence")]
    (call,) = recorded
    assert call["db"] is session and call["entity_type"] == "run_cadence"
    assert call["entity_id"] == body["id"] and call["entity_label"] == "nightly"
    assert call["context"] == {"summary": body["summary"]}
    assert call["actor"].actor_id == lead.id


async def test_create_refusals_carry_code_and_message(journal, recorded):
    session = _Session(journal, cadences=[_cadence()])
    async with _client(session, _user("ADMIN")) as client:
        duplicate = await client.post(BASE, json=_body(suite_name="Nightly "))
        zone = await client.post(BASE, json=_body(suite_name="other", timezone="Mars/Olympus"))
        extra = await client.post(BASE, json={**_body(suite_name="other"), "cadence_id": str(uuid.uuid4())})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {"code": "duplicate_scope", "message": CADENCE_ERRORS["duplicate_scope"]}
    assert zone.status_code == 422
    assert zone.json()["detail"] == {"code": "unknown_timezone", "message": CADENCE_ERRORS["unknown_timezone"]}
    assert extra.status_code == 422                                     # extra="forbid": no id in a body
    full = _Session(journal, cadences=[_cadence(suite_name=f"s{i}", suite_key=f"s{i}") for i in range(20)])
    async with _client(full, _user("ADMIN")) as client:
        many = await client.post(BASE, json=_body(suite_name="twenty-first"))
    assert many.status_code == 409
    assert many.json()["detail"] == {"code": "too_many", "message": CADENCE_ERRORS["too_many"]}
    assert recorded == [] and ("commit", "") not in journal


async def test_a_suite_name_with_markup_round_trips_as_text(journal, recorded):
    session = _Session(journal)
    async with _client(session, _user("QA_LEAD")) as client:
        created = await client.post(BASE, json=_body(suite_name=XSS))
        listed = await client.get(BASE)
    assert created.status_code == 201, created.text
    assert created.json()["suite_name"] == XSS
    assert listed.json()["items"][0]["suite_name"] == XSS
    assert recorded[0]["entity_label"] == XSS


async def test_another_projects_cadence_is_the_unknown_id_404_everywhere(journal, recorded):
    foreign = _cadence(project_id=uuid.uuid4(), paused_at=datetime.now(timezone.utc),
                       paused_until=datetime.now(timezone.utc) + timedelta(days=1), paused_reason="x")
    session = _Session(journal, cadences=[foreign])
    pause = {"until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "reason": "freeze"}
    async with _client(session, _user("ADMIN")) as client:
        for target in (foreign.id, uuid.uuid4()):
            responses = [
                await client.patch(f"{BASE}/{target}", json={"grace_minutes": 5}),
                await client.delete(f"{BASE}/{target}"),
                await client.post(f"{BASE}/{target}/pause", json=pause),
                await client.post(f"{BASE}/{target}/resume"),
            ]
            for resp in responses:
                assert resp.status_code == 404, resp.text
                assert resp.json()["detail"] == {"code": "cadence_not_found",
                                                 "message": CADENCE_ERRORS["cadence_not_found"]}
    assert recorded == [] and session.deleted == [] and foreign.grace_minutes == 60
    assert all("run_cadences.project_id" in s for s in session.sql if "FROM run_cadences" in s)


async def test_patch_records_only_a_real_change(journal, recorded):
    row = _cadence()
    session = _Session(journal, cadences=[row])
    async with _client(session, _user("QA_LEAD")) as client:
        same = await client.patch(f"{BASE}/{row.id}", json={})
        assert same.status_code == 200 and recorded == [] and journal == []
        changed = await client.patch(f"{BASE}/{row.id}", json={"grace_minutes": 15})
        conflict = await client.patch(f"{BASE}/{row.id}", json={"suite_name": "x", "clear_suite": True})
    assert changed.status_code == 200, changed.text
    assert changed.json()["summary"].endswith("· grace 15 min")
    (call,) = recorded
    assert call["event_type"] == "run_cadence.updated" and call["changed_fields"] == ["grace_minutes"]
    assert (call["before"], call["after"], call["context"]) == (
        {"grace_minutes": 60}, {"grace_minutes": 15}, {"changed": "grace_minutes"})
    assert _handler_steps(journal) == [
        ("activity", "run_cadence.updated"), ("commit", ""), ("refresh", "RunCadence")]
    assert conflict.status_code == 422
    assert conflict.json()["detail"]["code"] == "suite_conflict"


async def test_delete_stages_the_row_then_commits(journal, recorded):
    row = _cadence()
    session = _Session(journal, cadences=[row])
    async with _client(session, _user("QA_LEAD")) as client:
        resp = await client.delete(f"{BASE}/{row.id}")
    assert resp.status_code == 204
    assert session.deleted == [row]
    assert _handler_steps(journal) == [("activity", "run_cadence.deleted"), ("commit", "")]
    (call,) = recorded
    assert (call["entity_id"], call["entity_label"]) == (str(row.id), "nightly")
    assert call["before"]["timezone"] == "Europe/Berlin" and "id" not in call["before"]


async def test_pause_then_resume(journal, recorded):
    row = _cadence()
    session = _Session(journal, cadences=[row])
    until = datetime.now(timezone.utc) + timedelta(days=2)
    async with _client(session, _user("QA_LEAD")) as client:
        past = await client.post(f"{BASE}/{row.id}/pause", json={
            "until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), "reason": "freeze"})
        naive = await client.post(f"{BASE}/{row.id}/pause", json={"until": "2030-01-01T00:00:00", "reason": "x"})
        not_paused = await client.post(f"{BASE}/{row.id}/resume")
        paused = await client.post(f"{BASE}/{row.id}/pause", json={"until": until.isoformat(),
                                                                   "reason": " Release freeze sentinel "})
        resumed = await client.post(f"{BASE}/{row.id}/resume")
    assert past.status_code == 422
    assert past.json()["detail"] == {"code": "pause_until_past", "message": CADENCE_ERRORS["pause_until_past"]}
    assert naive.status_code == 422
    assert not_paused.status_code == 409
    assert not_paused.json()["detail"] == {"code": "not_paused", "message": CADENCE_ERRORS["not_paused"]}
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused"] is True and paused.json()["paused_reason"] == "Release freeze sentinel"
    assert datetime.fromisoformat(paused.json()["paused_until"].replace("Z", "+00:00")) == until
    assert resumed.status_code == 200 and resumed.json()["paused"] is False
    assert [c["event_type"] for c in recorded] == ["run_cadence.paused", "run_cadence.resumed"]
    assert recorded[0]["context"]["reason"] == "Release freeze sentinel"
    assert recorded[0]["context"]["until"].endswith("(Europe/Berlin)")
    assert _handler_steps(journal) == [
        ("activity", "run_cadence.paused"), ("commit", ""), ("refresh", "RunCadence"),
        ("activity", "run_cadence.resumed"), ("commit", ""), ("refresh", "RunCadence")]
