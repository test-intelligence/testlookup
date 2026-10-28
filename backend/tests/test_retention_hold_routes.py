"""W22a ACT-05 — the retention-hold routes, through FastAPI's real guards.

A mini app mounts ``routers/retention.py`` alone: the role and project guards
run as dependencies (only ``get_current_active_user`` and ``get_db`` are
overridden), so the role table, the project-bound key refusal and the JWT-only
check are exercised for real. The session is a statement-routing fake; the
ledger producer is patched with a recorder that journals into the same list as
the session's ``commit`` / ``refresh``, so the order is asserted, not assumed.
"""
from __future__ import annotations

import ast
import inspect
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
from app.models.postgres import RetentionHold, SettingsAuditLog  # noqa: E402
from app.routers import retention as route  # noqa: E402
from app.services.retention_vocab import HOLD_ERROR_SENTENCES  # noqa: E402


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
        return self._scalar if self._scalar is not None else (self._rows[0] if self._rows else None)

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows))


class _Session:
    """Routes each statement by its FROM; journals commit / refresh."""

    def __init__(self, journal, *, project_id, member=True, entity=("2026.10",), hold=None, holds=()):
        self.journal = journal
        self.project_id = project_id
        self.member = member
        self.entity = entity          # the entity row place_hold resolves (None = not in this project)
        self.hold = hold              # the row release_hold loads (None = not in this project)
        self.holds = list(holds)      # the list route's rows
        self.added: list = []
        self.sql: list[str] = []

    async def execute(self, stmt, *_a, **_kw):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.sql.append(sql)
        if "FROM project_members" in sql:
            return _Result(scalar=uuid.uuid4() if self.member else None)
        if "FROM projects" in sql:
            return _Result([(self.project_id,)])
        if "FROM releases" in sql or "FROM test_runs" in sql or "FROM compliance_packs" in sql:
            return _Result([self.entity] if self.entity is not None else [])
        if "FOR UPDATE" in sql and "FROM retention_holds" in sql:
            return _Result(scalar=self.hold)
        if "GROUP BY" in sql:
            return _Result([("live", len(self.holds))])
        if "count(*)" in sql:
            return _Result(scalar=len(self.holds))
        if "FROM retention_holds" in sql:
            return _Result(self.holds)
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.journal.append(("flush", ""))

    async def commit(self):
        self.journal.append(("commit", ""))

    async def refresh(self, obj, *_a, **_kw):
        self.journal.append(("refresh", type(obj).__name__))

    async def rollback(self):
        self.journal.append(("rollback", ""))


def _user(role, *, credential=CREDENTIAL_KIND_JWT, bound_project=None):
    user = SimpleNamespace(
        id=uuid.uuid4(), role=role, full_name="Quinn Lead", username="qlead",
        email="qlead@example.invalid", is_active=True,
    )
    if bound_project is not None:
        _bind_api_key_project(user, bound_project)
    if credential is not None:
        _bind_credential_kind(user, credential)
    return user


@pytest.fixture
def journal():
    return []


@pytest.fixture
def recorded(monkeypatch, journal):
    """The ledger producer, as a recorder sharing the session's journal."""
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


def _hold_row(project_id, **kw):
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid.uuid4(), "project_id": project_id, "entity_type": "release",
        "entity_id": uuid.uuid4(), "entity_label": "release 2026.10", "reason": "Audit Q4 sentinel",
        "expires_at": None, "placed_by_name": "Quinn Lead", "placed_at": now - timedelta(days=1),
        "released_at": None, "released_by_id": None, "released_by_name": None, "release_reason": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _place_body(**kw):
    body = {"entity_type": "release", "entity_id": str(uuid.uuid4()), "reason": "Audit Q4 sentinel"}
    body.update(kw)
    return body


# ── place: QA_LEAD and above, JWT only ───────────────────────────────────────


