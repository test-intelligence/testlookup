"""VIZ-201 -- the analytics scope: one parser, one authoriser, one suite rule.

What a report is filtered by (contract C1, ``contracts/viz/README.md``):

* ``project_id`` -- ONE project, or absent for "every project I can read".
  Repeating it is a 422: a release belongs to one project, and comparing
  projects is ``group_by=project`` (VIZ-203), not a second filter value.
* ``release_id`` -- repeatable, each a UUID or the ``unattributed`` sentinel.
* ``suite_name`` -- repeatable, 1-500 code points each.
* ``days`` -- 1..N (N is the route's historical cap, at most 365).

**OR within a dimension, AND across dimensions.** An empty list is "no
parameter". One value is exactly the pre-VIZ-201 request: the SQL each builder
below emits for one value is the text the route emitted before, so a legacy
single-value call answers byte-for-byte as it did (pinned by
``tests/integration/test_analytics_scope_postgres.py``).

Three pieces, each defined once:

1. :func:`parse_scope` -- pure validation, before any database access, raising
   :class:`~app.core.analytics_errors.AnalyticsQueryError` with the C1 rule id.
2. :func:`resolve_analytics_scope` -- authorisation. The project goes through
   ``resolve_project_scope`` and EVERY release id through
   ``resolve_release_query_scopes`` (the batch form of
   ``resolve_release_query_scope``: one ``IN`` query, same 404/403), all
   before a single data query runs, so one forbidden id is a 403/404 and no
   data for any id.
3. The SQL builders -- the effective-suite rule and every suite-match clause
   the analytics surfaces use. ``tests/test_analytics_scope_ratchet.py`` fails
   when a module outside this one spells a suite-match clause itself ("two
   modules, one rule" had already produced five drifting copies).

Index discipline: every filter is a CONDITIONAL fragment -- nothing is emitted
when nothing was asked -- and never a null-tolerant ``(:x IS NULL OR col = :x)``,
which a generic plan cannot resolve and which costs
``ix_test_runs_project_release_created`` on every call. Multi-value binds are
``bindparam(..., expanding=True)`` (see :func:`scoped_text`).
"""
from __future__ import annotations

import inspect
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Literal, Optional, Sequence, Union

from fastapi import Depends, Query, Request
from sqlalchemy import and_, bindparam, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, TextClause

from app.core.analytics_errors import POLICY_ATTR, AnalyticsQueryError
from app.core.release_filter import UNATTRIBUTED, is_unattributed
from app.models.viz_contracts import (
    MAX_RELEASES,
    MAX_SUITE_NAME_LENGTH,
    MAX_SUITES,
    MAX_WINDOW_DAYS,
)

#: A release filter as the services accept it: absent, one id (the legacy
#: shape), or several.
ReleaseArg = Union[None, str, uuid.UUID, Sequence[str]]
#: A suite filter as the services accept it: absent, one name, or several.
SuiteArg = Union[None, str, Sequence[str]]


# ── The scope ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalyticsScope:
    """A request's analytics scope after parsing AND authorisation.

    ``project_id`` and ``allowed_project_ids`` are ``resolve_project_scope``'s
    two slots, mutually exclusive as there: filter by the pin when set, else by
    the allowed set when not ``None``, else (admin) by nothing -- the services'
    SQL still applies the ``is_active`` guard in every branch.
    """

    project_id: Optional[uuid.UUID]
    allowed_project_ids: Optional[frozenset[uuid.UUID]]
    #: Canonical ``str(UUID)`` or :data:`UNATTRIBUTED`, deduplicated, in order.
    release_ids: tuple[str, ...]
    #: As sent, exact duplicates removed, in order.
    suite_names: tuple[str, ...]
    days: Optional[int]
    #: The caller may not read the requested scope and the route answers with
    #: its empty shape instead of a 403 (the metrics routes' historical
    #: behaviour, see :class:`ScopePolicy.on_denied`). Nothing else is resolved.
    denied: bool = False

    @property
    def project(self) -> Optional[str]:
        return str(self.project_id) if self.project_id is not None else None

    @property
    def window_days(self) -> int:
        """``days`` for a route whose policy has a window (it always does then)."""
        if self.days is None:
            raise RuntimeError("this route's scope policy declares no days window")
        return self.days

    @property
    def release_arg(self) -> Union[None, str, tuple[str, ...]]:
        """The service argument: ``None``, the single id (legacy), or all of them."""
        return _single_or_tuple(self.release_ids)

    @property
    def suite_arg(self) -> Union[None, str, tuple[str, ...]]:
        return _single_or_tuple(self.suite_names)


