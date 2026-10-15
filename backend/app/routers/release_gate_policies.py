"""Release Gate Policy router — CRUD, publish, deactivate, simulate (ENT-02)."""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    _api_key_bound_project,
    _enforce_api_key_project_binding,
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

#: What a project-bound API key is told when it tries to write the system default.
SYSTEM_DEFAULT_REFUSED_DETAIL = (
    "This API key is bound to one project; the system-default policy applies "
    "to every project without a policy of its own, so only an instance "
    "administrator may change it"
)

#: GATE-03: what the loser of a concurrent publish in one scope is told.
PUBLISH_RACE_DETAIL = (
    "Another policy was published for this scope at the same moment. Reload "
    "the policies and publish again if you still want this version."
)
#: GATE-03: what the loser of a concurrent draft create in one scope is told.
CREATE_RACE_DETAIL = (
    "Another policy version was created for this scope at the same moment. Try again."
)
ALREADY_PUBLISHED_DETAIL = "Policy is already published"

#: The indexes whose violation is a scope race (migration 0210, plus the
#: project-scope version constraint of 0032). Any other IntegrityError is a
#: real bug and is re-raised.
_SCOPE_RACE_INDEX_PREFIX = "uq_rgp_"


def _scope_lock_key(project_id: Optional[uuid.UUID]) -> str:
    return "release_gate_policy:" + (str(project_id) if project_id else "system")


async def _lock_scope(db: AsyncSession, project_id: Optional[uuid.UUID]) -> None:
    """Serialise policy writes within ONE scope (a project, or the system default).

    A transaction-scoped advisory lock on the request session (pattern
    ``services/agent_authority_lock.py``): released by the router's commit or
    rollback, never held across requests. Two scopes never wait on each other.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _scope_lock_key(project_id)},
    )


def _is_scope_race(exc: IntegrityError) -> bool:
    """True when the violated constraint is one of the policy-scope indexes."""
    return _SCOPE_RACE_INDEX_PREFIX in str(getattr(exc, "orig", None) or exc)


async def _active_ids_in_scope(
    db: AsyncSession, project_id: Optional[uuid.UUID], exclude: uuid.UUID
) -> set[uuid.UUID]:
    rows = await db.execute(
        select(ReleaseGatePolicy.id).where(
            # ``== None`` renders ``IS NULL`` for the system scope.
            ReleaseGatePolicy.project_id == project_id,
            ReleaseGatePolicy.is_active == True,  # noqa: E712
            ReleaseGatePolicy.id != exclude,
        )
    )
    return set(rows.scalars().all())


async def _policy_scope_projects(
    db: AsyncSession, policy_project_id: Optional[uuid.UUID]
) -> list[uuid.UUID]:
    """Projects whose cached readiness band an active-policy change moves
    (VIZ-212): the policy's own project, or — for the system default, which
    every project without a policy of its own falls back to — every project."""
    if policy_project_id is not None:
        return [policy_project_id]
    from app.models.postgres import Project

    return list((await db.execute(select(Project.id))).scalars().all())


def _enforce_policy_binding(
    current_user: User, policy_project_id: Optional[uuid.UUID]
) -> None:
    """Confine a project-bound API key to its own project's policies (re-audit N20).

    The write routes below opt in with ``require_role(UserRole.ADMIN,
    allow_project_key=True)``, because a CI key may manage its own project's
    release gate; this is the check that makes that opt-in safe.
    ``require_project_access()`` cannot do it: it reads a PATH parameter and
    passes silently when there is none, and here the project arrives in the
    body or on the stored policy.

    ``policy_project_id=None`` is the system default, which every project
    without a policy of its own falls back to, so a bound key may never write
    it. An unbound caller (a JWT, or a user-scoped key) is not affected.
    """
    if _api_key_bound_project(current_user) is None:
        return
    if policy_project_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=SYSTEM_DEFAULT_REFUSED_DETAIL,
        )
    _enforce_api_key_project_binding(current_user, policy_project_id)


@router.get("", response_model=list[ReleaseGatePolicyResponse])
async def list_policies(
    project_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all release gate policies with optional filters."""
    # F-042: this check ran ONLY in the ``not project_id`` branch, so naming a
    # project skipped it entirely — the guard fired only where there was
    # nothing to guard. Third recurrence of the class (F-033 digests, F-040
    # chat). ``resolve_project_scope`` 403s a non-admin naming a project they
    # do not belong to; the empty return below preserves today's behaviour for
    # a non-admin who names no project at all.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    if scoped_project_id is None and allowed is not None:
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
    # Re-audit N26: this read checked nothing, so any signed-in caller (and a
    # key bound to another project) read any project's gate rules by id. A
    # project's policy needs access to that project; the system default
    # (project_id NULL) stays readable by everyone, as /system-history is.
    if policy.project_id is not None:
        from app.core.deps import resolve_project_scope  # noqa: PLC0415

        await resolve_project_scope(db, current_user, str(policy.project_id))
    return policy