@pytest.mark.parametrize(
    "role, status",
    [("VIEWER", 403), ("TESTER", 403), ("QA_ENGINEER", 403), ("QA_LEAD", 201), ("ADMIN", 201)],
)
@pytest.mark.asyncio
async def test_place_is_qa_lead_and_above(role, status, journal, recorded):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id)
    async with _client(session, _user(role)) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/retention-holds", json=_place_body())
    assert resp.status_code == status, resp.text
    if status == 403:
        assert resp.json()["detail"] == "Requires at least QA_LEAD role"
        assert session.added == [] and recorded == []
    else:
        body = resp.json()
        assert body["state"] == "live"
        assert body["entity_label"] == "release 2026.10"
        assert body["reason"] == "Audit Q4 sentinel"


@pytest.mark.asyncio
async def test_place_stages_the_ledger_row_before_the_commit_on_the_request_session(journal, recorded):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id)
    entity_id = str(uuid.uuid4())
    async with _client(session, _user("QA_LEAD")) as client:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/retention-holds",
            json=_place_body(entity_id=entity_id, expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat()),
        )
    assert resp.status_code == 201, resp.text
    events = [e for e in journal if e[0] in ("activity", "commit", "refresh")]
    assert events == [("activity", "retention_hold.placed"), ("commit", ""), ("refresh", "RetentionHold")]
    (call,) = recorded
    assert call["db"] is session, "outcome mode: the REQUEST session, beside the hold row"
    hold = next(o for o in session.added if isinstance(o, RetentionHold))
    assert call["entity_id"] == hold.id and call["source_id"] == hold.id
    assert call["source_table"] == "retention_holds"
    assert (call["target_type"], call["target_id"]) == ("release", entity_id)
    assert str(call["release_id"]) == entity_id
    assert call["context"]["reason"] == "Audit Q4 sentinel" and call["context"]["expires_at"]
    (audit,) = [o for o in session.added if isinstance(o, SettingsAuditLog)]
    assert (audit.setting_key, audit.action) == (f"retention_hold:{project_id}", "hold_placed")


@pytest.mark.asyncio
async def test_a_project_hold_records_no_target(journal, recorded):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/retention-holds",
            json={"entity_type": "project", "reason": "Litigation sentinel"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["entity_label"] == "the whole project"
    (call,) = recorded
    assert call["target_id"] is None and call["release_id"] is None


@pytest.mark.parametrize(
    "body, code",
    [
        ({"entity_type": "defect", "entity_id": str(uuid.UUID(int=3)), "reason": "Audit Q4 sentinel"}, "unknown_entity_type"),
        ({"entity_type": "release", "reason": "Audit Q4 sentinel"}, "entity_id_required"),
        ({"entity_type": "project", "entity_id": str(uuid.UUID(int=3)), "reason": "Audit Q4 sentinel"}, "entity_id_forbidden"),
        ({"entity_type": "project", "reason": "no"}, "reason_length"),
        ({"entity_type": "project", "reason": "Audit Q4 sentinel", "expires_at": "2001-01-01T00:00:00Z"}, "expiry_not_future"),
    ],
)
@pytest.mark.asyncio
async def test_place_refuses_with_the_fixed_sentence(body, code, journal, recorded):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/retention-holds", json=body)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {"code": code, "message": HOLD_ERROR_SENTENCES[code]}
    assert ("commit", "") not in journal and recorded == []


@pytest.mark.asyncio
async def test_an_entity_of_another_project_is_the_unknown_entity_404(journal, recorded):
    """The BODY entity_id is resolved WHERE id AND project_id: another
    project's release answers no row — the same 404 as a missing one."""
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id, entity=None)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/retention-holds", json=_place_body())
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "entity_not_found"
    entity_sql = next(s for s in session.sql if "FROM releases" in s)
    assert "releases.project_id = " in entity_sql and "FOR SHARE" in entity_sql
    assert ("commit", "") not in journal and recorded == []