def _single_or_tuple(values: tuple[str, ...]) -> Union[None, str, tuple[str, ...]]:
    if not values:
        return None
    return values[0] if len(values) == 1 else values


@dataclass(frozen=True)
class ScopePolicy:
    """How one route reads the scope. Every field reflects that route's
    behaviour before VIZ-201, so converting it changed nothing observable."""

    #: ``None``: the route has no ``days`` parameter.
    default_days: Optional[int] = 30
    max_days: int = MAX_WINDOW_DAYS
    project_required: bool = False
    suites: bool = True
    releases: bool = True
    #: ``"raise"``: a project the caller cannot read is a 403
    #: (``resolve_project_scope``). ``"empty"``: a non-admin who names no
    #: project, or one they cannot read, gets the route's empty payload -- the
    #: metrics routes have always answered that way, and a 403 there would
    #: confirm the project exists.
    on_denied: Literal["raise", "empty"] = "raise"


@dataclass(frozen=True)
class ScopeRequest:
    """The parsed, not yet authorised, request."""

    project_id: Optional[uuid.UUID]
    release_ids: tuple[str, ...]
    suite_names: tuple[str, ...]
    days: Optional[int]


# ── 1. Parsing (no database) ────────────────────────────────────────────────


def _as_list(value: Any) -> list:
    """Query lists, a legacy single value from a direct caller, or nothing."""
    if value is None:
        return []
    if isinstance(value, (str, uuid.UUID)):
        return [value]
    return list(value)


def parse_project_id(value: Any, *, repeated: int = 1) -> Optional[uuid.UUID]:
    if repeated > 1:
        raise AnalyticsQueryError(
            "project_single_valued",
            "project_id is single-valued: send it once, or omit it for every "
            "project you can read. To compare projects, group by project "
            "(group_by=project) instead of repeating project_id.",
            param="project_id",
            allowed={"max": 1, "compare_with": "group_by=project"},
        )
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        # The value is never echoed: it is untrusted text.
        raise AnalyticsQueryError(
            "project_id_format",
            "project_id is a UUID, or omitted for every project you can read",
            param="project_id",
            allowed="UUID",
        ) from None


def parse_release_ids(values: Any) -> tuple[str, ...]:
    """UUIDs (any spelling ``uuid.UUID`` accepts, compared case-insensitively
    by canonicalising) and the ``unattributed`` sentinel. Duplicates collapse;
    more than :data:`MAX_RELEASES` distinct ids is ``release_cap``."""
    out: dict[str, None] = {}
    for index, raw in enumerate(_as_list(values)):
        if is_unattributed(raw if isinstance(raw, str) else None):
            out[UNATTRIBUTED] = None
            continue
        try:
            out[str(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))] = None
        except (ValueError, TypeError, AttributeError):
            raise AnalyticsQueryError(
                "release_id_format",
                f"release_id #{index + 1} is neither a UUID nor {UNATTRIBUTED!r}",
                param="release_id",
                allowed=["UUID", UNATTRIBUTED],
            ) from None
    if len(out) > MAX_RELEASES:
        raise AnalyticsQueryError(
            "release_cap",
            f"at most {MAX_RELEASES} distinct release_id values, got {len(out)}",
            param="release_id",
            allowed={"max": MAX_RELEASES},
        )
    return tuple(out)


