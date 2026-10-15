"""AI history routes (W03 AIH-02, AIH-03, AIH-05, AIH-06; contract §6).

  GET /api/v1/runs/{run_id}/ai-history                      stored versions of one kind, newest first
  GET /api/v1/runs/{run_id}/ai-history/diff                 structured diff of two digest versions of the run
  GET /api/v1/runs/{run_id}/ai-history/export               the whole history as one JSON download (E8.4 gated)
  GET /api/v1/runs/{run_id}/ai-history/{version_id}         one stored version with its payload
  GET /api/v1/runs/{run_id}/tests/{test_id}/ai-history      the per-test analysis versions
  GET /api/v1/runs/{run_id}/previous                        the D42 "previous run" of this run
  GET /api/v1/runs/{run_id}/ai-compare                      this run's AI analysis against a baseline run's

Every handler is member-level (``require_run_access`` -> ``get_current_active_user``;
a VIEWER gets 200s) and loads the run ITSELF: ``require_run_access`` lets an
ADMIN through without checking that the run exists (contract 0.17). Every id in
a query param is verified against the run (or the run's project) inside the
read service, and every failure of that check is a 404 with the same body as
"does not exist". Nothing here writes on a GET except the export's E8.4
distribution-audit row. A Mongo failure is ``503 ai_history_unavailable``.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.core.deps import get_current_active_user, require_run_access
from app.db.mongo import get_mongo_db
from app.db.postgres import get_db
from app.models.ai_history_schemas import (
    AICompareResponse,
    AIDigestDiffResponse,
    AIHistoryListResponse,
    AIPreviousRunResponse,
    AITestHistoryResponse,
    AIVersionResponse,
)
from app.models.postgres import TestRun, User
from app.services import ai_history_read_service as reads
from app.services.ai_history_service import AIHistoryUnavailable
from app.services.report_distribution_policy import (
    decide_run_distribution,
    record_distribution,
    refusal_detail,
)
from app.services.review_envelope import (
    review_envelope_for_pipeline_subject,
    review_envelope_for_run,
)

router = APIRouter(prefix="/api/v1/runs", tags=["AI History"])

#: Stable 503 code (mirrors ``decision_report_versions_unavailable``).
UNAVAILABLE = "ai_history_unavailable"
EXPORT_CHANNEL = "ai_history_export"
_KIND_PATTERN = "^(analysis_digest|run_summary)$"
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


async def _load_run(db: Any, run_id: uuid.UUID) -> TestRun:
    run: Optional[TestRun] = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found")
    return run


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE)


def _not_found(exc: LookupError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=getattr(exc, "detail", None) or "AI version not found",
    )


async def _with_run_envelope(db: Any, response: Response, run_id: uuid.UUID, body: dict) -> dict:
    """The E8.3 envelope of the run's report, applied at response time (never stored)."""
    envelope = await review_envelope_for_run(db, run_id)
    envelope.apply_headers(response)
    return {**body, **envelope.fields()}


def _diff_target(to: str) -> str:
    """``to`` = ``latest`` | ``live`` | a version id (uuid); anything else is a 422."""
    if to in ("latest", "live"):
        return to
    try:
        return str(uuid.UUID(to))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="to must be a version id, 'latest' or 'live'",
        ) from None


def export_filename(build_number: Optional[str], run_id: uuid.UUID) -> str:
    """``run-<build>-ai-history.json`` with the build sanitised: a CI-supplied build number
    must never inject a quote, CR/LF or a path into the header."""
    safe = _UNSAFE_FILENAME.sub("_", build_number or str(run_id)[:8])[:60]
    return f"run-{safe}-ai-history.json"


