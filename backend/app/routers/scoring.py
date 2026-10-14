"""
Scoring Model router.

GET /api/v1/scoring-model
  Returns the 7-dimension risk scoring model: version, thresholds,
  dimension weights, and human-readable descriptions.

Consumed by the frontend CriticalityMatrix component to render
"Why this score?" expandable text without hardcoding descriptions.

GATE-05: with ``?project_id=`` (or ``?run_id=``, resolved to its project) the
model is the one that project's verdict applies -- the active release-gate
policy's thresholds and dimension weights, read through the evaluator's own
extractors, plus which policy it is. Without either, the deployment default is
returned exactly as before.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, resolve_project_scope
from app.db.postgres import get_db
from app.models.postgres import TestRun, User
from app.services.criticality_service import get_scoring_model_info
from app.services.policy_evaluator_service import resolve_effective_policy

router = APIRouter(prefix="/api/v1", tags=["Scoring Model"])

_RUN_NOT_FOUND = "Test run not found"


async def _run_project(db: AsyncSession, user: User, run_id: str) -> uuid.UUID:
    """The run's project, after the caller's access to it is verified.

    A run the caller cannot read is a 404, as ``require_run_access`` answers,
    so the endpoint does not confirm that another tenant's run exists.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid run ID") from None
    project_id = (
        await db.execute(select(TestRun.project_id).where(TestRun.id == run_uuid))
    ).scalar_one_or_none()
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_RUN_NOT_FOUND)
    try:
        await resolve_project_scope(db, user, str(project_id))
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_RUN_NOT_FOUND) from None
        raise
    return project_id


@router.get("/scoring-model")
async def get_scoring_model(
    project_id: Optional[str] = Query(
        None,
        description="Return the model this project's verdict applies (its active "
        "release-gate policy's thresholds and weights). 403 without access.",
    ),
    run_id: Optional[str] = Query(
        None,
        description="As project_id, for the run's project. 404 for a run you cannot read.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return the risk scoring model.

    Response shape:
    {
      "version": 1,
      "go_threshold": 20,
      "no_go_threshold": 55,
      "dimensions": [
        { "name": "user_impact", "weight": 0.25, "description": "..." },
        ...
      ]
    }

    Scoped (``project_id`` or ``run_id``), it adds ``pass_rate_minimum``,
    ``pass_rate_hard_floor_factor`` and ``policy`` =
    ``{policy_id, name, version, level}`` where ``level`` is ``project``,
    ``system`` or ``hardcoded`` (no active policy: config defaults).
    """
    if not project_id and not run_id:
        return get_scoring_model_info()

    scoped: Optional[uuid.UUID]
    if run_id:
        scoped = await _run_project(db, current_user, run_id)
        if project_id and str(scoped) != project_id.strip().lower():
            # Both named and they disagree: answering for either would be a guess.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="run_id does not belong to project_id",
            )
    else:
        scoped, _ = await resolve_project_scope(db, current_user, project_id)

    policy, level = await resolve_effective_policy(scoped, db)
    return get_scoring_model_info(policy, level=level)
