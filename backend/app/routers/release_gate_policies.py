"""Release Gate Policy router — CRUD, publish, deactivate, simulate (ENT-02)."""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_project_access,
    require_role,
)
from app.db.postgres import get_db
from app.models.postgres import ReleaseDecision, ReleaseGatePolicy, TestRun, User, UserRole
from app.models.schemas import (
    PolicyDocument,
    PolicySimulateRequest,
    PolicySimulateResponse,
    ReleaseGatePolicyCreate,
    ReleaseGatePolicyResponse,
    ReleaseGatePolicyUpdate,
    RuleEvaluationResponse,
)

logger = logging.getLogger("routers.release_gate_policies")

router = APIRouter(prefix="/api/v1/release-gate-policies", tags=["Release Gate Policies"])


@router.get("", response_model=list[ReleaseGatePolicyResponse])
async def list_policies(
    project_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all release gate policies with optional filters."""
    if project_id is None:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return []
    query = select(ReleaseGatePolicy).order_by(ReleaseGatePolicy.created_at.desc())
    if project_id is not None:
        query = query.where(ReleaseGatePolicy.project_id == project_id)
    if is_active is not None:
        query = query.where(ReleaseGatePolicy.is_active == is_active)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/effective/{project_id}", response_model=Optional[ReleaseGatePolicyResponse])
async def get_effective_policy(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Get the resolved effective policy for a project (project → system → None)."""
    from app.services.policy_evaluator_service import resolve_effective_policy

    policy, level = await resolve_effective_policy(project_id, db)
    if policy is None:
        return None
    return policy


@router.get("/history/{project_id}", response_model=list[ReleaseGatePolicyResponse])
async def get_policy_history(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_access()),
):
    """Get all policy versions for a project scope, ordered by version desc."""
    result = await db.execute(
        select(ReleaseGatePolicy)
        .where(ReleaseGatePolicy.project_id == project_id)
        .order_by(ReleaseGatePolicy.version.desc())
    )
    return result.scalars().all()


