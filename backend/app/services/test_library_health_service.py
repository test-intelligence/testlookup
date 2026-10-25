"""Library health over the WHOLE authored library, on the server (W13 TCL-03).

Replaces the Cases tab's 200-row roll and its client-side synthesis. Every
statement is scoped ``project_id = :p``; every component carries its numerator
and denominator; an empty library is "not measured" (never ``0 %``); every list
returns its ``total`` beside its first rows. Read-only and commit-free: a GET
never writes (nothing here adds, flushes or commits).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.test_library_vocab import (
    AUTOMATION_TARGET_DEFAULT,
    FLAKY_SCORE_FLOOR,
    FLAKY_UNMEASURED_NO_LINKS,
    FLAKY_UNMEASURED_NO_SCORES,
    FLAKY_WEAK_CONFIDENCES,
    HEALTH_COMPONENT_LABELS,
    HEALTH_COMPONENTS,
    HEALTH_LIST_PREVIEW,
    HEALTH_TAG_THRESHOLDS,
    HEALTH_TAGS,
    HEALTH_WEIGHTS,
    LIBRARY_EMPTY_SENTENCE,
    OLD_CASE_DAYS,
    OPEN_PLAN_EXCLUDED_STATUSES,
    RETIRED_STATES,
    REVIEW_FRESHNESS_DAYS,
    REVIEW_SLA_DAYS,
    REVIEW_STATES,
    RUNNABLE_STATES,
    STALE_DRAFT_DAYS,
    STRATEGY_GAP_TITLES,
    SUITE_NAMES_CAP,
)
from app.models.postgres import FlakyScore, ManagedTestCase, Project, TestPlan, TestPlanItem, User
from app.services.case_automation_identity import automation_fingerprint_expr

_DAY_SECONDS = 86400.0


def round_half_up(value: float) -> int:
    """JavaScript's ``Math.round`` (the page's old rounding), not banker's."""
    return int(math.floor(value + 0.5))


def _days_between(now: datetime, then: Optional[datetime]) -> Optional[int]:
    if then is None:
        return None
    return max(0, int(math.floor((now - then).total_seconds() / _DAY_SECONDS)))


def _owner_name(full_name: Optional[str], username: Optional[str]) -> Optional[str]:
    return (full_name or "").strip() or (username or "").strip() or None


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def component_values(
    *, total: int, automated: int, stale: int, oldest_review_days: Optional[int], retired_in_open: int,
) -> dict[str, Optional[float]]:
    """Today's formulas (the page's old synthesis), the inputs now whole-library.

    ``None`` per component when the library is empty (not measured).
    """
    if total <= 0:
        return {key: None for key in HEALTH_COMPONENTS}
    oldest = oldest_review_days or 0
    return {
        "automation": automated / total * 100,
        "stale_drafts": max(0.0, 100 - stale / total * 400),
        "review_freshness": 100.0 if oldest == 0 else max(0.0, 100 - oldest / REVIEW_FRESHNESS_DAYS * 100),
        "retired_in_open_plans": max(0.0, 100 - retired_in_open / total * 400),
    }


def health_score(components: Sequence[dict]) -> tuple[Optional[int], Optional[str]]:
    """Weighted sum of the MEASURED components, re-normalised over their weights.

    ``(None, None)`` when none is measured. Tag thresholds: >= 85 / >= 70 / else.
    """
    measured = [c for c in components if c.get("measured") and c.get("value") is not None]
    weight = sum(float(c["weight"]) for c in measured)
    if not measured or weight <= 0:
        return None, None
    score = round_half_up(sum(float(c["value"]) * float(c["weight"]) for c in measured) / weight)
    healthy, attention = HEALTH_TAG_THRESHOLDS
    tag = HEALTH_TAGS[0] if score >= healthy else HEALTH_TAGS[1] if score >= attention else HEALTH_TAGS[2]
    return score, tag


def _component_detail(key: str, facts: dict[str, Any]) -> str:
    total = facts["total"]
    if key == "automation":
        return f"{facts['automated']} of {total} cases automated (target {facts['target']} %)"
    if key == "stale_drafts":
        return f"{facts['stale']} of {total} cases are drafts untouched for {STALE_DRAFT_DAYS} days or more"
    if key == "review_freshness":
        queue = facts["review_total"]
        if queue == 0:
            return f"No case awaits review (0 of {REVIEW_FRESHNESS_DAYS} days used)"
        return (
            f"The oldest of {_plural(queue, 'review')} has waited {facts['oldest_review_days'] or 0} "
            f"of {REVIEW_FRESHNESS_DAYS} days"
        )
    return f"{facts['retired_in_open']} of {total} cases are retired but still in an open test plan"