def parse_suite_names(values: Any) -> tuple[str, ...]:
    """1-500 code points each; exact duplicates collapse; at most
    :data:`MAX_SUITES` distinct names (``suite_cap``). Markup is not rejected:
    a suite name is untrusted plain text, matched as it was ingested."""
    out: dict[str, None] = {}
    for index, raw in enumerate(_as_list(values)):
        name = str(raw)
        if not 1 <= len(name) <= MAX_SUITE_NAME_LENGTH:
            raise AnalyticsQueryError(
                "suite_name_length",
                f"suite_name #{index + 1} must be 1-{MAX_SUITE_NAME_LENGTH} characters",
                param="suite_name",
                allowed={"min_length": 1, "max_length": MAX_SUITE_NAME_LENGTH},
            )
        out[name] = None
    if len(out) > MAX_SUITES:
        raise AnalyticsQueryError(
            "suite_cap",
            f"at most {MAX_SUITES} distinct suite_name values, got {len(out)}",
            param="suite_name",
            allowed={"max": MAX_SUITES},
        )
    return tuple(out)


def parse_days(value: Any, *, max_days: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= max_days:
        raise AnalyticsQueryError(
            "window_days_range",
            f"days is an integer 1-{max_days}",
            param="days",
            allowed={"min": 1, "max": max_days},
        )
    return int(value)


def parse_scope(
    *,
    policy: ScopePolicy,
    project_id: Any = None,
    release_id: Any = None,
    suite_name: Any = None,
    days: Any = None,
    window_from: Any = None,
    window_to: Any = None,
    project_repeats: int = 1,
) -> ScopeRequest:
    """Validate everything before the database is touched (VIZ-210)."""
    if window_from is not None or window_to is not None:
        # Declared so it is never silently ignored. An explicit range needs an
        # end bound threaded through every query and the cache identity; that
        # is VIZ-203's chart-data endpoint, and these routes answer in days.
        raise AnalyticsQueryError(
            "window_range_unsupported",
            "this route takes a window in days; from/to is not supported here",
            param="from" if window_from is not None else "to",
            allowed=["days"],
        )
    project = parse_project_id(project_id, repeated=project_repeats)
    if policy.project_required and project is None:
        raise AnalyticsQueryError(
            "missing_parameter",
            "project_id is required on this route",
            param="project_id",
            allowed="UUID",
        )
    releases = parse_release_ids(release_id) if policy.releases else ()
    suites = parse_suite_names(suite_name) if policy.suites else ()
    window: Optional[int] = None
    if policy.default_days is not None:
        window = parse_days(policy.default_days if days is None else days, max_days=policy.max_days)
    return ScopeRequest(project, releases, suites, window)


# ── 2. Authorisation ────────────────────────────────────────────────────────


async def authorize_scope(
    db: AsyncSession, user: Any, request: ScopeRequest, policy: ScopePolicy
) -> AnalyticsScope:
    """Project first, then every release id; nothing is queried for data here.

    Imported from ``core.deps`` at call time so a test patching
    ``app.core.deps.get_accessible_project_ids`` reaches every check.
    """
    from app.core import deps

    if policy.on_denied == "empty":
        accessible = await deps.get_accessible_project_ids(db, user)
        if accessible is not None and (
            request.project_id is None or request.project_id not in accessible
        ):
            # The release ids are deliberately NOT resolved: the answer is the
            # route's empty payload whatever they are, as it always was.
            return AnalyticsScope(
                request.project_id, frozenset(accessible), (), request.suite_names,
                request.days, denied=True,
            )
        pinned, allowed = request.project_id, None
    else:
        pinned, allowed_set = await deps.resolve_project_scope(
            db, user, str(request.project_id) if request.project_id else None
        )
        allowed = frozenset(allowed_set) if allowed_set is not None else None

    # 404 unknown, 403 unreadable -- raised before any data query, so a list
    # holding one forbidden id returns nothing for the others. One IN query
    # and one membership lookup for all of them, with the answer the
    # per-id ``resolve_release_query_scope`` would give in request order.
    releases = await deps.resolve_release_query_scopes(db, request.release_ids, user)

    return AnalyticsScope(
        project_id=pinned,
        allowed_project_ids=allowed,
        release_ids=tuple(releases),
        suite_names=request.suite_names,
        days=request.days,
    )


async def resolve_analytics_scope(
    db: AsyncSession,
    user: Any,
    *,
    policy: ScopePolicy,
    project_id: Any = None,
    release_id: Any = None,
    suite_name: Any = None,
    days: Any = None,
    window_from: Any = None,
    window_to: Any = None,
    project_repeats: int = 1,
) -> AnalyticsScope:
    """Parse, then authorise. The plain-function form of the dependency, for
    callers outside a request (tests, jobs) that hold a session and a user."""
    parsed = parse_scope(
        policy=policy, project_id=project_id, release_id=release_id,
        suite_name=suite_name, days=days, window_from=window_from,
        window_to=window_to, project_repeats=project_repeats,
    )
    return await authorize_scope(db, user, parsed, policy)


def analytics_scope(policy: ScopePolicy) -> Callable[..., Awaitable[AnalyticsScope]]:
    """The FastAPI dependency for one route's policy.

    The signature FastAPI reads is built from the policy, so a route declares
    exactly the parameters it honours: a parameter it does not take stays
    undeclared (and ignored, as before VIZ-202 decides its fate), never
    declared-and-dropped.
    """
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    async def dependency(
        request: Request,
        project_id: Optional[str] = None,
        release_id: Optional[list[str]] = None,
        suite_name: Optional[list[str]] = None,
        days: Optional[int] = None,
        window_from: Optional[str] = None,
        window_to: Optional[str] = None,
        db: Any = None,
        current_user: Any = None,
    ) -> AnalyticsScope:
        return await resolve_analytics_scope(
            db, current_user, policy=policy,
            project_id=project_id, release_id=release_id, suite_name=suite_name,
            days=days, window_from=window_from, window_to=window_to,
            project_repeats=len(request.query_params.getlist("project_id")),
        )

    kw = inspect.Parameter.POSITIONAL_OR_KEYWORD
    params = [
        inspect.Parameter("request", kw, annotation=Request),
        inspect.Parameter(
            "project_id", kw,
            annotation=str if policy.project_required else Optional[str],
            default=Query(
                ... if policy.project_required else None,
                description=(
                    "One project (single-valued). Omit for every project you can read."
                    if not policy.project_required else "Project to read. Single-valued."
                ),
            ),
        ),
    ]
    if policy.releases:
        params.append(inspect.Parameter(
            "release_id", kw, annotation=Optional[list[str]],
            default=Query(
                None,
                description=(
                    f"Repeatable (OR, at most {MAX_RELEASES}): a release UUID or "
                    f"'{UNATTRIBUTED}' for runs no release claims."
                ),
            ),
        ))
    if policy.suites:
        params.append(inspect.Parameter(
            "suite_name", kw, annotation=Optional[list[str]],
            default=Query(
                None,
                description=(
                    f"Repeatable (OR, at most {MAX_SUITES}), 1-{MAX_SUITE_NAME_LENGTH} "
                    "characters; matched case-insensitively on the effective suite."
                ),
            ),
        ))
    if policy.default_days is not None:
        params.append(inspect.Parameter(
            "days", kw, annotation=int,
            default=Query(policy.default_days, description=f"Window in days, 1-{policy.max_days}."),
        ))
        params.append(inspect.Parameter(
            "window_from", kw, annotation=Optional[str],
            default=Query(None, alias="from", include_in_schema=False),
        ))
        params.append(inspect.Parameter(
            "window_to", kw, annotation=Optional[str],
            default=Query(None, alias="to", include_in_schema=False),
        ))
    params.append(inspect.Parameter("db", kw, annotation=AsyncSession, default=Depends(get_db)))
    params.append(inspect.Parameter("current_user", kw, default=Depends(get_current_active_user)))
    dependency.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    setattr(dependency, POLICY_ATTR, policy)
    return dependency


# ── 3. SQL builders: the effective suite and every suite-match clause ──────
#
# The effective suite of a test_case row: the run-level label for live_stream
# runs (their SDKs often stamp the class name on every event), the per-row
# suite for everything else (multi-<testsuite> uploads need each row bucketed
# by its own suite). Match by equality on it, never a loose OR across the two
# columns -- that attributed every test of a multi-suite run to its primary
# suite (Bug 2026-05-20).

#: Parameters :func:`scoped_text` binds as expanding lists.
_EXPANDING = ("suite_names", "suite_keys", "release_ids")


def scoped_text(sql: str, params: dict) -> TextClause:
    """``text(sql)`` with every multi-value bind this module emitted declared
    ``expanding=True``. Identical to ``text(sql)`` when none was emitted -- the
    single-value and no-filter statements are unchanged."""
    clause = text(sql)
    expanding: list[Any] = [
        bindparam(name, expanding=True)
        for name in _EXPANDING
        if name in params and re.search(rf":{name}\b", sql)
    ]
    return clause.bindparams(*expanding) if expanding else clause


def suite_keys(suite_name: SuiteArg) -> tuple[str, ...]:
    """The normalised match keys: trimmed, lower-cased, empties dropped,
    duplicates collapsed. A whitespace-only name normalises to nothing and so
    filters nothing -- the pre-VIZ-201 behaviour of every analytics route."""
    names = [suite_name] if isinstance(suite_name, str) else list(suite_name or ())
    keys = ((name or "").strip().lower() for name in names)
    return tuple(dict.fromkeys(key for key in keys if key))


def cache_identity(values: Iterable[str]) -> Optional[str]:
    """One cache-key segment for a filter dimension.

    ``None`` when empty, the value itself when single (so the pre-VIZ-201 key
    is unchanged and no cached entry is orphaned), and otherwise the sorted
    list behind a leading space. A normalised suite key or a release id never
    starts with a space, so a multi-value identity can never equal a
    single-value one.
    """
    items = list(dict.fromkeys(values))
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return " " + json.dumps(sorted(items), ensure_ascii=False)


def effective_suite_sql(tr: str = "tr", tc: str = "tc") -> str:
    """The effective suite of a ``test_cases`` row, as raw SQL."""
    return (
        "COALESCE("
        f"CASE WHEN {tr}.trigger_source = 'live_stream' "
        f"THEN NULLIF(TRIM({tr}.primary_suite_name), '') ELSE NULL END, "
        f"NULLIF(TRIM({tc}.suite_name), '')"
        ")"
    )


def effective_suite_expr() -> ColumnElement:
    """The effective suite as a Core expression over ``TestRun`` / ``TestCase``."""
    from app.models.postgres import TestCase, TestRun

    return func.coalesce(
        case(
            (
                TestRun.trigger_source == "live_stream",
                func.nullif(func.trim(TestRun.primary_suite_name), ""),
            ),
            else_=None,
        ),
        func.nullif(func.trim(TestCase.suite_name), ""),
    )


def _bind(params: dict, keys: tuple[str, ...], single: str, multi: str) -> str:
    """Bind ``keys`` and return the right-hand side: ``= :single`` or ``IN :multi``."""
    if len(keys) > 1:
        params[multi] = list(keys)
        return f"IN :{multi}"
    params[single] = keys[0] if keys else ""
    return f"= :{single}"


def suite_filter_sql(params: dict, suite_name: SuiteArg) -> str:
    """``AND`` effective suite in the requested suites; ``""`` when none."""
    keys = suite_keys(suite_name)
    if not keys:
        return ""
    return f"AND LOWER({effective_suite_sql()}) {_bind(params, keys, 'suite_name', 'suite_names')}"


def suite_match_sql(params: dict, suite_name: SuiteArg) -> str:
    """A bare predicate for a query that is ABOUT the suite (suite detail):
    always emitted, so no suite matches nothing rather than everything."""
    keys = suite_keys(suite_name)
    return f"LOWER({effective_suite_sql()}) {_bind(params, keys, 'suite_key', 'suite_keys')}"


def run_label_match_sql(params: dict, suite_name: SuiteArg) -> str:
    """A run matched by its run-level label alone -- for runs whose per-test
    rows never landed, where the label is the only evidence of the suite."""
    keys = suite_keys(suite_name)
    rhs = _bind(params, keys, "suite_name", "suite_names")
    return f"LOWER(TRIM(COALESCE(tr.primary_suite_name, ''))) {rhs}"


def run_touches_suite_sql(params: dict, suite_name: SuiteArg) -> str:
    """``AND`` the run is labelled with, or has a row in, a requested suite.

    Run-level (the trend chart sums run aggregates), so a run is kept whole
    when it touches the suite; ``""`` when no suite was asked for.
    """
    keys = suite_keys(suite_name)
    if not keys:
        return ""
    rhs = _bind(params, keys, "suite_name", "suite_names")
    return f"""
        AND (
          LOWER(TRIM(tr.primary_suite_name)) {rhs}
          OR EXISTS (
            SELECT 1 FROM test_cases tc
            WHERE tc.test_run_id = tr.id
              AND LOWER(TRIM(tc.suite_name)) {rhs}
          )
        )
        """


def row_or_run_label_sql(params: dict, suite_name: SuiteArg) -> str:
    """``AND`` the row's suite OR its run's label matches -- the dashboard's
    flaky headline rule, which keeps the run-level arm unrestricted. It is NOT
    the effective suite (a multi-suite upload's primary label admits its other
    suites' rows); preserved as-is because the count feeds a release-gate cap.
    ``""`` when no suite was asked for."""
    keys = suite_keys(suite_name)
    if not keys:
        return ""
    rhs = _bind(params, keys, "suite_name", "suite_names")
    return (
        f"AND (LOWER(TRIM(tc.suite_name)) {rhs} "
        f"OR LOWER(TRIM(tr.primary_suite_name)) {rhs})"
    )


def _in_or_eq(column: ColumnElement, keys: tuple[str, ...]) -> ColumnElement:
    return column.in_(keys) if len(keys) > 1 else column == keys[0]


def row_or_live_label_clause(suite_name: SuiteArg) -> Optional[ColumnElement]:
    """Core: the row's suite, or a ``live_stream`` run's label, matches."""
    from app.models.postgres import TestCase, TestRun

    keys = suite_keys(suite_name)
    if not keys:
        return None
    return or_(
        _in_or_eq(func.lower(func.trim(TestCase.suite_name)), keys),
        and_(
            TestRun.trigger_source == "live_stream",
            _in_or_eq(func.lower(func.trim(TestRun.primary_suite_name)), keys),
        ),
    )


def row_or_run_label_clause(suite_name: SuiteArg) -> ColumnElement:
    """Core: the row's suite, or its run's label (NULL read as ''), matches.
    The test-management case list's rule: a live-stream run's rows often carry
    no suite of their own. Unlike its siblings this never returns ``None``:
    an empty filter falls back to matching ``''`` on both arms (the
    historical no-filter behaviour), so callers can pass the result straight
    to ``.where()`` with no guard."""
    from app.models.postgres import TestCase, TestRun

    keys = suite_keys(suite_name)
    if not keys:
        # The historical behaviour of an empty suite: match '' on both arms.
        keys = ("",)
    return or_(
        _in_or_eq(func.lower(func.trim(TestCase.suite_name)), keys),
        _in_or_eq(func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, ""))), keys),
    )


