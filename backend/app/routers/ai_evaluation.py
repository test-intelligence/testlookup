"""AI Evaluation Dashboard router — datasets, eval runs, quality metrics, release gate (OPS-02 + P5)."""
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import (
    AIEvalBaseline,
    AIEvalDataset,
    AIEvalGateRun,
    AIEvalRun,
    DecisionReportEvalCycle,
    User,
    UserRole,
)
# W16: the schema names are module-level because FastAPI reads a route's
# ``response_model`` and body annotation at import; every SERVICE and ORM name a
# W16 handler needs is imported inside that handler (this file's style).
from app.models.schemas import (
    AIEvalDatasetCreate,
    AIEvalGateRunListResponse,
    AIEvalGateRunResponse,
    AIEvalItemListResponse,
    AIEvalManifestResponse,
    AIEvalDatasetResponse,
    AIEvalRunListResponse,
    AIEvalRunResponse,
    AIQualityDashboardResponse,
    CandidateEvaluationAccepted,
    CandidateEvaluationRequest,
    CandidateOptionsResponse,
    EvalProvenanceHealthResponse,
    ReviewerQualityRunListResponse,
    ShadowPairLabelRequest,
    ShadowPairListResponse,
    ShadowPairRead,
)
from app.services.activity.service import ActorRef, record as record_activity
from app.services.reviewer_quality_service import (
    ReviewerCleanObservation,
    ReviewerHumanOutcome,
    ReviewerMutationObservation,
)

logger = logging.getLogger("routers.ai_evaluation")

router = APIRouter(prefix="/api/v1/ai-eval", tags=["AI Evaluation"])


def _refusal(status_code: int, code: str, message: str) -> HTTPException:
    """Every refusal W16 adds is ``detail = {"code", "message"}`` (one fixed sentence)."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# ── Dashboard ────────────────────────────────────────────────────────────────


@router.get("/dashboard", response_model=AIQualityDashboardResponse)
async def get_quality_dashboard(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Get the AI quality dashboard: agreement, drift, recent evals, model history."""
    from app.services.ai_eval_service import (
        compute_agreement_rate,
        detect_quality_drift,
        get_label_health,
        get_model_version_history,
    )
    from app.services.eval_provenance_service import eval_provenance_health
    from app.services.feedback_service import get_feedback_stats

    agreement = await compute_agreement_rate(db, days=days)
    drift = await detect_quality_drift(db, task_type="classification", window_days=min(days, 14))
    label_health = await get_label_health(db)

    # Recent eval runs
    run_result = await db.execute(
        select(AIEvalRun).order_by(AIEvalRun.evaluated_at.desc()).limit(10)
    )
    recent_runs = [AIEvalRunResponse.model_validate(r) for r in run_result.scalars().all()]

    model_versions = await get_model_version_history(db, limit=10)

    # Feedback summary
    feedback_stats = await get_feedback_stats(db)
    provenance = await eval_provenance_health(db)

    return AIQualityDashboardResponse(
        agreement=agreement,
        drift=drift,
        recent_eval_runs=recent_runs,
        model_versions=model_versions,
        feedback_summary=feedback_stats,
        label_health=label_health,
        eval_provenance=EvalProvenanceHealthResponse.model_validate(provenance),
    )


