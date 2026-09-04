"""Regression: /runs scopes by the SAME release definition as everything else.

Two defects, one slice
-----------------------
**The semantics disagreed.** ``list_project_runs`` selected every run with ANY
link to the release, while every other release-scoped read in the product — the
analytics endpoints, the KPI cards, the trend charts — filters the denormalized
``primary_release_id``. Behind a single global release picker that meant the run
list and the numbers above it disagreed for any run linked to two releases, with
nothing on screen to explain the difference.

The same function already read ``is_primary`` to build each row's release BADGE,
so a run could appear in release B's list wearing a badge that said release A.
One file, two definitions of "in this release".

**The route did not verify the release it was given.** Every other endpoint
accepting ``release_id`` calls ``resolve_release_query_scope``; this one did not.
A malformed value reached ``uuid.UUID()`` in the service and raised
``ValueError`` — a 500 where the rest of the API answers 422 — and a well-formed
id belonging to another tenant was never checked at all.

The authorization ratchet cannot catch that: it checks evidence per-ROUTE and
stops at the first scoped parameter a route satisfies, and this route satisfies
it on ``project_id``. That blind spot is the one that hid nine IDORs.

The accepted cost, stated rather than discovered
-------------------------------------------------
A run linked to a release as a SECONDARY link no longer appears in that
release's run list. It was already absent from that release's analytics, so this
makes the two agree rather than introducing a new exclusion.
"""
from __future__ import annotations

import inspect

from app.services import runs_service


def code_only(fn) -> str:
    """Source with comment lines and trailing comments removed.

    A source assertion against raw ``getsource`` matches the prose EXPLAINING
    the thing it forbids — this repo has been bitten by that three times, most
    recently in ``test_kpi_release_scope``. The alignment being asserted here is
    described at length in a comment that names the rejected approach, so
    stripping is mandatory rather than tidy.
    """
    body = []
    for line in inspect.getsource(fn).splitlines():
        if line.strip().startswith("#"):
            continue
        body.append(line.split("#", 1)[0] if "#" in line else line)
    return " ".join(body)


class TestOneDefinitionOfInThisRelease:
    def test_filters_the_primary_column(self):
        src = code_only(runs_service.list_project_runs)

        assert "TestRun.primary_release_id == release_uuid" in src

    def test_does_not_filter_by_link_membership(self):
        src = code_only(runs_service.list_project_runs)

        # The rejected shape. Selecting run ids out of ``release_test_run_links``
        # by release makes ANY link count, which is what disagreed with every
        # other surface.
        assert "ReleaseTestRunLink.release_id ==" not in src

    def test_the_badge_and_the_filter_now_read_the_same_thing(self):
        """The row BADGE and the row FILTER must name the same release.

        The badge is built by ``fetch_release_map``, a different function in
        this module, which has always been primary-only. The filter was not,
        which is how a run could appear in release B's list wearing a badge
        that said release A. This asserts the badge half so the pair cannot
        diverge again from the other direction.
        """
        badge = code_only(runs_service.fetch_release_map)

        assert "ReleaseTestRunLink.is_primary.is_(True)" in badge, (
            "the release badge must stay primary-only, or it will disagree with "
            "the filter this slice just aligned to primary"
        )


class TestAnUnparseableFilterFailsClosed:
    def test_a_bad_release_id_matches_nothing_rather_than_everything(self):
        src = code_only(runs_service.list_project_runs)

        # Falling through to "no filter" would answer a narrow question with
        # every run in the project. The caller asked to see one release; showing
        # them all of them reads as data, not as an error, so it is the
        # dangerous direction to fail in.
        assert "filters.append(false())" in src
        assert "except (ValueError, TypeError, AttributeError):" in src

    def test_the_parse_is_guarded_at_all(self):
        src = code_only(runs_service.list_project_runs)

        # The pre-fix shape: a bare ``uuid.UUID(release_id)`` inline in the
        # filter expression, which raises straight out of the service as a 500.
        assert "ReleaseTestRunLink.release_id == uuid.UUID(release_id)" not in src