def run_touches_suite_clause(suite_name: SuiteArg) -> Optional[ColumnElement]:
    """Core twin of :func:`run_touches_suite_sql`, correlated to ``TestRun``."""
    from app.models.postgres import TestCase, TestRun

    keys = suite_keys(suite_name)
    if not keys:
        return None
    # Select from TestCase explicitly and correlate ONLY TestRun: a bare
    # exists() auto-correlates every table it mentions and is left with no
    # FROM at all (InvalidRequestError, a 500 on every suite filter).
    tc_match = (
        select(TestCase.id)
        .where(
            TestCase.test_run_id == TestRun.id,
            _in_or_eq(func.lower(func.trim(TestCase.suite_name)), keys),
        )
        .correlate(TestRun)
        .exists()
    )
    return or_(_in_or_eq(func.lower(func.trim(TestRun.primary_suite_name)), keys), tc_match)


def run_in_suite_scope_clause(suite_name: SuiteArg) -> Optional[ColumnElement]:
    """Core, correlated to ``TestRun``: the run is IN a suite-scoped report.

    The rule the report's rows use, applied to the run: the run has at least
    one ``test_cases`` row whose EFFECTIVE suite is requested, or -- when its
    per-test rows never landed (the live-stream gap) -- its label is, because
    the label is then the only suite it has and the suites table attributes
    its aggregates to it. Unlike :func:`run_touches_suite_clause`, a run whose
    label matches but whose rows are all in another suite is NOT in scope
    (it would count a run with zero rows in the suite), and a live-stream
    run's rows are read by their effective suite, not ``tc.suite_name``.
    ``None`` when no suite was asked for.
    """
    from app.models.postgres import TestCase, TestRun

    keys = suite_keys(suite_name)
    if not keys:
        return None
    rows_in_scope = (
        select(TestCase.id)
        .where(
            TestCase.test_run_id == TestRun.id,
            _in_or_eq(func.lower(effective_suite_expr()), keys),
        )
        .correlate(TestRun)
        .exists()
    )
    any_row = select(TestCase.id).where(TestCase.test_run_id == TestRun.id).correlate(TestRun).exists()
    label = _in_or_eq(func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, ""))), keys)
    return or_(rows_in_scope, and_(~any_row, label))


