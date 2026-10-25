"""
Test management service — CRUD + review workflow for managed test cases and
test plans.

Transaction model (item #2): every function in this module stages changes
only. The calling router handler owns ``await db.commit()`` so that a single
commit covers the business mutation, the version snapshot, the audit row,
and any downstream recomputed counts atomically.
"""
from __future__ import annotations

import uuid
import structlog
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.run_compare_service import normalize_suite_name
from app.models.postgres import (
    ManagedTestCase,
    Project,
    ProjectMember,
    TestCaseComment,
    TestCaseReview,
    TestPlan,
    TestPlanItem,
    TestCaseLifecycleState,
    User,
    UserRole,
)
from app.models.schemas import (
    ManagedTestCaseCreate,
    ManagedTestCaseUpdate,
    ReviewActionRequest,
    TestCaseCommentCreate,
    TestPlanCreate,
    TestPlanItemCreate,
    TestPlanUpdate,
)
from app.routers.test_management_shared import apply_model_updates, get_or_404, paginate_scalars
from app.services.test_management_audit_service import audit_event
from app.services.test_management_metrics_service import stage_test_management_counter
from app.services.test_case_lifecycle_service import (
    DEFINITION_FIELDS,
    RETIRED_STATES,
    RUNNABLE_STATES,
    LifecycleAction,
    auto_claim_and_decide,
    stage_test_case_snapshot,
    transition,
    version_source,
)
from app.services.test_case_parameters import mask_parameters, merge_masked_parameters
from app.services.test_case_review_policy import project_requires_case_review

logger = structlog.get_logger(__name__)