@router.post("", response_model=ReleaseGatePolicyResponse, status_code=201)
async def create_policy(
    payload: ReleaseGatePolicyCreate,
    # N20: _enforce_policy_binding below confines a project-bound key.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft policy (ADMIN only).

    A project-bound API key may create drafts for its own project only, never
    for another project or the system default (re-audit N20).
    """
    _enforce_policy_binding(current_user, payload.project_id)
    if payload.project_id is not None:
        # A real scope call the authorization scan can see (code review round
        # 4): an admin passes by role, a bound key only for its own project.
        from app.core.deps import resolve_project_scope  # noqa: PLC0415

        await resolve_project_scope(db, current_user, str(payload.project_id))
    # Validate the policy document
    _validate_policy_document(payload.rules)

    # GATE-03: serialise version allocation within the scope. Without the lock
    # two concurrent creates read the same max(version) and the loser's INSERT
    # hit the unique version index as an unhandled IntegrityError (a 500).
    await _lock_scope(db, payload.project_id)

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
    try:
        await db.commit()
    except IntegrityError as exc:
        # Backstop for a writer that skipped the lock (manual SQL, an old
        # replica mid-deploy): a version collision is a 409, never a 500.
        await db.rollback()
        if not _is_scope_race(exc):
            raise
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CREATE_RACE_DETAIL)
    await db.refresh(policy)
    logger.info("Policy created: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.patch("/{policy_id}", response_model=ReleaseGatePolicyResponse)
async def update_policy(
    policy_id: uuid.UUID,
    payload: ReleaseGatePolicyUpdate,
    # N20: _enforce_policy_binding below confines a project-bound key.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Update a draft policy (ADMIN only). Fails if already published."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    _enforce_policy_binding(current_user, policy.project_id)
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
    # N20: _enforce_policy_binding below confines a project-bound key.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Publish a draft policy: deactivates any previous active policy for the same scope."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    _enforce_policy_binding(current_user, policy.project_id)
    if not policy.is_draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_PUBLISHED_DETAIL)

    # GATE-03: exactly one active policy per scope, and a clear 409 for the
    # loser of a concurrent publish rather than a silent last-writer-wins.
    #
    # ``before`` is what this admin was looking at; after the scope lock is
    # held, anything different means another publish committed in between.
    before = await _active_ids_in_scope(db, policy.project_id, policy.id)
    await _lock_scope(db, policy.project_id)
    # Re-read under the lock: the same draft published by someone else is no
    # longer a draft.
    await db.refresh(policy)
    if not policy.is_draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_PUBLISHED_DETAIL)
    current = await _active_ids_in_scope(db, policy.project_id, policy.id)
    if current != before:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PUBLISH_RACE_DETAIL)

    # Deactivate any existing active policy for the same scope
    if current:
        existing_active = await db.execute(
            select(ReleaseGatePolicy).where(ReleaseGatePolicy.id.in_(current))
        )
        for old_policy in existing_active.scalars().all():
            old_policy.is_active = False
        # The demote MUST reach the database before the promote. SQLAlchemy
        # orders persistent UPDATEs by primary key, and policy ids are uuid4, so
        # without this flush the promote is emitted first about half the time
        # and the one-active index refuses it -- even with no race at all.
        await db.flush()

    from datetime import datetime, timezone

    policy.is_draft = False
    policy.is_active = True
    policy.activated_by = current_user.id
    policy.activated_at = datetime.now(timezone.utc)

    try:
        await db.commit()
    except IntegrityError as exc:
        # Backstop for a writer that skipped the scope lock.
        await db.rollback()
        if not _is_scope_race(exc):
            raise
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PUBLISH_RACE_DETAIL)
    await db.refresh(policy)
    # VIZ-212: the cached dashboard's readiness band reads the ACTIVE policy.
    from app.services.cache_service import bump_analytics_epochs

    await bump_analytics_epochs(await _policy_scope_projects(db, policy.project_id))
    logger.info("Policy published: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.post("/{policy_id}/deactivate", response_model=ReleaseGatePolicyResponse)
async def deactivate_policy(
    policy_id: uuid.UUID,
    # N20: _enforce_policy_binding below confines a project-bound key.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a policy (falls back to system default or hardcoded)."""
    result = await db.execute(
        select(ReleaseGatePolicy).where(ReleaseGatePolicy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    _enforce_policy_binding(current_user, policy.project_id)

    policy.is_active = False
    await db.commit()
    await db.refresh(policy)
    # VIZ-212: the readiness band falls back to another policy now.
    from app.services.cache_service import bump_analytics_epochs

    await bump_analytics_epochs(await _policy_scope_projects(db, policy.project_id))
    logger.info("Policy deactivated: %s v%d by %s", policy.name, policy.version, current_user.username)
    return policy


@router.post("/simulate", response_model=PolicySimulateResponse)
async def simulate_policy(
    payload: PolicySimulateRequest,
    # N20: the run's project is checked against a project-bound key below.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Simulate a draft policy against a past run's decision data."""
    # The run's project is checked like any other scoped id: the response
    # carries the run's recommendation, composite score and rule trail. A
    # project-bound API key may simulate only against its own project's runs
    # (re-audit N20); the scope call is what the authorization scan reads
    # (code review round 4).
    run_project_id = (
        await db.execute(select(TestRun.project_id).where(TestRun.id == payload.run_id))
    ).scalar_one_or_none()
    if run_project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found")
    _enforce_api_key_project_binding(current_user, run_project_id)
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    await resolve_project_scope(db, current_user, str(run_project_id))

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
        # US-9.3 — kind breakdown frozen at decision time. Absent on
        # pre-feature snapshots, in which case kind_rules policies simulate
        # conservatively (no downgrade) and say so in the rule trail.
        "failure_kind_counts": snapshot.get("failure_kind_counts"),
        # AI-4 — per-failure kind confidences frozen at decision time.
        # Absent on pre-AI-4 snapshots, in which case confidence-floor
        # budgets conservatively excuse nothing.
        "failure_kind_confidences": snapshot.get("failure_kind_confidences"),
    }
    # EXEC-05 — the run's stored duration findings, for a perf_regression
    # rule. Absent for a run that was not evaluated: the rule then reports
    # "not measured" and never blocks.
    if run is not None and isinstance(getattr(run, "duration_findings", None), dict):
        from app.services.duration_regression_service import policy_summary

        context["duration_regressions"] = policy_summary(run.duration_findings)

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


def _validate_perf_regression_params(params: dict) -> None:
    """``perf_regression`` params (EXEC-05): counts are whole numbers >= 0,
    ``min_sigma`` >= 0, ``suite_ratio`` >= 1, ``action`` BLOCK | WARN | INFO.
    A wrong value is a 422 naming the field, not a rule that never fires."""
    def refuse(detail: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"perf_regression: {detail}",
        )

    for key in ("max_spikes", "max_suite_regressions"):
        value = params.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            refuse(f"{key} must be a whole number of 0 or more (got {value!r})")
    for key, floor in (("min_sigma", 0.0), ("suite_ratio", 1.0)):
        value = params.get(key, floor)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < floor:
            refuse(f"{key} must be a number of {floor:g} or more (got {value!r})")
    action = params.get("action", "BLOCK")
    if action not in ("BLOCK", "WARN", "INFO"):
        refuse(f"action must be BLOCK, WARN or INFO (got {action!r})")


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

    # Validate rule types. Every type here must have an evaluator (or be
    # override_rules, applied at override time): a type the editor offers and
    # this set refuses is a save that fails with a 422 the page never shows.
    valid_types = {
        "flaky_recurrence", "open_defect_limit", "dimension_ceiling", "override_rules",
        # EXEC-05 (D44): opt-in duration regression rule.
        "perf_regression",
    }
    for rule in doc.rules:
        if rule.type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown rule type '{rule.type}'. Valid: {', '.join(sorted(valid_types))}",
            )

        if rule.type == "perf_regression":
            _validate_perf_regression_params(rule.params)

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