def effective_suite_clause(suite_name: SuiteArg) -> Optional[ColumnElement]:
    """Core: the row's effective suite is one of the requested suites."""
    keys = suite_keys(suite_name)
    if not keys:
        return None
    return _in_or_eq(func.lower(effective_suite_expr()), keys)


def suite_label_in_sql(params: dict, column: str, names: Sequence[str]) -> str:
    """``LOWER(TRIM(coalesce(<column>, ''))) IN (LOWER(TRIM(:suite_name_0)), ...)``.

    The suite catalogue's rule (``suite_history_service``): an already
    projected label column against a page of names, each normalised in SQL.
    """
    placeholders = []
    for index, name in enumerate(names):
        key = f"suite_name_{index}"
        params[key] = name
        placeholders.append(f"LOWER(TRIM(:{key}))")
    return f"LOWER(TRIM(coalesce({column}, ''))) IN (" + ", ".join(placeholders) + ")"


def suite_label_eq_sql(column: str, param: str = "suite_name") -> str:
    """``LOWER(TRIM(<column>)) = LOWER(TRIM(:<param>))`` -- a projected label
    column against one name normalised in SQL (the suite trend)."""
    return f"LOWER(TRIM({column})) = LOWER(TRIM(:{param}))"


# ── Release fragments (raw SQL) ─────────────────────────────────────────────


