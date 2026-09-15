"""Canonical instance-admin API for dead-letter inspection and replay."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import get_current_active_user, require_instance_admin
from app.models.postgres import User
from app.services.ingestion_dlq import (
    DLQEntryNotFound,
    DLQReplayInProgress,
    DLQReplayRefused,
    DLQUnavailable,
    get_dlq_count,
    get_stream_dlq_count,
    list_recent_failures,
    list_recent_stream_failures,
    replay_stream_failure,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin/dlq", tags=["Admin DLQ"])


@router.get("", dependencies=[Depends(require_instance_admin())])
async def list_dead_letters(
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_active_user),
):
    """List newest dead letters from both existing Redis stores."""
    try:
        persist = await list_recent_failures("persist_live_session", limit=limit)
        stream = await list_recent_stream_failures(limit=limit)
    except DLQUnavailable as exc:
        raise HTTPException(status_code=503, detail="Dead-letter storage is unavailable.") from exc
    logger.info("admin_list_dead_letters", actor_user_id=str(current_user.id))
    return {
        "limit": limit,
        "sources": {
            "persist_live_session": {
                "count": await get_dlq_count("persist_live_session"),
                "entries": persist,
            },
            "stream": {"count": await get_stream_dlq_count(), "entries": stream},
        },
    }


# activity: none -- instance-global queue repair has no single project activity
# stream; the structured admin event carries actor, entry, task name, and task id.
@router.post(
    "/{entry_id}/replay",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_instance_admin())],
)
async def replay_dead_letter(
    entry_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Replay one allowlisted Celery entry after the broker accepts it."""
    try:
        result = await replay_stream_failure(entry_id)
    except DLQEntryNotFound as exc:
        raise HTTPException(status_code=404, detail="Dead-letter entry not found.") from exc
    except DLQReplayInProgress as exc:
        raise HTTPException(status_code=409, detail="Dead-letter replay already in progress.") from exc
    except DLQReplayRefused as exc:
        raise HTTPException(status_code=409, detail="This dead-letter entry cannot be replayed.") from exc
    except DLQUnavailable as exc:
        raise HTTPException(status_code=503, detail="Dead-letter replay is unavailable.") from exc
    logger.warning(
        "admin_replayed_dead_letter",
        actor_user_id=str(current_user.id),
        entry_id=entry_id,
        task_name=result["task_name"],
        task_id=result["task_id"],
    )
    return result
