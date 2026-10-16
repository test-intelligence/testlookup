"""In-memory stand-ins for the W06 defect triage / approval / webhook unit tests.

* :class:`MemDB` — an ``AsyncSession`` stand-in that answers ``select(Model)``
  and ``select(Model.col)`` by EVALUATING the statement's WHERE criteria
  (``==``, ``!=``, ``IN`` / ``NOT IN`` incl. a sub-select, ``IS [NOT]``) over ORM
  instances it holds, so the code under test runs its real queries (a filter
  dropped from a query is a test failure, not a silently ignored kwarg). It
  journals ``flush`` / ``commit`` / ``rollback`` / ``close``, and, given a
  :class:`OneConnectionPool`, takes the pool's connection on the first query
  and gives it back on ``commit`` / ``rollback`` / ``close`` -- what an
  ``AsyncSession`` does with its pooled connection.
* :class:`HttpRecorder` — a catch-all for EVERY ``httpx.AsyncClient`` in the
  process (``AsyncClient.send`` patched): routed requests get scripted answers,
  anything else gets a 599 and is recorded, so "zero HTTP calls" is an
  assertion over everything, not over one client.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional

import httpx
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList, Null, True_, False_, UnaryExpression
from sqlalchemy.sql.selectable import ScalarSelect, Select, Subquery


class OneConnectionPool:
    """One pooled connection with a bounded checkout, like QueuePool's."""

    def __init__(self, timeout: float = 1.0) -> None:
        self._slot = asyncio.Semaphore(1)
        self._timeout = timeout
        self.held = 0
        self.max_held = 0
        self.attempts = 0
        # Checkouts that waited out the timeout: a session opened while another
        # held the connection. Code that swallows the error (a webhook emitter
        # never raises) would otherwise hide it.
        self.timeouts = 0

    async def checkout(self) -> None:
        self.attempts += 1
        try:
            await asyncio.wait_for(self._slot.acquire(), timeout=self._timeout)
        except asyncio.TimeoutError:
            self.timeouts += 1
            raise
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    def checkin(self) -> None:
        self.held -= 1
        self._slot.release()


class _Result:
    def __init__(self, rows: list[Any], scalar_rows: bool) -> None:
        self._rows = rows
        self._scalar_rows = scalar_rows

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        first = self._rows[0]
        return first if self._scalar_rows else first[0]

    def scalar_one(self):
        value = self.scalar_one_or_none()
        if value is None:
            raise LookupError("no row")
        return value

    def scalar(self):
        return self.scalar_one_or_none()

    def first(self):
        if not self._rows:
            return None
        first = self._rows[0]
        return (first,) if self._scalar_rows else first

    def all(self):
        return [(r,) if self._scalar_rows else r for r in self._rows]

    def scalars(self):
        values = list(self._rows) if self._scalar_rows else [r[0] for r in self._rows]
        return SimpleNamespace(all=lambda: values, first=lambda: values[0] if values else None)


_MISSING = object()