# ── keys can only read ───────────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["place", "release"])
@pytest.mark.asyncio
async def test_a_project_bound_key_can_neither_place_nor_release(action, journal, recorded):
    """A CI credential (an ADMIN's key bound to THIS project) cannot defeat a
    hold: neither route opts a bound key in, so the role guard refuses it."""
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id, hold=_hold_row(project_id))
    key = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY, bound_project=project_id)
    async with _client(session, key) as client:
        if action == "place":
            resp = await client.post(f"/api/v1/projects/{project_id}/retention-holds", json=_place_body())
        else:
            resp = await client.post(
                f"/api/v1/projects/{project_id}/retention-holds/{session.hold.id}/release",
                json={"reason": "Audit closed"},
            )
    assert resp.status_code == 403
    assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL
    assert session.added == [] and recorded == []
    assert session.hold.released_at is None


@pytest.mark.parametrize("credential", [CREDENTIAL_KIND_API_KEY, None])
@pytest.mark.parametrize("action", ["place", "release"])
@pytest.mark.asyncio
async def test_a_user_key_or_an_unknown_credential_needs_an_interactive_login(action, credential, journal, recorded):
    """Unbound keys pass the role guard; the handler's JWT-only check refuses
    them — and an unknown credential falls out of "jwt" too."""
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id, hold=_hold_row(project_id))
    async with _client(session, _user("ADMIN", credential=credential)) as client:
        if action == "place":
            resp = await client.post(f"/api/v1/projects/{project_id}/retention-holds", json=_place_body())
        else:
            resp = await client.post(
                f"/api/v1/projects/{project_id}/retention-holds/{session.hold.id}/release",
                json={"reason": "Audit closed"},
            )
    assert resp.status_code == 403
    assert resp.json()["detail"] == {
        "code": "interactive_login_required",
        "message": HOLD_ERROR_SENTENCES["interactive_login_required"],
    }
    assert session.sql == [], "refused before any read"
    assert recorded == []


# ── release: ADMIN only ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_qa_lead_cannot_release(journal, recorded):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id, hold=_hold_row(project_id))
    async with _client(session, _user("QA_LEAD")) as client:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/retention-holds/{session.hold.id}/release",
            json={"reason": "Audit closed"},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Requires at least ADMIN role"
    assert session.hold.released_at is None and recorded == []


@pytest.mark.asyncio
async def test_an_admin_releases_and_the_ledger_row_precedes_the_commit(journal, recorded):
    project_id = uuid.uuid4()
    hold = _hold_row(project_id)
    session = _Session(journal, project_id=project_id, hold=hold)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/retention-holds/{hold.id}/release",
            json={"reason": "Audit closed"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["state"], body["release_reason"], body["released_by_name"]) == ("released", "Audit closed", "Quinn Lead")
    events = [e for e in journal if e[0] in ("activity", "commit", "refresh")]
    assert events == [("activity", "retention_hold.released"), ("commit", ""), ("refresh", "SimpleNamespace")]
    (call,) = recorded
    assert call["db"] is session and call["entity_id"] == hold.id
    assert call["context"] == {"reason": "Audit closed"}
    (audit,) = [o for o in session.added if isinstance(o, SettingsAuditLog)]
    assert audit.action == "hold_released"
    load = next(s for s in session.sql if "FOR UPDATE" in s)
    assert "retention_holds.id = " in load and "retention_holds.project_id = " in load


@pytest.mark.asyncio
async def test_a_hold_of_another_project_is_not_found(journal, recorded):
    """The load carries project_id: another project's hold answers no row."""
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id, hold=None)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/retention-holds/{uuid.uuid4()}/release",
            json={"reason": "Audit closed"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"code": "hold_not_found", "message": "Hold not found."}
    assert recorded == [] and ("commit", "") not in journal


@pytest.mark.parametrize("state", ["released", "expired"])
@pytest.mark.asyncio
async def test_a_hold_that_is_not_live_is_409(state, journal, recorded):
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    hold = (
        _hold_row(project_id, released_at=now - timedelta(hours=2), release_reason="done before")
        if state == "released"
        else _hold_row(project_id, expires_at=now - timedelta(minutes=5))
    )
    session = _Session(journal, project_id=project_id, hold=hold)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/retention-holds/{hold.id}/release",
            json={"reason": "Audit closed"},
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "hold_not_live"
    assert recorded == []


