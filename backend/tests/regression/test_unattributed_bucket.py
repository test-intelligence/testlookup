"""Regression: the Unattributed bucket — runs no release claims.

Why it exists
-------------
Every project has an active release and the attribution ladder always lands
somewhere, so in a healthy system this bucket is EMPTY. That is precisely what
makes it worth having. A run reaches it when the linker failed and swallowed
the error — a state the product otherwise cannot show anyone, because the run
looks normal everywhere except in the release dimension, where it is simply
absent from every release's numbers.

It is an operational view, not a routine filter: "show me what fell through".

The property this file guards
-----------------------------
One sentinel, recognised at every point that filters by release. There are five,
across ``core.deps``, ``analytics_service``, ``metrics_service`` (twice — an ORM
path and a raw-SQL path) and ``runs_service``. A point that does not recognise
it does not fail loudly: ``"unattributed"`` is not a UUID, so it either 422s as
malformed or silently matches no rows. Both read to the user as "this release
has no runs", which is the same answer a working filter gives for an empty
release — indistinguishable, and wrong.

That is the producer/consumer vocabulary drift that made ``"quarantined"`` match
nothing forever (#735). The sentinel therefore lives in exactly one module and
every site imports it.
"""
from __future__ import annotations

import inspect

from app.core.release_filter import UNATTRIBUTED, is_unattributed


class _Result:
    """A result thin enough to build statements against, and no thinner."""

    def scalar(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())


def _captured(call, release_id):
    """Every SQL statement `call` compiles for this release filter."""
    import asyncio

    seen = []

    class _Session:
        async def execute(self, stmt, *a, **kw):
            seen.append(str(stmt))
            return _Result()

    try:
        asyncio.run(call(_Session(), release_id))
    except Exception:
        # The fake result is deliberately thin; what matters is what was
        # COMPILED, which happens before any row is consumed.
        pass
    assert seen, "no statement was compiled; the assertion would prove nothing"
    return seen


def _where(statements) -> str:
    """Just the predicates, PER STATEMENT.

    ``primary_release_id`` is a model column, so it appears in the SELECT list
    of every statement here — asserting against the whole string is the
    SELECT-list trap. Joining the statements first lets one statement's WHERE
    run into the next one's SELECT list, which is the same trap by another
    route.
    """
    regions = []
    for stmt in statements:
        flat = " ".join(stmt.split())
        for part in flat.split(" WHERE ")[1:]:
            for terminator in (" ORDER BY ", " LIMIT ", " GROUP BY "):
                part = part.split(terminator)[0]
            regions.append(part)
    return " ".join(regions)


async def _period_stats_call(session, release_id):
    from datetime import datetime, timedelta, timezone

    from app.services.metrics_service import _period_stats

    now = datetime.now(timezone.utc)
    return await _period_stats(session, "p1", now - timedelta(days=7), now, None, release_id)


async def _trend_call(session, release_id):
    from app.services.metrics_service import get_trend_data

    return await get_trend_data(session, "p1", 7, None, release_id)


async def _runs_call(session, release_id):
    from app.services.runs_service import list_project_runs

    return await list_project_runs(
        session, None, 1, 20, None, release_id,
        accessible_project_ids=None, days=None, suite_name=None,
    )


class TestTheSentinelItself:
    def test_is_not_a_uuid_and_not_empty(self):
        import uuid as _uuid

        # Empty already means "no filter". A UUID-shaped sentinel would be
        # indistinguishable from a real release id in a log line, a saved view,
        # or a shared link.
        assert UNATTRIBUTED
        try:
            _uuid.UUID(UNATTRIBUTED)
        except (ValueError, AttributeError, TypeError):
            pass
        else:  # pragma: no cover - only reached if someone makes it a UUID
            raise AssertionError("the sentinel must not parse as a UUID")

    def test_recognises_its_own_value(self):
        assert is_unattributed(UNATTRIBUTED)

    def test_tolerates_casing_and_surrounding_space(self):
        # It travels through query strings, saved-view JSON and shared URLs. A
        # near miss would fall through to being parsed as a UUID and rejected
        # as malformed — a 422 for what was really a capitalisation difference.
        assert is_unattributed("Unattributed")
        assert is_unattributed("  UNATTRIBUTED  ")

    def test_does_not_claim_anything_else(self):
        assert not is_unattributed(None)
        assert not is_unattributed("")
        assert not is_unattributed("11111111-1111-1111-1111-111111111111")
        # Near neighbours must not match, or a typo would silently switch the
        # user from one release to the fell-through bucket.
        assert not is_unattributed("unattributed-runs")
        assert not is_unattributed("un-attributed")


