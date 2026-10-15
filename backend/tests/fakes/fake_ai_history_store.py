"""A tiny in-memory stand-in for the Motor database the AI-history reads use (W03).

Only what ``ai_history_read_service`` touches: ``db[name]`` -> a collection with
``find(query, projection).sort(...).limit(n).to_list(length)``, ``find_one(query,
projection, sort=...)`` and ``count_documents(query)``. Queries are top-level
equality matches. Every WRITE method exists only to be counted: the GET routes
must never call one (``writes`` stays empty). ``fail`` makes every call raise,
the way a Mongo outage does.
"""
from __future__ import annotations

import copy
from typing import Any


class MongoDown(Exception):
    """Stands in for ``pymongo.errors.ServerSelectionTimeoutError``."""


def _matches(doc: dict, query: dict) -> bool:
    return all(doc.get(key) == value for key, value in (query or {}).items())


def _project(doc: dict, projection: dict | None) -> dict:
    out = copy.deepcopy(doc)
    if not projection:
        return out
    included = {k for k, v in projection.items() if v and k != "_id"}
    excluded = {k for k, v in projection.items() if not v}
    if included:
        out = {k: v for k, v in out.items() if k in included}
    for key in excluded:
        out.pop(key, None)
    return out


class _Cursor:
    def __init__(self, docs: list[dict], projection: dict | None, store: "FakeMongo"):
        self._docs = docs
        self._projection = projection
        self._limit: int | None = None
        self._store = store

    def sort(self, key: Any, direction: int | None = None) -> "_Cursor":
        keys = key if isinstance(key, list) else [(key, direction or 1)]
        for field, order in reversed(keys):
            self._docs.sort(key=lambda d: (d.get(field) is None, d.get(field)), reverse=order < 0)
        return self

    def limit(self, n: int) -> "_Cursor":
        self._limit = n
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        self._store._check()
        docs = self._docs[: self._limit] if self._limit else self._docs
        return [_project(d, self._projection) for d in docs]


class FakeCollection:
    def __init__(self, store: "FakeMongo", name: str):
        self._store = store
        self.name = name
        self.docs: list[dict] = []

    def find(self, query: dict | None = None, projection: dict | None = None) -> _Cursor:
        self._store.reads.append((self.name, "find", dict(query or {})))
        return _Cursor([d for d in self.docs if _matches(d, query or {})], projection, self._store)

    async def find_one(self, query: dict | None = None, projection: dict | None = None, sort: Any = None) -> dict | None:
        self._store._check()
        self._store.reads.append((self.name, "find_one", dict(query or {})))
        cursor = _Cursor([d for d in self.docs if _matches(d, query or {})], projection, self._store)
        if sort:
            cursor.sort(sort)
        rows = await cursor.limit(1).to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query: dict | None = None) -> int:
        self._store._check()
        return sum(1 for d in self.docs if _matches(d, query or {}))

    # ── writes: recorded, never expected from a read ──
    async def insert_one(self, doc: dict) -> Any:
        self._store.writes.append((self.name, "insert_one"))
        self.docs.append(dict(doc))

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        self._store.writes.append((self.name, "update_one"))

    async def delete_one(self, *args: Any, **kwargs: Any) -> Any:
        self._store.writes.append((self.name, "delete_one"))

    async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
        self._store.writes.append((self.name, "delete_many"))


class FakeMongo:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}
        self.writes: list[tuple[str, str]] = []
        self.reads: list[tuple[str, str, dict]] = []
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise MongoDown("server selection timed out")

    def __getitem__(self, name: str) -> FakeCollection:
        if self.fail:
            raise MongoDown("server selection timed out")
        if name not in self._collections:
            self._collections[name] = FakeCollection(self, name)
        return self._collections[name]
