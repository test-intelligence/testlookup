"""
Shared fixtures for API integration tests.

These tests differ from the unit tests under ``tests/`` in two ways:

1. They run real HTTP requests through the FastAPI app (router + middleware
   + dependency injection + Pydantic validation) via httpx + ASGITransport,
   rather than calling service functions directly.

2. The DB layer is NOT mocked at the call site — instead, FastAPI's
   ``dependency_overrides`` swap ``get_db`` (and the auth deps) for fakes
   that the test controls. This exercises the dispatch path while keeping
   the test hermetic (no real Postgres, no real Redis).

Why this style instead of a real DB:

- The repo's deployment uses Postgres-specific types (UUID, JSONB, INTERVAL).
  Standing up a sqlite-with-shims session for every test is brittle, and
  testcontainers-postgres is too heavy for fast feedback.
- The unit tests under ``tests/`` already exercise SQL correctness deeply
  (analyzer, services, query helpers). Integration tests focus on the layer
  the unit tests skip: HTTP semantics, auth wiring, validation, and routing.

Skip strategy:

- The whole package is skipped if ``jose``/``asyncpg`` are missing locally
  (same pattern as the existing tests in this repo). All integration tests
  therefore run in Docker CI and skip on bare local checkouts.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

# Tolerate missing heavy deps at import time — individual test files use
# pytest.importorskip("...") at module top so they skip cleanly. The
# conftest itself MUST NOT raise Skipped during collection or pytest will
# error out instead of skipping. We import lazily inside fixtures.
try:
    from httpx import ASGITransport, AsyncClient
    from app.core.deps import (
        get_accessible_project_ids,
        get_api_key_context,
        get_current_active_user,
        get_current_user,
        get_db,
    )
    from app.main import app
    from app.models.postgres import UserRole
    HAVE_INTEGRATION_DEPS = True
except Exception:  # noqa: BLE001 — any import failure should disable, not crash
    HAVE_INTEGRATION_DEPS = False
    UserRole = None  # type: ignore[assignment]


# ── Test user factory ───────────────────────────────────────────────────────


def make_user(
    *,
    role=None,
    is_active: bool = True,
    must_change_password: bool = False,
    user_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """Build a stand-in for app.models.postgres.User with the fields routers read."""
    if role is None and HAVE_INTEGRATION_DEPS:
        role = UserRole.QA_ENGINEER
    role_value = role.value if hasattr(role, "value") else str(role) if role else "QA_ENGINEER"
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.local",
        username=f"user_{uuid.uuid4().hex[:6]}",
        full_name="Test User",
        hashed_password="$2b$fake",
        role=role_value,
        is_active=is_active,
        must_change_password=must_change_password,
        avatar_color=None,
    )


# ── Fake DB session ─────────────────────────────────────────────────────────


class FakeAsyncSession:
    """
    Minimal AsyncSession stand-in for FastAPI dependency overrides.

    Each test installs its own scripted return values via
    ``set_execute_results([...])``. The session records mutations
    (add/delete/commit/rollback/flush) so tests can assert on them.
    """

    def __init__(self):
        self._results: list = []
        self._result_idx = 0
        self.added: list = []
        self.deleted: list = []
        self.committed = 0
        self.rolled_back = 0
        self.flushed = 0

    def set_execute_results(self, results: list) -> None:
        """Each entry is what ``await db.execute(...)`` should return next."""
        self._results = list(results)
        self._result_idx = 0

    async def execute(self, _stmt, _params=None):
        if self._result_idx >= len(self._results):
            # Default: empty result with the most-common method shape.
            r = MagicMock()
            r.scalar_one_or_none = MagicMock(return_value=None)
            r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            r.scalar = MagicMock(return_value=None)
            r.all = MagicMock(return_value=[])
            r.first = MagicMock(return_value=None)
            return r
        result = self._results[self._result_idx]
        self._result_idx += 1
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def flush(self):
        self.flushed += 1

    async def refresh(self, _obj):
        pass

    def begin_nested(self):
        sp = AsyncMock()
        sp.__aenter__ = AsyncMock(return_value=sp)
        sp.__aexit__ = AsyncMock(return_value=False)
        return sp


def fake_execute_result(
    *,
    scalar=None,
    scalars_all=None,
    all_=None,
    first=None,
    rowcount=0,
):
    """Convenience builder for one ``await db.execute(...)`` call."""
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=scalar)
    r.scalar = MagicMock(return_value=scalar)
    r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all or [])))
    r.all = MagicMock(return_value=all_ or [])
    r.first = MagicMock(return_value=first)
    r.rowcount = rowcount
    return r


# ── HTTP client + dependency override fixtures ──────────────────────────────


@pytest.fixture
def fake_db() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def override_db(fake_db: FakeAsyncSession) -> Iterator[FakeAsyncSession]:
    """Override ``get_db`` to yield the fake session for the test's lifetime."""
    if not HAVE_INTEGRATION_DEPS:
        pytest.skip("integration deps not installed")

    async def _get_db():
        yield fake_db
    app.dependency_overrides[get_db] = _get_db
    try:
        yield fake_db
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_as():
    """
    Factory that installs auth dependency overrides for a given user.

    Usage::

        async def test_x(client, auth_as):
            user = auth_as(role=UserRole.ADMIN)
            resp = await client.get("/api/v1/auth/me")
    """
    if not HAVE_INTEGRATION_DEPS:
        pytest.skip("integration deps not installed")

    installed: list = []

    def _install(
        *,
        role=None,
        is_active: bool = True,
        accessible_projects: set[uuid.UUID] | None = None,
        bound_project_id: uuid.UUID | None = None,
    ):
        if role is None:
            role = UserRole.QA_ENGINEER
        user = make_user(role=role, is_active=is_active)

        async def _current():
            return user

        async def _accessible(_db, _user):
            # ADMIN sees everything (None); others see the supplied set.
            if role == UserRole.ADMIN:
                return None
            return accessible_projects or set()

        async def _api_key_ctx():
            return user, bound_project_id

        app.dependency_overrides[get_current_user] = _current
        app.dependency_overrides[get_current_active_user] = _current
        app.dependency_overrides[get_accessible_project_ids] = _accessible
        app.dependency_overrides[get_api_key_context] = _api_key_ctx
        installed.append(user)
        return user

    yield _install

    for dep in (
        get_current_user,
        get_current_active_user,
        get_accessible_project_ids,
        get_api_key_context,
    ):
        app.dependency_overrides.pop(dep, None)


@pytest.fixture
async def client(override_db):
    """
    HTTPX AsyncClient bound to the FastAPI app via ASGITransport.

    No real network — requests go through the in-process middleware stack.
    base_url is required by httpx but the host portion is meaningless here.
    """
    if not HAVE_INTEGRATION_DEPS:
        pytest.skip("integration deps not installed")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