class TestEveryFilterPointRecognisesIt:
    """Five points, exercised rather than read.

    The first version of this class asserted that the string ``is_unattributed``
    appeared in each function's source. Mutation testing killed that idea
    outright: replacing ``if is_unattributed(release_id):`` with ``if False:``
    left the IMPORT line untouched, so every one of those assertions still
    passed while the bucket matched nothing anywhere. Five vacuous tests, one
    mutation.

    A point that stops recognising the sentinel does not fail loudly — it
    answers "no runs", which is what a working filter says about an empty
    release. So these call the code and read what it produces.
    """

    def test_the_analytics_helper_emits_a_null_predicate(self):
        from app.services.analytics_service import _add_release_param

        params: dict = {}
        fragment = _add_release_param(params, UNATTRIBUTED)

        assert "primary_release_id IS NULL" in fragment
        # The NULL predicate references no bind. Setting one anyway is dead
        # weight, and on some drivers a bind with no placeholder raises.
        assert "release_id" not in params

    def test_the_analytics_helper_still_binds_a_real_release(self):
        from app.services.analytics_service import _add_release_param

        params: dict = {}
        fragment = _add_release_param(params, "11111111-1111-1111-1111-111111111111")

        assert "= :release_id" in fragment
        assert params["release_id"] == "11111111-1111-1111-1111-111111111111"

    def test_the_scope_guard_returns_it_without_touching_the_database(self):
        import asyncio

        from app.core.deps import resolve_release_query_scope

        class _ExplodingSession:
            async def execute(self, *a, **kw):  # pragma: no cover - must not run
                raise AssertionError("the bucket names no release to look up")

        result = asyncio.run(
            resolve_release_query_scope(_ExplodingSession(), UNATTRIBUTED, object())
        )

        # Normalised, so every downstream comparison sees one spelling.
        assert result == UNATTRIBUTED

    def test_the_scope_guard_normalises_casing(self):
        import asyncio

        from app.core.deps import resolve_release_query_scope

        class _ExplodingSession:
            async def execute(self, *a, **kw):  # pragma: no cover
                raise AssertionError("the bucket names no release to look up")

        result = asyncio.run(
            resolve_release_query_scope(_ExplodingSession(), "  UnAttributed ", object())
        )

        assert result == UNATTRIBUTED

    def test_period_stats_compiles_a_null_predicate(self):
        sql = _captured(_period_stats_call, UNATTRIBUTED)

        assert "primary_release_id IS NULL" in _where(sql)

    def test_the_trend_query_compiles_a_null_predicate(self):
        sql = _captured(_trend_call, UNATTRIBUTED)

        assert "primary_release_id IS NULL" in _where(sql)

    def test_the_runs_list_compiles_a_null_predicate(self):
        sql = _captured(_runs_call, UNATTRIBUTED)

        assert "primary_release_id IS NULL" in _where(sql)

    def test_none_of_them_emit_a_null_predicate_for_a_real_release(self):
        # The mirror: a NULL test leaking into the normal path would silently
        # answer every release-scoped question with the fell-through bucket.
        real = "11111111-1111-1111-1111-111111111111"

        for name, call in (
            ("period_stats", _period_stats_call),
            ("trend", _trend_call),
            ("runs", _runs_call),
        ):
            where = _where(_captured(call, real))
            assert "primary_release_id IS NULL" not in where, name
            assert "primary_release_id" in where, name

    def test_no_site_inlines_the_literal(self):
        """Each site imports the sentinel rather than spelling it again.

        An inlined ``"unattributed"`` is how a producer and a consumer drift
        apart with no error between them.
        """
        from app.core import deps
        from app.services import analytics_service, metrics_service, runs_service

        sites = [
            deps.resolve_release_query_scope,
            analytics_service._add_release_param,
            metrics_service._period_stats,
            metrics_service.get_trend_data,
            runs_service.list_project_runs,
        ]
        assert len(sites) == 5

        for fn in sites:
            body = inspect.getsource(fn)
            assert '"unattributed"' not in body.lower(), (
                f"{fn.__name__} spells the sentinel itself instead of importing it"
            )