def build_components(facts: dict[str, Any]) -> list[dict]:
    """The four ``LibraryHealthComponent`` bodies, each with both numbers."""
    total = facts["total"]
    values = component_values(
        total=total,
        automated=facts["automated"],
        stale=facts["stale"],
        oldest_review_days=facts["oldest_review_days"],
        retired_in_open=facts["retired_in_open"],
    )
    numerators = {
        "automation": facts["automated"],
        "stale_drafts": facts["stale"],
        "review_freshness": facts["oldest_review_days"] or 0,
        "retired_in_open_plans": facts["retired_in_open"],
    }
    out: list[dict] = []
    for key in HEALTH_COMPONENTS:
        measured = total > 0
        denominator = (REVIEW_FRESHNESS_DAYS if key == "review_freshness" else total) if measured else 0
        out.append({
            "key": key,
            "label": HEALTH_COMPONENT_LABELS[key],
            "weight": HEALTH_WEIGHTS[key],
            "measured": measured,
            "value": values[key],
            "numerator": numerators[key] if measured else None,
            "denominator": denominator,
            "detail": _component_detail(key, facts) if measured else LIBRARY_EMPTY_SENTENCE,
        })
    return out


def build_strategy_gaps(facts: dict[str, Any], *, flaky: dict, no_owner: dict) -> list[dict]:
    """``StrategyGapRead`` bodies in ``STRATEGY_GAP_KINDS`` order, each only when its count > 0.

    Nothing when the library is not measured (an empty library has no gap).
    """
    total = facts["total"]
    if total <= 0:
        return []
    gaps: list[dict] = []

    def add(kind: str, severity: str, detail: str, count: int, pill: str) -> None:
        gaps.append({"kind": kind, "severity": severity, "title": STRATEGY_GAP_TITLES[kind],
                     "detail": detail, "count": count, "pill": pill})

    retired = facts["retired_in_open"]
    if retired > 0:
        add("retired_in_open_plans", "critical", "still items of an open test plan", retired,
            f"{retired} in open plans")
    queue, oldest = facts["review_total"], facts["oldest_review_days"] or 0
    if queue > 0 and oldest >= REVIEW_SLA_DAYS:
        add("aging_reviews", "warn", f"oldest {oldest} days", queue, f"{queue} pending")
    target = facts["target"]
    pct = round_half_up(facts["automated"] / total * 100)
    if pct < target:
        add("automation_below_target", "warn", f"{pct} % automated · target {target} %", target - pct,
            f"{target - pct} % gap")
    stale = facts["stale"]
    if stale > 0:
        add("stale_drafts", "warn", "untouched in the last month", stale, f"{stale} stale")
    if flaky.get("measured") and flaky["total"] > 0:
        add("flaky_active", "warn", "approved or active cases whose automated test is flaky", flaky["total"],
            f"{flaky['total']} flaky")
    if no_owner.get("measured") and no_owner["total"] > 0:
        add("no_owner", "warn", "nobody active owns them", no_owner["total"], _plural(no_owner["total"], "case"))
    return gaps


# ── The statements (each scoped project_id = :p) ─────────────────────────────

_M = ManagedTestCase


def _review_started() -> Any:
    return func.coalesce(_M.lifecycle_state_changed_at, _M.updated_at)


def _owner_id() -> Any:
    return func.coalesce(_M.assignee_id, _M.author_id)


async def _aggregate(db: AsyncSession, project_id: Any, now: datetime) -> dict[str, Any]:
    """(1) ONE aggregate over ``managed_test_cases``."""
    live = _M.status.notin_(RETIRED_STATES)
    review = _M.status.in_(REVIEW_STATES)
    stmt = select(
        func.count().filter(live).label("total"),
        func.count().filter(_M.status == "active").label("active"),
        func.count().filter(_M.status.in_(RETIRED_STATES)).label("retired"),
        func.count().filter(and_(live, _M.is_automated.is_(True))).label("automated"),
        func.count().filter(review).label("review_total"),
        func.min(_review_started()).filter(review).label("oldest_review"),
        func.count().filter(
            and_(_M.status == "draft", _M.updated_at < now - timedelta(days=STALE_DRAFT_DAYS))
        ).label("stale"),
        func.avg(func.extract("epoch", _M.created_at)).filter(live).label("avg_created_epoch"),
        func.count().filter(
            and_(live, _M.created_at < now - timedelta(days=OLD_CASE_DAYS))
        ).label("older_than_180"),
    ).where(_M.project_id == project_id)
    row = (await db.execute(stmt)).one()
    return dict(row._mapping)


