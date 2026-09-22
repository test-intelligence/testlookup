"""Analytics endpoints: flaky tests, failure clusters, coverage, defects."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
    resolve_project_scope,
)
from app.core.release_filter import release_predicate as _release_predicate
from app.db.postgres import get_db
from app.models.postgres import (
    FlakyClassifierCalibration,
    FlakyScore,
    SystemicFlakeCluster,
    SystemicFlakeClusterMember,
    TestCase,
    TestRun,
    User,
    UserRole,
)
from app.models.schemas import (
    ClassifyUncategorizedRequest,
    ClassifyUncategorizedResponse,
    DefectIntakeRequest,
    DefectIntakeResponse,
    NotifyTestOwnerRequest,
    NotifyTestOwnerResponse,
)
from app.services import analytics_service, chart_data_service
from app.services.analytics_meta import build_meta, with_meta
from app.services.analytics_scope import (
    AnalyticsScope,
    ScopePolicy,
    analytics_scope,
    effective_suite_clause,
)
from app.services.flake_load_service import get_flake_load
from app.services.flaky_suppression_gate import decide as gate_decide
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

_EMPTY_LIST = {"items": [], "period_days": 0, "total": 0}

# VIZ-201: one scope resolver for every route that filters by release or
# suite. ``project_id`` is single-valued (403 for a project the caller cannot
# read, via ``resolve_project_scope``); ``release_id`` and ``suite_name``
# repeat, OR within a dimension and AND across; EVERY release id is authorised
# (403/404) before any query runs. With one value each, the SQL is exactly what
# these routes ran before -- ``tests/integration/test_analytics_scope_postgres.py``.
#
# VIZ-204: every response below carries ``meta`` (``analytics_meta``), built
# from the same resolved scope, so the envelope states what was applied.
_WINDOWED = ScopePolicy(default_days=30, max_days=365)
# VIZ-202: flaky scores take suites too -- as membership, like releases.
_FLAKY_SCORES = ScopePolicy(default_days=None, project_required=True)
# VIZ-202: clusters are per project (a required id, as before) and have no
# window; release and suite select the members that ran in scope.
_CLUSTERS = ScopePolicy(default_days=None, project_required=True)
_AI_SUMMARY = ScopePolicy(default_days=30, max_days=365)
# The defect list has no window (open defects of any age).
_DEFECTS = ScopePolicy(default_days=None)


def _membership_note(scope: AnalyticsScope, subject: str) -> dict:
    """The ``scope`` block of a membership-filtered list (flaky scores,
    systemic clusters): which tests are listed is filtered, the statistic is
    not. Stated in the payload because the number is what gets read."""
    dims = [name for name, on in (("release", scope.release_ids), ("suite", scope.suite_names)) if on]
    return {
        "membership": "+".join(dims) if dims else "project",
        "note": (
            f"{subject} are computed project-wide and are NOT recomputed per "
            f"{' or '.join(dims)}: the filter selects which tests ran in scope, "
            "not how they behaved there."
        ) if dims else None,
    }


def _ran_in_scope(project_id, scope: AnalyticsScope):
    """Fingerprints with at least one execution in the scope's releases and
    suites (effective suite), in ``project_id`` -- the membership filter."""
    stmt = (
        select(TestCase.test_fingerprint)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestRun.project_id == project_id, *_release_predicate(scope.release_arg))
    )
    in_suite = effective_suite_clause(scope.suite_arg)
    if in_suite is not None:
        stmt = stmt.where(in_suite)
    return stmt


# ── Flaky Test Leaderboard ─────────────────────────────────────────────────

@router.get("/flaky-tests")
@analytics_error_contract
@analytics_read(namespace="flaky_tests")
async def flaky_tests(
    limit: int = Query(20, ge=1, le=100),
    scope: AnalyticsScope = Depends(analytics_scope(_WINDOWED)),
    db: AsyncSession = Depends(get_db),
):
    """Return tests with highest flakiness rate (intermittent pass/fail pattern)."""
    # Both project slots reach the service so the raw-SQL ``_tenant_filter``
    # applies the right ``=`` or ``IN (...)`` clause as defence-in-depth.
    payload = await analytics_service.flaky_tests(
        db,
        scope.project,
        scope.window_days,
        limit,
        suite_name=scope.suite_arg,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
    )
    # ``limit`` is a requested top-N, not a cap on the data: not truncated.
    return with_meta(payload, await build_meta(db, scope))


@router.get("/flake-load")
async def flake_load(
    project_id: str = Query(
        ..., description="Project to measure — load is never a fleet average",
    ),
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """What share of recent runs carried at least one retried test?

    This is deliberately a **load**, not a debt burndown. Flaky-test insertion
    rate tracks the fix rate even under sustained investment, so a "remaining"
    count trending to zero is a promise that will never be kept — it will sit
    near a floor forever and teach users the tool is broken rather than that
    the target was wrong. A load has no implied zero: it is read against a
    budget the team chooses, like an error budget.

    Returns ``flake_load: null`` with a reason below the minimum run count,
    since a share computed from three runs is noise wearing a percentage sign.
    """
    scoped, _allowed = await resolve_project_scope(db, current_user, project_id)
    if scoped is None:
        # Unreachable in practice: with a required project_id,
        # resolve_project_scope either returns the parsed UUID, 400s on a
        # malformed one, or 403s on a project the caller cannot see. Kept
        # because the alternative — passing None into a project-scoped query —
        # would silently widen it to every project.
        raise HTTPException(status_code=400, detail="Invalid project ID")
    return await get_flake_load(db, scoped, window_days=days)


@router.get("/flaky-scores")
@analytics_error_contract
@analytics_read(namespace="flaky_scores")
async def flaky_scores(
    limit: int = Query(50, ge=1, le=200),
    # ``project_id`` is required (never a fleet average). ``release_id`` (S4b,
    # repeatable since VIZ-201) selects WHICH flaky tests ran in those
    # releases. It does NOT rescope the score -- see ``scope`` in the response.
    scope: AnalyticsScope = Depends(analytics_scope(_FLAKY_SCORES)),
    db: AsyncSession = Depends(get_db),
):
    """Continuous 0–1 flakiness scores for this project, most flaky first.

    Each row carries its **component breakdown and the weights used**, so the
    number can be decomposed and recomputed — a score nobody can audit is one
    users are asked to trust on faith.

    ``confidence`` is separate from ``score`` on purpose. A test seen 5 times
    and one seen 500 can both produce 0.5; collapsing that distinction is how a
    thin-history guess starts looking like a measurement. Fingerprints below the
    evidence floor are not scored at all and simply do not appear here.

    The response also carries this project's **suppression decision**: measured
    classifier specificity swings from 100% to no-better-than-random across
    projects, so how much authority a flaky verdict carries is a per-project
    question. It is never "may act" — see the ``policy`` field.
    """
    scoped = scope.project_id
    if scoped is None:  # unreachable: the policy requires a project
        raise HTTPException(status_code=400, detail="Invalid project ID")
    release_id = scope.release_arg

    stmt = (
        select(FlakyScore)
        .where(FlakyScore.project_id == scoped)
        .order_by(FlakyScore.score.desc())
    )
    if release_id is not None or scope.suite_names:
        # INTERSECTION, not a rescope. flaky_score is a rolling-window
        # statistic keyed on (project_id, test_fingerprint) — it has no release
        # dimension and deliberately gains none: the service refuses to emit a
        # score below 5 observations of the same test and only calls confidence
        # "high" at 20+, thresholds a single release frequently cannot reach.
        # Recomputing per release would multiply the table while most rows
        # reported no score at all.
        #
        # So the filter selects which already-scored tests actually ran in the
        # release, leaving each score on its full evidence base.
        #
        # The Unattributed bucket arrives as the literal sentinel rather than a
        # UUID, so parsing it unconditionally raised ValueError -> 500. The one
        # shared predicate handles it (and, since VIZ-201, several releases).
        # VIZ-202: a suite filter selects the same way -- which scored tests
        # ran in the suite (effective suite), score unchanged.
        stmt = stmt.where(FlakyScore.test_fingerprint.in_(_ran_in_scope(scoped, scope)))

    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    # How many scored tests matched, so a list cut at ``limit`` says so.
    matched = int(
        (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())))
        .scalar() or 0
    )

    calibration = (
        await db.execute(
            select(FlakyClassifierCalibration).where(
                FlakyClassifierCalibration.project_id == scoped
            )
        )
    ).scalar_one_or_none()

    decision = gate_decide(
        scoped,
        specificity=getattr(calibration, "specificity", None),
        sample_count=getattr(calibration, "sample_count", 0) or 0,
    )

    # Scores are stored by the scoring job, not derived from the window's
    # runs, so an empty list is a measured answer (``measured=True``).
    meta = await build_meta(
        db, scope, truncated=matched > len(rows), truncated_total=matched, measured=True,
    )
    return {
        "meta": meta,
        "items": [
            {
                "test_fingerprint": row.test_fingerprint,
                "test_name": row.test_name,
                "score": row.score,
                "components": row.components,
                "weights": row.weights,
                "observation_count": row.observation_count,
                "confidence": row.confidence,
                "computed_at": row.computed_at,
            }
            for row in rows
        ],
        "total": len(rows),
        "suppression": decision.to_dict(),
        # A filtered list LOOKS release-scoped, and here only half of it is:
        # membership is release-scoped, the score is not. Saying so in the
        # payload rather than only in the docs, because the number is what
        # gets read — a reader who takes `score` as "how flaky during 2.4.0"
        # would be wrong, and nothing in a bare filtered list would tell them.
        "scope": {
            "membership": _membership_note(scope, "Scores")["membership"],
            "score": "project_window",
            "release_id": list(release_id) if isinstance(release_id, tuple) else release_id,
            "note": (
                "Scores are computed project-wide over the scoring window and "
                "are NOT recomputed per release: a single release rarely "
                "reaches the evidence floor a score needs. A release filter "
                "selects which already-scored tests ran in that release, not "
                "how flaky they were during it."
            ) if release_id and not scope.suite_names else (
                _membership_note(scope, "Scores")["note"]
            ),
        },
    }


@router.get("/systemic-clusters")
@analytics_error_contract
@analytics_read(namespace="systemic_clusters")
async def systemic_clusters(
    scope: AnalyticsScope = Depends(analytics_scope(_CLUSTERS)),
    db: AsyncSession = Depends(get_db),
):
    """Tests that fail TOGETHER across runs, with the shared cause named.

    Most flaky failures are systemic rather than independent, so the useful
    triage unit is the cluster: "these 14 tests flip together and it smells
    like an external dependency" is one investigation where 14 individual
    flags are 14.

    **An empty list is a normal, frequent answer.** In the source study only 10
    of 22 projects containing flaky tests contained any cluster at all. Callers
    must render "no clusters" as a real result — never lower the bar until
    something appears, and never present a weak grouping as a cluster, which
    would send someone hunting a pattern that is not there.

    ``cause_family`` may be ``unknown``: a cluster is still actionable without
    a named cause, and inventing one would be worse than admitting we cannot
    tell from the failure text.

    VIZ-202: ``release_id`` / ``suite_name`` (repeatable) select the MEMBERS
    that ran in scope -- a member is kept when it has an execution in one of
    the releases and suites -- and a cluster with no member left is dropped.
    The cluster's own statistics (size, cohesion, co-failure runs) are
    project-level and are not recomputed; ``scope`` in the response says so.
    Clusters have no time window (``meta.ignored_filters``).
    """
    scoped = scope.project_id
    if scoped is None:  # unreachable: the policy requires a project
        raise HTTPException(status_code=400, detail="Invalid project ID")
    filtered = bool(scope.release_ids or scope.suite_names)

    clusters = list(
        (
            await db.execute(
                select(SystemicFlakeCluster)
                .where(SystemicFlakeCluster.project_id == scoped)
                .order_by(SystemicFlakeCluster.size.desc())
            )
        ).scalars().all()
    )
    members_by_cluster: dict = {}
    if clusters:
        member_stmt = select(SystemicFlakeClusterMember).where(
            SystemicFlakeClusterMember.cluster_id.in_([c.id for c in clusters])
        )
        if filtered:
            member_stmt = member_stmt.where(
                SystemicFlakeClusterMember.test_fingerprint.in_(_ran_in_scope(scoped, scope))
            )
        rows = (await db.execute(member_stmt)).scalars().all()
        for member in rows:
            members_by_cluster.setdefault(member.cluster_id, []).append(member)
    if filtered:
        clusters = [c for c in clusters if members_by_cluster.get(c.id)]

    payload = {
        "items": [
            {
                "cluster_key": cluster.cluster_key,
                "label": cluster.label,
                "cause_family": cluster.cause_family,
                "size": cluster.size,
                "cohesion": cluster.cohesion,
                "co_failure_runs": cluster.co_failure_runs,
                "window_days": cluster.window_days,
                "computed_at": cluster.computed_at,
                "members": [
                    {
                        "test_fingerprint": m.test_fingerprint,
                        "test_name": m.test_name,
                        "failure_runs": m.failure_runs,
                    }
                    for m in members_by_cluster.get(cluster.id, [])
                ],
            }
            for cluster in clusters
        ],
        "total": len(clusters),
        # Stated so an empty list is not read as a bug or a missing feature.
        "empty_is_normal": (
            "Most projects have no systemic clusters. An empty list means no "
            "group of tests met the co-failure cohesion bar, not that "
            "clustering failed."
        ),
    }
    if filtered:
        # Only when a filter is applied: the unfiltered body is unchanged.
        payload["scope"] = {
            **_membership_note(scope, "Cluster statistics"),
            "cluster_stats": "project",
        }
    return with_meta(payload, await build_meta(db, scope, measured=True))


# ── Failure Category Distribution ─────────────────────────────────────────

@router.get("/failure-categories")
@analytics_error_contract
@analytics_read(namespace="failure_categories")
async def failure_categories(
    scope: AnalyticsScope = Depends(analytics_scope(_WINDOWED)),
    db: AsyncSession = Depends(get_db),
):
    """Return distribution of failure categories for AI-analysed test cases."""
    payload = await analytics_service.failure_categories(
        db,
        scope.project,
        scope.window_days,
        suite_name=scope.suite_arg,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
    )
    return with_meta(payload, await build_meta(db, scope))


# ── Top Failing Tests ──────────────────────────────────────────────────────

@router.get("/top-failing")
@analytics_error_contract
@analytics_read(namespace="top_failing")
async def top_failing_tests(
    limit: int = Query(15, ge=1, le=50),
    scope: AnalyticsScope = Depends(analytics_scope(_WINDOWED)),
    db: AsyncSession = Depends(get_db),
):
    """Return tests with the highest total failure count in the period."""
    payload = await analytics_service.top_failing_tests(
        db,
        scope.project,
        scope.window_days,
        limit,
        suite_name=scope.suite_arg,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
    )
    # ``limit`` is a requested top-N, not a cap on the data: not truncated.
    return with_meta(payload, await build_meta(db, scope))


# ── Failure-kind evidence checklist (AI-4) ─────────────────────────────────

@router.get("/kind-evidence")
async def kind_evidence(
    project_id: str | None = None,
    test_fingerprint: str | None = Query(None, min_length=1),
    test_case_id: str | None = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Evidence-checklist record backing the AI-classified failure kind
    (AI-4) — for the kind-badge popovers. Two lookup modes:

    * ``project_id`` + ``test_fingerprint`` (the /failures aggregates carry
      fingerprints): resolves the fingerprint's most recent analyzed failure
      in the project.
    * ``test_case_id`` (run-detail rows carry the id directly).

    Uses the stored ``routing_metadata.kind_evidence`` blob when the
    pipeline persisted one, computing on demand otherwise (no backfill).
    Returns ``{found: false}`` when no analyzed failure matches — the UI
    renders the plain badge then.
    """
    import uuid as _uuid

    from app.models.postgres import AIAnalysis, TestCase, TestRun, TestStatus
    from app.services.kind_evidence import compute_kind_evidence_for_test_case

    if test_case_id:
        try:
            tc_uuid = _uuid.UUID(test_case_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid test_case_id format")
        # Tenant isolation: the test case's project must be accessible.
        owner_project = (
            await db.execute(
                select(TestRun.project_id)
                .join(TestCase, TestCase.test_run_id == TestRun.id)
                .where(TestCase.id == tc_uuid)
            )
        ).scalar_one_or_none()
        if owner_project is None:
            return {"found": False, "test_case_id": None, "kind_evidence": None}
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None and owner_project not in accessible:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project",
            )
        evidence = await compute_kind_evidence_for_test_case(db, tc_uuid)
        return {
            "found": evidence is not None,
            "test_case_id": str(tc_uuid),
            "kind_evidence": evidence,
        }

    if not test_fingerprint:
        raise HTTPException(
            status_code=422,
            detail="Provide test_case_id, or project_id + test_fingerprint",
        )

    # Pins + 403s exactly like the sibling endpoints; a definite project id
    # is required because fingerprints are only unique per tenant.
    scoped, _allowed = await resolve_project_scope(db, current_user, project_id)
    if scoped is None:
        raise HTTPException(status_code=422, detail="project_id is required")

    tc_id = (
        await db.execute(
            select(TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
            .where(
                TestRun.project_id == _uuid.UUID(str(scoped)),
                TestCase.test_fingerprint == test_fingerprint,
                TestCase.status.in_([TestStatus.FAILED.value, TestStatus.BROKEN.value]),
            )
            .order_by(TestCase.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if tc_id is None:
        return {"found": False, "test_case_id": None, "kind_evidence": None}

    evidence = await compute_kind_evidence_for_test_case(db, tc_id)
    return {
        "found": evidence is not None,
        "test_case_id": str(tc_id),
        "kind_evidence": evidence,
    }


# ── Coverage Snapshot ──────────────────────────────────────────────────────

@router.get("/coverage")
@analytics_error_contract
@analytics_read(namespace="coverage")
async def coverage_stats(
    scope: AnalyticsScope = Depends(analytics_scope(_WINDOWED)),
    db: AsyncSession = Depends(get_db),
):
    """Return test suite coverage stats aggregated over the period."""
    payload = await analytics_service.coverage_stats(
        db,
        scope.project,
        scope.window_days,
        suite_name=scope.suite_arg,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
    )
    # The per-suite table is capped (``LIMIT 50``); ``summary.suite_count``
    # counts every suite by the same grouping expression, so the cut is known.
    suite_count = int((payload.get("summary") or {}).get("suite_count") or 0)
    shown = len(payload.get("suites") or [])
    return with_meta(payload, await build_meta(
        db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        truncated=suite_count > shown, truncated_total=suite_count,
    ))


# ── Suite Detail ───────────────────────────────────────────────────────────

@router.get("/suite-detail")
@analytics_error_contract
@analytics_read(namespace="suite_detail")
async def suite_detail(
    scope: AnalyticsScope = Depends(analytics_scope(_WINDOWED)),
    db: AsyncSession = Depends(get_db),
):
    """
    Return detailed breakdown for a test suite (several ``suite_name`` values
    return their union, and ``suite_name`` in the response is then the list):
      - Summary KPIs (unique tests, executions, pass rate, avg duration)
      - Per-test-case aggregates with flakiness flag
      - Last 10 test runs that included this suite
    No ``suite_name`` matches nothing (``suite_name: ""``), as before.
    """
    suite = scope.suite_arg
    payload = await analytics_service.suite_detail(
        db,
        scope.project,
        suite if suite is not None else "",
        scope.window_days,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
    )
    return with_meta(payload, await build_meta(
        db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
    ))


# ── Defects List ───────────────────────────────────────────────────────────

@router.get("/defects")
@analytics_error_contract
async def list_defects(
    resolution_status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    scope: AnalyticsScope = Depends(analytics_scope(_DEFECTS)),
    db: AsyncSession = Depends(get_db),
):
    """Return defects for a project with optional resolution status filter.

    VIZ-202: ``release_id`` / ``suite_name`` (repeatable) keep the defects
    whose originating execution ran in one of the releases (the run's primary
    release) and suites (effective suite). A defect with no linked execution
    cannot be placed and is left out while either filter is set. The list has
    no time window (``meta.ignored_filters``): open defects of any age.
    """
    payload = await analytics_service.list_defects(
        db,
        scope.project,
        resolution_status,
        page,
        size,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
        suite_name=scope.suite_arg,
    )
    # Paged, not truncated: ``total`` and ``pages`` state the whole list.
    return with_meta(payload, await build_meta(db, scope, measured=True))


@router.post(
    "/defects",
    response_model=DefectIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.QA_ENGINEER))],
)
async def create_defect(
    payload: DefectIntakeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Manual Defect Intake. Creates an OPEN defect for the project,
    best-effort attaching to the most-recent matching TestCase when
    ``test_name`` (and optionally ``suite_name``) are supplied.
    """
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and payload.project_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )
    body = payload.model_dump()  # model_dump() already coerces Enum → its .value string
    # ``create_manual_defect`` flushes so id + server defaults (created_at) are
    # populated on the row. VIZ-212: commit HERE rather than leaving it to the
    # ``get_db`` teardown — that commit runs after the handler returns, so no
    # bump placed in the handler could follow it, and the cached dashboard
    # (open / open-critical defect counts) would stay stale until TTL.
    # ``expire_on_commit=False`` keeps the defect usable without a refresh.
    defect = await analytics_service.create_manual_defect(db, payload.project_id, body)
    await db.commit()
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(payload.project_id)
    raw_category = getattr(defect.failure_category, "value", defect.failure_category)
    return DefectIntakeResponse(
        id=defect.id,
        project_id=defect.project_id,
        title=defect.title or payload.title,
        severity=payload.severity,
        failure_category=raw_category if isinstance(raw_category, str) else None,
        component=defect.component,
        test_name=payload.test_name,
        suite_name=payload.suite_name,
        jira_ticket_id=defect.jira_ticket_id,
        jira_ticket_url=defect.jira_ticket_url,
        resolution_status=defect.resolution_status,
        ai_confidence_score=defect.ai_confidence_score,
        created_at=defect.created_at,
    )


# ── AI Analysis Summary ────────────────────────────────────────────────────

@router.get("/ai-summary")
@analytics_error_contract
async def ai_analysis_summary(
    scope: AnalyticsScope = Depends(analytics_scope(_AI_SUMMARY)),
    db: AsyncSession = Depends(get_db),
):
    """Return summary of AI analysis results for the project.

    VIZ-202: ``release_id`` / ``suite_name`` (repeatable) count the analyses
    of executions in those releases (run's primary release) and suites
    (effective suite).
    """
    payload = await analytics_service.ai_analysis_summary(
        db,
        scope.project,
        scope.window_days,
        allowed_project_ids=scope.allowed_project_ids,
        release_id=scope.release_arg,
        suite_name=scope.suite_arg,
    )
    return with_meta(payload, await build_meta(db, scope))


# ── Notify suite owner about a recurring failure ───────────────────────────


@router.post("/notify-owner", response_model=NotifyTestOwnerResponse)
async def notify_suite_owner(
    payload: NotifyTestOwnerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Fire an email at the suite owner of ``payload.test_name`` so they can
    triage the recurring failure. Resolution chain mirrors the suite-owner
    review feature: explicit ``test_suite_owners`` row → ``Project.manager_user_id``.
    Returns ``{queued: false, reason}`` when no owner can be resolved instead
    of erroring, so the UI can show a clear actionable message.
    """
    # Tenant isolation: non-admin callers must be members of the project.
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and payload.project_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )

    from app.models.postgres import Project
    from app.services.suite_review_service import resolve_test_to_suite_owner

    resolution = await resolve_test_to_suite_owner(
        db,
        payload.project_id,
        payload.test_name,
        payload.days,
    )
    if resolution["latest_run_id"] is None:
        return NotifyTestOwnerResponse(
            queued=False,
            reason=(
                f"\"{payload.test_name}\" has no failing runs in the last "
                f"{payload.days} days — nothing to notify on."
            ),
        )

    owner: User | None = resolution["owner"]
    if owner is None or not owner.email:
        return NotifyTestOwnerResponse(
            queued=False,
            suite_name=resolution["suite_name"],
            reason=(
                "No suite owner is configured for this test, and the project "
                "doesn't have a fallback project manager set. Assign an owner "
                "from /test-management → Test Suites or set a project manager."
            ),
        )

    project = (
        await db.execute(select(Project).where(Project.id == payload.project_id))
    ).scalar_one_or_none()

    from app.worker.tasks import notify_test_suite_owner as _task

    _task.delay(
        to_email=owner.email,
        owner_name=owner.full_name or owner.username,
        test_name=payload.test_name,
        suite_name=resolution["suite_name"],
        fail_count=payload.fail_count,
        days=payload.days,
        project_id=str(payload.project_id),
        project_name=project.name if project else None,
        latest_run_id=(
            str(resolution["latest_run_id"]) if resolution["latest_run_id"] else None
        ),
        latest_run_build=resolution["latest_run_build"],
        is_fallback_owner=resolution["is_fallback"],
        triggered_by=current_user.full_name or current_user.username or current_user.email,
    )

    return NotifyTestOwnerResponse(
        queued=True,
        sent_to=owner.email,
        owner_name=owner.full_name or owner.username,
        suite_name=resolution["suite_name"],
        is_fallback_owner=resolution["is_fallback"],
    )


# ── Bulk classify uncategorized failures ──────────────────────────────────


@router.post("/classify-uncategorized", response_model=ClassifyUncategorizedResponse)
async def classify_uncategorized_failures(
    payload: ClassifyUncategorizedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Assign ``payload.category`` to every failing test case in the project's
    window that's currently unlabelled (``failure_category IS NULL`` or
    ``UNKNOWN``). Mirrors ``AIAnalysis.failure_category`` for any AI rows
    backing those test cases.

    Used by the Failures page "Classify" CTA when the AI classifier left a
    large chunk of failures uncategorized — lets the user tag them all in
    one shot rather than per-test."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.models.postgres import (
        AIAnalysis,
        FailureCategory,
        TestCase,
        TestRun,
        TestStatus,
    )

    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and payload.project_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, payload.days))

    # Collect the test_case ids in scope — failing/broken cases without a
    # confident category, scoped to the user's project+window (+ optional
    # suite). Done as a SELECT so we can mirror the update onto AIAnalysis.
    select_stmt = (
        select(TestCase.id)
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(
            TestRun.project_id == payload.project_id,
            TestRun.created_at >= cutoff,
            TestCase.status.in_([TestStatus.FAILED.value, TestStatus.BROKEN.value]),
            or_(
                TestCase.failure_category.is_(None),
                TestCase.failure_category == FailureCategory.UNKNOWN.value,
            ),
        )
    )
    if payload.suite_name:
        select_stmt = select_stmt.where(TestCase.suite_name == payload.suite_name)

    tc_ids = [row[0] for row in (await db.execute(select_stmt)).all()]
    if not tc_ids:
        return ClassifyUncategorizedResponse(
            updated=0,
            category=payload.category.value,
            project_id=payload.project_id,
            days=payload.days,
            suite_name=payload.suite_name,
        )

    new_category = payload.category.value
    await db.execute(
        update(TestCase)
        .where(TestCase.id.in_(tc_ids))
        .values(failure_category=new_category)
    )
    await db.execute(
        update(AIAnalysis)
        .where(AIAnalysis.test_case_id.in_(tc_ids))
        .values(failure_category=new_category)
    )
    # VIZ-212: commit explicitly (not in the get_db teardown, which runs after
    # the handler returns) so the epoch bump can follow the commit.
    await db.commit()
    from app.services.cache_service import bump_analytics_epoch

    await bump_analytics_epoch(payload.project_id)

    return ClassifyUncategorizedResponse(
        updated=len(tc_ids),
        category=new_category,
        project_id=payload.project_id,
        days=payload.days,
        suite_name=payload.suite_name,
    )


