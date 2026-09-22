"""VIZ-204 -- the analytics envelope ``meta``: what a response covers.

Every analytics response carries an additive ``meta`` object (contract C2,
``contracts/viz/README.md``; validated by
``app.models.viz_contracts.EnvelopeMeta``). It states what the SERVER applied,
never what the client asked for:

* ``scope`` -- the projects (id + name), releases (id + name + status) and
  suites the numbers were filtered by, and the window (``from``/``to``/``days``,
  always ``timezone: "UTC"``).
* ``totals`` -- ``matched_*`` is the population the filters selected;
  ``total_*`` is the same project(s) and window with the release and suite
  filters removed, so a reader can see how much the filters excluded. Both come
  from ONE count on ``test_runs`` (:func:`_totals`). Soft-deleted projects are
  excluded from both, as everywhere else (``is_active``).
* ``ignored_filters`` -- a dimension the route received but cannot honour, with
  the reason. A filter is applied or declared here; it is never dropped.
* ``includes_in_progress`` -- in-progress runs ARE counted by the analytics
  queries (their aggregates move while they stream); this says how many are in
  the matched population, and ``partial_day`` names the current UTC day, which
  is still accumulating. ``as_of`` is when the numbers were read.

``meta`` is built by :func:`build_meta` alone, from the resolved
:class:`~app.services.analytics_scope.AnalyticsScope`, so no route can describe
its scope differently from the way the scope was applied. It never changes a
response's existing keys: MCP tools, the CLI and the SPA read those.

A list-shaped body (``/test-management/suites``) has no key to put ``meta``
under without breaking its readers, so a bounded SUMMARY of it travels in the
``X-Analytics-Meta`` response header instead (:data:`META_HEADER`). The
summary is deliberately not a C2 envelope -- ids and counts, no names, at
most :data:`HEADER_MAX_BYTES` -- for the reasons in :func:`header_summary`.
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.release_filter import UNATTRIBUTED
from app.services.analytics_scope import (
    AnalyticsScope,
    release_filter_sql,
    run_touches_suite_sql,
    scoped_text,
    suite_filter_sql,
    suite_keys,
)

#: The envelope's shape version. It is part of every cache key whose payload
#: carries ``meta`` (``metrics_service.get_dashboard_summary``): a cached
#: payload's shape change is invisible except live, so an entry written before
#: ``meta`` existed must never be served after a deploy. Bump it -- and the
#: key moves with it -- whenever ``meta`` changes shape.
META_SCHEMA_VERSION = 3

#: Where a SUMMARY of ``meta`` travels for a list-shaped body
#: (:func:`header_summary` -- not the C2 object, see there).
META_HEADER = "X-Analytics-Meta"

#: Hard cap on the header value, well under nginx's default 4 KB
#: ``proxy_buffer_size`` (which also has to hold every other header).
HEADER_MAX_BYTES = 2048

#: The totals window of a route whose figures have no window of their own
#: (flaky scores, systemic clusters, quarantine counts, defects). Such a route
#: declares ``window`` in ``ignored_filters``; ``scope.window`` and ``totals``
#: then describe the recent runs for context, not the figures.
REFERENCE_WINDOW_DAYS = 30

DIMENSIONS = ("release", "suite", "window")

_WINDOWLESS_REASON = (
    "These figures are not bounded by a time window. scope.window and totals "
    f"describe the last {REFERENCE_WINDOW_DAYS} days of runs for context only."
)


def ignored(dimension: str, reason: str) -> dict:
    """One ``ignored_filters`` entry. ``dimension`` is release, suite or window."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; expected one of {DIMENSIONS}")
    if not reason.strip():
        raise ValueError("an ignored filter needs a reason")
    return {"dimension": dimension, "reason": reason}


def utc_day_window_start(days: int) -> datetime:
    """UTC midnight ``days - 1`` days ago: the first bucket of a route that
    charts ``days`` whole UTC days ending today (``get_trend_data``)."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)


def _instant(moment: datetime) -> str:
    """RFC 3339 UTC (``...+00:00``), the contract's ``utc_instant`` profile."""
    return moment.astimezone(timezone.utc).isoformat()


