"""W23c AGT-10 G-2 — ``GET / PUT /api/v1/settings/review-gate`` through FastAPI's real guards.

A mini app mounts ``routers/app_settings.py`` alone: ``require_role(ADMIN)``
runs as a dependency (only ``get_current_active_user`` and ``get_db`` are
overridden), so the role table, the project-bound key refusal and the JWT-only
PUT are exercised for real. The session is a statement-routing fake that
records every statement, add, flush and commit: the GET writes nothing.
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

from app.core.config import settings  # noqa: E402
from app.core.deps import (  # noqa: E402
    CREDENTIAL_KIND_API_KEY,
    CREDENTIAL_KIND_JWT,
    PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
    get_db,
)
from app.models.postgres import SettingsAuditLog  # noqa: E402
from app.routers import app_settings as route  # noqa: E402
from app.services.review_gate_vocab import GATED_CHANNELS, REVIEW_GATE_ERRORS  # noqa: E402

_PATH = "/api/v1/settings/review-gate"


class _Result:
    def __init__(self, payload) -> None:
        self._payload = payload

    def first(self):
        return self._payload

    def one(self):
        return self._payload

    def all(self):
        return self._payload


class _GateDB:
    """Routes each statement by its SQL; records what a write would leave behind."""

    def __init__(self, *, value=None, audit_fails: bool = False) -> None:
        self.value = value
        self.updated_at = datetime.now(timezone.utc) - timedelta(days=2) if value else None
        self.audit_fails = audit_fails
        self.statements: list[str] = []
        self.added: list = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, statement, *args, **kwargs):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.statements.append(sql)
        if sql.startswith("INSERT INTO app_settings"):
            self.value = compiled.params["value"]
            self.updated_at = datetime.now(timezone.utc)
            return _Result(None)
        if "FROM app_settings" in sql:
            return _Result((self.value, self.updated_at) if self.value is not None else None)
        if "access_audit_logs" in sql:
            if self.audit_fails:
                raise OSError("audit table unreadable")
            if "GROUP BY" in sql:
                return _Result([("share_link_html", 30), ("report_pdf", 12)])
            return _Result((datetime.now(timezone.utc) - timedelta(days=60), 42, 12))
        raise AssertionError(f"unexpected statement: {sql}")

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:  # pragma: no cover - never reached
        raise AssertionError("rollback")


class _Redis:
    def __init__(self) -> None:
        self.deleted: list = []

    async def delete(self, *keys):
        self.deleted.append(keys)


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    return redis


def _user(role, *, credential=CREDENTIAL_KIND_JWT, bound_project=None):
    user = SimpleNamespace(
        id=uuid.uuid4(), role=role, full_name=None, username="Zq-admin-xyz",
        email="zq-admin@example.invalid", is_active=True,
    )
    if bound_project is not None:
        _bind_api_key_project(user, bound_project)
    if credential is not None:
        _bind_credential_kind(user, credential)
    return user


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


def _iso(instant: datetime) -> str:
    return instant.isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _call(client, method, body=None):
    if method == "PUT":
        return await client.put(_PATH, json=body if body is not None else {"enforced_from": None})
    return await client.get(_PATH)


# ── who may call ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
@pytest.mark.asyncio
async def test_both_routes_are_admin_only(role, method):
    session = _GateDB()
    async with _client(session, _user(role)) as client:
        resp = await _call(client, method)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least ADMIN role"
    assert session.statements == [] and session.added == [] and session.commits == 0


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.asyncio
async def test_a_project_bound_admin_key_is_refused(method):
    session = _GateDB()
    user = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY, bound_project=uuid.uuid4())
    async with _client(session, user) as client:
        resp = await _call(client, method)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL
    assert session.statements == [] and session.commits == 0


@pytest.mark.asyncio
async def test_an_admin_api_key_reads_but_cannot_write(_redis):
    session = _GateDB()
    user = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY)
    future = datetime.now(timezone.utc) + timedelta(days=3)
    async with _client(session, user) as client:
        put = await client.put(_PATH, json={"enforced_from": _iso(future)})
        assert put.status_code == 403, put.text
        assert put.json()["detail"] == {
            "code": "interactive_only", "message": REVIEW_GATE_ERRORS["interactive_only"],
        }
        assert session.statements == [] and session.commits == 0 and _redis.deleted == []
        read = await client.get(_PATH)
    assert read.status_code == 200, read.text
    assert read.json()["source"] == "unset"


# ── the PUT ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_naive_datetime_is_refused_with_its_sentence():
    session = _GateDB()
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.put(_PATH, json={"enforced_from": "2026-11-02T09:00:00"})
        unknown = await client.put(_PATH, json={"enforced_from": None, "enabled": True})
        garbage = await client.put(_PATH, json={"enforced_from": "Zq-next-tuesday"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {
        "code": "naive_datetime", "message": REVIEW_GATE_ERRORS["naive_datetime"],
    }
    assert unknown.status_code == 422 and garbage.status_code == 422  # extra="forbid"; not a datetime
    assert "Zq-next-tuesday" not in repr(session.statements)
    assert session.statements == [] and session.added == [] and session.commits == 0


@pytest.mark.asyncio
async def test_set_a_future_date_then_the_same_again_then_clear(_redis):
    session = _GateDB()
    future = (datetime.now(timezone.utc) + timedelta(days=4)).replace(microsecond=0)
    plus_one = future.astimezone(timezone(timedelta(hours=1)))
    async with _client(session, _user("ADMIN")) as client:
        first = await client.put(_PATH, json={"enforced_from": _iso(plus_one)})
        assert first.status_code == 200, first.text
        body = first.json()
        assert (body["enforced"], body["source"], body["env_override"]) == (False, "scheduled", False)
        assert _parse(body["enforced_from"]) == future
        assert _parse(body["enforced_from"]).utcoffset() == timedelta(0)  # stored as UTC
        assert session.value == {"enforced_from": future.isoformat()}
        assert session.commits == 1 and _redis.deleted == [("config:review_gate:v1",)]
        audits = [row for row in session.added if isinstance(row, SettingsAuditLog)]
        assert [(a.setting_key, a.action, a.changed_fields) for a in audits] == [
            ("review_gate", "updated", ["enforced_from"])
        ]

        again = await client.put(_PATH, json={"enforced_from": _iso(future)})
        assert again.status_code == 200 and again.json()["source"] == "scheduled"
        assert len(session.added) == 1  # the same value: no second audit row
        assert sum(sql.startswith("INSERT INTO app_settings") for sql in session.statements) == 1

        cleared = await client.put(_PATH, json={"enforced_from": None})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["source"], cleared.json()["enforced_from"]) == ("unset", None)
    assert session.value == {"enforced_from": None}
    assert len(session.added) == 2


@pytest.mark.asyncio
async def test_a_past_date_enforces_at_once():
    session = _GateDB()
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.put(_PATH, json={"enforced_from": _iso(past)})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["enforced"], resp.json()["source"]) == (True, "date")


# ── the GET ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_get_writes_nothing_and_lists_every_gated_channel(_redis):
    future = datetime.now(timezone.utc) + timedelta(days=9)
    session = _GateDB(value={"enforced_from": future.isoformat()})
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.get(_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["enforced"], body["source"]) == (False, "scheduled")
    assert _parse(body["updated_at"]) == session.updated_at
    assert body["channels"] == [{"channel": c, "label": label} for c, label in GATED_CHANNELS]
    would = body["would_refuse"]
    assert (would["measured"], would["window_days"], would["decisions"], would["runs"]) == (True, 30, 42, 12)
    assert would["by_channel"] == [
        {"channel": "share_link_html", "decisions": 30},
        {"channel": "report_pdf", "decisions": 12},
    ]
    assert (session.added, session.flushes, session.commits) == ([], 0, 0)
    assert all(sql.startswith("SELECT") for sql in session.statements)
    assert not any("FOR UPDATE" in sql for sql in session.statements)
    assert _redis.deleted == []


@pytest.mark.asyncio
async def test_an_unreadable_audit_log_reads_not_measured_never_zero():
    session = _GateDB(audit_fails=True)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.get(_PATH)
    assert resp.status_code == 200, resp.text
    assert resp.json()["would_refuse"] == {
        "measured": False, "window_days": 30, "since": None,
        "decisions": None, "runs": None, "by_channel": None,
    }


@pytest.mark.asyncio
async def test_the_env_flag_is_the_hard_override(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    future = datetime.now(timezone.utc) + timedelta(days=9)
    session = _GateDB(value={"enforced_from": future.isoformat()})
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.get(_PATH)
    body = resp.json()
    assert (body["enforced"], body["source"], body["env_override"]) == (True, "env", True)
    assert _parse(body["enforced_from"]) == future
