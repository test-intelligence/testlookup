"""Fakes shared by the W04 AI-config tests (AIC-04 / AIC-01).

* :class:`StoredConfigSession` answers the reads the effective-config resolver
  and the deep-investigation routes make: ``select(AppSetting)`` -> the stored
  ``ai_config`` row, ``select(SecretRef)`` -> no secret, ``select(TestRun)`` ->
  a run. It records every statement, so a test can prove which session was
  used (and that none was used at all on a cache hit).
* :class:`RecordingRedis` is an in-memory Redis that records ``set``/``setex``/
  ``delete`` calls with their TTL arguments, so "seeded WITHOUT ``ex``" is an
  assertion rather than an assumption.
* :func:`install` points ``get_redis`` and the resolver's fallback
  ``AsyncSessionLocal`` at them. The fallback session raises by default: a code
  path that should have used the request session and opened its own fails
  loudly instead of quietly reading something.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


class _Result:
    def __init__(self, scalar: Any) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        return self._scalar

    def scalars(self) -> Any:
        return SimpleNamespace(all=lambda: [], first=lambda: None)

    def all(self) -> list:
        return []


def _entity(stmt: Any) -> str:
    try:
        return str(stmt.column_descriptions[0]["name"])
    except Exception:
        return ""


class StoredConfigSession:
    """An AsyncSession stand-in holding one ``app_settings["ai_config"]`` row."""

    def __init__(self, stored: Optional[dict] = None, *, label: str = "request") -> None:
        # One persistent row object: a handler that assigns ``row.value`` (the
        # PUT) is visible to the next read, as with a real session.
        self.row = (
            SimpleNamespace(key="ai_config", value=dict(stored)) if stored is not None else None
        )
        self.label = label
        self.statements: list[Any] = []
        self.committed = False
        self.added: list[Any] = []

    @property
    def stored(self) -> Optional[dict]:
        return None if self.row is None else self.row.value

    def entities(self) -> list[str]:
        return [_entity(s) for s in self.statements]

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(stmt)
        entity = _entity(stmt)
        if entity == "AppSetting":
            return _Result(self.row)
        if entity == "SecretRef":
            return _Result(None)
        if entity == "TestRun":
            return _Result(SimpleNamespace(id=RUN_ID, project_id=PROJECT_ID, build_number="b-1"))
        if entity == "project_id":  # select(TestRun.project_id): the run guard
            return _Result(PROJECT_ID)
        return _Result(None)

    async def get(self, _model: Any, _pk: Any) -> Any:
        return SimpleNamespace(id=RUN_ID, project_id=PROJECT_ID, build_number="b-1")

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if getattr(obj, "key", None) == "ai_config" and self.row is None:
            self.row = SimpleNamespace(key="ai_config", value=dict(obj.value or {}))

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> "StoredConfigSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class RecordingRedis:
    """In-memory async Redis that records writes and their TTL arguments."""

    def __init__(self, data: Optional[dict] = None) -> None:
        self.data: dict[str, Any] = dict(data or {})
        self.calls: list[tuple] = []

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.calls.append(("set", key, value, args, kwargs))
        self.data[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: Any) -> bool:
        self.calls.append(("setex", key, value, (ttl,), {}))
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        self.calls.append(("delete", keys))
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    def sets_of(self, key: str) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("set", "setex") and c[1] == key]

    def flushdb(self) -> None:
        self.data.clear()


def install(monkeypatch: Any, *, redis: Any = None, fallback_session: Any = None) -> list[str]:
    """Point ``get_redis`` at ``redis`` (a raising stub when None) and the
    resolver's own ``AsyncSessionLocal`` at ``fallback_session``.

    Returns a list that records every ``AsyncSessionLocal()`` call. With no
    ``fallback_session`` the call also raises (the resolver swallows DB errors
    into env defaults, so the list is what makes a stray second session an
    assertion failure rather than a silent env read)."""
    if redis is None:
        def _no_redis() -> Any:
            raise RuntimeError("no redis in this test")

        monkeypatch.setattr("app.db.redis_client.get_redis", _no_redis)
    else:
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    opened: list[str] = []

    def _session_factory(*_a: Any, **_kw: Any) -> Any:
        opened.append("AsyncSessionLocal()")
        if fallback_session is None:
            raise AssertionError("a second database session was opened")
        return fallback_session

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory)
    return opened


def reset_analysis_router_state() -> None:
    """The router keeps process-wide caches; every test starts clean."""
    from app.services import analysis_router

    analysis_router._cached_mode = None
    analysis_router._cached_mode_ts = 0
    analysis_router._effective_provider = None
    analysis_router._effective_model = None
    analysis_router._effective_base_url = None
    analysis_router._ollama_model_available = None
    analysis_router._ollama_probe_ts = 0
    analysis_router._ollama_probe_target = None
