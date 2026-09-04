"""Which defects block a release (S7a).

The distinction this module exists for
---------------------------------------
A defect has two relationships to a release, and gating on the wrong one is
wrong in opposite directions at the same time:

* ``release_id`` — where it was FOUND. Gating on this alone lets every
  INHERITED defect through: a defect found in 2.3.0 and still open does not
  appear when you ask 2.4.0 what is blocking it, so the release ships over a
  known open bug.
* ``affects_releases`` — which releases it IMPACTS. Gating on this alone misses
  every defect nobody has triaged for impact, because the column is NULL until
  somebody asserts something.

So a release is blocked by defects that AFFECT it, with "found in it" as the
fallback when impact was never asserted. NULL means "not triaged", not "harmless"
— treating an untriaged defect as affecting nothing is how a gate quietly stops
blocking.

Why severity is not enough
--------------------------
The obvious criterion is "no open CRITICALs". That reads as a strict gate and is
a lenient one: severity is set by whoever filed the defect, is frequently absent
on machine-created rows, and a NULL severity is not a low one. Unrated defects
are reported separately rather than silently excluded, so a gate cannot be
passed by failing to fill in a field.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Defect

#: Severities that block on their own.
BLOCKING_SEVERITIES = ("CRITICAL", "HIGH")

#: Statuses that mean the defect is still live. Anything else — RESOLVED,
#: CLOSED, WONTFIX — has been dealt with, whatever the outcome.
OPEN_STATUS = "OPEN"


async def blocking_defects(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    project_id: uuid.UUID | str,
) -> list[Defect]:
    """Open defects that affect this release.

    Matches a defect when EITHER it names this release in ``affects_releases``
    OR it was found here and has asserted nothing about impact. The second half
    is the fallback that keeps an untriaged defect blocking: without it, a gate
    would stop blocking the moment somebody forgot to fill in a field.

    Project-scoped explicitly. ``release_id`` is already project-scoped, but
    nothing at the database level stops a defect row referencing a release in
    another project, and a gate is exactly the wrong place to trust that.
    """
    stmt = select(Defect).where(
        Defect.project_id == project_id,
        Defect.resolution_status == OPEN_STATUS,
        or_(
            # Either it asserts SOME impact — the exact release is checked in
            # Python below — or it was found here. Narrowing to these two cases
            # in SQL keeps the row set small without needing a JSON containment
            # operator, which differs between Postgres and SQLite.
            Defect.affects_releases.isnot(None),
            Defect.release_id == release_id,
        ),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    # The containment test is done in Python rather than SQL: `affects_releases`
    # is a portable JSON column, and the operators for searching inside one
    # differ between Postgres and SQLite. Filtering here keeps the query honest
    # on both and the row count is bounded by open defects in one project.
    return [d for d in rows if _affects(d, release_id)]


def _affects(defect: Defect, release_id: uuid.UUID | str) -> bool:
    """Whether this defect blocks the given release.

    Asserted impact wins when present. Otherwise it falls back to where the
    defect was found — an untriaged defect is not a harmless one.
    """
    asserted = defect.affects_releases
    if asserted:
        return str(release_id) in {str(r) for r in asserted}
    return str(defect.release_id) == str(release_id)


def summarise_blocking(defects: list[Defect]) -> dict[str, Any]:
    """The gate's view of a release's open defects.

    Reports unrated defects SEPARATELY rather than dropping them. Severity is
    set by whoever filed the defect and is often absent on machine-created rows;
    a NULL severity is not a low one, and a gate that silently ignores unrated
    defects can be passed by leaving a field blank.
    """
    blocking = [d for d in defects if (d.severity or "").upper() in BLOCKING_SEVERITIES]
    unrated = [d for d in defects if not (d.severity or "").strip()]
    return {
        "blocking_count": len(blocking),
        "blocking": [
            {"id": str(d.id), "title": d.title, "severity": d.severity}
            for d in blocking
        ],
        # Neither counted as blocking nor hidden. Somebody has to decide.
        "unrated_count": len(unrated),
        "unrated": [{"id": str(d.id), "title": d.title} for d in unrated],
        "open_total": len(defects),
        # A release with unrated defects has not been fully assessed, and the
        # gate says so rather than implying the rated ones are the whole story.
        "fully_triaged": not unrated,
    }


def verdict_contribution(summary: dict[str, Any]) -> tuple[str, list[str]]:
    """What the defect criterion contributes to a release verdict.

    Three-valued for the same reason the phase gate is: "blocked by a known
    critical" and "cannot tell, defects are untriaged" need different actions,
    and a boolean would send somebody to fix the wrong thing.
    """
    if summary["blocking_count"]:
        return "NO_GO", [
            f"{d['severity']} defect open: {d['title'] or d['id']}"
            for d in summary["blocking"]
        ]
    if not summary["fully_triaged"]:
        return "NOT_EVALUATED", [
            f"{summary['unrated_count']} open defect(s) have no severity, so the "
            f"blocking criterion cannot be evaluated"
        ]
    return "GO", []


async def defects_for_release(
    db: AsyncSession,
    release_id: uuid.UUID | str,
    project_id: uuid.UUID | str,
) -> dict[str, Any]:
    """The whole defect picture for one release, ready to attach to a verdict."""
    found = await blocking_defects(db, release_id, project_id)
    summary = summarise_blocking(found)
    verdict, reasons = verdict_contribution(summary)
    return {**summary, "verdict": verdict, "reasons": reasons}


def assert_affects(defect: Defect, release_ids: list[str]) -> Optional[list[str]]:
    """Record which releases a defect impacts, deduplicated and stable.

    Sorted so the stored value does not churn on rewrite — a column that
    reorders itself makes every audit diff look like a change.
    """
    if not release_ids:
        return None
    return sorted({str(r) for r in release_ids})