# ── list: any member, and a key bound to the project; writes nothing ─────────


@pytest.mark.parametrize(
    "user_kwargs",
    [
        {"role": "VIEWER"},
        {"role": "TESTER"},
        {"role": "ADMIN", "credential": CREDENTIAL_KIND_API_KEY, "bound_project": "self"},
    ],
)
@pytest.mark.asyncio
async def test_any_member_and_a_bound_key_can_list(user_kwargs, journal, recorded):
    project_id = uuid.uuid4()
    kwargs = dict(user_kwargs)
    if kwargs.get("bound_project") == "self":
        kwargs["bound_project"] = project_id
    rows = [RetentionHold(
        id=uuid.uuid4(), project_id=project_id, entity_type="project", entity_id=None,
        entity_label="the whole project", reason="Litigation sentinel", expires_at=None,
        placed_by_name="Quinn Lead", placed_at=datetime.now(timezone.utc),
    )]
    session = _Session(journal, project_id=project_id, holds=rows)
    async with _client(session, _user(kwargs.pop("role"), **kwargs)) as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/retention-holds", params={"state": "all", "entity_type": "project"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["counts"] == {"live": 1, "expired": 0, "released": 0}
    assert [i["state"] for i in body["items"]] == ["live"]
    assert ("commit", "") not in journal and ("flush", "") not in journal and recorded == []
    assert session.added == [], "a GET writes nothing"


@pytest.mark.asyncio
async def test_a_non_member_cannot_list(journal):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id, member=False)
    async with _client(session, _user("QA_LEAD")) as client:
        resp = await client.get(f"/api/v1/projects/{project_id}/retention-holds")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You do not have access to this project"


@pytest.mark.parametrize("params, code", [({"state": "pending"}, "unknown_state"), ({"entity_type": "defect"}, "unknown_entity_type")])
@pytest.mark.asyncio
async def test_list_refuses_an_unknown_filter(params, code, journal):
    project_id = uuid.uuid4()
    session = _Session(journal, project_id=project_id)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.get(f"/api/v1/projects/{project_id}/retention-holds", params=params)
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": code, "message": HOLD_ERROR_SENTENCES[code]}


@pytest.mark.asyncio
async def test_a_query_entity_id_is_always_scoped_by_the_path_project(journal):
    project_id, foreign = uuid.uuid4(), uuid.uuid4()
    session = _Session(journal, project_id=project_id)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/retention-holds", params={"entity_id": str(foreign)}
        )
    assert resp.status_code == 200
    hold_sql = [s for s in session.sql if "retention_holds" in s]
    assert hold_sql and all("retention_holds.project_id = " in s for s in hold_sql)


# ── source pins (the activity guards read these literally) ──────────────────


def _calls(fn, name):
    tree = ast.parse(inspect.getsource(fn).lstrip())
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name
    ]


@pytest.mark.parametrize(
    "handler, event",
    [(route.place_retention_hold, "retention_hold.placed"), (route.release_retention_hold, "retention_hold.released")],
)
def test_each_write_handler_records_its_event_literally_with_the_request_session(handler, event):
    (call,) = _calls(handler, "record_activity")
    event_kw = next(kw for kw in call.keywords if kw.arg == "event_type")
    assert isinstance(event_kw.value, ast.Constant) and event_kw.value.value == event
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "db"
    source = inspect.getsource(handler)
    assert source.index("record_activity(") < source.index("await db.commit()") < source.index("await db.refresh(hold)")


def test_no_hold_route_opts_a_project_key_in_or_checks_a_role_by_hand():
    for handler in (route.list_retention_holds, route.place_retention_hold, route.release_retention_hold):
        source = inspect.getsource(handler)
        assert "allow_project_key=True" not in source
        assert "role ==" not in source and ".role" not in source
