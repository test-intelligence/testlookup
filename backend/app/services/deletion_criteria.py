"""The criteria model for deletion, and the resolver that freezes a candidate set (S5).

**Run-scoped only, by construction.** ``resolve_criteria_candidates`` returns
run ids and nothing else. The audit logs, provenance records, expired agent
memory and compliance packs belong to the nightly policy purge and its four
clocks; a criterion like ``older_than_days`` cannot express them and must not
reach them. ``resolve_run_candidates`` already pins those plan filters to
``false()`` — this resolver simply never produces the inputs that would let
them fire.

**AND across fields, OR within a list.** ``statuses=[FAILED, STOPPED],
branches=[main]`` means *(failed OR stopped) AND on main*, not *failed OR
(stopped and on main)*. The two readings differ whenever more than one field is
supplied, which is why the tests assert a case where they disagree rather than
one where both happen to give the same answer.

**Why freezing matters (N3/N4).** Re-entrancy holds for the age-based purge:
its inputs are ``(policy, now)``, so re-running the resolver gives the same
answer. It does NOT hold for criteria over mutable columns — ``TestRun.status``
is rewritten by ``_update_run_aggregates`` and by live-session close, and
``primary_suite_name`` is stamped at session close. Execute is asynchronous, so
re-running the resolver at execute time would delete a different set from the
one an ADMIN authorised. Preview therefore materializes the ids and their hash;
execute replays that frozen set and refuses if the world moved.

Freezing is also what makes ``statuses`` a legitimate criterion at all: once
the id set is fixed, the source column's mutability stops mattering.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, or_, select

from app.models.postgres import LaunchStatus, TestCase, TestRun

logger = structlog.get_logger(__name__)

#: A criteria delete is interactive and its result is displayed for
#: confirmation; an unbounded set is neither reviewable nor safe to authorise.
MAX_RUN_IDS = 500
MAX_CANDIDATES = 10_000

#: Fields that actually narrow the set. ``project_id`` is not among them: it is
#: always required, so accepting it alone would turn "delete by criteria" into
#: "delete this entire project" behind a form that looks like a filter.
NARROWING_FIELDS = (
    "date_from",
    "date_to",
    "older_than_days",
    "run_ids",
    "statuses",
    "suite_names",
    "branches",
    "environments",
)


class RetentionCriteria(BaseModel):
    """What to delete. Every field is optional; at least one must be given.

    ``suite_match`` decides what "in this suite" means for a run that contains
    several. ``only`` — the default — matches the run's own suite label and is
    the conservative reading: it will not delete a multi-suite run because one
    of its suites was selected. ``any`` matches a run with any test case in the
    suite, and is the destructive reading, so it is opt-in.
    """

    model_config = ConfigDict(extra="forbid")

    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    older_than_days: Optional[int] = Field(None, ge=1, le=3650)
    run_ids: Optional[list[uuid.UUID]] = Field(None, max_length=MAX_RUN_IDS)
    statuses: Optional[list[str]] = None
    suite_names: Optional[list[str]] = None
    suite_match: Literal["only", "any"] = "only"
    branches: Optional[list[str]] = None
    environments: Optional[list[str]] = None

    @model_validator(mode="after")
    def _at_least_one_narrowing_field(self):
        if not any(getattr(self, f) for f in NARROWING_FIELDS):
            raise ValueError(
                "at least one criterion is required — a project id alone is a "
                "full project purge, not a filtered deletion"
            )
        return self

    @model_validator(mode="after")
    def _statuses_use_the_columns_vocabulary(self):
        if self.statuses:
            allowed = {s.value for s in LaunchStatus}
            unknown = [s for s in self.statuses if s not in allowed]
            if unknown:
                # A status outside the column's vocabulary matches nothing,
                # forever — the delete would report success having found zero.
                raise ValueError(
                    f"unknown status(es) {unknown}; allowed: {sorted(allowed)}"
                )
        return self

    @model_validator(mode="after")
    def _date_range_is_ordered(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    def effective_date_to(self, *, now: Optional[datetime] = None) -> Optional[datetime]:
        """``older_than_days`` is a convenience alias for ``date_to``.

        When both are supplied the EARLIER wins: each is a ceiling on how new a
        run may be, and honouring only one of them would silently delete runs
        the other excluded.
        """
        now = now or datetime.now(timezone.utc)
        bounds = [b for b in (
            self.date_to,
            now - timedelta(days=self.older_than_days) if self.older_than_days else None,
        ) if b is not None]
        return min(bounds) if bounds else None


def build_predicates(
    criteria: RetentionCriteria,
    *,
    project_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> list:
    """The WHERE clause: AND across fields, OR within a list.

    Returned as a list of predicates so callers ``and_(*...)`` them — the
    conjunction is the caller's, and a list makes the AND explicit rather than
    an artefact of how they were combined.
    """
    where = [TestRun.project_id == project_id]

    if criteria.date_from:
        where.append(TestRun.created_at >= criteria.date_from)
    date_to = criteria.effective_date_to(now=now)
    if date_to:
        where.append(TestRun.created_at < date_to)
    if criteria.run_ids:
        where.append(TestRun.id.in_(criteria.run_ids))
    if criteria.statuses:
        where.append(TestRun.status.in_(criteria.statuses))
    if criteria.branches:
        where.append(TestRun.branch.in_(criteria.branches))
    if criteria.environments:
        where.append(TestRun.environment.in_(criteria.environments))

    if criteria.suite_names:
        if criteria.suite_match == "only":
            # The run's own label — indexed (ix_test_runs_primary_suite_name)
            # and the conservative reading: a multi-suite run is not deleted
            # because one of its suites was named.
            where.append(TestRun.primary_suite_name.in_(criteria.suite_names))
        else:
            # "any": the run has a test case in one of these suites. Suite
            # membership lives on BOTH columns — old live-stream runs carry it
            # only at run level, file uploads only per test case — so a filter
            # reading either one alone misses runs and under-deletes silently.
            where.append(
                or_(
                    TestRun.primary_suite_name.in_(criteria.suite_names),
                    select(TestCase.id)
                    .where(
                        and_(
                            TestCase.test_run_id == TestRun.id,
                            TestCase.suite_name.in_(criteria.suite_names),
                        )
                    )
                    .exists(),
                )
            )

    return where


async def resolve_criteria_candidates(
    db,
    *,
    project_id: uuid.UUID,
    criteria: RetentionCriteria,
    now: Optional[datetime] = None,
) -> list[uuid.UUID]:
    """Materialize the run ids this criteria set selects, newest first.

    Run-scoped by construction: this returns run ids and nothing else, so the
    criteria path cannot reach the audit, provenance, memory or compliance-pack
    classes even by accident.

    Bounded at :data:`MAX_CANDIDATES`. A set larger than that is not something
    an operator can meaningfully review before authorising, and the caller
    reports the truncation rather than silently deleting the first ten thousand.
    """
    where = build_predicates(criteria, project_id=project_id, now=now)
    rows = (
        await db.execute(
            select(TestRun.id)
            .where(and_(*where))
            .order_by(TestRun.created_at.desc())
            .limit(MAX_CANDIDATES + 1)
        )
    ).scalars().all()
    return list(rows)


async def foreign_run_ids(
    db, *, project_id: uuid.UUID, run_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Ids in ``run_ids`` that do NOT belong to ``project_id``.

    Any hit fails the WHOLE request rather than being filtered out silently: a
    caller who names another tenant's run has either a bug or bad intent, and
    quietly deleting the subset that happened to be theirs tells them neither.
    It also confirms the foreign id exists, which is why the refusal must not
    say which of them was foreign.
    """
    if not run_ids:
        return []
    owned = set(
        (
            await db.execute(
                select(TestRun.id).where(
                    TestRun.id.in_(run_ids), TestRun.project_id == project_id
                )
            )
        ).scalars().all()
    )
    return [r for r in run_ids if r not in owned]
