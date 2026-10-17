"""An in-memory session for the W09 digest-scope tests (G-B).

``WhereSession`` answers a SELECT by EVALUATING its bound parameters against
the rows it holds, as an AND of ``column = value`` / ``column IN (values)`` --
the only predicate shapes ``digest_scope_service`` and the digests router emit
for ``releases``, ``saved_views`` and ``digest_subscriptions``. So a test
fails when a predicate (the project id, the owner) is dropped from the SQL,
which a session that answers by position or by table name cannot see.

Anything it cannot answer -- another table, a statement shape it does not
know -- raises ``AssertionError``: an unexpected statement is a finding.
"""
from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Iterable

_BIND_SUFFIX = re.compile(r"_\d+$")


def view(
    *,
    project_id: uuid.UUID | None,
    user_id: uuid.UUID,
    name: str = "Checkout regressions",
    is_shared: bool = False,
    filters: dict | None = None,
    view_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """A ``saved_views`` row; a report view unless ``filters`` says otherwise."""
    return SimpleNamespace(
        id=view_id or uuid.uuid4(),
        project_id=project_id,
        user_id=user_id,
        name=name,
        is_shared=is_shared,
        filters={"kind": "report_view", "page": "trends", "window": 7} if filters is None else filters,
    )


def release(*, project_id: uuid.UUID, name: str, release_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=release_id or uuid.uuid4(), project_id=project_id, name=name)


def subscription(**fields: Any) -> SimpleNamespace:
    """A ``digest_subscriptions`` row with the columns the router reads."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "project_id": None,
        "saved_view_id": None,
        "saved_view_name": None,
        "name": "digest",
        "schedule": "DAILY",
        "channel": "email",
        "scope_type": "project",
        "scope_value": None,
        "trigger_filter": "all",
        "next_delivery_at": None,
    }
    values.update(fields)
    return SimpleNamespace(**values)


class _Result:
    def __init__(self, rows: list, single: bool):
        self._rows = rows
        self._single = single

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        row = self._rows[0]
        return row[0] if isinstance(row, tuple) else row

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(
            all=lambda: [row[0] if isinstance(row, tuple) else row for row in self._rows]
        )


class WhereSession:
    """Rows by table name; every SELECT recorded in ``statements``."""

    def __init__(self, tables: dict[str, Iterable[Any]] | None = None, *, fail: Exception | None = None):
        self.tables = {name: list(rows) for name, rows in (tables or {}).items()}
        self.statements: list[tuple[str, dict]] = []
        self.added: list[Any] = []
        self.commits = 0
        self.savepoints = 0
        self._fail = fail

    async def execute(self, stmt):
        if self._fail is not None:
            raise self._fail
        froms = stmt.get_final_froms()
        if len(froms) != 1 or not hasattr(froms[0], "name"):
            raise AssertionError(f"WhereSession answers single-table SELECTs only: {stmt}")
        table = froms[0].name
        params = dict(stmt.compile().params)
        self.statements.append((table, params))
        if table not in self.tables:
            raise AssertionError(f"unexpected statement on {table}: {stmt}")
        rows = [row for row in self.tables[table] if _matches(row, params)]
        descriptions = stmt.column_descriptions
        if len(descriptions) == 1 and isinstance(descriptions[0]["expr"], type):
            return _Result(rows, single=True)
        names = [description["name"] for description in descriptions]
        return _Result([tuple(getattr(row, name) for name in names) for row in rows], single=len(names) == 1)

    @asynccontextmanager
    async def _savepoint(self):
        self.savepoints += 1
        yield self

    def begin_nested(self):
        return self._savepoint()

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        return None

    async def flush(self):
        return None


def _matches(row: Any, params: dict) -> bool:
    for key, value in params.items():
        column = _BIND_SUFFIX.sub("", key)
        actual = getattr(row, column)
        if isinstance(value, (list, tuple, set, frozenset)):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True
