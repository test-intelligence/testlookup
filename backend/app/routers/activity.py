"""Project activity ledger API (epic ACT).

Authorization notes, because this router is the whole tenant boundary for the
feed:

* ``project_id`` is a **PATH** parameter on every scoped route, never a query
  parameter. ``tests/test_architectural_authorization.py`` auto-passes a route
  that has no scoped path param, and nine IDORs once hid in exactly that blind
  spot. A query-param version of this endpoint would look guarded and not be.
* Read access is **project membership**, not ``QA_LEAD``. That is the point of
  the epic: the existing Audit Dashboard is lead-only, so a developer cannot
  see what happened on their own project. Export stays QA_LEAD+ because it
  leaves the building.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_project_access,
    require_role,
)
from app.models.postgres import User, UserRole
from app.services.activity import events as activity_events
from app.services.activity import query as activity_query
from app.services.activity.query import ActivityFilters, InvalidCursor
from app.services.activity.service import ActorRef, record

router = APIRouter(prefix="/api/v1", tags=["Activity"])
logger = structlog.get_logger("routers.activity")


def activity_filters(
    category: Optional[list[str]] = Query(None, description="Repeatable category filter"),
    event_type: Optional[list[str]] = Query(None, description="Repeatable event-type filter"),
    actor_id: Optional[uuid.UUID] = Query(None),
    actor_type: Optional[str] = Query(None, description="user|api_key|service_account|system|agent"),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    release_id: Optional[uuid.UUID] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    q: Optional[str] = Query(None, description="Free text over the summary; ignored under 3 characters"),
    limit: int = Query(activity_query.DEFAULT_LIMIT, ge=1, le=activity_query.MAX_LIMIT),
    cursor: Optional[str] = Query(None, description="Opaque keyset cursor from a previous page"),
) -> ActivityFilters:
    """Collapse the filter set into one object.

    Exists so handlers take ONE parameter instead of twelve ``Query(...)``
    defaults. A test that calls a handler directly gets a real
    ``ActivityFilters``; with bare ``Query(...)`` defaults it would receive
    twelve ``Query`` objects and fail in ways that look like business logic.
    """
    if since and until and since > until:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'since' must be earlier than 'until'.",
        )

    unknown = [
        name for name in (event_type or []) if name not in activity_events.ACTIVITY_EVENTS
    ]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown event_type: {', '.join(sorted(unknown))}. "
                f"See GET /api/v1/activity/event-types for the full list."
            ),
        )

    bad_categories = [
        name for name in (category or []) if name not in activity_events.ACTIVITY_CATEGORIES
    ]
    if bad_categories:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown category: {', '.join(sorted(bad_categories))}. "
                f"Valid: {', '.join(activity_events.ACTIVITY_CATEGORIES)}."
            ),
        )

    if actor_type and actor_type not in activity_events.ACTOR_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown actor_type. Valid: {', '.join(activity_events.ACTOR_TYPES)}.",
        )

    return ActivityFilters(
        categories=tuple(category or ()),
        event_types=tuple(event_type or ()),
        actor_id=actor_id,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=entity_id,
        release_id=release_id,
        since=since,
        until=until,
        q=q,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/activity/event-types",
    summary="The activity event registry, grouped by category",
)
async def list_event_types(
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Feeds the filter UI. Not project-scoped: the vocabulary is global."""
    return {
        "categories": list(activity_events.ACTIVITY_CATEGORIES),
        "actor_types": list(activity_events.ACTOR_TYPES),
        "entity_types": list(activity_events.ENTITY_TYPES),
        "events": activity_events.events_by_category(),
    }


@router.get(
    "/projects/{project_id}/activity",
    summary="Project activity feed (keyset paginated)",
)
async def list_activity(
    project_id: uuid.UUID,
    filters: ActivityFilters = Depends(activity_filters),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
) -> dict:
    """One page of the project's activity, newest first.

    Any member of the project can read this — see the module docstring.
    """
    try:
        return await activity_query.list_events(db, project_id, filters)
    except InvalidCursor as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get(
    "/projects/{project_id}/activity/entity/{entity_type}/{entity_id}",
    summary="Everything that happened to one entity",
)
async def list_entity_timeline(
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    limit: int = Query(50, ge=1, le=activity_query.MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
) -> dict:
    """Embedded by run and release detail pages."""
    if entity_type not in activity_events.ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown entity_type. Valid: {', '.join(activity_events.ENTITY_TYPES)}.",
        )
    items = await activity_query.entity_timeline(
        db, project_id, entity_type, entity_id, limit
    )
    return {"items": items}


@router.get(
    "/projects/{project_id}/activity/export",
    summary="Export the filtered activity history (QA_LEAD+)",
)
async def export_activity(
    project_id: uuid.UUID,
    request: Request,
    format: str = Query("csv", pattern="^(csv|ndjson)$"),
    filters: ActivityFilters = Depends(activity_filters),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    _: User = Depends(require_project_access()),
) -> Response:
    """Stream the history out as CSV or NDJSON.

    Capped at ``EXPORT_MAX_ROWS``; a truncated export says so in a header
    instead of silently returning a prefix that reads as the whole story.
    """
    rows, truncated = await activity_query.iter_export_rows(db, project_id, filters)

    if format == "ndjson":
        body = activity_query.rows_to_ndjson(rows)
        media_type = "application/x-ndjson"
        extension = "ndjson"
    else:
        body = activity_query.rows_to_csv(rows)
        media_type = "text/csv"
        extension = "csv"

    # The export itself is an activity. Recorded as an attempt so it survives
    # even if the response is abandoned mid-download.
    await record(
        None,
        project_id=project_id,
        event_type="activity.exported",
        actor=ActorRef.from_user(current_user),
        entity_id=str(project_id),
        entity_label="Activity history",
        context={"row_count": len(rows), "format": extension, "truncated": truncated},
        request_id=request.headers.get("x-request-id"),
    )

    filename = f"activity-{project_id}.{extension}"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Truncated": "true" if truncated else "false",
        "X-Row-Count": str(len(rows)),
    }
    return Response(content=body, media_type=media_type, headers=headers)


@router.get(
    "/projects/{project_id}/activity/{event_id}",
    summary="One activity event, with its before/after diff",
)
async def get_activity_event(
    project_id: uuid.UUID,
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
) -> dict:
    """The drawer's payload. Registered LAST so the literal-segment routes
    above (``export``, ``entity``) are matched before this UUID pattern."""
    event = await activity_query.get_event(db, project_id, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Activity event not found"
        )
    return event
