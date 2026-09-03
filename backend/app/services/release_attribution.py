"""Rungs 3 and 4 of the attribution ladder, plus phase attribution.

The ladder, and where this sits in it
-------------------------------------
1. explicit ``release_name`` from the client       (assertion)
2. external match — git tag / PR milestone         (assertion)
3. **project attribution rule**                    ← here
4. **release whose cutoff window contains the run** ← here
5. release active when the run executed
6. unattributed — legitimate, counted, not a page

Rungs 3 and 4 exist for projects that cannot supply a release name. Without
them such a project falls straight to rung 5, which cannot separate a hotfix
branch from a release candidate from trunk CI — every run lands in one release
and the release axis buys ordering but not de-blending.

Determinism is the whole point
------------------------------
Both rungs can match more than one thing, and "whichever the database returned
first" is not an answer: the same run re-ingested could land in a different
release, and nothing would look wrong. So both orderings are total, and both
tie-breaks end in ``id`` — a column that cannot tie.

That matters more here than in most places because the output is an
*attribution*, and a wrong one is invisible. The run still shows a release; it
is simply the wrong one.

Nothing here commits. Callers own their transaction.
"""
from __future__ import annotations

import fnmatch
import uuid
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AttributionMatchField,
    Release,
    ReleaseAttributionRule,
    ReleasePhase,
    TestRun,
)

logger = structlog.get_logger(__name__)

#: Fields matched with a case-insensitive glob. Branches and build labels are
#: patterned by nature ("release/*", "nightly-*").
_GLOB_FIELDS = {
    AttributionMatchField.BRANCH.value,
    AttributionMatchField.BUILD_NUMBER.value,
}


def _run_value(run: TestRun, field: str) -> Optional[str]:
    """Read the run attribute a rule matches on.

    ``tag`` is not a scalar — ``TestRun.tags`` is a JSON list — so it is
    handled by the caller rather than pretended to be one here.
    """
    if field == AttributionMatchField.BRANCH.value:
        return run.branch
    if field == AttributionMatchField.BUILD_NUMBER.value:
        return run.build_number
    if field == AttributionMatchField.ENVIRONMENT.value:
        return run.environment
    return None


def rule_matches(rule: ReleaseAttributionRule, run: TestRun) -> bool:
    """Does *rule* match *run*?

    Case-insensitive throughout. Branch names and environments arrive with
    inconsistent casing from different CI providers, and a rule that silently
    fails to fire because someone wrote ``Release/*`` is exactly the kind of
    defect that looks like "attribution just doesn't work".
    """
    pattern = (rule.match_pattern or "").strip()
    if not pattern:
        return False

    if rule.match_field == AttributionMatchField.TAG.value:
        # tags is a JSON list; a rule matches if ANY tag matches the glob.
        for tag in (run.tags or []):
            if isinstance(tag, str) and fnmatch.fnmatch(tag.lower(), pattern.lower()):
                return True
        return False

    value = _run_value(run, rule.match_field)
    if not value:
        # A rule cannot match a run that carries nothing in that field. Note
        # this is NOT the same as matching "*" — an absent branch is unknown,
        # not empty, and attributing on unknown data is how a rule quietly
        # captures every run in the project.
        return False

    if rule.match_field in _GLOB_FIELDS:
        return fnmatch.fnmatch(value.lower(), pattern.lower())
    return value.strip().lower() == pattern.lower()


async def match_attribution_rule(
    db: AsyncSession, project_id: uuid.UUID, run: TestRun
) -> Optional[ReleaseAttributionRule]:
    """Rung 3: the first enabled rule that matches, in a total order.

    Ordered by ``priority``, then ``created_at``, then ``id``. Priority is not
    unique — deliberately, so reordering is a single UPDATE rather than a dance
    around a constraint — and the two further keys make the tie deterministic
    anyway. Without them, two rules sharing a priority would attribute the same
    run differently on different days.
    """
    rules = (
        await db.execute(
            select(ReleaseAttributionRule)
            .where(
                ReleaseAttributionRule.project_id == project_id,
                ReleaseAttributionRule.is_enabled.is_(True),
            )
            .order_by(
                ReleaseAttributionRule.priority.asc(),
                ReleaseAttributionRule.created_at.asc(),
                ReleaseAttributionRule.id.asc(),
            )
        )
    ).scalars().all()

    for rule in rules:
        if rule_matches(rule, run):
            logger.info(
                "attribution_rule_matched",
                rule_id=str(rule.id),
                rule_name=rule.name,
                field=rule.match_field,
                run_id=str(run.id),
                target=rule.target_release_name,
            )
            return rule
    return None


async def match_cutoff_window(
    db: AsyncSession, project_id: uuid.UUID, executed_at: Optional[datetime]
) -> Optional[Release]:
    """Rung 4: the release whose cutoff window contains *executed_at*.

    Windows overlap in normal operation — a hotfix is validated while the next
    minor is in QA — so "the containing window" is usually several. The
    tie-break is **narrowest window first**, because a narrow window is the more
    specific claim: a two-day hotfix window inside a six-week release window
    means the hotfix, not the release.

    Then ``sort_key`` (lower version first, so a maintenance line wins over a
    newer one it overlaps), then ``created_at``, then ``id``. Four keys because
    each earlier one can genuinely tie, and the last cannot.

    Returns None when ``executed_at`` is unknown — guessing from ingest time
    here would defeat the point of having an execution timestamp at all.
    """
    if executed_at is None:
        return None

    candidates = (
        await db.execute(
            select(Release).where(
                Release.project_id == project_id,
                Release.cutoff_start_at.isnot(None),
                Release.cutoff_end_at.isnot(None),
                Release.cutoff_start_at <= executed_at,
                Release.cutoff_end_at >= executed_at,
            )
        )
    ).scalars().all()

    if not candidates:
        return None

    def _key(r: Release):
        return (
            (r.cutoff_end_at - r.cutoff_start_at),
            r.sort_key or "",
            r.created_at,
            str(r.id),
        )

    winner = sorted(candidates, key=_key)[0]
    if len(candidates) > 1:
        logger.info(
            "cutoff_window_tie_broken",
            candidates=len(candidates),
            chosen=str(winner.id),
            chosen_name=winner.name,
        )
    return winner


async def match_phase(
    db: AsyncSession,
    release_id: uuid.UUID,
    executed_at: Optional[datetime],
) -> Optional[uuid.UUID]:
    """Which phase of *release_id* was underway at *executed_at*.

    Same shape as the cutoff rung and the same tie-break reasoning: narrowest
    planned window first, then ``order_index``, then ``id``.

    Returns None freely. A run that belongs to a release but not to any phase
    is a legitimate, common state — phases are optional, and their planned
    windows need not cover the whole release. NULL ``phase_id`` means "in the
    release, not claimed by a phase", which is different from "unattributed"
    and must not be filled in with a guess.
    """
    if executed_at is None:
        return None

    phases = (
        await db.execute(
            select(ReleasePhase).where(
                ReleasePhase.release_id == release_id,
                ReleasePhase.planned_start.isnot(None),
                ReleasePhase.planned_end.isnot(None),
                ReleasePhase.planned_start <= executed_at,
                ReleasePhase.planned_end >= executed_at,
            )
        )
    ).scalars().all()

    if not phases:
        return None

    winner = sorted(
        phases,
        key=lambda p: (
            (p.planned_end - p.planned_start),
            p.order_index or 0,
            str(p.id),
        ),
    )[0]
    return winner.id
