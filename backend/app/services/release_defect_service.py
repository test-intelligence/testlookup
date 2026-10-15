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
from typing import Any, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.release_filter import is_unattributed
from app.models.postgres import Defect
from app.services.defect_status import ACTIVE_STATUSES

#: Severities that block on their own.
BLOCKING_SEVERITIES = ("CRITICAL", "HIGH")

#: Statuses that mean the defect is still live: OPEN and IN_PROGRESS (W06
#: DEF-01). An in-progress defect is not fixed, so it blocks exactly like an
#: open one. Anything else — RESOLVED, INVALID, the legacy CLOSED — has been
#: dealt with, whatever the outcome. The vocabulary is ``defect_status``'s.
OPEN_STATUSES = ACTIVE_STATUSES


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
        Defect.resolution_status.in_(OPEN_STATUSES),
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

    Asserted impact wins when present (a NON-EMPTY list). Otherwise it falls
    back to where the defect was found — an untriaged defect is not a harmless
    one. Ids compare case-insensitively on both sides, exactly as
    :func:`affects_release_sql` does, so a legacy upper-case id counts in the
    gate AND on the Defects page, never in only one of them.
    """
    wanted = str(release_id).lower()
    asserted = defect.affects_releases
    if isinstance(asserted, list) and asserted:
        return wanted in {str(r).lower() for r in asserted}
    if defect.release_id is None:
        return False
    return str(defect.release_id).lower() == wanted


#: "Asserted" in SQL: a non-empty JSON array. ``json_typeof`` of SQL NULL is
#: NULL, so a SQL-NULL column, the JSON ``null`` scalar (what SQLAlchemy's JSON
#: type stores for Python ``None``) and ``[]`` all count as NOT asserted, exactly
#: as :func:`_affects` reads them.
def asserted_impact_sql(alias: str) -> str:
    column = f"{alias}.affects_releases"
    # A CASE, not ``json_typeof(...) = 'array' AND json_array_length(...) > 0``:
    # SQL does not promise to evaluate an AND left to right, and
    # ``json_array_length`` raises on a scalar (the JSON ``null`` every
    # hand-filed defect stores).
    return f"(CASE WHEN json_typeof({column}) = 'array' THEN json_array_length({column}) > 0 ELSE false END)"


def affects_release_sql(params: dict, release_ids: Sequence[str], *, alias: str = "d") -> str:
    """``AND`` the defect affects one of ``release_ids`` by the release gate's rule; ``""`` for no ids.

    The SQL twin of :func:`_affects` (asserted impact, else where it was found),
    bound to it by a parity test on PostgreSQL. Ids are compared lower-cased on
    both sides. The ``unattributed`` sentinel means "asserted nothing AND found
    in no release"; ids plus the sentinel are the union. Each id is its own
    bind (``:affects_rid_<n>``), so the fragment needs no expanding parameter
    and runs under a plain ``text()``.

    PostgreSQL-only (``json_typeof`` / ``json_array_elements_text``), like the
    rest of the raw analytics SQL; the gate keeps its portable Python test.
    """
    if not alias.isidentifier():
        raise ValueError(f"not a SQL alias: {alias!r}")
    values = [release_ids] if isinstance(release_ids, (str, uuid.UUID)) else list(release_ids or ())
    ids: dict[str, None] = {}
    sentinel = False
    for value in values:
        text_value = str(value).strip()
        if not text_value:
            continue
        if is_unattributed(text_value):
            sentinel = True
        else:
            ids[text_value.lower()] = None
    if not ids and not sentinel:
        return ""
    asserted = asserted_impact_sql(alias)
    parts: list[str] = []
    if ids:
        names = []
        for value in ids:
            name = f"affects_rid_{len(names)}"
            params[name] = value
            names.append(f":{name}")
        in_list = "(" + ", ".join(names) + ")"
        parts.append(
            f"CASE WHEN {asserted} "
            f"THEN EXISTS (SELECT 1 FROM json_array_elements_text({alias}.affects_releases) AS a(rid) "
            f"WHERE lower(a.rid) IN {in_list}) "
            f"ELSE lower(CAST({alias}.release_id AS text)) IN {in_list} END"
        )
    if sentinel:
        parts.append(f"(NOT {asserted} AND {alias}.release_id IS NULL)")
    return "AND (" + " OR ".join(parts) + ")"


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