async def _retired_in_open_plans(db: AsyncSession, project_id: Any) -> int:
    """(2) Retired cases still items of a plan that is neither completed nor archived."""
    stmt = (
        select(func.count(func.distinct(_M.id)))
        .select_from(_M)
        .join(TestPlanItem, TestPlanItem.test_case_id == _M.id)
        .join(TestPlan, TestPlan.id == TestPlanItem.plan_id)
        .where(
            _M.project_id == project_id,
            _M.status.in_(RETIRED_STATES),
            TestPlan.project_id == project_id,
            func.coalesce(TestPlan.status, "draft").notin_(OPEN_PLAN_EXCLUDED_STATUSES),
        )
    )
    return int((await db.execute(stmt)).scalar() or 0)


# Shared with test_suite_service.list_test_case_evidence_gaps (the SAME predicates).
def flaky_join_condition() -> Any:
    """``flaky_score`` of the case's automation identity, in the CASE's project."""
    return and_(
        FlakyScore.project_id == _M.project_id,
        FlakyScore.test_fingerprint == automation_fingerprint_expr(),
    )


def flaky_active_where() -> list[Any]:
    """Approved / active, and flaky by the product's one bar (failure_attribution_service)."""
    return [
        _M.status.in_(RUNNABLE_STATES),
        FlakyScore.score >= FLAKY_SCORE_FLOOR,
        FlakyScore.confidence.notin_(sorted(FLAKY_WEAK_CONFIDENCES)),
    ]


def no_owner_join_condition() -> Any:
    return User.id == _owner_id()


def no_owner_where() -> list[Any]:
    """Non-retired, and nobody active owns it (assignee, else author; none or deactivated)."""
    return [_M.status.notin_(RETIRED_STATES), func.coalesce(User.is_active, False).is_(False)]


def owner_state(assignee_id: Any, author_id: Any) -> str:
    return "unassigned" if assignee_id is None and author_id is None else "deactivated"


def _case_list(total: int, items: list[dict], *, measured: bool = True, reason: Optional[str] = None) -> dict:
    return {"total": total if measured else 0, "items": items if measured else [], "measured": measured,
            "reason": None if measured else reason}


async def _review_queue(db: AsyncSession, project_id: Any, now: datetime) -> list[dict]:
    """(3) The review queue's first five, oldest first, owners named."""
    started = _review_started()
    stmt = (
        select(_M.id, _M.title, _M.status, started.label("started"), User.full_name, User.username)
        .select_from(_M)
        .outerjoin(User, User.id == _owner_id())
        .where(_M.project_id == project_id, _M.status.in_(REVIEW_STATES))
        .order_by(started.asc(), _M.id.asc())
        .limit(HEALTH_LIST_PREVIEW)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {"id": r.id, "title": r.title, "status": r.status, "owner": _owner_name(r.full_name, r.username),
         "age_days": _days_between(now, r.started), "detail": None}
        for r in rows
    ]


async def _flaky_measurement(db: AsyncSession, project_id: Any) -> tuple[int, int]:
    """(5) How many score rows the project has, and how many runnable cases have an identity."""
    scores = select(func.count()).select_from(FlakyScore).where(FlakyScore.project_id == project_id)
    linked = (
        select(func.count())
        .select_from(_M)
        .where(_M.project_id == project_id, _M.status.in_(RUNNABLE_STATES),
               automation_fingerprint_expr().isnot(None))
    )
    row = (await db.execute(select(scores.scalar_subquery(), linked.scalar_subquery()))).one()
    return int(row[0] or 0), int(row[1] or 0)


async def _flaky_active(db: AsyncSession, project_id: Any) -> tuple[list[dict], int]:
    """(4) The flaky-and-active cases, top five by score, with the window total."""
    stmt = (
        select(_M.id, _M.title, _M.status, FlakyScore.score, FlakyScore.confidence,
               User.full_name, User.username, func.count().over().label("total"))
        .select_from(_M)
        .join(FlakyScore, flaky_join_condition())
        .outerjoin(User, User.id == _owner_id())
        .where(_M.project_id == project_id, *flaky_active_where())
        .order_by(FlakyScore.score.desc(), _M.id.asc())
        .limit(HEALTH_LIST_PREVIEW)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        {"id": r.id, "title": r.title, "status": r.status, "owner": _owner_name(r.full_name, r.username),
         "age_days": None, "detail": f"flaky score {float(r.score):.2f} ({r.confidence} confidence)"}
        for r in rows
    ]
    return items, int(rows[0].total) if rows else 0