@router.get("/{run_id}/ai-history", response_model=AIHistoryListResponse)
async def list_ai_history(
    run_id: uuid.UUID,
    response: Response,
    kind: str = Query(
        default="analysis_digest", pattern=_KIND_PATTERN, description="analysis_digest | run_summary",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Stored AI versions of the run (one kind), newest first. Reads each version's
    ``meta``/``provenance`` only, never its payload. An empty store is ``versions: []``."""
    run = await _load_run(db, run_id)
    try:
        body = await reads.list_versions(db, get_mongo_db(), run=run, kind=kind, limit=limit)
    except AIHistoryUnavailable as exc:
        raise _unavailable(exc) from None
    return await _with_run_envelope(db, response, run_id, body)


@router.get(
    "/{run_id}/ai-history/diff",
    response_model=AIDigestDiffResponse,
    response_model_by_alias=True,
)
async def diff_ai_history(
    run_id: uuid.UUID,
    response: Response,
    from_version: uuid.UUID = Query(..., alias="from", description="A digest version id of THIS run"),
    to: str = Query(default="latest", max_length=64, description="A version id | latest | live"),
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """Structured diff of two analysis digests of the same run (mode ``versions``)."""
    target = _diff_target(to)
    run = await _load_run(db, run_id)
    try:
        body = await reads.diff_versions(db, get_mongo_db(), run=run, from_id=str(from_version), to_ref=target)
    except reads.AIVersionNotFound as exc:
        raise _not_found(exc) from None
    except AIHistoryUnavailable as exc:
        raise _unavailable(exc) from None
    return await _with_run_envelope(db, response, run_id, body)


@router.get("/{run_id}/ai-history/export")
async def export_ai_history(
    run_id: uuid.UUID,
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
    current_user: User = Depends(get_current_active_user),
):
    """The run's whole AI history as one JSON file (``AIHistoryExportBundle``).

    AI report content leaving the system: the E8.4 distribution policy decides
    (channel ``ai_history_export``) and its audit row is committed whether the
    export is allowed or refused (409) -- the one documented write of this
    family of GETs, exactly like the intelligence export.
    """
    from app.core.metrics import report_exports_total

    run = await _load_run(db, run_id)
    try:
        bundle = await reads.export_bundle(db, get_mongo_db(), run=run)
    except AIHistoryUnavailable as exc:
        raise _unavailable(exc) from None
    distribution = await decide_run_distribution(
        db, run_id=run_id, project_id=run.project_id, channel=EXPORT_CHANNEL,
    )
    await record_distribution(
        db, distribution, channel=EXPORT_CHANNEL, run_id=run_id, project_id=run.project_id, actor=current_user,
    )
    # The request session rolls back on close unless the audit row is committed.
    await db.commit()
    if not distribution.allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal_detail(distribution))
    if distribution.watermark:
        bundle["draft_watermark"] = distribution.watermark
    report_exports_total.labels(format="json", type="ai_history").inc()
    return Response(
        content=json.dumps(bundle, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename(run.build_number, run_id)}"',
        },
    )


@router.get("/{run_id}/ai-history/{version_id}", response_model=AIVersionResponse)
async def get_ai_history_version(
    run_id: uuid.UUID,
    version_id: uuid.UUID,
    response: Response,
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """One stored version (any kind) with its payload. A version of another run is 404."""
    run = await _load_run(db, run_id)
    try:
        body = await reads.get_version_detail(db, get_mongo_db(), run=run, version_id=str(version_id))
    except reads.AIVersionNotFound as exc:
        raise _not_found(exc) from None
    except AIHistoryUnavailable as exc:
        raise _unavailable(exc) from None
    # A deterministic run summary was not written by a model (the summary route's rule).
    ai_generated = not (body.get("kind") == "run_summary" and body.get("fallback_used"))
    if body.get("pipeline_run_id"):
        envelope = await review_envelope_for_pipeline_subject(
            db, body["pipeline_run_id"], ai_generated=ai_generated,
        )
    else:
        envelope = await review_envelope_for_run(db, run_id, ai_generated=ai_generated)
    envelope.apply_headers(response)
    return {**body, **envelope.fields()}


@router.get("/{run_id}/tests/{test_id}/ai-history", response_model=AITestHistoryResponse)
async def get_test_ai_history(
    run_id: uuid.UUID,
    test_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=50),
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """The stored analysis versions of one test result, newest first, plus the current row.
    Postgres only: never a 503."""
    await _load_run(db, run_id)
    body = await reads.list_test_versions(db, run_id=run_id, test_id=test_id, limit=limit)
    if body is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test case not found")
    return body


@router.get("/{run_id}/previous", response_model=AIPreviousRunResponse)
async def get_previous_run(
    run_id: uuid.UUID,
    suite_name: Optional[str] = Query(default=None, min_length=1, max_length=500),
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """The D42 previous run: same project, suite and branch, completed, newest finished
    before this run. "No baseline" is an answer (``reason`` + ``message``), never an error."""
    run = await _load_run(db, run_id)
    return await reads.previous_run_body(db, run=run, suite_name=suite_name)


@router.get("/{run_id}/ai-compare", response_model=AICompareResponse)
async def compare_ai_with_baseline(
    run_id: uuid.UUID,
    response: Response,
    baseline_run_id: Optional[uuid.UUID] = Query(default=None),
    suite_name: Optional[str] = Query(default=None, min_length=1, max_length=500),
    version_id: Optional[uuid.UUID] = Query(default=None, description="A digest version id of THIS run"),
    baseline_version_id: Optional[uuid.UUID] = Query(
        default=None, description="A digest version id of the BASELINE run",
    ),
    db: Any = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """This run's AI analysis against a baseline's (default: the D42 previous run). Live sides
    by default; a pinned version compares what a pipeline produced. Never triggers an LLM."""
    if baseline_run_id is not None and baseline_run_id == run_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="baseline_run_id must be a different run",
        )
    run = await _load_run(db, run_id)
    try:
        body = await reads.compare_runs_ai(
            db,
            get_mongo_db(),
            run=run,
            baseline_run_id=baseline_run_id,
            suite_name=suite_name,
            version_id=str(version_id) if version_id is not None else None,
            baseline_version_id=str(baseline_version_id) if baseline_version_id is not None else None,
        )
    except (reads.AIVersionNotFound, reads.BaselineRunNotFound) as exc:
        raise _not_found(exc) from None
    except AIHistoryUnavailable as exc:
        raise _unavailable(exc) from None
    return await _with_run_envelope(db, response, run_id, body)