class MemDB:
    """Evaluates simple ORM selects over in-memory rows. See the module docstring."""

    def __init__(self, rows: Iterable[Any] = (), *, pool: Optional[OneConnectionPool] = None,
                 journal: Optional[list] = None) -> None:
        self.rows: list[Any] = list(rows)
        self.added: list[Any] = []
        self.statements: list[Any] = []
        self.journal: list[str] = journal if journal is not None else []
        self.new: list[Any] = []
        self.dirty: list[Any] = []
        self.deleted: list[Any] = []
        self.pool = pool
        self.connected = False
        self.fail_flush_with: Optional[BaseException] = None
        self.fail_commit_with: Optional[BaseException] = None

    # ── connection handling ───────────────────────────────────────────────
    async def _connect(self) -> None:
        if self.pool is not None and not self.connected:
            await self.pool.checkout()
        self.connected = True

    def _release(self) -> None:
        if self.pool is not None and self.connected:
            self.pool.checkin()
        self.connected = False

    # ── AsyncSession surface ──────────────────────────────────────────────
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def execute(self, stmt, params=None):
        await self._connect()
        self.statements.append(stmt)
        if isinstance(stmt, Update):
            return self._update(stmt)
        if not isinstance(stmt, Select):
            return _Result([], scalar_rows=True)
        return self._run(stmt)

    async def scalar(self, stmt, params=None):
        return (await self.execute(stmt, params)).scalar_one_or_none()

    async def get(self, model, pk):
        await self._connect()
        for row in self.rows + self.added:
            if isinstance(row, model) and getattr(row, "id", None) == pk:
                return row
        return None

    def add(self, obj) -> None:
        self.added.append(obj)
        self.new.append(obj)

    async def flush(self) -> None:
        self.journal.append("flush")
        if self.fail_flush_with is not None:
            raise self.fail_flush_with
        for obj in self.new:  # the primary-key default a real flush applies
            if hasattr(type(obj), "id") and getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        self.new = []

    async def commit(self) -> None:
        self.journal.append("commit")
        if self.fail_commit_with is not None:
            raise self.fail_commit_with
        self.new = []
        self._release()

    async def rollback(self) -> None:
        self.journal.append("rollback")
        self.added = [a for a in self.added if a not in self.new]
        self.new = []
        self._release()

    async def close(self) -> None:
        self.journal.append("close")
        self._release()

    async def refresh(self, obj) -> None:
        self.journal.append("refresh")

    # ── evaluation ────────────────────────────────────────────────────────
    def of(self, model) -> list[Any]:
        if not isinstance(model, type):  # an aliased entity: not evaluated here
            return []
        return [r for r in self.rows + self.added if isinstance(r, model)]

    def _update(self, stmt: Update) -> Any:
        entity = stmt.entity_description["entity"]
        rows = [r for r in self.of(entity) if all(self._holds(c, r) for c in stmt._where_criteria)]
        for row in rows:
            for column, value in stmt._values.items():
                resolved = self._value(value)
                setattr(row, column.key, getattr(resolved, "value", resolved))
        self.journal.append(f"update {entity.__name__} x{len(rows)}")
        return SimpleNamespace(rowcount=len(rows))

    @staticmethod
    def _ordered(stmt: Select, rows: list[Any]) -> list[Any]:
        for clause in reversed(list(getattr(stmt, "_order_by_clauses", ()) or ())):
            descending = isinstance(clause, UnaryExpression) and clause.modifier is operators.desc_op
            column = clause.element if isinstance(clause, UnaryExpression) else clause
            key = getattr(column, "key", None)
            if key is None:
                continue
            rows = sorted(rows, key=lambda r: (getattr(r, key, None) is None, getattr(r, key, None) or 0),
                          reverse=descending)
        offset = getattr(stmt, "_offset", None) or 0
        limit = getattr(stmt, "_limit", None)
        rows = rows[offset:]
        return rows if limit is None else rows[:limit]

    def _run(self, stmt: Select) -> _Result:
        desc = stmt.column_descriptions
        first = desc[0]
        if first.get("entity") is None and getattr(first.get("expr"), "name", None) == "count":
            (sub,) = [f for f in stmt.get_final_froms() if isinstance(f, Subquery)]
            inner = self._run(sub.element)
            return _Result([(len(inner.all()),)], scalar_rows=False)
        entity = first.get("entity")
        if entity is None:
            return _Result([], scalar_rows=True)
        rows = [r for r in self.of(entity) if all(self._holds(c, r) for c in stmt._where_criteria)]
        rows = self._ordered(stmt, rows)
        whole_entity = first.get("expr") is entity
        if whole_entity and len(desc) == 1:
            return _Result(rows, scalar_rows=True)
        values = []
        for r in rows:
            values.append(tuple(getattr(r, d["expr"].key, None) if d.get("entity") is entity else None
                                for d in desc))
        return _Result(values, scalar_rows=False)

    def _value(self, right) -> Any:
        if isinstance(right, BindParameter):
            return right.effective_value
        if isinstance(right, Null):
            return None
        if isinstance(right, True_):
            return True
        if isinstance(right, False_):
            return False
        if isinstance(right, ScalarSelect):
            try:
                return [row[0] for row in self._run(right.element).all()]
            except Exception:  # a correlated sub-select: not this fake's to judge
                return _MISSING
        return _MISSING

    def _holds(self, crit, row) -> bool:
        if isinstance(crit, BooleanClauseList):
            parts = [self._holds(c, row) for c in crit.clauses]
            return all(parts) if crit.operator is operators.and_ else any(parts)
        left = getattr(crit, "left", None)
        key = getattr(left, "key", None)
        if key is None:
            return True
        actual = getattr(row, key, _MISSING)
        if actual is _MISSING:
            return True  # a joined table's column: not this row's to judge
        expected = self._value(crit.right)
        if expected is _MISSING:
            return True
        op = crit.operator
        actual = getattr(actual, "value", actual)
        if op is operators.eq:
            return actual == getattr(expected, "value", expected)
        if op is operators.ne:
            return actual != getattr(expected, "value", expected)
        if op is operators.in_op:
            return actual in [getattr(v, "value", v) for v in expected]
        if op is operators.not_in_op:
            return actual not in [getattr(v, "value", v) for v in expected]
        if op is operators.is_:
            return actual is expected or actual == expected
        if op is operators.is_not:
            return not (actual is expected or actual == expected)
        return True