async def build_meta(
    db: AsyncSession,
    scope: AnalyticsScope,
    *,
    pass_rate_basis: Optional[str] = None,
    window_start: Optional[datetime] = None,
    ignored_filters: Iterable[dict] = (),
    truncated: bool = False,
    truncated_total: Optional[int] = None,
    measured: Optional[bool] = None,
    reason: Optional[str] = None,
) -> dict:
    """The ``meta`` object for a response computed under ``scope``.

    ``window_start`` is the route's own lower bound when it is not
    ``now - days`` (the trend chart starts at a UTC midnight). A dimension in
    ``ignored_filters`` is removed from ``scope`` and from ``matched_*``: the
    envelope then describes exactly what the numbers were filtered by.
    ``measured=None`` derives it from the matched runs.
    """
    now = datetime.now(timezone.utc)
    declared = [dict(item) for item in ignored_filters]
    dims = {item["dimension"] for item in declared}
    if scope.days is None and "window" not in dims:
        declared.append(ignored("window", _WINDOWLESS_REASON))
        dims.add("window")

    days = REFERENCE_WINDOW_DAYS if (scope.days is None or "window" in dims) else scope.days
    start = window_start if (window_start is not None and "window" not in dims) else (
        now - timedelta(days=days)
    )
    release_ids = () if ("release" in dims or scope.denied) else scope.release_ids
    # The suites the SQL applied: the query's own normaliser (trimmed,
    # lower-cased, blanks dropped, duplicates collapsed), never the raw
    # request -- ``" "`` filters nothing and ``Orders``/``orders`` are one.
    # A denied scope applied nothing, so it lists nothing.
    suite_names = () if ("suite" in dims or scope.denied) else suite_keys(scope.suite_names)

    if scope.denied:
        projects: list[dict] = []
        releases: list[dict] = []
        counts = {"total_runs": 0, "total_executions": 0, "matched_runs": 0,
                  "matched_executions": 0, "in_progress": 0}
        if measured is None:
            measured = False
            reason = reason or "No project in this scope is readable by the caller."
    else:
        projects = await _projects(db, scope)
        releases = await _releases(db, release_ids)
        counts = await _totals(db, scope, start, release_ids, suite_names)

    if measured is None:
        measured = counts["matched_runs"] > 0
        if not measured:
            reason = reason or "No runs match this scope in the window."
    elif not measured and not (reason or "").strip():
        reason = "Not measured for this scope."

    return {
        "schema_version": META_SCHEMA_VERSION,
        "scope": {
            "projects": projects,
            "releases": releases,
            "suites": list(suite_names),
            "window": {
                "from": start.date().isoformat(),
                "to": now.date().isoformat(),
                "days": days,
                "timezone": "UTC",
            },
        },
        "totals": {
            "matched_runs": counts["matched_runs"],
            "total_runs": counts["total_runs"],
            "matched_executions": counts["matched_executions"],
            "total_executions": counts["total_executions"],
        },
        "pass_rate_basis": pass_rate_basis,
        "ignored_filters": declared,
        "truncated": bool(truncated),
        # ``truncated`` without a count is a caller bug the contract rejects
        # (``truncated_total``), so it is passed through rather than invented.
        "truncated_total": (
            int(truncated_total) if truncated and truncated_total is not None else None
        ),
        "measured": bool(measured),
        "reason": None if measured else reason,
        "includes_in_progress": counts["in_progress"],
        # The window always ends now, so today's UTC day is inside it and is
        # still accumulating (a streaming run's aggregates move between reads).
        "partial_day": now.date().isoformat(),
        "generated_at": _instant(now),
        "as_of": _instant(now),
    }


def nothing_applied(scope: AnalyticsScope) -> AnalyticsScope:
    """The scope of a route's "select a project" empty answer: no project was
    applied, so the envelope lists none and counts nothing (it must not list
    the caller's memberships as if the numbers covered them)."""
    return dataclasses.replace(scope, project_id=None, allowed_project_ids=frozenset())


def with_meta(payload: dict, meta: dict) -> dict:
    """``payload`` plus ``meta``; every existing key is left exactly as it was."""
    return {**payload, "meta": meta}


def refreshed(meta: dict) -> dict:
    """A cached ``meta``: ``as_of`` stays the moment the numbers were read,
    ``generated_at`` becomes this response's."""
    return {**meta, "generated_at": _instant(datetime.now(timezone.utc))}


def restamped(payload: Any) -> Any:
    """A cached ENVELOPE with :func:`refreshed` applied to its ``meta``.

    Returned unchanged (the same object, so a caller can test identity and
    skip re-rendering) when there is no ``meta`` dict to restamp. This is what
    VIZ-209 calls on a cache hit: replaying the stored bytes verbatim made a
    five-minute-old body claim ``generated_at`` was now, which is the one field
    a reader uses to decide whether to trust the numbers on screen.
    """
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or "generated_at" not in meta:
        return payload
    return {**payload, "meta": refreshed(meta)}


def without_generated_at(payload: Any) -> Any:
    """The same envelope with ``meta.generated_at`` removed, or unchanged (the
    same object) when there is none.

    VIZ-209's ETag is computed over THIS, not over the served bytes: an ETag
    that moved with ``generated_at`` would change on every response and no
    client could ever revalidate. Everything else, ``as_of`` included, stays
    in the material.
    """
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or "generated_at" not in meta:
        return payload
    return {**payload, "meta": {k: v for k, v in meta.items() if k != "generated_at"}}


