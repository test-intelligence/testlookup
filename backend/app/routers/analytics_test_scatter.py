"""VIZ-506-BE -- ``GET /api/v1/analytics/test-scatter``: one point per test.

A thin handler over ``test_scatter_service``, decorated like ``/chart-data``
(VIZ-209 layer, VIZ-210 errors, VIZ-201 scope). The body is contract C3 kind
``points`` plus the C2 ``meta`` envelope.

No ``from __future__ import annotations``: the VIZ-209 layer reads the real
annotations to tell repeatable parameters from single-valued ones.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import chart_data_service
from app.services import test_scatter_service as scatter_service
from app.services.analytics_meta import build_meta, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

#: A fingerprint is not unique across projects (the systemic-cluster member
#: comment, ``models/postgres.py``), so a point is only a test inside ONE
#: project: All Projects is refused rather than merging two projects' tests.
_SCATTER = ScopePolicy(
    default_days=30, max_days=scatter_service.MAX_WINDOW_DAYS, project_required=True,
)


def _identity(resolved: dict) -> tuple:
    spec = scatter_service.parse_scatter_spec(
        resolved["min_executions"], resolved["limit"], resolved["order"],
    )
    return scatter_service.cache_identity_parts(resolved["scope"], spec)


@router.get("/test-scatter")
@analytics_error_contract
@analytics_read(namespace="test_scatter", identity=_identity)
async def get_test_scatter(  # not ``test_*``: pytest would collect an import of it
    min_executions: int = Query(
        scatter_service.DEFAULT_MIN_EXECUTIONS,
        description=(
            f"Tests with fewer executions in scope are left out and counted "
            f"(1-{scatter_service.MAX_MIN_EXECUTIONS})."
        ),
    ),
    limit: int = Query(
        scatter_service.DEFAULT_LIMIT,
        description=f"At most this many points (1-{scatter_service.MAX_LIMIT}).",
    ),
    order: str = Query(
        scatter_service.DEFAULT_ORDER,
        description="Which tests are kept when more qualify: " + ", ".join(scatter_service.ORDERS),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_SCATTER)),
    db: AsyncSession = Depends(get_db),
):
    """p95 duration (ms, log x) x failure rate (%, y) x executions (size), one
    point per test; tests that cannot be placed are counted in ``excluded``."""
    spec = scatter_service.parse_scatter_spec(min_executions, limit, order)
    now = chart_data_service.request_clock()
    payload = await scatter_service.build_test_scatter(db, scope, spec, now=now)
    definitions = payload.pop("definitions")
    envelope = {key: payload.pop(key) for key in scatter_service.ENVELOPE_KEYS}
    meta = await build_meta(
        db,
        scope,
        pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        window_start=chart_data_service.window_start(scope.window_days, now=now),
        truncated=bool(envelope["truncated"]),
        truncated_total=envelope["truncated_total"],
    )
    meta["definitions"] = definitions
    return with_meta(payload, meta)
