"""Regression: the release read axis, *executed* rather than read.

Three defects, one root cause
-----------------------------
S4a and S4b shipped with thorough tests — ``test_release_read_axis.py``,
``test_flaky_in_release.py`` — and every one of them is a source assertion:
``"{release_filter}" in src``, ``"_add_release_param(params, release_id)" in
src``, ``"is_unattributed" in code``. Not one of them ever *calls* the function.
So the axis was verified as text and never as behaviour, and three defects
walked straight through:

**D1 — a bind that was never bound (500).** ``suite_detail``'s run-level
fallback branch builds a *fresh* params dict and then interpolates the same
``release_filter`` fragment that was built against the outer ``params``. The
rendered SQL said ``AND tr.primary_release_id = :release_id`` with nothing
bound: ``StatementError`` → 500. Reproduced by picking a release and opening a
suite that only ran in a different one — all three primary queries come back
empty, which is exactly what enters that branch.

**D2 — half a response scoped (wrong numbers, no error).**
``coverage_stats`` runs two queries and returns both in one payload. Only
``suite_query`` carried the filter, so the page showed a release-scoped table
underneath project-wide headline tiles, and the frontend derived its coverage
health score from the unscoped half. ``"{release_filter}" in src`` was true the
whole time — the string was present *once*, in the other query.

**D3 — the sentinel parsed as a UUID (500).** The Unattributed bucket travels
as the literal string ``unattributed``, not a UUID. ``/flaky-scores`` called
``uuid.UUID(release_id)`` unguarded. Its existing test asserts
``"is_unattributed" in code`` — which matched the *import line*.

What these tests do differently
-------------------------------
They execute. A recording session captures every ``(sql, params)`` pair the
service actually issues, and the invariants are checked against what was run,
not against what the source says. The two structural tests that remain are
count-based (D2) and position-based (D3), because "the name appears somewhere"
is the precise failure mode being guarded against.
"""
from __future__ import annotations

import ast
import inspect
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.services import analytics_service as svc  # noqa: E402

RELEASE_UUID = "3f9a5f7e-1c2b-4d6a-9e8f-0a1b2c3d4e5f"


# ── The recording session ────────────────────────────────────────────────────


class _Row:
    """A result row that answers anything with a zero.

    ``_mapping`` is empty on purpose: ``suite_detail`` decides whether to enter
    its fallback branch with ``summary.get("total_executions")``, and an empty
    mapping makes that falsy — which is the branch D1 lived in.
    """

    _mapping: dict = {}

    def __getattr__(self, name):
        return 0


class _Result:
    def one(self):
        return _Row()

    def fetchall(self):
        return []

    def scalars(self):
        return self

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        # VIZ-204: /flaky-scores counts the matched scores (``meta.truncated``).
        return 0


