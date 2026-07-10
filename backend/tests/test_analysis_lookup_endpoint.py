"""US-2.4: ``GET /api/v1/projects/{project_id}/analyses/lookup``.

The Failure Analysis page identifies tests by ``test_fingerprint`` (from the
analytics top-failing / flaky lists) while the feedback-correction endpoints
key on ``analysis_id``. This endpoint bridges the two so the "correct
classification" dialog can find the AI verdict to overwrite.

Pins:

1. **Latest-row semantics** — the service query is project-scoped (fingerprints
   are NOT salted per project; two tenants with a same-named test share one),
   ordered newest-first, and limited to one row.
2. **Null-body contract** — no analysis → 200 with all-null fields, never a
   404 (axios would surface that as an error toast; the UI wants an empty
   state).
3. **Router shape + guard** — the route lives under ``/api/v1/projects`` and
   carries ``require_project_access`` (authorization ratchet), and the router
   is actually registered in ``bootstrap.PROTECTED_ROUTERS``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import FailureCategory  # noqa: E402
from app.routers import feedback as feedback_router  # noqa: E402
from app.services import feedback_service  # noqa: E402


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _CapturingDB:
    """Fake AsyncSession that records the statements it executes."""

    def __init__(self, row=None):
        self.row = row
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _FakeResult(self.row)


# ── Service: latest-row semantics ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_query_is_project_scoped_latest_first():
    db = _CapturingDB(row=None)
    out = await feedback_service.latest_analysis_for_fingerprint(
        db, uuid.uuid4(), "deadbeefdeadbeef",
    )
    assert out is None
    assert len(db.stmts) == 1
    sql = str(db.stmts[0])
    # Project scope: fingerprints are shared across tenants, so the join
    # through test_runs.project_id is load-bearing (IDOR/tenant-bleed class).
    assert "test_runs.project_id" in sql, sql
    assert "test_cases.test_fingerprint" in sql, sql
    # Latest analysis wins…
    assert "ORDER BY ai_analysis.created_at DESC" in sql, sql
    # …and only one row comes back.
    assert "LIMIT" in sql.upper(), sql


@pytest.mark.asyncio
async def test_lookup_returns_latest_row_fields():
    analysis_id = uuid.uuid4()
    analyzed_at = datetime.now(timezone.utc)
    db = _CapturingDB(
        row=SimpleNamespace(
            id=analysis_id,
            failure_category="PRODUCT_BUG",
            created_at=analyzed_at,
        )
    )
    out = await feedback_service.latest_analysis_for_fingerprint(
        db, uuid.uuid4(), "deadbeefdeadbeef",
    )
    assert out == {
        "analysis_id": analysis_id,
        "failure_category": "PRODUCT_BUG",
        "analyzed_at": analyzed_at,
    }


@pytest.mark.asyncio
async def test_lookup_normalises_enum_category_to_wire_value():
    """The column is String(30) but ORM-loaded instances may carry the enum —
    the service must emit the plain wire value either way (``str(enum)``
    would produce ``"FailureCategory.PRODUCT_BUG"``)."""
    db = _CapturingDB(
        row=SimpleNamespace(
            id=uuid.uuid4(),
            failure_category=FailureCategory.INFRASTRUCTURE,
            created_at=datetime.now(timezone.utc),
        )
    )
    out = await feedback_service.latest_analysis_for_fingerprint(
        db, uuid.uuid4(), "fp",
    )
    assert out is not None
    assert out["failure_category"] == "INFRASTRUCTURE"


# ── Endpoint: null-body contract ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_returns_all_null_body_when_no_analysis():
    resp = await feedback_router.lookup_latest_analysis(
        project_id=uuid.uuid4(),
        fingerprint="deadbeefdeadbeef",
        db=_CapturingDB(row=None),
        _=SimpleNamespace(),
    )
    assert resp.analysis_id is None
    assert resp.failure_category is None
    assert resp.analyzed_at is None


@pytest.mark.asyncio
async def test_endpoint_returns_analysis_when_found():
    analysis_id = uuid.uuid4()
    analyzed_at = datetime.now(timezone.utc)
    db = _CapturingDB(
        row=SimpleNamespace(
            id=analysis_id, failure_category="FLAKY", created_at=analyzed_at,
        )
    )
    resp = await feedback_router.lookup_latest_analysis(
        project_id=uuid.uuid4(),
        fingerprint="deadbeefdeadbeef",
        db=db,
        _=SimpleNamespace(),
    )
    assert resp.analysis_id == analysis_id
    assert resp.failure_category == "FLAKY"
    assert resp.analyzed_at == analyzed_at


# ── Router shape + guard + registration ──────────────────────────────────────


def _walk_dep_names(dependant) -> list[str]:
    out: list[str] = []
    stack = [dependant]
    while stack:
        dep = stack.pop()
        call = dep.call
        if call is not None:
            out.append(getattr(call, "__qualname__", None) or type(call).__name__)
        stack.extend(dep.dependencies)
    return out


def test_route_shape_and_project_access_guard():
    from fastapi.routing import APIRoute

    routes = [
        r for r in feedback_router.lookup_router.routes if isinstance(r, APIRoute)
    ]
    matches = [
        r for r in routes if r.path == "/api/v1/projects/{project_id}/analyses/lookup"
    ]
    assert matches, [r.path for r in routes]
    route = matches[0]
    assert route.methods == {"GET"}
    dep_names = _walk_dep_names(route.dependant)
    assert any("require_project_access" in n for n in dep_names), dep_names


def test_lookup_router_registered_as_protected():
    from app import bootstrap

    assert feedback_router.lookup_router in bootstrap.PROTECTED_ROUTERS
