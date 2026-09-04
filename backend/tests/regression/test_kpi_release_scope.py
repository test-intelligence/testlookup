"""Regression: the KPI and trend endpoints answer for one release (S5-3b).

Why this slice came before the release-span window
---------------------------------------------------
The epic claims that stopping after S5 leaves a coherent product — "every
run-backed dashboard filterable by release". It did not. ``/overview``, the
default landing route, had ONE of five data sources release-scoped, and
``/metrics/summary`` and ``/metrics/trends`` are the two that feed its KPI
cards and every trend chart in the app.

Partial scoping is worse than none. A page where one card visibly changes when
you pick a release teaches the reader that the filter works there, so they read
the pass rate and the trend line beside it as the same release's story. A page
where nothing changes at least invites suspicion.

The property most of this file guards: the CACHE KEY
-----------------------------------------------------
``get_dashboard_summary`` memoises into Redis for ``CACHE_TTL_DASHBOARD``. The
key carries no user identity, so a release that reaches the QUERY but not the
KEY does not merely mis-scope one response — the first reader to ask about a
release poisons the shared entry, and every other viewer's unfiltered dashboard
serves that release's KPIs for the rest of the TTL.

That is the same defect class as an SWR key missing a parameter (fixed in
S5-3a), with the blast radius changed from one browser to every viewer. It is
also invisible: the numbers are individually well-formed, so nothing surfaces
as an error.

The mirror property is NFR1: with no release asked for, the key must be
byte-identical to what it was before this axis existed, or every deploy
silently orphans the cache and the "omitting the parameter changes nothing"
guarantee is only half true.
"""
from __future__ import annotations

import inspect

from app.services import metrics_service
from app.services.cache_service import _build_cache_key


def code_only(fn) -> str:
    """Source with comments and docstrings stripped.

    A source-inspection assertion that reads raw ``getsource`` matches the
    prose EXPLAINING the thing it forbids. This file did exactly that on its
    first run: ``"release_test_run_links" not in src`` failed against the
    comment saying why we deliberately do not join that table. The same shape
    has now bitten three times in this repo, so the stripping is shared rather
    than repeated per assertion.
    """
    body = []
    for line in inspect.getsource(fn).splitlines():
        if line.strip().startswith("#"):
            continue
        # Trailing comments too — an inline ``# ... IS NULL OR ...`` is just as
        # matchable as a whole-line one.
        body.append(line.split("#", 1)[0] if "#" in line else line)
    # Joined on a space rather than a newline: every assertion against this
    # helper looks for a token that lives on ONE line, so line structure buys
    # nothing, and building the separator would mean embedding an escape in
    # generated code — a shape that has produced silent damage here before.
    return " ".join(body)


class TestCacheKeyCarriesTheRelease:
    """The shared-cache half, which is the part that bleeds across users."""

    def test_two_releases_do_not_share_a_cache_entry(self):
        a = _build_cache_key("dashboard_summary_v2", "p1", days=7, suite="", release="rel-a")
        b = _build_cache_key("dashboard_summary_v2", "p1", days=7, suite="", release="rel-b")

        assert a != b, (
            "two releases sharing one Redis entry means the first reader's "
            "release decides what every other viewer sees"
        )

    def test_a_release_does_not_share_the_unfiltered_entry(self):
        unfiltered = _build_cache_key("dashboard_summary_v2", "p1", days=7, suite="")
        scoped = _build_cache_key("dashboard_summary_v2", "p1", days=7, suite="", release="rel-a")

        assert unfiltered != scoped

    def test_the_unfiltered_key_is_unchanged_by_this_axis(self):
        # NFR1 at the cache layer. Passing ``release=""`` unconditionally would
        # render a permanent segment into the key, changing it for every caller
        # who never asked about a release and orphaning every existing entry.
        assert (
            _build_cache_key("dashboard_summary_v2", "p1", days=7, suite="")
            == "analytics:dashboard_summary_v2:p1:days=7:suite="
        )

    def test_the_summary_passes_the_release_to_both_cache_calls(self):
        # A release threaded into ``cache_get`` but not ``cache_set`` (or vice
        # versa) reads one key and writes another: a permanent miss that
        # silently turns the cache off for release-scoped callers, and the
        # symptom is a slow dashboard rather than a wrong one.
        src = inspect.getsource(metrics_service.get_dashboard_summary)
        assert src.count("**release_key") == 2, (
            "both cache_get and cache_set must key on the release"
        )
        assert 'release_key = {"release": release_id} if release_id else {}' in src, (
            "the kwarg must be CONDITIONAL, or the unfiltered key changes"
        )