async def _no_owner(db: AsyncSession, project_id: Any, now: datetime) -> tuple[list[dict], int]:
    """(6) Non-retired cases nobody active owns, oldest first, with the window total."""
    stmt = (
        select(_M.id, _M.title, _M.status, _M.updated_at, _M.assignee_id, _M.author_id,
               func.count().over().label("total"))
        .select_from(_M)
        .outerjoin(User, no_owner_join_condition())
        .where(_M.project_id == project_id, *no_owner_where())
        .order_by(_M.created_at.asc(), _M.id.asc())
        .limit(HEALTH_LIST_PREVIEW)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        {"id": r.id, "title": r.title, "status": r.status, "owner": None,
         "age_days": _days_between(now, r.updated_at),
         "detail": "unassigned" if owner_state(r.assignee_id, r.author_id) == "unassigned" else "owner deactivated"}
        for r in rows
    ]
    return items, int(rows[0].total) if rows else 0


async def _suite_names(db: AsyncSession, project_id: Any) -> tuple[list[str], int]:
    """(7) The authored suite names (the Suite filter's options): distinct, trimmed, sorted, capped."""
    name = func.btrim(_M.suite_name)
    names = (
        select(name.label("name"))
        .where(_M.project_id == project_id, _M.status.notin_(RETIRED_STATES),
               _M.suite_name.isnot(None), name != "")
        .distinct()
        .subquery()
    )
    stmt = (
        select(names.c.name, func.count().over().label("total"))
        .order_by(names.c.name.asc())
        .limit(SUITE_NAMES_CAP)
    )
    rows = (await db.execute(stmt)).all()
    return [r.name for r in rows], int(rows[0].total) if rows else 0


async def _automation_target(db: AsyncSession, project_id: Any) -> int:
    """(8) The project's automation target (default 60)."""
    stmt = select(Project.automation_target_pct).where(Project.id == project_id)
    value = (await db.execute(stmt)).scalar()
    return int(value) if value is not None else AUTOMATION_TARGET_DEFAULT


async def library_health(db: AsyncSession, *, project_id: Any, now: datetime, include_requirements: bool) -> dict:
    """A ``LibraryHealthResponse`` body for one project. Never writes."""
    agg = await _aggregate(db, project_id, now)
    total = int(agg["total"] or 0)
    facts: dict[str, Any] = {
        "total": total,
        "automated": int(agg["automated"] or 0),
        "stale": int(agg["stale"] or 0),
        "review_total": int(agg["review_total"] or 0),
        "oldest_review_days": _days_between(now, agg["oldest_review"]),
        "retired_in_open": await _retired_in_open_plans(db, project_id),
    }
    review_items = await _review_queue(db, project_id, now)
    score_rows, linked = await _flaky_measurement(db, project_id)
    if score_rows == 0:
        flaky = _case_list(0, [], measured=False, reason=FLAKY_UNMEASURED_NO_SCORES)
    elif linked == 0:
        flaky = _case_list(0, [], measured=False, reason=FLAKY_UNMEASURED_NO_LINKS)
    else:
        flaky_items, flaky_total = await _flaky_active(db, project_id)
        flaky = _case_list(flaky_total, flaky_items)
    owner_items, owner_total = await _no_owner(db, project_id, now)
    no_owner = _case_list(owner_total, owner_items, measured=total > 0, reason=LIBRARY_EMPTY_SENTENCE)
    suite_names, suite_names_total = await _suite_names(db, project_id)
    facts["target"] = await _automation_target(db, project_id)

    components = build_components(facts)
    score, tag = health_score(components)
    avg_epoch = agg["avg_created_epoch"]
    older = int(agg["older_than_180"] or 0)
    requirements = None
    if include_requirements:
        from app.services.requirement_traceability_service import requirement_summary

        requirements = await requirement_summary(db, project_id=project_id, top_gaps=HEALTH_LIST_PREVIEW)
    review_queue = _case_list(facts["review_total"], review_items, measured=total > 0, reason=LIBRARY_EMPTY_SENTENCE)
    review_queue["oldest_age_days"] = facts["oldest_review_days"] if facts["review_total"] else None
    return {
        "project_id": project_id,
        "computed_at": now,
        "measured": total > 0,
        "total_cases": total,
        "active_cases": int(agg["active"] or 0),
        "retired_cases": int(agg["retired"] or 0),
        "automated_cases": facts["automated"],
        "automation_target_pct": facts["target"],
        "stale_drafts": facts["stale"],
        "retired_in_open_plans": facts["retired_in_open"],
        "avg_age_days": (
            round((now.timestamp() - float(avg_epoch)) / _DAY_SECONDS, 1) if total and avg_epoch is not None else None
        ),
        "older_than_180_days": older,
        "older_than_180_pct": round(older / total * 100, 1) if total else None,
        "score": score,
        "tag": tag,
        "components": components,
        "review_queue": review_queue,
        "strategy_gaps": build_strategy_gaps(facts, flaky=flaky, no_owner=no_owner),
        "flaky_active": flaky,
        "no_owner": no_owner,
        "suite_names": suite_names,
        "suite_names_total": suite_names_total,
        "requirements": requirements,
    }