# ── Chart data (VIZ-203) ───────────────────────────────────────────────────
#
# One guarded endpoint for "a metric, grouped by up to two things", so a new
# chart does not need a new route. Everything about the numbers lives in
# ``chart_data_service``; this handler only names the policy, hands the
# validated spec over, and lifts the chart's truncation and definitions into
# the VIZ-204 envelope.
#
# Caching, ETag, the per-user rate limit and the statement timeout are
# VIZ-209's ``analytics_read`` (``core/analytics_read_layer.py``), applied
# below: this route is a pure GET whose whole identity is its route, its query
# string, the caller's project horizon and the project's analytics epoch, which
# is exactly what that layer hashes. Its per-principal limit is the one
# ``RATE_LIMITED_ROUTES`` already named for this path.
_CHART_DATA = ScopePolicy(default_days=30, max_days=365)


def _chart_data_identity(resolved: dict) -> tuple:
    """This route's cache identity, from the RESOLVED request.

    VIZ-209 keys on what it is given here rather than on the query string,
    because the query string is not the question: ``group_by`` is
    order-significant (first dimension is the x axis, second keys the series),
    and a key built from sorted values made ``?group_by=status&group_by=env``
    and its reverse -- transposes of each other -- one entry and one ETag.
    ``cache_identity_parts`` is the seam ``chart_data_service`` owns for
    exactly this; the spec it is given is the same one the handler builds
    below, from the same validated arguments, so the two cannot drift.
    """
    # Subscripted, not ``.get``: every one of these is a parameter the route
    # declares, so FastAPI has already bound it (to its default if absent). A
    # KeyError here would mean the signature and this hook had drifted apart,
    # which is exactly the thing that must not fail quietly.
    scope: AnalyticsScope = resolved["scope"]
    spec = chart_data_service.parse_chart_spec(
        resolved["metric"], resolved["group_by"], resolved["top_n"], scope=scope,
    )
    return chart_data_service.cache_identity_parts(scope, spec)