class TestTheNullBranchBindsNothing:
    def test_the_raw_sql_paths_do_not_bind_a_parameter_they_never_mention(self):
        from app.services import analytics_service, metrics_service
        from app.services.analytics_scope import release_filter_sql

        # VIZ-201: both raw-SQL paths use the one builder. Behaviour, not text:
        # the NULL predicate references no bind, and setting one anyway is dead
        # weight at best -- on some drivers a bind with no placeholder raises.
        for sentinel in (UNATTRIBUTED, "Unattributed", [UNATTRIBUTED]):
            params: dict = {}
            assert release_filter_sql(params, sentinel) == "AND tr.primary_release_id IS NULL"
            assert params == {}, sentinel
        assert "release_filter_sql(" in inspect.getsource(analytics_service._add_release_param)
        trend = inspect.getsource(metrics_service.get_trend_data)
        assert "release_filter_sql(params, release_id)" in trend


class TestTheTwoHalvesCannotDrift:
    """The frontend sends this value; the backend recognises it. Nothing else
    connects them.

    If they drift, nothing raises. The backend either rejects the unknown string
    as a malformed UUID (422, which axios suppresses) or matches no rows — and
    both render as "this release has no runs", the same answer a working filter
    gives for an empty release. That is the producer/consumer vocabulary drift
    that made ``"quarantined"`` match nothing forever, and the only place it can
    be caught is a test that reads both sides.
    """

    @staticmethod
    def _picker_source() -> str:
        from pathlib import Path

        picker = (
            Path(__file__).resolve().parents[3]
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "ReleasePicker.tsx"
        )
        assert picker.is_file(), f"expected the picker at {picker}"
        return picker.read_text(encoding="utf-8")

    def test_the_frontend_declares_the_same_literal(self):
        src = self._picker_source()

        assert f"export const UNATTRIBUTED_RELEASE = '{UNATTRIBUTED}'" in src, (
            "the picker's sentinel no longer matches the backend's; the filter "
            "will silently match no rows"
        )

    def test_the_frontend_offers_it_as_an_option(self):
        # Declaring the constant is not offering it. A constant nothing renders
        # is the same shape as a metric nothing increments.
        src = self._picker_source()

        assert "<option value={UNATTRIBUTED_RELEASE}>" in src

    def test_the_frontend_exempts_it_from_the_stale_drop(self):
        """Two exemptions, and the loose assertion could only see one.

        ``activeReleaseId === UNATTRIBUTED_RELEASE`` appears in BOTH
        ``selectedIsKnown`` and the stale-drop effect, so asserting the bare
        string passed even with the effect's guard deleted — mutation testing
        caught that. Each is asserted by the form unique to it.

        Without the effect guard the picker clears the bucket the moment the
        release list loads, with an error toast blaming the user for asking a
        question the product supports.
        """
        src = self._picker_source()

        # The render-time exemption: keeps the <select> showing the selection.
        assert (
            "activeReleaseId === UNATTRIBUTED_RELEASE || releases.some" in src
        ), "selectedIsKnown no longer exempts the bucket"
        # The effect-time exemption, which reads LIVE store state — the form
        # only the stale-drop uses.
        assert (
            "useReleaseStore.getState().activeReleaseId === UNATTRIBUTED_RELEASE" in src
        ), "the stale-drop effect no longer exempts the bucket"
