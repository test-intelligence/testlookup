"""VIZ-205 -- ``GET /api/v1/analytics/heatmap``, the matrix read.

A thin handler: the request is validated by ``heatmap_service.parse_heatmap_spec``
(422 bodies of VIZ-210, nothing echoed), the scope by ``analytics_scope``
(every release id authorised before any data query), and the read is wrapped in
the VIZ-209 layer exactly as ``/chart-data`` is: rate limit
(``RATE_LIMITED_ROUTES`` declares 60 a minute for this path), the 5 s
``SET LOCAL statement_timeout`` with the planner settings, the project-epoch
cache key with ETag and 304.

Its own module rather than another handler in ``routers/analytics.py``, so the
Wave 3 builders do not serialise on one file; it shares that router's prefix
and is registered next to it in ``bootstrap.py``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import heatmap_service
from app.services.analytics_meta import build_meta, with_meta
from app.services.analytics_scope import AnalyticsScope, analytics_scope
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


def _heatmap_identity(resolved: dict) -> tuple:
    """This route's cache identity, from the RESOLVED request (the scope object
    and the validated spec), so an absent ``rows`` and ``rows=40`` share one
    entry and one ETag. Subscripted, not ``.get``: each is a declared
    parameter, and a KeyError here means the signature and this hook drifted."""
    scope: AnalyticsScope = resolved["scope"]
    spec = heatmap_service.parse_heatmap_spec(
        resolved["kind"], resolved["rows"], resolved["runs"], scope=scope,
    )
    return heatmap_service.cache_identity_parts(scope, spec)


@router.get("/heatmap")
@analytics_error_contract
@analytics_read(namespace="heatmap", identity=_heatmap_identity)
async def heatmap(
    kind: Optional[str] = Query(
        None,
        description=(
            "Which matrix. One of: " + ", ".join(heatmap_service.KINDS) + ". "
            + " and ".join(f"kind={kind}" for kind in sorted(heatmap_service.PROJECT_KINDS))
            + " answer for one project: without project_id they are refused with 422 "
            "'project_required'. The other kinds take All Projects."
        ),
    ),
    rows: Optional[int] = Query(
        None,
        description=(
            f"Rows to keep, worst first (default {heatmap_service.DEFAULT_ROWS}, "
            f"1-{heatmap_service.MAX_ROWS})."
        ),
    ),
    runs: Optional[int] = Query(
        None,
        description=(
            f"kind=test_run only: the last N runs as columns (default "
            f"{heatmap_service.DEFAULT_RUNS}, 1-{heatmap_service.MAX_RUNS})."
        ),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(heatmap_service.HEATMAP_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """A two-dimensional read as contract C3 ``matrix``.

    Rate kinds carry the pass rate in percentage points (``unit: "percent"``),
    with the five status counts behind each cell; a cell with nothing
    evaluated is ``null``, never 0. ``test_run`` carries each execution's
    status. Rows and columns beyond the caps are dropped and counted in
    ``meta.truncated_axes``.
    """
    spec = heatmap_service.parse_heatmap_spec(kind, rows, runs, scope=scope)
    # ONE clock for the window bound, the day axis, the partial day and the
    # envelope's window, so they cannot straddle UTC midnight and disagree.
    now = heatmap_service.request_clock()
    payload = await heatmap_service.build_heatmap(db, scope, spec, now=now)
    definitions = payload.pop("definitions")
    envelope = {key: payload.pop(key) for key in heatmap_service.ENVELOPE_KEYS}
    meta = await build_meta(
        db,
        scope,
        # A status matrix has no rate, so it claims no basis for one.
        pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS if spec.is_rate else None,
        window_start=heatmap_service.window_start(scope.window_days, now=now),
        truncated=bool(envelope["truncated"]),
        truncated_total=envelope["truncated_total"],
    )
    meta["definitions"] = definitions
    if envelope["truncated_axes"]:
        meta["truncated_axes"] = envelope["truncated_axes"]
    if envelope["outside_window"]:
        meta["outside_window"] = envelope["outside_window"]
    return with_meta(payload, meta)