class TestTheRouteVerifiesTheRelease:
    def test_list_runs_resolves_the_release_scope(self):
        from app.routers import runs as runs_router

        src = inspect.getsource(runs_router.list_runs)

        assert "resolve_release_query_scope" in src, (
            "/runs accepted release_id without verifying it — 500 instead of "
            "422 on a malformed id, and no tenant check on a well-formed one"
        )

    def test_the_guard_runs_before_either_branch_uses_the_id(self):
        from app.routers import runs as runs_router

        src = inspect.getsource(runs_router.list_runs)
        guard_at = src.index("resolve_release_query_scope")
        # Both the "no project_id" and the explicit-project branches pass
        # release_id into the service. A guard placed after either one would
        # verify nothing on that path — the exact shape of F-042, where a check
        # sat inside the branch that had nothing to check.
        first_use = src.index("list_project_runs")

        assert guard_at < first_use


class TestTheFilterIsRealNotJustWritten:
    """Behaviour, not source text.

    Every other test here inspects source. The previous slice proved why that
    is not sufficient on its own: a mutation that kept all the strings while
    neutering the behaviour survived the whole file, and the positive control
    was what exposed it. These compile the statement the function actually
    builds.
    """

    @staticmethod
    def _captured_sql(release_id):
        import asyncio

        captured = []

        class _Result:
            def scalar(self):
                return 0

            def scalars(self):
                return self

            def all(self):
                return []

            def fetchall(self):
                return []

            def __iter__(self):
                return iter(())

        class _CapturingSession:
            async def execute(self, stmt, *a, **kw):
                captured.append(str(stmt))
                return _Result()

        try:
            asyncio.run(
                runs_service.list_project_runs(
                    _CapturingSession(), None, 1, 20, None, release_id,
                    accessible_project_ids=None, days=None, suite_name=None,
                )
            )
        except Exception:
            # The fake result is deliberately thin. What matters is what was
            # COMPILED, which happens before any row is consumed.
            pass
        return captured

    @staticmethod
    def _where(statements) -> str:
        """Just the predicates.

        ``primary_release_id`` is a model COLUMN, so it appears in the SELECT
        list of every statement this function compiles — asserting its absence
        against the whole string is the SELECT-list trap that has produced
        vacuous tests in this repo twice. Cut at ORDER BY / LIMIT too, so a
        column named in the ordering cannot stand in for a predicate either.
        """
        # SQLAlchemy renders keywords on their own lines, so the separator
        # is a NEWLINE, not a space. Flatten before splitting.
        # PER STATEMENT. Joining them first lets one statement's WHERE run
        # into the next statement's SELECT list, which is how the SELECT-list
        # trap sneaks back in through the side door.
        regions = []
        for stmt in statements:
            flat = " ".join(stmt.split())
            for part in flat.split(" WHERE ")[1:]:
                for terminator in (" ORDER BY ", " LIMIT ", " GROUP BY "):
                    part = part.split(terminator)[0]
                regions.append(part)
        return " ".join(regions)

    def test_a_valid_release_compiles_a_primary_release_predicate(self):
        sql = self._captured_sql("11111111-1111-1111-1111-111111111111")

        assert any("test_runs" in s for s in sql), "no statement captured; this test proves nothing"
        assert "primary_release_id" in self._where(sql)

    def test_a_malformed_release_matches_nothing_and_does_not_raise(self):
        # The fail-closed property, exercised rather than described. Before this
        # slice the same input raised ValueError out of the service, which the
        # route turned into a 500.
        sql = self._captured_sql("not-a-uuid")

        assert any("test_runs" in s for s in sql), "no statement captured; this test proves nothing"
        where = self._where(sql)
        # SQLAlchemy renders a false() literal; either spelling is acceptable,
        # what matters is that the predicate cannot match a row.
        assert "false" in where.lower()
        assert "primary_release_id" not in where

    def test_no_release_predicate_when_none_is_asked_for(self):
        sql = self._captured_sql(None)

        assert any("test_runs" in s for s in sql), "no statement captured; this test proves nothing"
        assert "primary_release_id" not in self._where(sql)
