"""VIZ-207 -- ``GET /api/v1/analytics/failure-groups``.

A thin handler: the grouping, the statement and the assembly live in
:mod:`app.services.failure_groups_service`. The route adds what every
Wave 3 analytics read has -- the VIZ-209 layer (rate limit, statement timeout,
project-epoch cache key, ETag / 304), the VIZ-210 error contract and the
VIZ-201 scope (every release id authorised, ``project_id`` single-valued,
suites OR within / AND across, the ``is_active`` tenant guard) -- and the C2
envelope with ``meta.definitions``.

Registered by the integrator in ``bootstrap.py`` next to ``analytics.router``.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import failure_groups_service
from app.services.analytics_meta import build_meta, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.chart_data_service import window_start

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

# Project optional: without one the tenant fragment bounds the read to the
# caller's projects (all active projects for an admin).
_FAILURE_GROUPS = ScopePolicy(default_days=30, max_days=365)


@router.get("/failure-groups")
@analytics_error_contract
@analytics_read(namespace="failure_groups")
async def failure_groups(
    include: Optional[list[str]] = Query(
        None,
        description=(
            "Optional parts of the answer (repeatable). One of: "
            + ", ".join(sorted(failure_groups_service.INCLUDE_TOKENS))
            + ". 'edges' links groups that fail in the same tests."
        ),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_FAILURE_GROUPS)),
    db: AsyncSession = Depends(get_db),
):
    """Failing executions grouped by error signature, as contract C3 ``graph``.

    ``nodes`` are the largest groups (size = failures, ``group`` = dominant
    failure category); ``groups`` carries their counts, share, categories,
    trend and top tests in rank order; ``no_message`` and ``singletons`` are
    roll-ups, not nodes. ``edges`` is empty unless ``include=edges``. Labels
    are raw error text: untrusted, render as text.
    """
    include_tokens = failure_groups_service.parse_include(include)
    # ONE clock for the window, the trend axis and the envelope.
    now = failure_groups_service.request_clock()
    payload = await failure_groups_service.build_failure_groups(
        db, scope, include=include_tokens, now=now
    )
    definitions = payload.pop("definitions")
    envelope = {key: payload.pop(key) for key in failure_groups_service.ENVELOPE_KEYS}
    meta = await build_meta(
        db,
        scope,
        window_start=window_start(scope.window_days, now=now),
        truncated=bool(envelope["truncated"]),
        truncated_total=envelope["truncated_total"],
    )
    if envelope["outside_window"]:  # absent, never a zero (C2)
        meta["outside_window"] = envelope["outside_window"]
    meta["definitions"] = definitions
    return with_meta(payload, meta)