@router.get("/chart-data")
@analytics_error_contract
@analytics_read(namespace="chart_data", identity=_chart_data_identity)
async def chart_data(
    metric: str = Query(
        "executions",
        description=(
            "What to measure. One of: "
            + ", ".join(sorted(chart_data_service.METRICS))
        ),
    ),
    group_by: Optional[list[str]] = Query(
        None,
        description=(
            "The axis, and optionally a second dimension to key the series by "
            "(at most two, the time dimension first). One of: "
            + ", ".join(sorted(chart_data_service.DIMENSIONS))
        ),
    ),
    top_n: Optional[int] = Query(
        None,
        description=(
            "Keep the largest N keys and merge the rest into an 'other' "
            "bucket. Required for group_by=test outside a single suite."
        ),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_CHART_DATA)),
    db: AsyncSession = Depends(get_db),
):
    """A metric over up to two dimensions, as contract C3 ``chart_series``.

    Series are keyed by the second ``group_by`` and zero-filled over the first,
    so the table view and CSV export need no transformation. Each point carries
    ``y`` and ``n`` (the sample behind it); a rate with nothing evaluated is
    ``y: null`` with ``measured: false`` and a reason, never 0.
    """
    spec = chart_data_service.parse_chart_spec(metric, group_by, top_n, scope=scope)
    # ONE clock for the whole request. The service took its own, the statement
    # took another and this line took a third; three ``datetime.now()`` calls
    # can straddle UTC midnight, and the answer is then an axis whose last
    # bucket is outside the window the SQL bounded.
    now = chart_data_service.request_clock()
    payload = await chart_data_service.build_chart_data(db, scope, spec, now=now)
    definitions = payload.pop("definitions")
    envelope = {key: payload.pop(key) for key in chart_data_service.ENVELOPE_KEYS}
    meta = await build_meta(
        db,
        scope,
        pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        window_start=chart_data_service.window_start(scope.window_days, now=now),
        truncated=bool(envelope["truncated"]),
        truncated_total=envelope["truncated_total"],
    )
    meta["definitions"] = definitions
    # Per axis, because ``truncated_total`` is one number and a chart can lose
    # buckets AND series at once; and the runs whose bucket fell outside the
    # generated axis, which used to disappear without a word.
    if envelope["truncated_axes"]:
        meta["truncated_axes"] = envelope["truncated_axes"]
    if envelope["outside_window"]:
        meta["outside_window"] = envelope["outside_window"]
    return with_meta(payload, meta)