class HttpRecorder:
    """Every httpx request in the process, recorded; routed ones answered.

    ``route(method, path, answer)``: ``answer`` is an ``httpx.Response``-ish
    tuple ``(status, json_body)``, an exception instance to raise, or a
    callable ``(request) -> (status, body)``. Answers for one route are used
    in order; the last one repeats.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[tuple[str, str], list[Any]] = {}

    def route(self, method: str, path: str, *answers: Any) -> None:
        self._routes[(method.upper(), path)] = list(answers)

    def calls(self, method: Optional[str] = None, path: Optional[str] = None) -> list[httpx.Request]:
        return [
            r for r in self.requests
            if (method is None or r.method == method.upper()) and (path is None or r.url.path == path)
        ]

    def body(self, request: httpx.Request) -> Any:
        return json.loads(request.content or b"null")

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answers = self._routes.get((request.method, request.url.path))
        if not answers:
            return httpx.Response(599, json={"error": "unrouted"}, request=request)
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if callable(answer) and not isinstance(answer, BaseException):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body, request=request)

    def install(self, monkeypatch) -> "HttpRecorder":
        recorder = self
        original = httpx.AsyncClient.send

        async def _send(self_client, request, *args, **kwargs):
            # The test's own client talks to the app in-process: let it through.
            if isinstance(getattr(self_client, "_transport", None), httpx.ASGITransport):
                return await original(self_client, request, *args, **kwargs)
            return await recorder.send(request)

        monkeypatch.setattr(httpx.AsyncClient, "send", _send)
        return self


def user(role, *, active: bool = True, service: bool = False, synthetic: bool = False,
         name: str = "Priya Nair", username: Optional[str] = None):
    from app.models.postgres import User

    uid = uuid.uuid4()
    return User(
        id=uid, username=username or f"u-{uid.hex[:6]}", email=f"{uid.hex[:6]}@example.test",
        full_name=name, role=role, is_active=active, is_service_account=service,
        is_synthetic=synthetic,
    )


def membership(user_obj, project_id, role=None):
    from app.models.postgres import ProjectMember

    return ProjectMember(id=uuid.uuid4(), user_id=user_obj.id, project_id=project_id,
                         role=role or "QA_ENGINEER")


def defect(project_id, **fields):
    from app.models.postgres import Defect

    base = dict(
        id=uuid.uuid4(), project_id=project_id, title="Checkout total off by a cent",
        resolution_status="OPEN", approval_status="approved", severity="HIGH",
        recurrence_count=0, external_status_conflict=False,
    )
    base.update(fields)
    return Defect(**base)


ONLINE_CFG = {
    "enabled": True,
    "domain": "acme.atlassian.net",
    "email": "bot@example.test",
    "api_token": "tok-secret-value",
    "default_project_key": "QA",
}


def patch_jira_config(monkeypatch, cfg: Optional[dict] = None) -> dict:
    """``resolve_jira_config`` answers ``cfg`` without touching the database."""
    from app.services import defect_jira_service as svc

    resolved = dict(cfg or ONLINE_CFG)

    async def _cfg(db):
        return dict(resolved)

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    return resolved


def jira_issue_payload(key: str, *, name: str = "To Do", category: str = "new",
                       summary: str = "Checkout total off by a cent") -> dict:
    return {"id": "10001", "key": key, "fields": {
        "summary": summary, "status": {"name": name, "statusCategory": {"key": category}},
    }}


def make_app(router, db_factory: Callable[[], Any], principal) -> Any:
    """A FastAPI app with ``router``, ``get_db`` yielding ``db_factory()`` and ``principal`` signed in."""
    from fastapi import FastAPI

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    app.include_router(router)

    async def _get_db():
        session = db_factory()
        try:
            yield session
        finally:
            await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: principal
    return app