@router.get("/system-history", response_model=list[ReleaseGatePolicyResponse])
async def get_system_policy_history(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all system default policy versions, ordered by version desc."""
    result = await db.execute(
        select(ReleaseGatePolicy)
        .where(ReleaseGatePolicy.project_id.is_(None))
        .order_by(ReleaseGatePolicy.version.desc())
    )
    return result.scalars().all()


@router.get("/{policy_id}", response_model=ReleaseGatePolicyResponse)
async def get_policy(
    policy_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single release gate policy by ID."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    return policy


@router.post("", response_model=ReleaseGatePolicyResponse, status_code=201)
async def create_policy(
    payload: ReleaseGatePolicyCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft policy (ADMIN only)."""
    # Validate the policy document
    _validate_policy_document(payload.rules)

    # Auto-increment version for this scope
    version_result = await db.execute(
        select(func.coalesce(func.max(ReleaseGatePolicy.version), 0))
        .where(ReleaseGatePolicy.project_id == payload.project_id)
    )
    next_version = (version_result.scalar() or 0) + 1

    policy = ReleaseGatePolicy(
        project_id=payload.project_id,
        version=next_version,
        name=payload.name,
        description=payload.description,
        rules=payload.rules.model_dump(),
        is_draft=True,
        is_active=False,
        created_by=current_user.id,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    logger.info("Policy created: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.patch("/{policy_id}", response_model=ReleaseGatePolicyResponse)
async def update_policy(
    policy_id: uuid.UUID,
    payload: ReleaseGatePolicyUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Update a draft policy (ADMIN only). Fails if already published."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    if not policy.is_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot edit a published policy. Create a new version instead.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "rules" in update_data and update_data["rules"] is not None:
        doc = PolicyDocument(**update_data["rules"]) if isinstance(update_data["rules"], dict) else update_data["rules"]
        _validate_policy_document(doc)
        update_data["rules"] = doc.model_dump() if hasattr(doc, "model_dump") else doc

    for field, value in update_data.items():
        setattr(policy, field, value)

    await db.commit()
    await db.refresh(policy)
    logger.info("Policy updated: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.post("/{policy_id}/publish", response_model=ReleaseGatePolicyResponse)
async def publish_policy(
    policy_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Publish a draft policy: deactivates any previous active policy for the same scope."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    if not policy.is_draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Policy is already published")

    # Deactivate any existing active policy for the same scope
    existing_active = await db.execute(
        select(ReleaseGatePolicy).where(
            ReleaseGatePolicy.project_id == policy.project_id,
            ReleaseGatePolicy.is_active == True,  # noqa: E712
            ReleaseGatePolicy.id != policy_id,
        )
    )
    for old_policy in existing_active.scalars().all():
        old_policy.is_active = False

    from datetime import datetime, timezone

    policy.is_draft = False
    policy.is_active = True
    policy.activated_by = current_user.id
    policy.activated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(policy)
    logger.info("Policy published: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.post("/{policy_id}/deactivate", response_model=ReleaseGatePolicyResponse)
async def deactivate_policy(
    policy_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a policy (falls back to system default or hardcoded)."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")

    policy.is_active = False
    await db.commit()
    await db.refresh(policy)
    logger.info("Policy deactivated: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.post("/simulate", response_model=PolicySimulateResponse)
async def simulate_policy(
    payload: PolicySimulateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Simulate a draft policy against a past run's decision data."""
    # Load the existing release decision
    result = await db.execute(
        select(ReleaseDecision).where(ReleaseDecision.test_run_id == payload.run_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No release decision found for this run. Trigger deep investigation first.",
        )

    dim_scores = decision.dimension_scores or {}
    pass_rate = 0.0

    # Get pass rate from test run or input snapshot
    run_result = await db.execute(select(TestRun).where(TestRun.id == payload.run_id))
    run = run_result.scalar_one_or_none()
    if run:
        pass_rate = run.pass_rate or 0.0
    elif decision.input_snapshot:
        pass_rate = decision.input_snapshot.get("pass_rate", 0.0)

    # Count context data from input_snapshot
    snapshot = decision.input_snapshot or {}
    context = {
        "flaky_count": snapshot.get("flaky_finding_count", 0),
        "open_defects": len(snapshot.get("open_defects_by_component", [])),
        "regression_test_count": snapshot.get("regression_test_count", 0),
    }

    # Build a mock policy object for the evaluator
    mock_policy = ReleaseGatePolicy(
        id=uuid.uuid4(),
        version=0,
        name="Simulation",
        rules=payload.policy_document.model_dump(),
        is_active=False,
        is_draft=True,
        created_by=current_user.id,
    )

    from app.services.policy_evaluator_service import evaluate_policy

    sim_result = await evaluate_policy(
        project_id=None,
        dim_scores=dim_scores,
        pass_rate=pass_rate,
        context=context,
        db=db,
        policy_override=mock_policy,
    )

    # Build diff summary
    original_rec = decision.recommendation
    original_comp = decision.composite_risk or float(decision.risk_score)
    sim_rec = sim_result.recommendation
    sim_comp = sim_result.effective_composite

    if original_rec == sim_rec:
        diff_summary = f"No change: recommendation stays {sim_rec} (composite {original_comp:.1f} → {sim_comp:.1f})"
    else:
        diff_summary = f"Would change {original_rec} → {sim_rec} (composite {original_comp:.1f} → {sim_comp:.1f})"
        # Add failing rule context
        failing_rules = [ev for ev in sim_result.rule_evaluations if not ev.passed]
        if failing_rules:
            names = ", ".join(f"'{r.rule_name}'" for r in failing_rules)
            diff_summary += f" due to rule(s): {names}"

    rule_evals = [
        RuleEvaluationResponse(
            rule_id=ev.rule_id,
            rule_name=ev.rule_name,
            rule_type=ev.rule_type,
            passed=ev.passed,
            action=ev.action,
            message=ev.message,
            actual_value=ev.actual_value,
            threshold_value=ev.threshold_value,
        )
        for ev in sim_result.rule_evaluations
    ]

    return PolicySimulateResponse(
        original_recommendation=original_rec,
        simulated_recommendation=sim_rec,
        original_composite=original_comp,
        simulated_composite=sim_comp,
        rule_evaluations=rule_evals,
        diff_summary=diff_summary,
    )


def _validate_policy_document(doc: PolicyDocument) -> None:
    """Validate a policy document for business rule correctness."""
    # Thresholds: go < no_go
    if doc.thresholds.go_threshold >= doc.thresholds.no_go_threshold:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="go_threshold must be less than no_go_threshold",
        )

    # Dimension weights should sum to approximately 1.0
    weights = doc.dimension_weights
    total = (
        weights.user_impact + weights.env_sensitivity + weights.reproducibility
        + weights.regression_likely + weights.hist_recurrence + weights.blast_radius
        + weights.diagnosis_conf
    )
    if abs(total - 1.0) > 0.01:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Dimension weights must sum to 1.0 (got {total:.4f})",
        )

    # Validate rule types
    valid_types = {"flaky_recurrence", "open_defect_limit", "dimension_ceiling", "override_rules"}
    for rule in doc.rules:
        if rule.type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown rule type '{rule.type}'. Valid: {', '.join(sorted(valid_types))}",
            )

        # Validate dimension_ceiling params
        if rule.type == "dimension_ceiling":
            dim = rule.params.get("dimension")
            valid_dims = {
                "user_impact", "env_sensitivity", "reproducibility",
                "regression_likely", "hist_recurrence", "blast_radius", "diagnosis_conf",
            }
            if dim not in valid_dims:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"dimension_ceiling: unknown dimension '{dim}'. Valid: {', '.join(sorted(valid_dims))}",
                )
