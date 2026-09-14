"""AI Evaluation Dashboard router — datasets, eval runs, quality metrics, release gate (OPS-02 + P5)."""
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
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
from app.models.schemas import (
    AIEvalDatasetCreate,
    AIEvalGateRunResponse,
    AIEvalDatasetResponse,
    AIEvalRunResponse,
    AIQualityDashboardResponse,
)

logger = logging.getLogger("routers.ai_evaluation")

router = APIRouter(prefix="/api/v1/ai-eval", tags=["AI Evaluation"])


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

    return AIQualityDashboardResponse(
        agreement=agreement,
        drift=drift,
        recent_eval_runs=recent_runs,
        model_versions=model_versions,
        feedback_summary=feedback_stats,
        label_health=label_health,
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
            detail="No feedback data available to build a dataset",
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


@router.get("/runs", response_model=list[AIEvalRunResponse])
async def list_eval_runs(
    dataset_id: uuid.UUID | None = None,
    task_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List evaluation runs with optional filters."""
    query = select(AIEvalRun).order_by(AIEvalRun.evaluated_at.desc()).limit(limit)
    if dataset_id:
        query = query.where(AIEvalRun.dataset_id == dataset_id)
    if task_type:
        query = query.where(AIEvalRun.task_type == task_type)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/runs/evaluate/{dataset_id}", response_model=AIEvalRunResponse, status_code=201)
async def run_evaluation(
    dataset_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Run evaluation against a dataset using the current active model (ADMIN only)."""
    import time

    from app.services.ai_eval_service import compute_metrics_for_task_type

    result = await db.execute(select(AIEvalDataset).where(AIEvalDataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")

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


class SetBaselineRequest(BaseModel):
    task_type: str = "classification"
    agent_name: str = "AnalysisAgent"
    prompt_version: str = "v1"
    model_name: Optional[str] = None
    dataset_id: Optional[str] = None
    min_accuracy: float = 0.80
    min_f1: float = 0.75
    max_regression_pct: float = 5.0


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
    from app.services.eval_gate_service import evaluate_pre_release_gate

    return await evaluate_pre_release_gate(
        db,
        task_type=body.task_type,
        agent_name=body.agent_name,
        dataset_id=body.dataset_id,
    )


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


@router.get("/agent-stack-release-gate/runs", response_model=list[AIEvalGateRunResponse])
async def list_agent_stack_gate_runs(
    change_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """List historical agent-stack release gate decisions."""
    query = select(AIEvalGateRun).order_by(AIEvalGateRun.evaluated_at.desc()).limit(limit)
    if change_id:
        query = query.where(AIEvalGateRun.change_id == change_id)
    if status_filter:
        query = query.where(AIEvalGateRun.status == status_filter)
    result = await db.execute(query)
    return result.scalars().all()


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
    from app.services.eval_gate_service import set_baseline_from_eval

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
        )
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