class RecordingSession:
    """Captures every statement and the params it was executed with.

    The whole point of this file: a fake that records the *pair*. Checking the
    SQL alone reproduces the blindness that let D1 ship, because the rendered
    SQL was always correct — it was the params dict that was missing a key.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _Result()


def _placeholders(sql: str) -> set[str]:
    """Named binds actually referenced by the statement.

    ``:release_id`` only — never ``::text``, which is a Postgres cast and
    appears in these queries (``tr.id::text``). A naive ``:(\\w+)`` match reads
    ``::text`` as a bind named ``text``.
    """
    return set(re.findall(r"(?<!:):([a-z_][a-z0-9_]*)", sql))


def _assert_every_bind_is_bound(session: RecordingSession) -> None:
    """D1's invariant, and it generalises to every future analytics query.

    A statement that references a bind the params dict does not carry is a
    ``StatementError`` at execute time — a 500, not a wrong number.
    """
    for sql, params in session.calls:
        missing = _placeholders(sql) - set(params)
        assert not missing, (
            f"statement references {sorted(missing)} but was executed with "
            f"{sorted(params)} — this raises StatementError at runtime.\n"
            f"SQL:\n{sql}"
        )


def _release_scoped(sql: str) -> bool:
    return "primary_release_id" in sql


def _touches_runs(sql: str) -> bool:
    return re.search(r"(FROM|JOIN)\s+test_runs\b", sql) is not None


# ── D1: the fallback branch is reachable and its binds are bound ─────────────


@pytest.mark.asyncio
async def test_suite_detail_fallback_binds_the_release_it_filters_on():
    """The exact 500. Enters the fallback branch *with* a release set.

    Before the fix this session records a statement containing
    ``:release_id`` alongside a params dict that has no such key — which in
    production is a ``StatementError`` from asyncpg.
    """
    db = RecordingSession()
    await svc.suite_detail(
        db, project_id=str(uuid.uuid4()), suite_name="api", days=30,
        release_id=RELEASE_UUID,
    )
    fallback = [c for c in db.calls if "NOT EXISTS" in c[0]]
    assert fallback, (
        "the run-level fallback never ran — this test no longer reaches the "
        "branch it exists to guard"
    )
    _assert_every_bind_is_bound(db)


@pytest.mark.asyncio
async def test_suite_detail_fallback_actually_filters_by_release():
    """Binding the value is necessary, not sufficient.

    A fallback that bound ``release_id`` but dropped the predicate would pass
    the test above while answering project-wide — the empty state would show
    runs from every release.
    """
    db = RecordingSession()
    await svc.suite_detail(
        db, project_id=str(uuid.uuid4()), suite_name="api", days=30,
        release_id=RELEASE_UUID,
    )
    for sql, params in (c for c in db.calls if "NOT EXISTS" in c[0]):
        assert _release_scoped(sql), (
            "the fallback answers project-wide under a release filter"
        )
        assert params.get("release_id") == RELEASE_UUID


@pytest.mark.asyncio
async def test_every_statement_of_every_release_accepting_read_is_bound():
    """The class, not the instance.

    D1 was one branch in one function. This runs each release-accepting
    analytics read end to end and applies the bind invariant to every
    statement it issues, so the next function that builds a second params dict
    fails here rather than in production.
    """
    pid = str(uuid.uuid4())
    # (name, extra positional args after `db, project_id, days`)
    reads = [
        ("coverage_stats", ()),
        ("flaky_tests", (50,)),
        ("failure_categories", ()),
        ("top_failing_tests", (50,)),
    ]
    for name, extra in reads:
        db = RecordingSession()
        await getattr(svc, name)(db, pid, 30, *extra, release_id=RELEASE_UUID)
        assert db.calls, f"{name} issued no statements — did the call fail?"
        _assert_every_bind_is_bound(db)

    db = RecordingSession()
    await svc.suite_detail(db, pid, "api", 30, release_id=RELEASE_UUID)
    assert db.calls
    _assert_every_bind_is_bound(db)


# ── D2: one response, one scope ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_both_halves_of_coverage_share_one_scope():
    """The defect that produced no error at all.

    ``coverage_stats`` returns ``summary`` and ``suites`` in one payload. With
    only the suite half filtered, the page put release-scoped rows under
    project-wide tiles — two different questions answered side by side under
    one heading, with nothing to tell them apart.
    """
    db = RecordingSession()
    await svc.coverage_stats(
        db, str(uuid.uuid4()), 30, release_id=RELEASE_UUID
    )
    assert len(db.calls) == 2, "coverage_stats should issue exactly two queries"
    unscoped = [sql for sql, _ in db.calls if not _release_scoped(sql)]
    assert not unscoped, (
        f"{len(unscoped)} of {len(db.calls)} coverage queries ignored the "
        "release — the summary tiles and the suite rows would answer "
        "different questions in one response"
    )


# The summary report joined the release axis later than the analytics reads, and
# it is a whole page's worth of numbers: five statements across six helpers. The
# same two invariants apply, so it is checked by the same machinery rather than
# a parallel set of assertions that could drift.
SUMMARY_HELPERS = (
    "_window_totals",
    "_latest_totals",
    "_per_suite_breakdown_window",
    "_per_suite_breakdown_latest",
    "_top_failing_tests",
    "_per_suite_step_success",
)


@pytest.mark.parametrize("fn_name", SUMMARY_HELPERS)
def test_every_summary_helper_takes_a_release(fn_name):
    """A report whose headline is release-scoped and whose per-suite rows are
    not answers two different questions under one heading — the coverage defect
    (D2), one page over."""
    from app.services import summary_report_service as srs

    fn = getattr(srs, fn_name)
    sig = inspect.signature(fn)
    assert "release_id" in sig.parameters, (
        f"{fn_name} cannot be told which release the report is about"
    )
    assert sig.parameters["release_id"].default is None, (
        "required would break every existing caller, which is the opposite of "
        "shipping this incrementally"
    )


@pytest.mark.parametrize("fn_name", SUMMARY_HELPERS)
def test_every_summary_run_query_carries_the_filter(fn_name):
    """Counted per statement, not 'the name appears in the function'."""
    from app.services import summary_report_service as srs

    fn = getattr(srs, fn_name)
    src = inspect.getsource(fn)
    # ``_per_suite_breakdown_latest`` (and its suite-scoped totals sibling)
    # build their SQL from the shared ``_latest_per_suite_ctes`` -- the same
    # WITH clause a filter change to one cannot drift from the other. Follow
    # it one level down, the way this suite's own helper-delegation checks do.
    if "_latest_per_suite_ctes(" in src:
        src += "\n" + inspect.getsource(srs._latest_per_suite_ctes)

    # This module writes SQL two ways, and BOTH have to be scoped. A skip here
    # would be the vacuous kind: the first version of this test only understood
    # raw SQL, so the one helper that uses a Core select reported "nothing to
    # check" while being genuinely unfiltered.
    statements = re.findall(r'f"""(.*?)"""', src, re.S)
    run_backed = [st for st in statements if re.search(r"(FROM|JOIN)\s+test_runs", st)]
    unfiltered = [
        st for st in run_backed if not re.search(r"\{\w*release_filter\w*\}", st)
    ]
    assert not unfiltered, (
        f"{len(unfiltered)} of {len(run_backed)} raw test_runs queries in "
        f"{fn_name} omit the release filter, so one report would mix scopes"
    )

    uses_core = "select(" in src and "TestRun" in src
    assert run_backed or uses_core, (
        f"{fn_name} issues no recognisable run-backed query — this test no "
        "longer understands how the module builds SQL"
    )
    if uses_core:
        assert "_release_predicate" in src, (
            f"{fn_name} builds a Core select over TestRun without the release "
            "predicate — the same gap as an unfiltered raw query, in the form "
            "the raw-SQL check cannot see"
        )


def test_the_summary_report_accepts_a_release():
    from app.services import summary_report_service as srs

    sig = inspect.signature(srs.build_summary_report)
    assert "release_id" in sig.parameters
    assert sig.parameters["release_id"].default is None


def test_the_summary_report_reuses_the_one_release_rule():
    """Not a second copy.

    "Which runs belong to this release" is a conditional fragment plus the
    Unattributed sentinel. Restating it here would let this page and the
    analytics pages disagree about what a release contains.
    """
    from app.services import summary_report_service as srs

    src = inspect.getsource(srs._release_filter)
    assert "_add_release_param" in src, (
        "the summary report has its own release predicate — it must share the "
        "one in analytics_service"
    )


@pytest.mark.parametrize(
    "fn_name",
    ["coverage_stats", "suite_detail", "flaky_tests", "failure_categories",
     "top_failing_tests"],
)
def test_every_run_query_in_the_function_carries_the_filter(fn_name):
    """Counted, not merely present.

    ``test_release_read_axis`` asserts ``"{release_filter}" in src``. That was
    true of ``coverage_stats`` throughout D2's life: the fragment appeared
    once, in the first of two queries. A substring check over a whole function
    cannot see the second one, so count them instead.
    """
    src = inspect.getsource(getattr(svc, fn_name))
    statements = re.findall(r'f"""(.*?)"""', src, re.S)
    run_backed = [s for s in statements if _touches_runs(s)]
    assert run_backed, f"no test_runs query found in {fn_name} — parse broken?"
    # Any ``{…release_filter}`` fragment, not the one variable name: a branch
    # that builds its own params dict needs its own fragment bound to that
    # dict (``m_release_filter`` in ``flaky_tests``), and pinning the name
    # would push the next author toward reusing a fragment built against the
    # wrong dict — which is D1 exactly.
    unfiltered = [
        s for s in run_backed if not re.search(r"\{\w*release_filter\}", s)
    ]
    assert not unfiltered, (
        f"{len(unfiltered)} of {len(run_backed)} test_runs queries in "
        f"{fn_name} omit the release filter, so one response would mix scopes"
    )


# ── D3: the Unattributed sentinel is a string, not a UUID ────────────────────


@pytest.mark.asyncio
async def test_flaky_scores_survives_the_unattributed_sentinel():
    """``uuid.UUID("unattributed")`` raises ValueError → 500.

    This calls the handler. Its existing test asserts ``"is_unattributed" in
    code``, which matched the import statement at the top of the module — the
    name was present and the guard was not.
    """
    from app.routers import analytics as router_mod

    db = RecordingSession()
    scoped = uuid.uuid4()

    async def _scope(_db, _user, _pid):
        return scoped, None

    async def _release(_db, rid, _user):
        return rid

    stmts: list = []

    class _CaptureSession(RecordingSession):
        async def execute(self, stmt, params=None):
            stmts.append(stmt)
            return _Result()

    db = _CaptureSession()
    # VIZ-201: the handler takes the scope the shared dependency resolved;
    # build it through the same parser so the sentinel is normalised as live.
    from app.services.analytics_scope import AnalyticsScope, parse_release_ids

    del _scope, _release
    result = await router_mod.flaky_scores(
        limit=50,
        scope=AnalyticsScope(scoped, None, parse_release_ids(["Unattributed"]), (), None),
        db=db,
    )

    assert result is not None
    compiled = " ".join(str(s) for s in stmts)
    assert "IS NULL" in compiled, (
        "the Unattributed bucket must compile to a NULL test on "
        "primary_release_id, not to an equality against a parsed UUID"
    )


@pytest.mark.asyncio
async def test_flaky_scores_still_filters_on_a_real_release():
    """The positive control.

    A guard that swallowed every release into the NULL branch would pass the
    sentinel test above while making the feature answer the wrong question.
    """
    from app.routers import analytics as router_mod

    scoped = uuid.uuid4()
    stmts: list = []

    async def _scope(_db, _user, _pid):
        return scoped, None

    async def _release(_db, rid, _user):
        return rid

    class _CaptureSession(RecordingSession):
        async def execute(self, stmt, params=None):
            stmts.append(stmt)
            return _Result()

    db = _CaptureSession()
    from app.services.analytics_scope import AnalyticsScope, parse_release_ids

    del _scope, _release
    await router_mod.flaky_scores(
        limit=50,
        scope=AnalyticsScope(scoped, None, parse_release_ids([RELEASE_UUID]), (), None),
        db=db,
    )

    compiled = " ".join(str(s) for s in stmts)
    assert "IS NULL" not in compiled, (
        "a real release id was routed into the Unattributed branch"
    )
    assert "primary_release_id" in compiled


def test_no_route_parses_a_release_query_param_outside_a_sentinel_guard():
    """The class check, by position rather than by name.

    Six routers resolve a release query param, and any of them may one day
    parse it. ``uuid.UUID(release_id)`` is only safe underneath a branch that
    has already ruled out the sentinel — so walk up from each parse to see
    whether such a branch encloses it, instead of asking whether the name
    appears in the file.
    """
    routers = Path(svc.__file__).parent.parent / "routers"
    offenders: list[str] = []

    for path in sorted(routers.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "resolve_release_query_scope" not in source:
            continue
        tree = ast.parse(source)
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name != "UUID":
                continue
            if not (
                node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "release_id"
            ):
                continue

            guarded = False
            cur: ast.AST | None = node
            while cur is not None and not guarded:
                parent = parents.get(cur)
                if isinstance(parent, (ast.If, ast.IfExp)):
                    test_src = ast.unparse(parent.test)
                    guarded = "is_unattributed" in test_src
                cur = parent
            if not guarded:
                offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "release query param parsed as a UUID with no enclosing "
        f"is_unattributed guard at {offenders} — the Unattributed bucket "
        "arrives as the literal string 'unattributed' and raises ValueError"
    )