def _compact(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def header_summary(meta: dict) -> dict:
    """The bounded summary of ``meta`` that :func:`meta_header` sends.

    **Not a C2 envelope** (decision, VIZ-204 review). A header has to stay
    far below nginx's default 4 KB ``proxy_buffer_size`` -- past it the proxy
    answers 502 -- and what makes ``meta`` grow is names: an admin's
    All-Projects scope lists every project by name (~40 projects is already
    4 KB), and 50 suite names of 500 CJK characters are ~150 KB once
    ASCII-escaped. C2 requires those names (``ScopeProject.name``, the
    ``suites`` strings -- a suite has no id), so any header small enough to
    be safe would either fail C2 or pass it by lying (``suites: []`` reads as
    "no suite filter"). The header therefore carries a different, documented
    object, marked ``"form": "summary"``:

    * ids and counts, never names: ``projects``/``releases`` as
      ``{"count", "ids"}``, ``suites`` as ``{"count"}``;
    * ``window``, ``totals``, ``pass_rate_basis``, ``truncated``,
      ``truncated_total``, ``measured``, ``reason``, ``includes_in_progress``,
      ``partial_day``, ``generated_at``, ``as_of`` exactly as in ``meta`` (all
      server-authored and bounded), and ``ignored_dimensions`` (the
      dimensions of ``ignored_filters``, without their prose);
    * ``header_truncated: false``.

    When even that exceeds :data:`HEADER_MAX_BYTES` (a large All-Projects id
    list), :func:`meta_header` sends :func:`_minimal_summary` instead.
    """
    scope = meta.get("scope") or {}
    project_ids = [str(p.get("id")) for p in scope.get("projects") or ()]
    release_ids = [str(r.get("id")) for r in scope.get("releases") or ()]
    return {
        "schema_version": meta.get("schema_version"),
        "form": "summary",
        "header_truncated": False,
        "projects": {"count": len(project_ids), "ids": project_ids},
        "releases": {"count": len(release_ids), "ids": release_ids},
        "suites": {"count": len(scope.get("suites") or ())},
        "window": scope.get("window"),
        "totals": meta.get("totals"),
        "pass_rate_basis": meta.get("pass_rate_basis"),
        "ignored_dimensions": sorted(
            {str(item.get("dimension")) for item in meta.get("ignored_filters") or ()}
        ),
        "truncated": meta.get("truncated"),
        "truncated_total": meta.get("truncated_total"),
        "measured": meta.get("measured"),
        "reason": meta.get("reason"),
        "includes_in_progress": meta.get("includes_in_progress"),
        "partial_day": meta.get("partial_day"),
        "generated_at": meta.get("generated_at"),
        "as_of": meta.get("as_of"),
    }


def _minimal_summary(meta: dict) -> dict:
    """The over-the-cap form: counts and totals only, fixed size."""
    scope = meta.get("scope") or {}
    return {
        "schema_version": meta.get("schema_version"),
        "form": "summary",
        "header_truncated": True,
        "projects": {"count": len(scope.get("projects") or ())},
        "releases": {"count": len(scope.get("releases") or ())},
        "suites": {"count": len(scope.get("suites") or ())},
        "totals": meta.get("totals"),
        "measured": meta.get("measured"),
        "as_of": meta.get("as_of"),
    }


def meta_header(meta: dict) -> str:
    """The ``X-Analytics-Meta`` value for ``meta``: :func:`header_summary` as
    compact ASCII JSON (``json.dumps`` escapes every control character, so
    nothing can split the header), at most :data:`HEADER_MAX_BYTES` bytes."""
    value = _compact(header_summary(meta))
    if len(value) <= HEADER_MAX_BYTES:
        return value
    return _compact(_minimal_summary(meta))


def query_dimensions(query_params: Any) -> set[str]:
    """The filter dimensions a request actually sent, from its query string."""
    sent = set()
    if query_params.getlist("release_id"):
        sent.add("release")
    if query_params.getlist("suite_name"):
        sent.add("suite")
    return sent


# ── queries ─────────────────────────────────────────────────────────────────


async def _projects(db: AsyncSession, scope: AnalyticsScope) -> list[dict]:
    """The live projects the numbers were computed over, by name."""
    if scope.project_id is not None:
        stmt = text("SELECT id, name FROM projects WHERE id = :pid AND is_active")
        params: dict = {"pid": scope.project_id}
    elif scope.allowed_project_ids is not None:
        if not scope.allowed_project_ids:
            return []
        stmt = text(
            "SELECT id, name FROM projects WHERE id IN :pids AND is_active ORDER BY name, id"
        ).bindparams(bindparam("pids", expanding=True))
        params = {"pids": sorted(scope.allowed_project_ids, key=str)}
    else:
        stmt = text("SELECT id, name FROM projects WHERE is_active ORDER BY name, id")
        params = {}
    rows = (await db.execute(stmt, params)).all()
    return [{"id": str(row.id), "name": str(row.name or "")} for row in rows]


async def _releases(db: AsyncSession, release_ids: tuple[str, ...]) -> list[dict]:
    """Each applied release with its name and status, in request order. An
    archived release keeps its name and says so in ``status``."""
    real = [rid for rid in release_ids if rid != UNATTRIBUTED]
    found: dict[str, Any] = {}
    if real:
        stmt = text(
            "SELECT id, name, status FROM releases WHERE id IN :rids"
        ).bindparams(bindparam("rids", expanding=True))
        for row in (await db.execute(stmt, {"rids": [uuid.UUID(r) for r in real]})).all():
            found[str(row.id)] = row
    out: list[dict] = []
    for rid in release_ids:
        if rid == UNATTRIBUTED:
            out.append({"id": UNATTRIBUTED, "name": "Unattributed", "status": UNATTRIBUTED})
            continue
        release = found.get(rid)
        out.append({
            "id": rid,
            "name": str(release.name or "") if release is not None else "",
            "status": str(release.status or "unknown") if release is not None else "unknown",
        })
    return out


def _tenant_sql(params: dict, scope: AnalyticsScope) -> str:
    """The project restriction the analytics queries apply, plus ``is_active``."""
    live = "AND tr.project_id IN (SELECT id FROM projects WHERE is_active)"
    if scope.project_id is not None:
        params["project_id"] = str(scope.project_id)
        return f"AND tr.project_id = :project_id {live}"
    if scope.allowed_project_ids is not None:
        if not scope.allowed_project_ids:
            return "AND FALSE"
        params["meta_project_ids"] = sorted(str(p) for p in scope.allowed_project_ids)
        return f"AND tr.project_id IN :meta_project_ids {live}"
    return live


async def _totals(
    db: AsyncSession,
    scope: AnalyticsScope,
    start: datetime,
    release_ids: tuple[str, ...],
    suite_names: tuple[str, ...],
) -> dict:
    """One count on ``test_runs``: the window's runs and executions, and the
    part of them the release and suite filters select.

    Executions are the runs' recorded ``total_tests``. Under a suite filter a
    run contributes its rows in the suite (by effective suite), or -- when its
    per-test rows have not landed yet, the mid-ingest live-stream case -- its
    run total, the same fallback the dashboard applies
    (``metrics_service._period_stats``). A run's share is capped at the run's
    own total, so ``matched <= total`` holds even while rows and aggregates
    disagree mid-ingest.
    """
    params: dict = {"start": start}
    tenant = _tenant_sql(params, scope)
    release_arg: Any = release_ids[0] if len(release_ids) == 1 else (release_ids or None)
    suite_arg: Any = suite_names[0] if len(suite_names) == 1 else (suite_names or None)
    match = " ".join((
        "TRUE",
        release_filter_sql(params, release_arg),
        run_touches_suite_sql(params, suite_arg),
    ))
    executions = "COALESCE(tr.total_tests, 0)"
    if suite_names:
        in_suite = suite_filter_sql(params, suite_arg)
        executions = (
            "CASE WHEN EXISTS (SELECT 1 FROM test_cases tcx WHERE tcx.test_run_id = tr.id) "
            "THEN LEAST(COALESCE(tr.total_tests, 0), "
            f"(SELECT COUNT(*) FROM test_cases tc WHERE tc.test_run_id = tr.id {in_suite})) "
            "ELSE COALESCE(tr.total_tests, 0) END"
        )
    stmt = scoped_text(
        f"""
        SELECT
            COUNT(*)                                          AS total_runs,
            COALESCE(SUM(COALESCE(tr.total_tests, 0)), 0)     AS total_executions,
            COUNT(*) FILTER (WHERE {match})                   AS matched_runs,
            COALESCE(SUM({executions}) FILTER (WHERE {match}), 0) AS matched_executions,
            COUNT(*) FILTER (WHERE {match} AND tr.status = 'IN_PROGRESS') AS in_progress
        FROM test_runs tr
        WHERE tr.created_at >= :start
          {tenant}
        """,
        params,
    )
    if "meta_project_ids" in params:
        stmt = stmt.bindparams(bindparam("meta_project_ids", expanding=True))
    row = (await db.execute(stmt, params)).one()
    return {
        "total_runs": int(row.total_runs or 0),
        "total_executions": int(row.total_executions or 0),
        "matched_runs": int(row.matched_runs or 0),
        "matched_executions": int(row.matched_executions or 0),
        "in_progress": int(row.in_progress or 0),
    }