async def get_test_case_or_404(
    db: AsyncSession,
    case_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ManagedTestCase:
    if for_update:
        test_case = (
            await db.execute(
                select(ManagedTestCase)
                .where(ManagedTestCase.id == case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if test_case is None:
            raise HTTPException(status_code=404, detail="Test case not found")
        return test_case
    return cast(
        ManagedTestCase,
        await get_or_404(db, ManagedTestCase, case_id, "Test case not found"),
    )


def requirement_search_clause(search: str):
    """W13 TCL-06: the case carries a requirement key CONTAINING the search.

    ``EXISTS`` over the case's own links, compared on the normalised key
    (``normalize_requirement_key``: ``[req-pay-07]`` and ``REQ-PAY-07`` are one
    key) and always through the link's own ``project_id`` = the case's, so the
    subquery stays on ``ix_tcrl_project_key``'s project. ``None`` when the
    search normalises to nothing.
    """
    from sqlalchemy import exists

    from app.core.test_library_vocab import normalize_requirement_key
    from app.models.postgres import TestCaseRequirementLink
    from app.services.sql_utils import like_contains

    norm = normalize_requirement_key(search)
    if not norm:
        return None
    return exists().where(
        TestCaseRequirementLink.test_case_id == ManagedTestCase.id,
        TestCaseRequirementLink.project_id == ManagedTestCase.project_id,
        TestCaseRequirementLink.requirement_key_norm.like(like_contains(norm), escape="\\"),
    )


def managed_case_search_clause(search: str, *, requirements: bool = False):
    """HON-07: the authored-case search, as wide as the Cases box promises.

    The placeholder said "ID, title, tags, owner, or requirement" while this
    matched the title alone. It now matches the title, the description, any
    tag (the JSON list cast to text; the list holds strings, so a key name
    cannot match by accident), and the case id by PREFIX, with or without the
    ``TC-`` the page prints before the id's first six characters. Owner is
    not searched. A full scan per project, which is acceptable at library sizes.

    ``requirements=True`` (W13 TCL-06: the list and export routes ask for it)
    adds one more term, a requirement key on the case containing the search
    (``requirement_search_clause``). Off by default, so a direct caller builds
    exactly the four terms it always did.
    """
    from sqlalchemy import Text

    from app.services.sql_utils import escape_like, like_contains

    pattern = like_contains(search)
    clauses = [
        ManagedTestCase.title.ilike(pattern, escape="\\"),
        ManagedTestCase.description.ilike(pattern, escape="\\"),
        ManagedTestCase.tags.cast(Text).ilike(pattern, escape="\\"),
    ]
    id_term = search.strip().lower()
    if id_term.startswith("tc-"):
        id_term = id_term[3:]
    if id_term:
        clauses.append(ManagedTestCase.id.cast(Text).ilike(f"{escape_like(id_term)}%", escape="\\"))
    if requirements:
        requirement_term = requirement_search_clause(search)
        if requirement_term is not None:
            clauses.append(requirement_term)
    return or_(*clauses)


@dataclass(frozen=True, slots=True)
class CaseListFilters:
    """W13 TCL-01: the case list's filters beyond today's single values.

    Built by the router from the QUERY (validated there); every list holds
    validated values, de-duplicated, at most ``CASE_FILTER_MAX_VALUES``.
    An empty instance adds NOTHING to a statement.
    """

    statuses: tuple[str, ...] = ()
    test_types: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()
    owner_id: uuid.UUID | None = None          # resolved: "me" -> current_user.id
    automation: str | None = None              # CASE_AUTOMATION_FILTERS
    requirement_key: str | None = None         # normalize_requirement_key(...) of the QUERY value

    @property
    def authored_only(self) -> bool:
        """Only authored cases can answer: an owner, an automation state, a
        requirement, or several values of one list (the automation union
        filters by none of them)."""
        return bool(
            self.owner_id is not None
            or self.automation is not None
            or self.requirement_key
            or len(self.statuses) > 1
            or len(self.test_types) > 1
            or len(self.priorities) > 1
        )


def apply_case_list_filters(query, filters: CaseListFilters):
    """AND the filters onto an authored-case statement. Pure.

    ``status IN``, ``test_type IN``, ``priority IN``; the owner is the
    assignee, else the author (the Owner column's rule); ``automated`` is
    ``is_automated IS TRUE`` and ``not_automated`` counts a NULL as not
    automated; a requirement is an exact match on the normalised key of a
    link in the case's own project. Each term sits on ``managed_test_cases``
    itself (no OR across tables). An empty ``CaseListFilters`` adds nothing.
    """
    from sqlalchemy import exists, false

    if filters.statuses:
        query = query.where(ManagedTestCase.status.in_(list(filters.statuses)))
    if filters.test_types:
        query = query.where(ManagedTestCase.test_type.in_(list(filters.test_types)))
    if filters.priorities:
        query = query.where(ManagedTestCase.priority.in_(list(filters.priorities)))
    if filters.owner_id is not None:
        query = query.where(
            func.coalesce(ManagedTestCase.assignee_id, ManagedTestCase.author_id) == filters.owner_id
        )
    if filters.automation == "automated":
        query = query.where(ManagedTestCase.is_automated.is_(True))
    elif filters.automation == "not_automated":
        query = query.where(func.coalesce(ManagedTestCase.is_automated, false()) == false())
    if filters.requirement_key:
        from app.models.postgres import TestCaseRequirementLink

        query = query.where(
            exists().where(
                TestCaseRequirementLink.test_case_id == ManagedTestCase.id,
                TestCaseRequirementLink.project_id == ManagedTestCase.project_id,
                TestCaseRequirementLink.requirement_key_norm == filters.requirement_key,
            )
        )
    return query


async def resolve_owner_filter(
    db: AsyncSession,
    owner: Optional[str],
    *,
    project_id: Optional[uuid.UUID],
    current_user,
) -> Optional[uuid.UUID]:
    """The QUERY ``owner`` as a user id, checked against the scoped project.

    ``None`` / ``""`` -> no filter; ``me`` -> the reader (no statement, every
    scope); a user id -> someone a case of THAT project may be assigned to
    (``require_assignable``: an active member, or an admin), else 422 with the
    filter's own sentence; a user id with no project -> 422; anything else ->
    422. The caller has already resolved the project scope.
    """
    from app.core.test_library_vocab import (
        OWNER_INVALID_SENTENCE,
        OWNER_ME,
        OWNER_NEEDS_PROJECT_SENTENCE,
        OWNER_NOT_MEMBER_SENTENCE,
    )

    value = (owner or "").strip()
    if not value:
        return None
    if value.lower() == OWNER_ME:
        return cast(uuid.UUID, current_user.id)
    try:
        user_id = uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=OWNER_INVALID_SENTENCE) from None
    if project_id is None:
        raise HTTPException(status_code=422, detail=OWNER_NEEDS_PROJECT_SENTENCE)
    try:
        await require_assignable(db, project_id, user_id)
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        raise HTTPException(status_code=422, detail=OWNER_NOT_MEMBER_SENTENCE) from None
    return user_id


def _filter_values(raw: object) -> list:
    """A repeatable QUERY list: stripped, empties dropped, de-duplicated (order
    kept), at most ``CASE_FILTER_MAX_VALUES`` (else 422). A non-list (``None``,
    or the ``Query(None)`` default a direct Python call leaves) is no filter."""
    from app.core.test_library_vocab import CASE_FILTER_MAX_VALUES, TOO_MANY_VALUES_SENTENCE

    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    values: list = []
    for item in raw:
        text = (item.value if isinstance(item, TestCaseLifecycleState) else str(item)).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(item if isinstance(item, TestCaseLifecycleState) else text)
    if len(values) > CASE_FILTER_MAX_VALUES:
        raise HTTPException(status_code=422, detail=TOO_MANY_VALUES_SENTENCE)
    return values


def _priority_values(values: list) -> list[str]:
    """Priorities as stored: any case, ``P0``-``P3`` aliased (``PRIORITY_ALIASES``),
    then de-duplicated again (``P0`` and ``critical`` are one value). Anything
    outside ``CASE_PRIORITIES`` is a 422: an unknown value used to match
    nothing and answer an empty page that looked like a real result."""
    from app.core.test_library_vocab import CASE_PRIORITIES, PRIORITY_ALIASES, PRIORITY_INVALID_SENTENCE

    normalised: list[str] = []
    for value in values:
        lowered = str(value).lower()
        priority = PRIORITY_ALIASES.get(lowered, lowered)
        if priority not in CASE_PRIORITIES:
            raise HTTPException(status_code=422, detail=PRIORITY_INVALID_SENTENCE)
        if priority not in normalised:
            normalised.append(priority)
    return normalised


def _status_values(values: list) -> list:
    """Lifecycle states only (the list route's enum already refuses others; the
    export takes plain strings, so it is judged here the same way)."""
    from app.core.test_library_vocab import LIFECYCLE_STATES, STATUS_INVALID_SENTENCE

    for value in values:
        if not isinstance(value, TestCaseLifecycleState) and value not in LIFECYCLE_STATES:
            raise HTTPException(status_code=422, detail=STATUS_INVALID_SENTENCE)
    return values


def _type_values(values: list) -> list[str]:
    """Types stay free text (cases carry types beyond the chip's nine, and the
    combined path reads ``automation``), bounded by the column's 50 characters."""
    from app.core.test_library_vocab import CASE_VIEW_TYPE_MAX, TYPE_INVALID_SENTENCE

    for value in values:
        if len(value) > CASE_VIEW_TYPE_MAX:
            raise HTTPException(status_code=422, detail=TYPE_INVALID_SENTENCE)
    return values


async def parse_case_list_filters(
    db: AsyncSession,
    current_user,
    *,
    project_id: Optional[uuid.UUID],
    status: object = None,
    test_type: object = None,
    priority: object = None,
    owner: object = None,
    automation: object = None,
    requirement: object = None,
) -> tuple[CaseListFilters, dict]:
    """W13 TCL-01: the case list's QUERY filters, judged ONE way for the list and the export.

    Returns the ``CaseListFilters`` and today's single-value meaning (``status``
    / ``test_type`` / ``priority`` when exactly one value was sent; a status
    stays the enum member the list route parsed), so a one-value request keeps
    today's call shape. The router calls it AFTER ``resolve_project_scope``:
    ``owner=<id>`` is checked against that project. A non-str / non-list value
    (a direct Python call's ``Query`` default) is no filter. A priority is
    aliased (``P0``-``P3``, any case) and must be one of ``CASE_PRIORITIES``,
    a status one of ``LIFECYCLE_STATES``, a type at most 50 characters (422 otherwise).
    """
    from app.core.test_library_vocab import (
        AUTOMATION_INVALID_SENTENCE,
        CASE_AUTOMATION_FILTERS,
        normalize_requirement_key,
    )

    statuses = _status_values(_filter_values(status))
    types = _type_values(_filter_values(test_type))
    priorities = _priority_values(_filter_values(priority))
    automation_value = automation.strip() if isinstance(automation, str) else None
    if automation_value is not None and automation_value not in CASE_AUTOMATION_FILTERS:
        raise HTTPException(status_code=422, detail=AUTOMATION_INVALID_SENTENCE)
    owner_id = await resolve_owner_filter(
        db, owner if isinstance(owner, str) else None, project_id=project_id, current_user=current_user,
    )
    requirement_key = (
        normalize_requirement_key(requirement) or None
        if isinstance(requirement, str) and requirement
        else None
    )
    filters = CaseListFilters(
        statuses=tuple(s.value if isinstance(s, TestCaseLifecycleState) else s for s in statuses),
        test_types=tuple(types),
        priorities=tuple(priorities),
        owner_id=owner_id,
        automation=automation_value,
        requirement_key=requirement_key,
    )
    single = {
        "status": statuses[0] if len(statuses) == 1 else None,
        "test_type": types[0] if len(types) == 1 else None,
        "priority": priorities[0] if len(priorities) == 1 else None,
    }
    return filters, single


async def list_managed_test_cases(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    page: int,
    size: int,
    status: Optional[TestCaseLifecycleState | str] = None,
    include_archived: bool = False,
    test_type: Optional[str] = None,
    priority: Optional[str] = None,
    feature_area: Optional[str] = None,
    ai_generated: Optional[bool] = None,
    search: Optional[str] = None,
    suite_name: Optional[str] = None,
    *,
    filters: Optional[CaseListFilters] = None,
    search_requirements: bool = False,
):
    """One page of authored cases.

    ``filters`` None builds today's statement exactly. With ``filters`` (W13
    TCL-01, the list and export routes), the single ``status`` / ``test_type``
    / ``priority`` fold into it (its own lists win), ``apply_case_list_filters``
    replaces those three predicates, and a status list (like one ``status``
    today) drops the default retired exclusion. ``search_requirements`` adds
    the requirement term to the search (``managed_case_search_clause``).
    """
    query = select(ManagedTestCase)
    if project_id:
        query = query.where(ManagedTestCase.project_id == project_id)
    if filters is not None:
        status_value = status.value if isinstance(status, TestCaseLifecycleState) else status
        effective = replace(
            filters,
            statuses=filters.statuses or ((status_value,) if status_value else ()),
            test_types=filters.test_types or ((test_type,) if test_type else ()),
            priorities=filters.priorities or ((priority,) if priority else ()),
        )
        if not effective.statuses and include_archived:
            query = query.where(ManagedTestCase.status != TestCaseLifecycleState.DEPRECATED.value)
        elif not effective.statuses:
            query = query.where(
                ManagedTestCase.status.notin_(
                    [
                        TestCaseLifecycleState.DEPRECATED.value,
                        TestCaseLifecycleState.ARCHIVED.value,
                    ]
                )
            )
        query = apply_case_list_filters(query, effective)
        # Folded: today's single-value predicates below must not apply twice.
        test_type = priority = None
    elif status:
        status_value = status.value if isinstance(status, TestCaseLifecycleState) else status
        query = query.where(ManagedTestCase.status == status_value)
    elif include_archived:
        query = query.where(ManagedTestCase.status != TestCaseLifecycleState.DEPRECATED.value)
    else:
        query = query.where(
            ManagedTestCase.status.notin_(
                [
                    TestCaseLifecycleState.DEPRECATED.value,
                    TestCaseLifecycleState.ARCHIVED.value,
                ]
            )
        )
    if test_type:
        query = query.where(ManagedTestCase.test_type == test_type)
    if priority:
        query = query.where(ManagedTestCase.priority == priority)
    if feature_area:
        query = query.where(ManagedTestCase.feature_area == feature_area)
    if ai_generated is not None:
        query = query.where(ManagedTestCase.ai_generated == ai_generated)
    if suite_name:
        # Case-insensitive, like every other suite filter in the product.
        # A raw ``==`` made this the odd one out: measured live, suite "api"
        # returned 5 rows here while "API" returned 0, where both
        # ``analytics/coverage`` and ``runs/compare`` returned 5 for either
        # spelling. See ``run_compare_service.normalize_suite_name``.
        query = query.where(
            func.lower(func.trim(ManagedTestCase.suite_name))
            == normalize_suite_name(suite_name)
        )
    if search:
        query = query.where(managed_case_search_clause(search, requirements=search_requirements))
    return await paginate_scalars(db, query.order_by(ManagedTestCase.created_at.desc()), page, size)


async def list_managed_test_case_fingerprints(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
) -> set[tuple[uuid.UUID, str]]:
    """Return project-scoped identities independent of authored-list filters."""
    stmt = select(ManagedTestCase.project_id, ManagedTestCase.test_fingerprint).where(
        ManagedTestCase.test_fingerprint.is_not(None)
    )
    if project_id is not None:
        stmt = stmt.where(ManagedTestCase.project_id == project_id)
    result = await db.execute(stmt)
    return {
        (row_project_id, fingerprint)
        for row_project_id, fingerprint in result.all()
        if fingerprint
    }


async def list_combined_test_case_identities(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    *,
    include_archived: bool,
    test_type: Optional[str],
    search: Optional[str],
    suite_name: Optional[str],
    page: int,
    size: int,
    search_requirements: bool = False,
) -> tuple[list[tuple[str, uuid.UUID]], int, int]:
    """Page managed and latest automation identities in one bounded SQL query.

    ``search_requirements`` (W13 TCL-06) adds the requirement term to the
    authored half's search; the automation half has no requirement keys.
    """
    managed = select(
        literal("managed").label("source"),
        ManagedTestCase.id.label("entity_id"),
        func.coalesce(
            ManagedTestCase.last_executed_at,
            ManagedTestCase.created_at,
        ).label("recency"),
    )
    if project_id is not None:
        managed = managed.where(ManagedTestCase.project_id == project_id)
    excluded_states = [TestCaseLifecycleState.DEPRECATED.value]
    if not include_archived:
        excluded_states.append(TestCaseLifecycleState.ARCHIVED.value)
    managed = managed.where(ManagedTestCase.status.notin_(excluded_states))
    if test_type:
        managed = managed.where(ManagedTestCase.test_type == test_type)
    if suite_name:
        managed = managed.where(
            func.lower(func.trim(ManagedTestCase.suite_name))
            == normalize_suite_name(suite_name)
        )
    if search:
        managed = managed.where(managed_case_search_clause(search, requirements=search_requirements))

    sources = [managed]
    if not test_type or test_type.lower() == "automation":
        from app.models.postgres import TestCase, TestRun
        from app.services.sql_utils import like_contains

        automation_base = select(
            TestCase.id.label("entity_id"),
            TestCase.test_fingerprint.label("fingerprint"),
            TestRun.project_id.label("project_id"),
            TestRun.created_at.label("recency"),
            func.row_number().over(
                partition_by=(TestRun.project_id, TestCase.test_fingerprint),
                order_by=(TestRun.created_at.desc(), TestCase.id.asc()),
            ).label("row_number"),
        ).join(TestRun, TestCase.test_run_id == TestRun.id).where(
            TestCase.test_fingerprint.is_not(None)
        )
        if project_id is not None:
            automation_base = automation_base.where(TestRun.project_id == project_id)
        if suite_name:
            suite_key = normalize_suite_name(suite_name)
            automation_base = automation_base.where(
                or_(
                    func.lower(func.trim(TestCase.suite_name)) == suite_key,
                    and_(
                        TestRun.trigger_source == "live_stream",
                        func.lower(func.trim(TestRun.primary_suite_name)) == suite_key,
                    ),
                )
            )
        if search:
            pattern = like_contains(search)
            automation_base = automation_base.where(
                TestCase.test_name.ilike(pattern, escape="\\")
                | TestCase.class_name.ilike(pattern, escape="\\")
            )
        latest = automation_base.subquery()
        linked = select(ManagedTestCase.id).where(
            ManagedTestCase.project_id == latest.c.project_id,
            ManagedTestCase.test_fingerprint == latest.c.fingerprint,
        ).exists()
        sources.append(
            select(
                literal("automation").label("source"),
                latest.c.entity_id,
                latest.c.recency,
            ).where(latest.c.row_number == 1, ~linked)
        )

    combined = union_all(*sources).subquery() if len(sources) > 1 else managed.subquery()
    total = int(
        (await db.execute(select(func.count()).select_from(combined))).scalar() or 0
    )
    rows = (
        await db.execute(
            select(combined.c.source, combined.c.entity_id)
            .order_by(combined.c.recency.desc(), combined.c.entity_id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return [(source, entity_id) for source, entity_id in rows], total, -(-total // size)


async def list_automation_test_cases(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    *,
    search: Optional[str] = None,
    suite_name: Optional[str] = None,
    exclude_fingerprints: Optional[set[str] | set[tuple[uuid.UUID, str]]] = None,
    case_ids: Optional[set[uuid.UUID]] = None,
) -> list[dict]:
    """Return synthesized ``ManagedTestCase``-shaped rows derived from per-run
    ``TestCase`` rows.

    Backs the "include automation-ingested tests" toggle on
    /test-management. Dedupes by ``test_fingerprint`` (one row per logical
    test) and joins via the latest TestRun so ``last_executed_at`` /
    ``last_execution_status`` reflect the most recent run.

    When ``project_id`` is ``None`` the project filter is dropped and rows
    span every project — this is the All-Projects path and is only safe
    when the caller has already enforced admin gating (the router does this
    via ``get_accessible_project_ids`` before reaching the merge path).
    Each synthesised row reports its own ``project_id`` from the joined
    ``TestRun`` so the frontend can route mutations correctly.

    The result rows are NOT inserted into ``managed_test_cases`` — they're
    serialised through ``ManagedTestCaseResponse`` for frontend display
    only. The router tags each row with ``source='automation'`` so the UI
    can render them differently from authored test cases.
    """
    from app.models.postgres import TestCase, TestRun
    from app.services.sql_utils import like_contains

    base = (
        select(
            TestCase.id,
            TestCase.test_fingerprint,
            TestCase.test_name,
            TestCase.class_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.owner,
            TestCase.canonical_test_case_id,
            TestCase.failure_category,
            TestCase.tags,
            TestRun.created_at.label("run_created_at"),
            TestRun.id.label("run_id"),
            TestRun.project_id.label("run_project_id"),
        )
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(TestCase.test_fingerprint.isnot(None))
    )
    if project_id is not None:
        base = base.where(TestRun.project_id == project_id)
    if case_ids is not None:
        if not case_ids:
            return []
        base = base.where(TestCase.id.in_(case_ids))
    if suite_name:
        # Case-insensitive (see ``list_managed_test_cases``) *and* honouring the
        # effective-suite rule: for a ``live_stream`` run the SDK sends the
        # suite once at session-create, so it lands on
        # ``TestRun.primary_suite_name`` while per-event ``TestCase.suite_name``
        # stays NULL. A bare per-row filter returns nothing for those runs even
        # though they are correctly tagged. Mirrors
        # ``analytics_service._effective_suite_sql()`` and the shape #559 gave
        # ``run_compare_service._load_test_rows``: the run-level label applies
        # to live_stream runs only, since a multi-``<testsuite>`` upload has an
        # authoritative per-row value that must win.
        #
        # Stated honestly: the live-stream half is NOT reproducible on the
        # homelab today — no live_stream run there has both a NULL per-row
        # suite and a ``primary_suite_name``. The case-sensitivity half was
        # reproduced (api -> 5, API -> 0).
        suite_key = normalize_suite_name(suite_name)
        base = base.where(
            or_(
                func.lower(func.trim(TestCase.suite_name)) == suite_key,
                and_(
                    TestRun.trigger_source == "live_stream",
                    func.lower(func.trim(TestRun.primary_suite_name)) == suite_key,
                ),
            )
        )
    if search:
        pattern = like_contains(search)
        base = base.where(
            TestCase.test_name.ilike(pattern, escape="\\")
            | TestCase.class_name.ilike(pattern, escape="\\")
        )
    base_sq = base.subquery()

    latest = (
        select(base_sq)
        .distinct(base_sq.c.run_project_id, base_sq.c.test_fingerprint)
        .order_by(
            base_sq.c.run_project_id,
            base_sq.c.test_fingerprint,
            base_sq.c.run_created_at.desc(),
        )
        .subquery()
    )
    stmt = select(latest).order_by(latest.c.run_created_at.desc())
    rows = (await db.execute(stmt)).all()

    exclude = exclude_fingerprints or set()
    result: list[dict] = []
    for r in rows:
        row_project_id = r.run_project_id if project_id is None else project_id
        if (
            r.test_fingerprint in exclude
            or (row_project_id, r.test_fingerprint) in exclude
        ):
            continue
        result.append({
            "id": r.id,  # per-run TestCase id; safe as a list-row key
            "owner": r.owner,
            "latest_run_id": r.run_id,
            "latest_test_case_id": r.id,
            "canonical_test_case_id": r.canonical_test_case_id,
            "project_id": row_project_id,
            "title": r.test_name,
            "description": None,
            "objective": None,
            "preconditions": None,
            "steps": None,
            "expected_result": None,
            "test_data": None,
            "test_type": "automation",
            "priority": "medium",
            "severity": "major",
            "feature_area": r.class_name,
            "suite_name": r.suite_name,
            "tags": r.tags,
            "status": "active",
            "version": 1,
            "author_id": None,
            "assignee_id": None,
            "reviewer_id": None,
            "is_automated": True,
            "automation_status": "automated",
            "test_fingerprint": r.test_fingerprint,
            "ai_generated": False,
            "ai_quality_score": None,
            "ai_review_notes": None,
            "estimated_duration_minutes": None,
            "last_executed_at": r.run_created_at,
            "last_execution_status": r.status,
            "created_at": r.run_created_at,
            "updated_at": r.run_created_at,
            # Source tag — frontend distinguishes automation rows from
            # authored ManagedTestCase rows (these synthesised rows aren't
            # in the managed_test_cases table).
            "source": "automation",
        })
    return result


async def _require_assignable(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """An assignee is an active user who is a member of the project, or an admin.

    The New Test Case form offered an Assignee the create payload dropped
    silently: ``ManagedTestCaseCreate`` had no such field (the UX redesign's
    browser E2E pass, 2026-10-08). Taken unchecked, it would let a caller
    assign a case to any user id, a member of another tenant's project
    included, and an unknown id would surface as an opaque foreign-key 500.
    """
    user = await db.get(User, user_id)
    if user is not None and user.is_active:
        role = getattr(user.role, "value", user.role)
        if role == UserRole.ADMIN.value:
            return
        member = (
            await db.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if member is not None:
            return
    raise HTTPException(
        status_code=422,
        detail="The assignee must be an active member of this project.",
    )


async def require_assignable(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Public alias of :func:`_require_assignable` (W28: the bulk Owner action)."""
    await _require_assignable(db, project_id, user_id)


def _json_normalized(value: object) -> object:
    """A value as JSON would store it, so a re-sent equal value compares equal."""
    import json  # noqa: PLC0415

    try:
        return json.loads(json.dumps(value, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return value


def _audit_values(update_data: dict) -> dict:
    """The edit's ``new_values`` as the audit row can store them.

    JSON-safe (a UUID / datetime as text: the column is JSON, and the suite id
    or the new owner id would fail the flush) and with masked parameter values
    removed (a secret never lands in the audit trail).
    """
    safe: dict = {}
    for key, value in update_data.items():
        if key == "parameters":
            value = mask_parameters(value)
        safe[key] = _json_normalized(value)
    return safe


def _definition_changed(test_case: ManagedTestCase, update_data: dict) -> bool:
    """Whether the edit really changes steps, the expected result or preconditions."""
    return any(
        field in update_data
        and _json_normalized(update_data[field]) != _json_normalized(getattr(test_case, field, None))
        for field in DEFINITION_FIELDS
    )


async def create_managed_test_case(
    db: AsyncSession,
    payload: ManagedTestCaseCreate,
    current_user: User,
) -> ManagedTestCase:
    # Validate the project exists before insert — otherwise asyncpg surfaces a
    # ForeignKeyViolationError that becomes an opaque 500. This commonly hits
    # when the frontend has a stale activeProjectId in localStorage.
    project = await db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {payload.project_id} not found — refresh the page or pick a different project.",
        )
    # Tenant guard: the caller must be a member of the target project. Without
    # this a user could author a case in ANY project by supplying its id.
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))
    if payload.assignee_id is not None:
        await _require_assignable(db, payload.project_id, payload.assignee_id)

    # Migration 0087 — resolve or create the structured suite anchor when the
    # caller supplied a ``suite_name``. This lets authored cases participate
    # in the same catalog graph as executed ones (rename-propagation,
    # cross-project move refusal, suite-scoped queries). Legacy callers that
    # don't set ``suite_name`` keep working — ``test_suite_id`` stays NULL.
    test_suite_id: Optional[uuid.UUID] = None
    suite_name = (payload.suite_name or "").strip() or None
    if suite_name is not None:
        from app.services.test_suite_service import get_or_create_suite_by_name
        suite = await get_or_create_suite_by_name(
            db, payload.project_id, suite_name
        )
        test_suite_id = suite.id

    # Keep authored cases discoverable from executed results when the
    # automation uses the same title and has no class discriminator. This is
    # only a fallback identity; an explicit canonical link remains authoritative.
    from app.services.ingestion import make_test_fingerprint
    test_case = ManagedTestCase(
        **payload.model_dump(exclude_unset=True, exclude={"change_summary"}),
        author_id=current_user.id,
        status="draft",
        version=1,
        test_suite_id=test_suite_id,
        test_fingerprint=make_test_fingerprint(payload.title, None),
    )
    db.add(test_case)
    await db.flush()

    stage_test_case_snapshot(
        db,
        test_case,
        actor_id=current_user.id,
        source=version_source(current_user),
        change_summary="Initial creation",
        change_type="created",
        changed_fields=[
            "title", "description", "objective", "preconditions", "steps",
            "parameters", "expected_result", "test_data", "test_type",
            "priority", "severity", "feature_area", "suite_name", "tags",
            "estimated_duration_minutes", "is_automated", "automation_status",
            "test_fingerprint", "status",
        ],
    )
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "created", current_user,
        details=f"Test case '{test_case.title}' created",
    )
    return test_case


async def update_managed_test_case(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: ManagedTestCaseUpdate,
    current_user: User,
    *,
    change_type: str = "updated",
) -> ManagedTestCase:
    """Stage one edit of an authored case: a new version, never a new status.

    W28 TCM-01 (owner decision OR-04): an edit bumps the version and snapshots
    the case in every non-retired state; it no longer moves an approved /
    active case to Needs update. With the project requiring review, a real
    change of steps / expected result / preconditions on an approved or active
    case sets ``edited_since_approval_version`` (the lifecycle clears it).
    ``change_type`` names the version (``updated``; ``bulk_updated`` and
    ``restored`` for the bulk and restore services).
    """
    test_case = await get_test_case_or_404(db, case_id, for_update=True)
    if test_case.status in RETIRED_STATES:
        raise HTTPException(status_code=409, detail=f"Cannot edit a {test_case.status} test case")
    if test_case.version != payload.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "current_version": test_case.version,
                "expected_version": payload.expected_version,
                "message": "Test case changed; refresh before saving edits",
            },
        )

    old = {"title": test_case.title, "status": test_case.status, "version": test_case.version}
    update_data = payload.model_dump(
        exclude_unset=True,
        exclude={"change_summary", "expected_version"},
    )
    if not update_data:
        return test_case

    # The Owner: validated like create's, and only when one is named (an
    # explicit null unassigns; an omitted field reads nothing).
    if update_data.get("assignee_id") is not None:
        await _require_assignable(db, test_case.project_id, update_data["assignee_id"])
    # A masked row sent back with ``value: null`` keeps its stored secret: the
    # edit form never received it (services/test_case_parameters.py).
    if isinstance(update_data.get("parameters"), list):
        update_data["parameters"] = merge_masked_parameters(
            getattr(test_case, "parameters", None), update_data["parameters"]
        )

    # Migration 0087 — keep ``test_suite_id`` in lockstep with ``suite_name``
    # changes. When the caller renames the suite (or clears it), we
    # resolve-or-create the new suite under the case's own project; never
    # cross-project. The structured FK update happens *before*
    # apply_model_updates so the version snapshot below already reflects
    # the resolved anchor.
    if "suite_name" in update_data:
        new_suite_name = (update_data["suite_name"] or "").strip() or None
        if new_suite_name is None:
            update_data["test_suite_id"] = None
        else:
            from app.services.test_suite_service import get_or_create_suite_by_name
            suite = await get_or_create_suite_by_name(
                db, test_case.project_id, new_suite_name
            )
            update_data["test_suite_id"] = suite.id

    definition_changed = _definition_changed(test_case, update_data)
    apply_model_updates(test_case, update_data)
    changed_fields = sorted(update_data)
    # Editing never changes the status (W28 TCM-01): no FLAG_STALE any more.
    test_case.version += 1
    if (
        old["status"] in RUNNABLE_STATES
        and definition_changed
        and getattr(test_case, "edited_since_approval_version", None) is None
        and await project_requires_case_review(db, test_case.project_id)
    ):
        test_case.edited_since_approval_version = test_case.version
    stage_test_case_snapshot(
        db,
        test_case,
        actor_id=current_user.id,
        source=version_source(current_user),
        change_summary=payload.change_summary or f"Updated to v{test_case.version}",
        change_type=change_type,
        changed_fields=changed_fields,
    )
    await audit_event(
        db, "test_case", test_case.id, test_case.project_id, "updated", current_user,
        old_values=old, new_values=_audit_values(update_data),
    )
    return test_case


async def deprecate_managed_test_case(
    db: AsyncSession,
    case_id: uuid.UUID,
    current_user: User,
    reason: Optional[str] = None,
) -> None:
    test_case = await get_test_case_or_404(db, case_id)
    compatibility_reason = reason
    if not compatibility_reason or not compatibility_reason.strip():
        compatibility_reason = "(no reason supplied)"
        logger.warning(
            "test_case_deprecated_without_reason",
            test_case_id=str(test_case.id),
            project_id=str(test_case.project_id),
        )
        stage_test_management_counter(
            db,
            "deprecation_without_reason",
            (str(test_case.project_id),),
        )
    await transition(
        db,
        case_id,
        LifecycleAction.DEPRECATE,
        current_user,
        reason=compatibility_reason,
    )


async def request_test_case_review(db: AsyncSession, case_id: uuid.UUID, current_user: User) -> TestCaseReview:
    # If ``case_id`` doesn't resolve to a ManagedTestCase, check whether
    # it's actually a per-run TestCase id — that's the common mistake
    # when a caller hits this endpoint with an automation-row id from
    # the merged /cases response. The clearer 400 with explicit guidance
    # is much more debuggable than the bare "Test case not found".
    managed = await db.get(ManagedTestCase, case_id)
    if managed is None:
        from app.models.postgres import TestCase as _TC
        is_automation = await db.execute(
            select(_TC.id).where(_TC.id == case_id).limit(1),
        )
        if is_automation.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This id belongs to an automation TestCase row, not a "
                    "managed_test_cases row. Automation rows must be "
                    "promoted to a managed test case before a review can "
                    "be requested."
                ),
            )
        raise HTTPException(status_code=404, detail="Test case not found")
    result = await transition(
        db, case_id, LifecycleAction.REQUEST_REVIEW, current_user
    )
    assert result.review is not None
    return result.review


async def apply_review_action(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: ReviewActionRequest,
    current_user: User,
) -> ManagedTestCase:
    result = await auto_claim_and_decide(
        db,
        case_id,
        payload.action,
        current_user,
        notes=payload.notes,
    )
    return result.case


async def add_test_case_comment(
    db: AsyncSession,
    case_id: uuid.UUID,
    payload: TestCaseCommentCreate,
    current_user: User,
) -> TestCaseComment:
    await get_test_case_or_404(db, case_id)
    comment = TestCaseComment(
        test_case_id=case_id,
        author_id=current_user.id,
        **payload.model_dump(exclude_unset=True),
    )
    db.add(comment)
    await db.flush()  # materialize comment.id for the handler response
    return comment


async def get_plan_or_404(
    db: AsyncSession,
    plan_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> TestPlan:
    if not for_update:
        return cast(
            TestPlan,
            await get_or_404(db, TestPlan, plan_id, "Test plan not found"),
        )
    statement = select(TestPlan).where(TestPlan.id == plan_id)
    statement = statement.with_for_update()
    plan = (await db.execute(statement)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Test plan not found")
    return cast(TestPlan, plan)


async def get_plan_item_or_404(db: AsyncSession, plan_id: uuid.UUID, item_id: uuid.UUID) -> TestPlanItem:
    item = cast(TestPlanItem, await get_or_404(db, TestPlanItem, item_id, "Item not found"))
    if item.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


async def recompute_plan_counts(db: AsyncSession, plan: TestPlan) -> None:
    # Aggregate the per-status counts in one query instead of materialising
    # every TestPlanItem and running five Python passes. ``executed`` is
    # derived as ``total - not_run`` (NOT count(status != 'not_run')) so a NULL
    # execution_status counts as executed — matching the original
    # ``status not in ('not_run',)`` (None is "not not_run"); the column is
    # nullable. passed/failed/blocked use ``== X``, which excludes NULL in both
    # the SQL and the original Python, so they match exactly.
    row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count(
                    case((TestPlanItem.execution_status == "not_run", 1))
                ).label("not_run"),
                func.count(
                    case((TestPlanItem.execution_status == "passed", 1))
                ).label("passed"),
                func.count(
                    case((TestPlanItem.execution_status == "failed", 1))
                ).label("failed"),
                func.count(
                    case((TestPlanItem.execution_status == "blocked", 1))
                ).label("blocked"),
            ).where(TestPlanItem.plan_id == plan.id)
        )
    ).one()
    total = int(row.total or 0)
    plan.total_cases = total
    plan.executed_cases = total - int(row.not_run or 0)
    plan.passed_cases = int(row.passed or 0)
    plan.failed_cases = int(row.failed or 0)
    plan.blocked_cases = int(row.blocked or 0)


async def list_test_plans(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    page: int,
    size: int,
    status: Optional[str] = None,
):
    query = select(TestPlan)
    if project_id:
        query = query.where(TestPlan.project_id == project_id)
    if status:
        query = query.where(TestPlan.status == status)
    return await paginate_scalars(db, query.order_by(TestPlan.created_at.desc()), page, size)


async def create_test_plan(db: AsyncSession, payload: TestPlanCreate, current_user: User) -> TestPlan:
    project = await db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {payload.project_id} not found — refresh the page or pick a different project.",
        )
    # Tenant guard — caller must be a member of the target project.
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))
    plan = TestPlan(**payload.model_dump(exclude_unset=True), created_by_id=current_user.id)
    db.add(plan)
    await db.flush()  # materialize plan.id so the audit row can reference it
    await audit_event(db, "test_plan", plan.id, plan.project_id, "created", current_user, details=f"Test plan '{plan.name}' created")
    return plan


async def update_test_plan(
    db: AsyncSession,
    plan_id: uuid.UUID,
    payload: TestPlanUpdate,
    current_user: User,
) -> TestPlan:
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    apply_model_updates(plan, payload.model_dump(exclude_unset=True))
    await audit_event(db, "test_plan", plan.id, plan.project_id, "updated", current_user)
    return plan


async def add_test_plan_item(
    db: AsyncSession,
    plan_id: uuid.UUID,
    payload: TestPlanItemCreate,
    current_user: User,
) -> TestPlanItem:
    # The plan row is the aggregate and membership serialization point. This
    # prevents concurrent add/remove/execute requests from publishing counts
    # calculated from different snapshots.
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    case_id = await db.scalar(
        select(ManagedTestCase.id).where(
            ManagedTestCase.id == payload.test_case_id,
            ManagedTestCase.project_id == plan.project_id,
        )
    )
    if case_id is None:
        # Do not disclose whether an identifier belongs to another project.
        raise HTTPException(status_code=404, detail="Test case not found")
    existing_item_id = await db.scalar(
        select(TestPlanItem.id).where(
            TestPlanItem.plan_id == plan_id,
            TestPlanItem.test_case_id == payload.test_case_id,
        )
    )
    if existing_item_id is not None:
        raise HTTPException(status_code=409, detail="Test case is already in this plan")
    item = TestPlanItem(plan_id=plan_id, **payload.model_dump(exclude_unset=True))
    db.add(item)
    await db.flush()  # materialize item.id and expose it to recompute_plan_counts
    await recompute_plan_counts(db, plan)
    await audit_event(
        db,
        "test_plan_item",
        item.id,
        plan.project_id,
        "added",
        current_user,
        new_values={
            "plan_id": str(plan.id),
            "test_case_id": str(item.test_case_id),
        },
    )
    return item


async def remove_test_plan_item(
    db: AsyncSession,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: User,
) -> None:
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    test_case_id = item.test_case_id
    await db.delete(item)
    await recompute_plan_counts(db, plan)
    await audit_event(
        db,
        "test_plan_item",
        item.id,
        plan.project_id,
        "removed",
        current_user,
        old_values={
            "plan_id": str(plan.id),
            "test_case_id": str(test_case_id),
        },
    )


async def record_test_plan_execution(
    db: AsyncSession,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    execution_status: str,
    execution_notes: Optional[str],
    actual_duration_minutes: Optional[int],
    current_user: User,
) -> TestPlanItem:
    plan = await get_plan_or_404(db, plan_id, for_update=True)
    item = await get_plan_item_or_404(db, plan_id, item_id)
    old_values = {
        "execution_status": item.execution_status,
        "execution_notes": item.execution_notes,
        "actual_duration_minutes": item.actual_duration_minutes,
        "executed_by_id": (
            str(item.executed_by_id) if item.executed_by_id is not None else None
        ),
        "executed_at": (
            item.executed_at.isoformat() if item.executed_at is not None else None
        ),
    }
    executed_at = datetime.now(timezone.utc)
    item.execution_status = execution_status
    item.executed_by_id = current_user.id
    item.executed_at = executed_at
    item.execution_notes = execution_notes
    item.actual_duration_minutes = actual_duration_minutes
    # W28 TCM-05: the case version this execution ran against (one row per
    # plan and case, so a re-execution records the version current then).
    # Not part of the audit values below, which stay as they were.
    case_version = (
        await db.execute(
            select(ManagedTestCase.version).where(ManagedTestCase.id == item.test_case_id)
        )
    ).scalar_one_or_none()
    item.executed_case_version = (
        case_version
        if isinstance(case_version, int) and not isinstance(case_version, bool)
        else None
    )
    await recompute_plan_counts(db, plan)
    await audit_event(
        db,
        "test_plan_item",
        item.id,
        plan.project_id,
        "executed",
        current_user,
        old_values=old_values,
        new_values={
            "execution_status": execution_status,
            "execution_notes": execution_notes,
            "actual_duration_minutes": actual_duration_minutes,
            "executed_by_id": str(current_user.id),
            "executed_at": executed_at.isoformat(),
        },
    )
    return item