@router.get("/label-health")
async def get_training_label_health(
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Human-label coverage of the ML training pool (AI-F1).

    Reports live counts per label-provenance bucket (human_direct /
    human_indirect / llm_pseudo), the human-label floor, and the provenance
    composition the deployed model was actually trained on — so the "learning
    loop" claim is verifiable instead of implied.
    """
    from app.services.ai_eval_service import get_label_health

    return await get_label_health(db)


# ── Datasets ─────────────────────────────────────────────────────────────────


@router.get("/datasets", response_model=list[AIEvalDatasetResponse])
async def list_datasets(
    task_type: str | None = None,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List evaluation datasets."""
    query = select(AIEvalDataset).order_by(AIEvalDataset.created_at.desc())
    if task_type:
        query = query.where(AIEvalDataset.task_type == task_type)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/datasets", response_model=AIEvalDatasetResponse, status_code=201)
async def create_dataset(
    payload: AIEvalDatasetCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new evaluation dataset (ADMIN only)."""
    dataset = AIEvalDataset(
        name=payload.name,
        description=payload.description,
        task_type=payload.task_type,
        items=payload.items,
        item_count=len(payload.items),
        created_by=current_user.id,
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return dataset


@router.post("/datasets/from-feedback", response_model=AIEvalDatasetResponse, status_code=201)
async def create_dataset_from_feedback(
    name: str = "Auto-generated from feedback",
    task_type: str = "classification",
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate a labeled dataset from existing human feedback (ADMIN only)."""
    from app.services.ai_eval_service import build_dataset_from_feedback

    items = await build_dataset_from_feedback(db, task_type=task_type)
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No held-out feedback with eval-manifest provenance is available",
        )

    dataset = AIEvalDataset(
        name=name,
        description=f"Auto-generated from {len(items)} feedback records",
        task_type=task_type,
        items=items,
        item_count=len(items),
        created_by=current_user.id,
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return dataset


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Delete an evaluation dataset (ADMIN only)."""
    result = await db.execute(select(AIEvalDataset).where(AIEvalDataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    await db.delete(dataset)
    await db.commit()
    return None


# ── Eval Runs ────────────────────────────────────────────────────────────────


@router.get("/runs", response_model=AIEvalRunListResponse)
async def list_eval_runs(
    dataset_id: uuid.UUID | None = None,
    task_type: str | None = None,
    metric_kind: str | None = Query(default=None, max_length=20),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List evaluation runs, newest first, with the ``total`` of the filtered set.

    W16: ``{items, total}`` (was a bare list no client read); ``metric_kind``
    (legacy label agreement / replay) and ``status`` filter. Writes nothing.
    """
    from sqlalchemy import func  # noqa: PLC0415

    from app.services.ai_eval_vocab import METRIC_KINDS, RUN_STATUSES  # noqa: PLC0415

    if metric_kind is not None and metric_kind not in METRIC_KINDS:
        raise _refusal(422, "metric_kind_invalid", "metric_kind must be label_agreement or replay.")
    if status_filter is not None and status_filter not in RUN_STATUSES:
        raise _refusal(
            422, "status_invalid", "status must be queued, running, completed, partial or failed.",
        )
    query = select(AIEvalRun).order_by(AIEvalRun.evaluated_at.desc()).limit(limit)
    count = select(func.count()).select_from(AIEvalRun)
    if dataset_id:
        query = query.where(AIEvalRun.dataset_id == dataset_id)
        count = count.where(AIEvalRun.dataset_id == dataset_id)
    if task_type:
        query = query.where(AIEvalRun.task_type == task_type)
        count = count.where(AIEvalRun.task_type == task_type)
    if metric_kind:
        query = query.where(AIEvalRun.metric_kind == metric_kind)
        count = count.where(AIEvalRun.metric_kind == metric_kind)
    if status_filter:
        query = query.where(AIEvalRun.status == status_filter)
        count = count.where(AIEvalRun.status == status_filter)
    rows = (await db.execute(query)).scalars().all()
    total = (await db.execute(count)).scalar_one()
    return AIEvalRunListResponse(
        items=[AIEvalRunResponse.model_validate(r) for r in rows], total=int(total or 0),
    )


@router.post(
    "/runs/evaluate/{dataset_id}",
    response_model=AIEvalRunResponse,
    status_code=201,
    responses={202: {"model": CandidateEvaluationAccepted}},
)
async def run_evaluation(
    dataset_id: uuid.UUID,
    body: Optional[CandidateEvaluationRequest] = None,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Run evaluation against a dataset (ADMIN only).

    No body: the LEGACY label-agreement evaluation, synchronous, 201 with the
    run (unchanged). A ``candidate`` body (W16 AGT-09): a queued REPLAY of the
    candidate through the real engine, 202 ``{run_id, status: "queued"}``.
    """
    import time

    from app.services.ai_eval_service import compute_metrics_for_task_type

    result = await db.execute(select(AIEvalDataset).where(AIEvalDataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    if body is not None:
        return await _queue_candidate_evaluation(db, current_user, dataset, body)

    start = time.monotonic()
    items = dataset.items or []
    metrics = compute_metrics_for_task_type(dataset.task_type, items)
    duration_ms = int((time.monotonic() - start) * 1000)

    # Get current model info
    from app.core.config import settings
    model_name = settings.LLM_MODEL

    agreement_rate = metrics["correct"] / metrics["total"] if metrics["total"] > 0 else None

    eval_run = AIEvalRun(
        dataset_id=dataset_id,
        model_name=model_name,
        task_type=dataset.task_type,
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1_score=metrics["f1_score"],
        accuracy=metrics["accuracy"],
        agreement_rate=agreement_rate,
        total_items=metrics["total"],
        correct_items=metrics["correct"],
        duration_ms=duration_ms,
    )
    db.add(eval_run)
    await db.commit()
    await db.refresh(eval_run)
    return eval_run


async def _queue_candidate_evaluation(
    db: AsyncSession, current_user: User, dataset: AIEvalDataset, body: CandidateEvaluationRequest,
):
    """Queue a candidate REPLAY of ``dataset`` (W16 AGT-09, contract §2.1 (b)).

    Every check runs before anything is written; the run row and its activity
    row share ONE commit; the task is dispatched AFTER the commit, so a broker
    error leaves a ``queued`` row for the reconcile sweep, never a 500 after a
    commit. The AI config is read on THIS request's session (``resolve_candidate``).
    """
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    from app.services.ai_eval_vocab import CANDIDATE_TIERS, MAX_CANDIDATE_ITEMS, TASK_TIERS  # noqa: PLC0415

    await _authorize_body_project(db, current_user, body.project_id)
    tier = body.candidate.tier
    if tier not in CANDIDATE_TIERS:
        raise _refusal(422, "candidate_tier_invalid", "tier must be rules, ml or llm.")
    allowed = TASK_TIERS.get(dataset.task_type, ())
    if tier not in allowed:
        raise _refusal(
            422,
            "candidate_tier_unsupported",
            f"{dataset.task_type} replays the {' or '.join(allowed) or 'no'} tier only.",
        )
    from app.services import ai_eval_runner  # noqa: PLC0415

    try:
        resolved = await ai_eval_runner.resolve_candidate(
            db, task_type=dataset.task_type, candidate=body.candidate, project_id=body.project_id,
        )
    except ai_eval_runner.CandidateRefused as exc:
        code = str(getattr(exc, "code", None) or exc)
        raise _refusal(422, code, str(getattr(exc, "message", None) or code)) from None

    in_flight = (
        await db.execute(
            select(AIEvalRun.id).where(
                AIEvalRun.dataset_id == dataset.id,
                AIEvalRun.metric_kind == "replay",
                AIEvalRun.status.in_(("queued", "running")),
                AIEvalRun.candidate["tier"].astext == tier,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if in_flight is not None:
        raise _refusal(
            409, "candidate_run_in_progress", f"A {tier} evaluation of this dataset is already running.",
        )
    items = list(dataset.items or [])
    if not items:
        raise _refusal(422, "dataset_empty", "The dataset has no items to replay.")

    total_items = min(len(items), body.limit or MAX_CANDIDATE_ITEMS, MAX_CANDIDATE_ITEMS)
    run_id = uuid.uuid4()
    db.add(AIEvalRun(
        id=run_id,
        dataset_id=dataset.id,
        model_name=(resolved.model or resolved.tier)[:200],
        task_type=dataset.task_type,
        metric_kind="replay",
        status="queued",
        candidate=resolved.model_dump(mode="json"),
        project_id=body.project_id,
        requested_by=current_user.id,
        total_items=total_items,
        correct_items=0,
        items_capped=len(items) > total_items,
    ))
    await record_activity(
        db,
        project_id=body.project_id,
        event_type="ai_eval.candidate_requested",
        actor=ActorRef.from_user(current_user),
        entity_id=body.project_id,
        entity_label=(dataset.name or "")[:120],
        context={
            "run_id": str(run_id),
            "dataset_id": str(dataset.id),
            "task_type": dataset.task_type,
            "tier": resolved.tier,
            "item_count": total_items,
        },
    )
    await db.commit()
    try:
        from app.worker.tasks import run_candidate_evaluation  # noqa: PLC0415

        run_candidate_evaluation.apply_async(kwargs={"run_id": str(run_id)}, queue="ai_analysis")
    except Exception as exc:  # the row stays queued; reconcile_stale_eval_runs settles it
        logger.warning(
            "candidate_eval_dispatch_failed",
            extra={"run_id": str(run_id), "error_type": type(exc).__name__},
        )
    accepted = CandidateEvaluationAccepted(run_id=str(run_id), status="queued")
    return JSONResponse(status_code=202, content=accepted.model_dump())


@router.get("/candidate-options", response_model=CandidateOptionsResponse)
async def get_candidate_options(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """The tiers a candidate evaluation may replay, with the reason a tier is unavailable.

    ADMIN (the Evaluate form is admin-only). Reads the effective AI config on
    this request's session and the ML model's metadata; writes nothing.
    """
    from app.services import ai_eval_runner  # noqa: PLC0415

    return await ai_eval_runner.candidate_options(db)


async def _load_eval_run(db: AsyncSession, eval_run_id: uuid.UUID) -> AIEvalRun:
    run = (await db.execute(select(AIEvalRun).where(AIEvalRun.id == eval_run_id))).scalar_one_or_none()
    if run is None:
        raise _refusal(404, "eval_run_not_found", "Evaluation run not found.")
    return run


@router.get("/runs/{eval_run_id}", response_model=AIEvalRunResponse)
async def get_eval_run(
    eval_run_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """One evaluation run (legacy or replay) with its aggregate scores. Writes nothing."""
    return AIEvalRunResponse.model_validate(await _load_eval_run(db, eval_run_id))


def _clean_payload(value: Any) -> Any:
    """Re-sanitize a stored payload on the read path (it was sanitized on write too)."""
    from app.services.evidence_sanitizer import sanitize_persistence_payload  # noqa: PLC0415

    return sanitize_persistence_payload(value)[0]


class _SentenceFacts(dict):
    """``format_map`` facts for an item sentence; a fact the row lacks reads ``unknown``."""

    def __missing__(self, key: str) -> str:
        return "unknown"


def _item_error_text(error_code: Optional[str], facts: dict[str, Any]) -> Optional[str]:
    """The item's sentence: the one the runner stored (``score.error_text``), else
    ``ITEM_ERROR_SENTENCES[error_code]`` filled from the row's stored facts."""
    from app.services.ai_eval_vocab import ITEM_ERROR_SENTENCES  # noqa: PLC0415

    stored = facts.get("error_text")
    if isinstance(stored, str) and stored:
        return stored
    template = ITEM_ERROR_SENTENCES.get(error_code or "")
    if template is None:
        return None
    return template.format_map(_SentenceFacts({k: v for k, v in facts.items() if isinstance(v, (str, int, float))}))


def _item_read(row: Any, dataset_items: list) -> dict[str, Any]:
    """One stored item output as ``AIEvalItemOutputRead``; every payload re-sanitized."""
    index = int(row.item_index)
    source = dataset_items[index] if 0 <= index < len(dataset_items) else None
    item_input = source.get("input") if isinstance(source, dict) else None
    score = row.score if isinstance(row.score, dict) else {}
    output = row.output if isinstance(row.output, dict) else None
    return {
        "item_index": index,
        "item_key": row.item_key,
        "status": row.status,
        "error_code": row.error_code,
        "error_text": _item_error_text(row.error_code, {**(output or {}), **score}),
        "input": _clean_payload(item_input) if isinstance(item_input, dict) else {},
        "expected": _clean_payload(row.expected) if isinstance(row.expected, dict) else {},
        "output": _clean_payload(output) if output is not None else None,
        "score": _clean_payload(score),
        "latency_ms": row.latency_ms,
        "tokens": row.tokens,
        "mode_used": row.mode_used,
    }


@router.get("/runs/{eval_run_id}/items", response_model=AIEvalItemListResponse)
async def list_eval_run_items(
    eval_run_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """A replay run's per-item outputs in stored order (ADMIN only, contract D-4).

    Items carry dataset text that a feedback-built dataset draws from every
    project, so QA leads see aggregates only. Inputs, expected values and
    outputs are re-sanitized here. A legacy run stored no items. Writes nothing.
    """
    from sqlalchemy import func  # noqa: PLC0415

    from app.models.postgres import AIEvalItemOutput  # noqa: PLC0415
    from app.models.schemas import AIEvalItemOutputRead  # noqa: PLC0415
    from app.services.ai_eval_vocab import ITEM_STATUSES  # noqa: PLC0415

    if status_filter is not None and status_filter not in ITEM_STATUSES:
        raise _refusal(422, "status_invalid", "status must be correct, incorrect, unscored or error.")
    run = await _load_eval_run(db, eval_run_id)
    if (run.metric_kind or "label_agreement") != "replay":
        return AIEvalItemListResponse(items=[], total=0, counts={})

    counted = await db.execute(
        select(AIEvalItemOutput.status, func.count())
        .where(AIEvalItemOutput.eval_run_id == run.id)
        .group_by(AIEvalItemOutput.status)
    )
    counts = {str(row[0]): int(row[1]) for row in counted.all()}
    total = counts.get(status_filter, 0) if status_filter else sum(counts.values())
    page = select(AIEvalItemOutput).where(AIEvalItemOutput.eval_run_id == run.id)
    if status_filter:
        page = page.where(AIEvalItemOutput.status == status_filter)
    rows = (
        await db.execute(page.order_by(AIEvalItemOutput.item_index).limit(limit).offset(offset))
    ).scalars().all()
    dataset_items: list = []
    if rows:
        stored = await db.execute(select(AIEvalDataset.items).where(AIEvalDataset.id == run.dataset_id))
        dataset_items = list(stored.scalar_one_or_none() or [])
    return AIEvalItemListResponse(
        items=[AIEvalItemOutputRead.model_validate(_item_read(row, dataset_items)) for row in rows],
        total=total,
        counts=counts,
    )


# ── Drift Detection ─────────────────────────────────────────────────────────


@router.get("/drift")
async def get_drift(
    task_type: str = "classification",
    window_days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Detect AI quality drift by comparing current vs previous eval windows."""
    from app.services.ai_eval_service import detect_quality_drift

    return await detect_quality_drift(db, task_type=task_type, window_days=window_days)


# ── Phase 5: Pre-Release Evaluation Gate ─────────────────────────────────────


class PreReleaseGateRequest(BaseModel):
    task_type: str = "classification"
    agent_name: str = "AnalysisAgent"
    dataset_id: Optional[str] = None
    # W16 AGT-09: gate a completed REPLAY run on its stored scores.
    candidate_run_id: Optional[uuid.UUID] = None


class ReportEvalCycleRequest(BaseModel):
    corpus_version: str = Field(min_length=1, max_length=120)
    reports: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    authorized_evidence_ids: list[str] = Field(default_factory=list, max_length=5_000)
    project_id: uuid.UUID | None = None
    test_run_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    cycle_key: str | None = Field(default=None, max_length=128)


def _report_cycle_response(row: DecisionReportEvalCycle) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "cycle_key": row.cycle_key,
        "corpus_version": row.corpus_version,
        "corpus_sha256": row.corpus_sha256,
        "report_count": row.report_count,
        "status": row.status,
        "metrics": row.metrics,
        "checks": row.checks,
        "unavailable_metrics": row.unavailable_metrics,
        "consecutive_passes": row.consecutive_passes,
        "evaluated_by": str(row.evaluated_by) if row.evaluated_by else None,
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
    }


@router.get("/report-cycles")
async def list_report_eval_cycles(
    corpus_version: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List durable report-level evaluation evidence."""
    query = (
        select(DecisionReportEvalCycle)
        .order_by(DecisionReportEvalCycle.evaluated_at.desc())
        .limit(limit)
    )
    if corpus_version:
        query = query.where(DecisionReportEvalCycle.corpus_version == corpus_version)
    rows = (await db.execute(query)).scalars().all()
    return [_report_cycle_response(row) for row in rows]


@router.get("/report-cycles/readiness")
async def get_report_eval_readiness(
    corpus_version: str = Query(..., min_length=1, max_length=120),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Return the fail-closed representative-corpus pilot readiness gate."""
    from app.services.decision_report_eval_cycle_service import assess_report_eval_readiness

    rows = (
        await db.execute(
            select(DecisionReportEvalCycle)
            .where(DecisionReportEvalCycle.corpus_version == corpus_version)
            .order_by(DecisionReportEvalCycle.evaluated_at.desc())
            .limit(2)
        )
    ).scalars().all()
    return assess_report_eval_readiness(rows)


@router.post("/report-cycles", status_code=201)
async def create_report_eval_cycle(
    payload: ReportEvalCycleRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate and persist one bounded report corpus cycle (ADMIN only)."""
    from app.services.decision_report_eval_cycle_service import (
        evaluate_and_persist_report_cycle,
        load_authoritative_report_projections,
        summarize_decision_report_actions,
        summarize_decision_report_feedback,
    )
    from app.core.config import settings

    if payload.project_id is not None:
        # The named project is read below (its published reports); check it
        # like any scoped id (code review round 4). An admin passes by role.
        from app.core.deps import resolve_project_scope  # noqa: PLC0415

        await resolve_project_scope(db, current_user, str(payload.project_id))

    try:
        reports = payload.reports
        feedback_summary = None
        if payload.test_run_ids:
            if payload.project_id is None or payload.reports or payload.authorized_evidence_ids:
                raise ValueError("report_eval_source_invalid")
            from app.db.mongo import get_mongo_db

            reports = await load_authoritative_report_projections(
                mongo_db=get_mongo_db(),
                db=db,
                project_id=payload.project_id,
                test_run_ids=payload.test_run_ids,
            )
            feedback_summary = await summarize_decision_report_feedback(
                db,
                project_id=payload.project_id,
                test_run_ids=payload.test_run_ids,
            )
            action_summary = await summarize_decision_report_actions(
                db,
                project_id=payload.project_id,
                test_run_ids=payload.test_run_ids,
            )
        elif payload.reports:
            if not settings.AI_REPORT_EVAL_ALLOW_CALLER_CORPUS:
                raise ValueError("report_eval_authoritative_source_required")
            action_summary = None
        else:
            raise ValueError("report_eval_source_required")
        row, _created = await evaluate_and_persist_report_cycle(
            db,
            corpus_version=payload.corpus_version,
            reports=reports,
            cycle_key=payload.cycle_key,
            authorized_evidence_ids=payload.authorized_evidence_ids,
            feedback_summary=feedback_summary,
            action_summary=action_summary,
            evaluated_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await db.commit()
    await db.refresh(row)
    return _report_cycle_response(row)


class AgentStackReleaseGateRequest(BaseModel):
    change_id: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)
    routing_versions: dict[str, str] = Field(default_factory=dict)
    required_gates: list[dict[str, str]] | None = None
    persist: bool = True


class TierOutputPairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=160)
    incumbent_output: Any
    candidate_output: Any
    incumbent_cost_usd: float = Field(default=0.0, ge=0)
    candidate_cost_usd: float = Field(default=0.0, ge=0)
    incumbent_latency_ms: int = Field(default=0, ge=0)
    candidate_latency_ms: int = Field(default=0, ge=0)
    incumbent_tokens: int = Field(default=0, ge=0)
    candidate_tokens: int = Field(default=0, ge=0)


class TierComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    agent_id: str = Field(min_length=1, max_length=80)
    capability: str = Field(min_length=1, max_length=80)
    incumbent_tier: str = Field(pattern="^(deterministic|slm|llm|auto)$")
    candidate_tier: str = Field(pattern="^(deterministic|slm|llm|auto)$")
    delta: float = Field(default=0.05, ge=0, le=1)
    pairs: list[TierOutputPairRequest] = Field(default_factory=list, max_length=1_000)
    persist: bool = True
    # W16 AGT-13: add the project's human / golden-labelled live shadow pairs
    # as a second corpus (a BODY field: the direct handler callers keep working).
    include_live_labels: bool = False


class ReviewerQualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    agent_id: str = Field(default="agent.reviewer.v1", min_length=1, max_length=80)
    mutations: list[ReviewerMutationObservation] = Field(default_factory=list, max_length=10_000)
    clean: list[ReviewerCleanObservation] = Field(default_factory=list, max_length=10_000)
    human_outcomes: list[ReviewerHumanOutcome] = Field(default_factory=list, max_length=10_000)
    persist: bool = True
    auto_disable: bool = True


class SetBaselineRequest(BaseModel):
    task_type: str = "classification"
    agent_name: str = "AnalysisAgent"
    prompt_version: str = "v1"
    model_name: Optional[str] = None
    dataset_id: Optional[str] = None
    min_accuracy: float = 0.80
    min_f1: float = 0.75
    max_regression_pct: float = 5.0
    # W16 AGT-09: make a completed REPLAY run the baseline (metrics, model and
    # dataset from the run; prompt_version ``replay-<tier>``).
    eval_run_id: Optional[uuid.UUID] = None


@router.post("/pre-release-gate")
async def run_pre_release_gate(
    body: PreReleaseGateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the pre-release evaluation gate for an agent.

    Compares current metrics against baseline thresholds. Returns pass, fail,
    or insufficient_samples
    with per-rule results. Prompt, model, or routing changes should not ship
    if the gate returns FAIL.
    """
    from app.services.eval_gate_service import CandidateRunRefused, evaluate_pre_release_gate

    try:
        return await evaluate_pre_release_gate(
            db,
            task_type=body.task_type,
            agent_name=body.agent_name,
            dataset_id=body.dataset_id,
            candidate_run_id=body.candidate_run_id,
        )
    except CandidateRunRefused as exc:
        raise _refusal(exc.status_code, exc.code, exc.message) from None


@router.post("/agent-stack-release-gate")
async def run_agent_stack_release_gate(
    body: AgentStackReleaseGateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the release gate for prompt/model/routing changes across the agent stack.

    The returned manifest checksum makes the gated change set auditable.
    """
    from app.services.eval_gate_service import evaluate_agent_stack_release_gate

    return await evaluate_agent_stack_release_gate(
        db,
        change_id=body.change_id,
        prompt_versions=body.prompt_versions,
        model_versions=body.model_versions,
        routing_versions=body.routing_versions,
        required_gates=body.required_gates,
        evaluated_by=current_user.id,
        persist=body.persist,
    )


async def _authorize_body_project(db: AsyncSession, user: User, project_id: uuid.UUID) -> None:
    """Membership of the project named in the BODY.

    ``require_project_access()`` / ``require_project_role()`` read the URL
    path, and these routes carry the project in the body, so both were
    no-ops: any signed-in user wrote a G2 verdict into any project (which
    gates that project's agent tier changes), and a global QA lead switched
    off another project's reviewer second-model check (E2E 2026-10-10).
    """
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    await resolve_project_scope(db, user, str(project_id))


@router.post("/tier-comparison")
async def run_tier_comparison(
    body: TierComparisonRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Run G2 against paired golden outputs and optionally retain the decision."""
    await _authorize_body_project(db, current_user, body.project_id)
    from app.services.tier_comparison_service import (
        TierOutputPair,
        compare_tier_outputs,
        persist_tier_comparison,
    )
    from app.services.agent_capability_registry import get_capability

    try:
        if get_capability(body.capability).capability_id != body.agent_id:
            raise ValueError("agent_id does not match the capability registry entry")
        live: dict[str, Any] = {}
        if body.include_live_labels:
            from app.services import shadow_pair_service  # noqa: PLC0415

            # Read inside the body's project only (checked above).
            live["labelled_pairs"] = await shadow_pair_service.labelled_pairs_for(
                db,
                project_id=body.project_id,
                agent_id=body.agent_id,
                incumbent_tier=body.incumbent_tier,
                candidate_tier=body.candidate_tier,
            )
        result = compare_tier_outputs(
            capability=body.capability,
            incumbent_tier=body.incumbent_tier,
            candidate_tier=body.candidate_tier,
            delta=body.delta,
            pairs=[TierOutputPair(**pair.model_dump()) for pair in body.pairs],
            **live,
        )
        if body.include_live_labels:
            # Asked for live labels and found none: say 0, never leave it to be guessed.
            result.setdefault("live_labelled_pairs", 0)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if body.persist:
        row = await persist_tier_comparison(
            db,
            project_id=body.project_id,
            agent_id=body.agent_id,
            result=result,
            evaluated_by=current_user.id,
        )
        await record_activity(
            db,
            project_id=body.project_id,
            event_type="ai_eval.tier_compared",
            actor=ActorRef.from_user(current_user),
            entity_id=body.project_id,
            entity_label=body.agent_id,
            context={
                "verdict": result["verdict"],
                "sample_count": result["sample_count"],
                "candidate_tier": body.candidate_tier,
                "gate_run_id": str(row.id),
                "live_labelled_pairs": int(result.get("live_labelled_pairs") or 0),
            },
        )
        await db.commit()
        result["gate_run_id"] = str(row.id)
    return result


@router.post("/reviewer-quality")
async def run_reviewer_quality(
    body: ReviewerQualityRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Run G3 and apply its guarded second-model retirement decision."""
    await _authorize_body_project(db, current_user, body.project_id)
    from app.services.agent_capability_registry import get_capability
    from app.services.reviewer_quality_service import (
        disable_second_model_if_eligible,
        evaluate_reviewer_quality,
        persist_reviewer_quality,
    )

    if body.agent_id != get_capability("reviewer").capability_id:
        raise HTTPException(status_code=422, detail="G3 evaluates agent.reviewer.v1 only")
    if body.auto_disable and not body.persist:
        raise HTTPException(
            status_code=422,
            detail="auto_disable requires persist=true so the config change has durable evidence",
        )
    try:
        result = evaluate_reviewer_quality(
            mutations=body.mutations,
            clean=body.clean,
            human_outcomes=body.human_outcomes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if body.auto_disable:
        await disable_second_model_if_eligible(
            db,
            project_id=body.project_id,
            agent_id=body.agent_id,
            result=result,
            updated_by=current_user.id,
        )
    if body.persist:
        row = await persist_reviewer_quality(
            db,
            project_id=body.project_id,
            agent_id=body.agent_id,
            result=result,
            mutations=body.mutations,
            clean=body.clean,
            human_outcomes=body.human_outcomes,
            source="observation_batch",
            evaluated_by=current_user.id,
        )
        await record_activity(
            db,
            project_id=body.project_id,
            event_type="ai_eval.reviewer_evaluated",
            actor=ActorRef.from_user(current_user),
            entity_id=body.project_id,
            entity_label=body.agent_id,
            context={
                "verdict": result["verdict"],
                "sample_count": sum(result["semantic_sample_counts"].values()),
                "auto_disable_applied": result["auto_disable_applied"],
                "reviewer_quality_id": str(row.id),
            },
        )
        await db.commit()
        result["reviewer_quality_id"] = str(row.id)
    return result


@router.get("/agent-stack-release-gate/runs", response_model=AIEvalGateRunListResponse)
async def list_agent_stack_gate_runs(
    change_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List historical agent-stack release gate decisions, newest first, with ``total``.

    Agent-stack rows only (``gate_type IS NULL``): the project-scoped G2 tier
    comparisons share the table and are read per project through
    ``GET /tier-comparison/runs`` -- listing them here showed every QA lead
    other projects' tier verdicts (W16 contract §0.4).
    """
    from sqlalchemy import func  # noqa: PLC0415

    query = (
        select(AIEvalGateRun)
        .where(AIEvalGateRun.gate_type.is_(None))
        .order_by(AIEvalGateRun.evaluated_at.desc())
        .limit(limit)
    )
    count = select(func.count()).select_from(AIEvalGateRun).where(AIEvalGateRun.gate_type.is_(None))
    if change_id:
        query = query.where(AIEvalGateRun.change_id == change_id)
        count = count.where(AIEvalGateRun.change_id == change_id)
    if status_filter:
        query = query.where(AIEvalGateRun.status == status_filter)
        count = count.where(AIEvalGateRun.status == status_filter)
    rows = (await db.execute(query)).scalars().all()
    total = (await db.execute(count)).scalar_one()
    return AIEvalGateRunListResponse(
        items=[AIEvalGateRunResponse.model_validate(r) for r in rows], total=int(total or 0),
    )


@router.get("/tier-comparison/runs", response_model=AIEvalGateRunListResponse)
async def list_tier_comparison_runs(
    project_id: uuid.UUID = Query(...),
    agent_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """The project's recorded G2 tier comparisons, newest first, with ``total`` (W16).

    The QUERY project is checked like a body project before anything is read;
    ``agent_id`` filters INSIDE that project. Writes nothing.
    """
    from sqlalchemy import func  # noqa: PLC0415

    await _authorize_body_project(db, current_user, project_id)
    scope = [AIEvalGateRun.gate_type == "tier_comparison", AIEvalGateRun.project_id == project_id]
    if agent_id:
        scope.append(AIEvalGateRun.agent_id == agent_id)
    rows = (
        await db.execute(
            select(AIEvalGateRun).where(*scope).order_by(AIEvalGateRun.evaluated_at.desc()).limit(limit)
        )
    ).scalars().all()
    total = (await db.execute(select(func.count()).select_from(AIEvalGateRun).where(*scope))).scalar_one()
    return AIEvalGateRunListResponse(
        items=[AIEvalGateRunResponse.model_validate(r) for r in rows], total=int(total or 0),
    )


@router.get("/reviewer-quality/runs", response_model=ReviewerQualityRunListResponse)
async def list_reviewer_quality_runs(
    project_id: uuid.UUID = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """The project's G3 reviewer-quality history, newest first, with ``total`` (W16).

    READ-ONLY (the page never posts ``/reviewer-quality``, whose default
    ``auto_disable`` can retire the second model). The observation arrays are
    never selected. Writes nothing.
    """
    from sqlalchemy import func  # noqa: PLC0415

    from app.models.postgres import AIEvalReviewerQuality as Q  # noqa: PLC0415
    from app.models.schemas import ReviewerQualityRunRead  # noqa: PLC0415
    from app.services.agent_capability_registry import get_capability  # noqa: PLC0415

    await _authorize_body_project(db, current_user, project_id)
    scope = [Q.project_id == project_id, Q.agent_id == get_capability("reviewer").capability_id]
    columns = (
        Q.id, Q.project_id, Q.agent_id, Q.source, Q.status, Q.window_started_at, Q.window_ended_at,
        Q.evaluated_at, Q.metrics, Q.regressions, Q.semantic_sample_counts, Q.clean_sample_count,
        Q.human_outcome_count, Q.deterministic_recall, Q.second_model_recall, Q.recall_delta,
        Q.false_flag_rate, Q.false_omission_rate, Q.auto_disable_eligible, Q.auto_disable_applied,
    )
    rows = (
        await db.execute(select(*columns).where(*scope).order_by(Q.evaluated_at.desc()).limit(limit))
    ).mappings().all()
    total = (await db.execute(select(func.count()).select_from(Q).where(*scope))).scalar_one()
    return ReviewerQualityRunListResponse(
        items=[ReviewerQualityRunRead.model_validate(dict(row)) for row in rows], total=int(total or 0),
    )


@router.get("/gates/{checksum}", response_model=AIEvalManifestResponse)
async def get_eval_manifest(
    checksum: str,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the immutable eval manifest stamped on an agent pipeline run."""
    from app.services.eval_provenance_service import resolve_eval_manifest

    manifest = await resolve_eval_manifest(db, checksum.lower())
    if manifest is None:
        raise HTTPException(status_code=404, detail="Evaluation manifest not found")
    return manifest


@router.post("/baselines", status_code=201)
async def set_baseline(
    body: SetBaselineRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """
    Set a baseline from a dataset evaluation (ADMIN only).

    Computes metrics and stores them as the active baseline for the
    specified agent/task_type. Deactivates any prior baseline.
    """
    from app.services.eval_gate_service import CandidateRunRefused, set_baseline_from_eval

    try:
        return await set_baseline_from_eval(
            db,
            task_type=body.task_type,
            agent_name=body.agent_name,
            prompt_version=body.prompt_version,
            model_name=body.model_name,
            dataset_id=body.dataset_id,
            min_accuracy=body.min_accuracy,
            min_f1=body.min_f1,
            max_regression_pct=body.max_regression_pct,
            created_by=current_user.id,
            eval_run_id=body.eval_run_id,
        )
    except CandidateRunRefused as exc:
        raise _refusal(exc.status_code, exc.code, exc.message) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/baselines")
async def list_baselines(
    task_type: str | None = None,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List active evaluation baselines."""
    query = select(AIEvalBaseline).where(AIEvalBaseline.is_active.is_(True)).order_by(AIEvalBaseline.created_at.desc())
    if task_type:
        query = query.where(AIEvalBaseline.task_type == task_type)
    result = await db.execute(query)
    baselines = result.scalars().all()
    return [
        {
            "id": str(b.id),
            "task_type": b.task_type,
            "agent_name": b.agent_name,
            "prompt_version": b.prompt_version,
            "model_name": b.model_name,
            "baseline_accuracy": b.baseline_accuracy,
            "baseline_precision": b.baseline_precision,
            "baseline_recall": b.baseline_recall,
            "baseline_f1": b.baseline_f1,
            "min_accuracy": b.min_accuracy,
            "min_f1": b.min_f1,
            "max_regression_pct": b.max_regression_pct,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "metric_kind": b.metric_kind or "label_agreement",
            "eval_run_id": str(b.eval_run_id) if b.eval_run_id else None,
            "dataset_id": str(b.dataset_id) if b.dataset_id else None,
        }
        for b in baselines
    ]


@router.post("/golden-datasets/seed", status_code=201)
async def seed_golden_datasets(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """
    Seed the golden reference datasets for all evaluation categories (ADMIN only).

    Creates 4 golden datasets (classification, root_cause, duplicate_detection,
    release_decision) if they don't already exist.
    """
    # One implementation, shared with the scheduled evaluation task. A second
    # copy of this loop is how the route and the schedule would drift apart.
    from app.services.eval_gate_service import ensure_golden_datasets

    created = await ensure_golden_datasets(db, created_by=current_user.id)
    await db.commit()
    return {"seeded": created, "total": len(created)}


# ── W16 AGT-13: shadow-tier pairs (the labelling queue) ─────────────────────

_SHADOW_PAIR_NOT_FOUND = ("shadow_pair_not_found", "Shadow pair not found.")


def _require_interactive_login(user: User) -> None:
    """Labelling is a person's judgement: an API key is refused whatever its role."""
    from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind  # noqa: PLC0415

    if credential_kind(user) != CREDENTIAL_KIND_JWT:
        raise _refusal(
            403,
            "interactive_login_required",
            "Labelling shadow pairs requires an interactive login; API keys are refused.",
        )


def require_shadow_pair_access():
    """Resolve ``{shadow_pair_id}`` to its pair and check the caller may reach its project.

    A missing pair and a pair of a project the caller cannot reach answer the
    SAME 404 ``shadow_pair_not_found``: a UUID-only route must not confirm that
    another project's pair exists. ``get_accessible_project_ids`` decides --
    ``None`` (an instance admin) passes, a member set must hold the pair's
    project. Returns the loaded pair, so the handler never reads it by id again.
    """
    from app.core.deps import get_current_active_user  # noqa: PLC0415

    async def _check(
        shadow_pair_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> Any:
        from app.core.deps import get_accessible_project_ids  # noqa: PLC0415
        from app.services import shadow_pair_service  # noqa: PLC0415

        pair = await shadow_pair_service.get_pair(db, shadow_pair_id)
        if pair is None:
            raise _refusal(404, *_SHADOW_PAIR_NOT_FOUND)
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None and pair.project_id not in accessible:
            raise _refusal(404, *_SHADOW_PAIR_NOT_FOUND)
        return pair

    return _check


@router.get("/shadow-pairs", response_model=ShadowPairListResponse)
async def list_shadow_pairs(
    project_id: uuid.UUID = Query(...),
    agent_id: str | None = Query(default=None, max_length=80),
    label_status: str = Query(default="pending", max_length=20),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """The project's live shadow pairs, newest first (QA lead+, interactive login only).

    The QUERY project is checked before anything is read; ``agent_id`` and
    ``label_status`` (``all`` = every status) filter INSIDE it. Outputs are
    re-sanitized; each pair's source is resolved inside its project. Writes nothing.
    """
    from app.services import shadow_pair_service  # noqa: PLC0415
    from app.services.ai_eval_vocab import SHADOW_AGENT_LABELS, SHADOW_LABEL_STATUSES  # noqa: PLC0415

    _require_interactive_login(current_user)
    await _authorize_body_project(db, current_user, project_id)
    if agent_id is not None and agent_id not in SHADOW_AGENT_LABELS:
        raise _refusal(
            422, "agent_id_invalid", "agent_id must be agent.root_cause_analysis.v1 or agent.summary.v1.",
        )
    if label_status != "all" and label_status not in SHADOW_LABEL_STATUSES:
        raise _refusal(
            422,
            "label_status_invalid",
            "label_status must be pending, human_labelled, golden_match or all.",
        )
    return await shadow_pair_service.list_pairs(
        db, project_id=project_id, agent_id=agent_id, label_status=label_status, limit=limit, offset=offset,
    )


#: The label refusals (§2.2), when the service's error carries no sentence of its own.
_LABEL_REFUSALS: dict[str, tuple[int, str]] = {
    "shadow_pair_already_labelled": (409, "This pair is already labelled ({label_status})."),
    "verdict_invalid": (422, "verdict must be incumbent, candidate, both or neither."),
    "failure_category_invalid": (
        422,
        "failure_category must be one of PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, "
        "AUTOMATION_DEFECT, FLAKY, UNKNOWN.",
    ),
    "failure_category_not_applicable": (422, "failure_category applies to root-cause pairs only."),
}


@router.post("/shadow-pairs/{shadow_pair_id}/label", response_model=ShadowPairRead)
async def label_shadow_pair(
    body: ShadowPairLabelRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    pair: Any = Depends(require_shadow_pair_access()),
    db: AsyncSession = Depends(get_db),
):
    """Label one live shadow pair with a person's verdict (QA lead+, interactive login only).

    Every check runs before anything is written; the label, ``labelled_by``
    and the activity row share ONE commit. A label is final (no relabel).
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.services import shadow_pair_service  # noqa: PLC0415
    from app.services.ai_eval_vocab import SHADOW_AGENT_LABELS  # noqa: PLC0415

    _require_interactive_login(current_user)
    if bool(getattr(current_user, "is_synthetic", False)):
        raise _refusal(403, "synthetic_account", "Synthetic accounts cannot label shadow pairs.")
    status_before = pair.label_status
    try:
        pair = await shadow_pair_service.label_pair(
            db, pair=pair, body=body, actor=current_user, now=datetime.now(timezone.utc),
        )
    except ValueError as exc:
        # ShadowPairLabelError: str() is the code; it carries its status and sentence.
        code = str(exc)
        status_code, message = _LABEL_REFUSALS.get(code, (422, "The label was refused."))
        raise _refusal(
            int(getattr(exc, "status_code", None) or status_code),
            code,
            str(getattr(exc, "message", None) or message.format(label_status=status_before)),
        ) from None
    agent_label = SHADOW_AGENT_LABELS.get(pair.agent_id, pair.agent_id)
    await record_activity(
        db,
        project_id=pair.project_id,
        event_type="ai_eval.shadow_pair_labelled",
        actor=ActorRef.from_user(current_user),
        entity_id=pair.project_id,
        entity_label=agent_label,
        context={
            "pair_id": str(pair.id),
            "agent_id": pair.agent_id,
            "verdict": (pair.label or {}).get("verdict"),
            "label_status": pair.label_status,
        },
    )
    await db.commit()
    await db.refresh(pair)
    # The pair's subject, resolved inside ITS project (as the list does); a read after the commit.
    sources = await shadow_pair_service.resolve_sources(db, project_id=pair.project_id, pairs=[pair])
    labelled_by = getattr(current_user, "full_name", None) or getattr(current_user, "username", None)
    return shadow_pair_service.serialize_pair(pair, source=sources.get(pair.id), labelled_by_name=labelled_by)
