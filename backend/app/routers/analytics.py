"""Analytics endpoints: flaky tests, failure clusters, coverage, defects."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.release_filter import is_unattributed
from app.core.deps import (
    resolve_release_query_scope,
    get_accessible_project_ids,
    get_current_active_user,
    require_role,
    resolve_project_scope,
)
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
from app.services import analytics_service
from app.services.flake_load_service import get_flake_load
from app.services.flaky_suppression_gate import decide as gate_decide

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

_EMPTY_LIST = {"items": [], "period_days": 0, "total": 0}


# ── Flaky Test Leaderboard ─────────────────────────────────────────────────

@router.get("/flaky-tests")
async def flaky_tests(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    suite_name: str | None = Query(None, min_length=1),
    # S4a. Optional: omitting it returns byte-identical results to before
    # the release axis existed (NFR1), including issuing no extra query.
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return tests with highest flakiness rate (intermittent pass/fail pattern)."""
    # ``resolve_project_scope`` returns (pinned_uuid_or_None, allowed_ids_or_None)
    # and raises 403 when a non-admin requests a project they don't belong to.
    # Both slots get forwarded to the service so the raw-SQL ``_tenant_filter``
    # applies the right ``=`` or ``IN (...)`` clause as defence-in-depth.
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    return await analytics_service.flaky_tests(
        db,
        str(scoped) if scoped else None,
        days,
        limit,
        suite_name=suite_name,
        allowed_project_ids=allowed,
        release_id=release_id,
    )


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
async def flaky_scores(
    project_id: str = Query(..., description="Project to score — never a fleet average"),
    limit: int = Query(50, ge=1, le=200),
    # S4b. Selects WHICH flaky tests ran in this release. It does NOT rescope
    # the score — see the ``scope`` block in the response and the note below.
    release_id: str | None = Query(
        None, description="Only flaky tests that ran in this release"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    scoped, _allowed = await resolve_project_scope(db, current_user, project_id)
    if scoped is None:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    release_id = await resolve_release_query_scope(db, release_id, current_user)

    stmt = (
        select(FlakyScore)
        .where(FlakyScore.project_id == scoped)
        .order_by(FlakyScore.score.desc())
    )
    if release_id is not None:
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
        # UUID, so parsing it unconditionally raised ValueError -> 500. Every
        # other filter site guards with `is_unattributed()` first; this one was
        # missed because `/flaky-scores` has no frontend caller, so nothing
        # exercised it. One predicate, chosen here, rather than two branches
        # that could drift.
        release_predicate = (
            TestRun.primary_release_id.is_(None)
            if is_unattributed(release_id)
            else TestRun.primary_release_id == uuid.UUID(release_id)
        )
        ran_in_release = (
            select(TestCase.test_fingerprint)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestRun.project_id == scoped, release_predicate)
        )
        stmt = stmt.where(FlakyScore.test_fingerprint.in_(ran_in_release))

    rows = (await db.execute(stmt.limit(limit))).scalars().all()

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

    return {
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
            "membership": "release" if release_id else "project",
            "score": "project_window",
            "release_id": release_id,
            "note": (
                "Scores are computed project-wide over the scoring window and "
                "are NOT recomputed per release: a single release rarely "
                "reaches the evidence floor a score needs. A release filter "
                "selects which already-scored tests ran in that release, not "
                "how flaky they were during it."
            ) if release_id else None,
        },
    }


@router.get("/systemic-clusters")
async def systemic_clusters(
    project_id: str = Query(..., description="Project to read — clusters are per project"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    """
    scoped, _allowed = await resolve_project_scope(db, current_user, project_id)
    if scoped is None:
        raise HTTPException(status_code=400, detail="Invalid project ID")

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
        rows = (
            await db.execute(
                select(SystemicFlakeClusterMember).where(
                    SystemicFlakeClusterMember.cluster_id.in_([c.id for c in clusters])
                )
            )
        ).scalars().all()
        for member in rows:
            members_by_cluster.setdefault(member.cluster_id, []).append(member)

    return {
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


# ── Failure Category Distribution ─────────────────────────────────────────

@router.get("/failure-categories")
async def failure_categories(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    suite_name: str | None = Query(None, min_length=1),
    # S4a. Optional: omitting it returns byte-identical results to before
    # the release axis existed (NFR1), including issuing no extra query.
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return distribution of failure categories for AI-analysed test cases."""
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    return await analytics_service.failure_categories(
        db,
        str(scoped) if scoped else None,
        days,
        suite_name=suite_name,
        allowed_project_ids=allowed,
        release_id=release_id,
    )


# ── Top Failing Tests ──────────────────────────────────────────────────────

@router.get("/top-failing")
async def top_failing_tests(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(15, ge=1, le=50),
    suite_name: str | None = Query(None, min_length=1),
    # S4a. Optional: omitting it returns byte-identical results to before
    # the release axis existed (NFR1), including issuing no extra query.
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return tests with the highest total failure count in the period."""
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    return await analytics_service.top_failing_tests(
        db,
        str(scoped) if scoped else None,
        days,
        limit,
        suite_name=suite_name,
        allowed_project_ids=allowed,
        release_id=release_id,
    )


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
async def coverage_stats(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    suite_name: str | None = Query(None, min_length=1),
    # S4a. Optional: omitting it returns byte-identical results to before
    # the release axis existed (NFR1), including issuing no extra query.
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return test suite coverage stats aggregated over the period."""
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    return await analytics_service.coverage_stats(
        db,
        str(scoped) if scoped else None,
        days,
        suite_name=suite_name,
        allowed_project_ids=allowed,
        release_id=release_id,
    )


# ── Suite Detail ───────────────────────────────────────────────────────────

@router.get("/suite-detail")
async def suite_detail(
    project_id: str | None = None,
    suite_name: str = "",
    days: int = Query(30, ge=1, le=365),
    # S4a. Optional: omitting it returns byte-identical results to before
    # the release axis existed (NFR1), including issuing no extra query.
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return detailed breakdown for a single test suite:
      - Summary KPIs (unique tests, executions, pass rate, avg duration)
      - Per-test-case aggregates with flakiness flag
      - Last 10 test runs that included this suite
    """
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    return await analytics_service.suite_detail(
        db,
        str(scoped) if scoped else None,
        suite_name,
        days,
        allowed_project_ids=allowed,
        release_id=release_id,
    )


# ── Defects List ───────────────────────────────────────────────────────────

@router.get("/defects")
async def list_defects(
    project_id: str | None = None,
    resolution_status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return defects for a project with optional resolution status filter."""
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    return await analytics_service.list_defects(
        db,
        str(scoped) if scoped else None,
        resolution_status,
        page,
        size,
        allowed_project_ids=allowed,
    )


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
    # populated on the row. The request-scoped session is committed by the
    # ``get_db`` dependency once the response is built — see
    # ``backend/app/db/postgres.py``. ``expire_on_commit=False`` keeps the
    # defect usable without a refresh.
    defect = await analytics_service.create_manual_defect(db, payload.project_id, body)
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
async def ai_analysis_summary(
    project_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return summary of AI analysis results for the project."""
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    return await analytics_service.ai_analysis_summary(
        db,
        str(scoped) if scoped else None,
        days,
        allowed_project_ids=allowed,
    )


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

    return ClassifyUncategorizedResponse(
        updated=len(tc_ids),
        category=new_category,
        project_id=payload.project_id,
        days=payload.days,
        suite_name=payload.suite_name,
    )