class TestTheReleaseReachesTheQuery:
    """Sending the id and keying on it are separate failures; both are needed."""

    def test_period_stats_filters_on_the_denormalized_column(self):
        src = code_only(metrics_service._period_stats)

        assert "TestRun.primary_release_id == release_id" in src
        # The denormalized column, not a join through release_test_run_links: a
        # join multiplies rows for a run linked to more than one release and
        # would inflate every SUM in this function.
        assert "release_test_run_links" not in src

    def test_period_stats_appends_nothing_when_unasked(self):
        src = code_only(metrics_service._period_stats)

        assert "if release_id:" in src
        # The null-tolerant predicate is the index-defeating shape: the planner
        # cannot resolve which branch applies, so it stops using the composite
        # index for EVERY caller, including the majority passing no release.
        assert "IS NULL OR" not in src

    def test_the_summary_scopes_both_periods(self):
        """The trend arrow compares the current period against the previous one.

        Scoping only the current call compares a RELEASE against the WHOLE
        PROJECT and reports the difference as movement — a fabricated trend
        that looks most dramatic exactly when a release is small.

        Counting ``release_id`` occurrences is not enough to see it: a mutation
        that drops the argument from the second call leaves plenty of other
        occurrences behind, and it survived that assertion. Parse instead, and
        require BOTH call sites to pass it.
        """
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(metrics_service.get_dashboard_summary)))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_period_stats"
        ]

        assert len(calls) == 2, f"expected two _period_stats calls, found {len(calls)}"
        for i, call in enumerate(calls):
            names = {a.id for a in call.args if isinstance(a, ast.Name)}
            names |= {k.value.id for k in call.keywords if isinstance(k.value, ast.Name)}
            assert "release_id" in names, (
                f"_period_stats call {i} does not pass release_id — that period "
                f"is computed project-wide while the other is release-scoped"
            )

    def test_trend_data_emits_the_fragment_and_binds_it(self):
        src = inspect.getsource(metrics_service.get_trend_data)

        assert 'release_filter = "AND tr.primary_release_id = :release_id "' in src
        assert "{release_filter}" in src, "the fragment must be interpolated into the query"
        # A bind with no placeholder is dead weight at best and raises on some
        # drivers; a placeholder with no bind always raises.
        assert 'params["release_id"]' in src
        # Three branches, not two: no filter, the unattributed NULL test, and
        # the equality. The NULL branch must NOT bind a parameter, because its
        # predicate references none.
        assert "if not release_id:" in src
        assert 'release_filter = "AND tr.primary_release_id IS NULL "' in src
        assert "not _unattributed(release_id)" in src, (
            "the unattributed branch must be excluded from the bind, or the "
            "query binds a value its SQL never mentions"
        )


class TestTheRouteVerifiesTheReleaseItWasGiven:
    """The authorization ratchet cannot see this one."""

    def test_both_handlers_resolve_the_release_scope(self):
        from app.routers import metrics as metrics_router

        for fn in (metrics_router.dashboard_summary, metrics_router.trend_data):
            src = inspect.getsource(fn)
            # The ratchet checks evidence per-ROUTE and stops at the first
            # scoped parameter it can satisfy, so both of these pass on
            # ``project_id`` alone with the release entirely unchecked. That
            # blind spot hid nine IDORs before; the guard has to be explicit.
            assert "resolve_release_query_scope" in src, (
                f"{fn.__name__} accepts release_id without verifying it"
            )

    def test_both_handlers_accept_the_parameter(self):
        from app.routers import metrics as metrics_router

        for fn in (metrics_router.dashboard_summary, metrics_router.trend_data):
            assert "release_id" in inspect.signature(fn).parameters, fn.__name__


class TestTheFilterActuallyReachesTheSQL:
    """Behaviour, not source text.

    Every other test in this file inspects source. A mutation pass proved why
    that is not enough: dropping ``release_id`` from ``_period_stats``' own
    signature and rebinding it to ``None`` in the body left EVERY string those
    tests look for intact — the code still reads
    ``if release_id: conditions.append(...)`` — while the filter could never
    fire again. The positive control survived, which is the harness telling you
    the tests describe the code rather than exercising it.

    These compile the statement the function actually builds and read the SQL.
    """

    @staticmethod
    def _captured_sql(release_id):
        """Run ``_period_stats`` against a session that records statements."""
        import asyncio
        from datetime import datetime, timedelta, timezone

        captured = []

        class _Result:
            def first(self):
                return None

            def scalar(self):
                return 0

            def scalar_one_or_none(self):
                return 0

            def fetchall(self):
                return []

            def __iter__(self):
                return iter(())

        class _CapturingSession:
            async def execute(self, stmt, *a, **kw):
                captured.append(str(stmt))
                return _Result()

        now = datetime.now(timezone.utc)
        try:
            asyncio.run(
                metrics_service._period_stats(
                    _CapturingSession(), "p1", now - timedelta(days=7), now, None, release_id
                )
            )
        except Exception:
            # The fake result is deliberately thin; what matters is what was
            # COMPILED before any consumption of the rows.
            pass
        return " ".join(captured)

    def test_the_release_predicate_is_in_the_compiled_sql(self):
        sql = self._captured_sql("11111111-1111-1111-1111-111111111111")

        assert captured_has(sql), (
            "the compiled statement carries no primary_release_id predicate, so "
            "the KPI query is not release-scoped no matter what the source says"
        )

    def test_no_release_predicate_when_none_is_asked_for(self):
        sql = self._captured_sql(None)

        # Prove a statement was compiled at all FIRST. Without this the
        # assertion below passes on an empty string, so a change that made
        # ``_period_stats`` raise before issuing any query would read as
        # "correctly unscoped".
        assert "test_runs" in sql, "no statement was captured; the test proves nothing"
        # NFR1: the statement a caller gets without a release must be the one
        # they got before this axis existed.
        assert not captured_has(sql)


def captured_has(sql: str) -> bool:
    return "primary_release_id" in sql