def release_filter_sql(params: dict, release_id: ReleaseArg, *, table_alias: str = "tr") -> str:
    """``AND`` the run's primary release is one of the requested; ``""`` when none.

    One id emits exactly the pre-VIZ-201 fragment. Several are one expanding
    ``IN``; the ``unattributed`` sentinel among them adds ``OR ... IS NULL``.

    Two shapes this must never emit, both guarded by source tests that read
    this function with its docstring stripped (so naming them here is safe):

    * ``(:release_id IS NULL OR tr.primary_release_id = :release_id)`` -- the
      planner cannot tell which branch applies, stops using
      ``ix_test_runs_project_release_created`` and scans, for every caller,
      including the ones that asked for no release at all;
    * a join through ``release_test_run_links`` -- a run linked to two releases
      would be counted twice. The denormalised ``primary_release_id``
      (migration 0152) is one row per run.
    """
    ids = _release_list(release_id)
    column = f"{table_alias}.primary_release_id"
    if not ids:
        return ""
    if len(ids) == 1:
        if ids[0] == UNATTRIBUTED:
            return f"AND {column} IS NULL"
        params["release_id"] = ids[0]
        return f"AND {column} = :release_id"
    real = [item for item in ids if item != UNATTRIBUTED]
    params["release_ids"] = real
    if len(real) == len(ids):
        return f"AND {column} IN :release_ids"
    return f"AND ({column} IN :release_ids OR {column} IS NULL)"


def _release_list(release_id: ReleaseArg) -> list[str]:
    if release_id is None:
        return []
    values = [release_id] if isinstance(release_id, (str, uuid.UUID)) else list(release_id)
    out: dict[str, None] = {}
    for value in values:
        out[UNATTRIBUTED if is_unattributed(str(value)) else str(value)] = None
    return list(out)
