"""W39 STRAT-07 (first slice): ``GET /api/v1/runs/{run_id}/observability-timeline``.

One run's Splunk / OpenShift / Prometheus evidence the AI analysis ALREADY fetched, read from what is stored
(``services/run_observability_service.py``; contract D-1). A GET that fetches NOTHING -- no fetcher, tool, LLM or
``GET /runs/{id}/intelligence`` (which writes a snapshot) -- and writes nothing: no commit, no background task.

Access is the run timing route's rule (``routers/execution_metrics.py`` ``GET /runs/{run_id}/timing``):
``require_run_access()`` on the path id, so any member of the run's project reads it (VIEWER included; it is
read-only run data like the run's other tabs), an API key reads only runs of the project it is bound to, and an
unknown or unreadable run is the guard's 404 "Test run not found".
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_run_access
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import RunObservabilityTimeline
from app.services import run_observability_service as svc

router = APIRouter(prefix="/api/v1", tags=["Test Runs"])


@router.get("/runs/{run_id}/observability-timeline", response_model=RunObservabilityTimeline)
async def get_run_observability_timeline(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """The evidence the AI analysis already fetched for this run, on one time axis (W39 STRAT-07).

    Five lanes, always in this order: ``prometheus`` and ``splunk_trace`` (the newest decision evidence snapshot of
    the deep analysis), ``ocp_events`` (the pod events stored when the run was ingested), ``splunk_per_test`` and
    ``ocp_per_test`` (the per-test analysis' stored tool observations). Each lane names who fetched it
    (``fetched_by``) and says in words why it holds nothing (``state`` + ``sentence``). An item whose stored time
    cannot be read is listed in ``untimed``, never placed. ``offline_now`` / ``configured_now`` are read NOW, not at
    analysis time. Read-only: nothing is fetched or written.
    """
    payload = await svc.build_run_observability_timeline(db, run_id)
    if payload is None:   # an ADMIN (no bound key) passes the guard without its run lookup
        raise HTTPException(status_code=404, detail="Test run not found")
    return RunObservabilityTimeline(**payload)
