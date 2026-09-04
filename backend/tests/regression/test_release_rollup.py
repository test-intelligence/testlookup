"""Regression: the per-test-latest rollup and the verdict it supports (S6a-2).

Every test here builds a rollup and reads the numbers out. None of them inspect
source: three times in this epic a source-inspection test has described the code
instead of exercising it, most recently when five assertions on the string
``is_unattributed`` all passed against ``if False:`` because the name was also on
the import line.

The failures being guarded are all of one kind — the gate reporting a BETTER
release than it measured. That is the only direction a go/no-go must never fail
in, because nobody investigates good news.
"""
from __future__ import annotations

from app.services import release_rollup_service as svc
from app.services.release_rollup_service import ReleaseRollup


def rollup_of(**by_test: str) -> ReleaseRollup:
    """A rollup already reduced to one latest status per test."""
    r = ReleaseRollup(latest_by_test=dict(by_test))
    r.status_counts = {s: 0 for s in svc.STATUSES}
    for status in r.latest_by_test.values():
        r.status_counts[status] = r.status_counts.get(status, 0) + 1
    return r


class TestTheUnitIsTheTestNotTheResult:
    def test_the_denominator_counts_distinct_tests(self):
        r = rollup_of(a="PASSED", b="FAILED", c="SKIPPED")

        assert r.denominator == 3

    def test_a_rerun_test_does_not_outweigh_tests_that_ran_once(self):
        # The whole reason the rollup reduces to latest-per-test. Counting
        # results would let one test re-run ten times drown out nine others.
        r = rollup_of(flaky="PASSED", b="FAILED")

        assert r.denominator == 2


class TestFiveValuesNotFour:
    def test_unknown_is_carried_not_dropped(self):
        r = rollup_of(a="PASSED", b="UNKNOWN")

        # Dropping UNKNOWN shrinks the denominator and inflates the pass rate:
        # the product would report a better release than it measured.
        assert r.denominator == 2
        assert r.status_counts["UNKNOWN"] == 1

    def test_every_status_appears_in_the_breakdown(self):
        r = rollup_of(a="PASSED")

        # A missing key does not read as missing, it reads as zero — the
        # difference between "no broken tests" and "we did not look".
        assert svc.statuses_are_exhaustive(r.status_counts)

    def test_an_unrecognised_status_becomes_unknown_rather_than_vanishing(self):
        assert svc._normalise("WOBBLY") == "UNKNOWN"
        assert svc._normalise(None) == "UNKNOWN"
        # And a real one survives, including lowercase from an older writer.
        assert svc._normalise("passed") == "PASSED"


class TestSkippedIsNotEvidence:
    def test_skips_do_not_count_as_evidence(self):
        r = rollup_of(a="PASSED", b="SKIPPED", c="SKIPPED")

        # Counting skips as evidence is how a release with everything skipped
        # reports full coverage and a perfect score.
        assert r.denominator == 3
        assert r.evidence_count == 1

    def test_pass_rate_divides_by_evidence_not_by_the_denominator(self):
        r = rollup_of(a="PASSED", b="SKIPPED", c="SKIPPED", d="SKIPPED")

        # Dividing by the denominator would report 25% — a release that looks
        # broken when the truth is that it was barely tested.
        assert r.pass_rate() == 100.0

    def test_pass_rate_is_none_when_nothing_ran(self):
        r = rollup_of(a="SKIPPED", b="SKIPPED")

        # None, not 0.0. A zero is a measurement that happened, and none did.
        assert r.pass_rate() is None


class TestBrokenBlocksLikeFailed:
    def test_broken_is_blocking(self):
        r = rollup_of(a="PASSED", b="BROKEN")

        # A test that errored before asserting anything did not prove the code
        # works. Treating "the harness fell over" as non-blocking is how an
        # infrastructure outage reads as a green release.
        assert r.blocking_count == 1

    def test_a_broken_test_produces_no_go(self):
        r = rollup_of(**{f"t{i}": "PASSED" for i in range(5)}, broken="BROKEN")

        verdict, reasons = svc.decide(r)

        assert verdict == "NO_GO"
        assert any("BROKEN" in reason for reason in reasons)


class TestTheEvidenceFloor:
    def test_too_little_evidence_is_not_evaluated(self):
        r = rollup_of(a="PASSED", b="PASSED")

        verdict, reasons = svc.decide(r)

        # Not GO. With almost nothing exercised, GO claims the release is sound
        # on no evidence — the "All clear over an empty window" failure this
        # codebase has shipped before.
        assert verdict == "NOT_EVALUATED"
        assert reasons and "evidence" in reasons[0]

    def test_an_all_skipped_release_is_not_evaluated_rather_than_go(self):
        r = rollup_of(**{f"t{i}": "SKIPPED" for i in range(50)})

        verdict, _ = svc.decide(r)

        # 50 tests, none run. A gate keyed on "nothing failed" would say GO.
        assert verdict == "NOT_EVALUATED"

    def test_enough_evidence_and_no_failures_is_go(self):
        r = rollup_of(**{f"t{i}": "PASSED" for i in range(svc.MIN_EVIDENCE)})

        verdict, reasons = svc.decide(r)

        assert verdict == "GO"
        assert reasons == []

    def test_the_floor_counts_evidence_not_the_denominator(self):
        # 20 tests but only 2 ran. A floor applied to the denominator would let
        # this through as evaluated.
        r = rollup_of(
            **{f"s{i}": "SKIPPED" for i in range(18)},
            a="PASSED",
            b="PASSED",
        )

        assert r.denominator == 20
        assert svc.decide(r)[0] == "NOT_EVALUATED"


class TestTheScorecardCarriesItsOwnCaveats:
    def test_the_denominator_travels_with_the_pass_rate(self):
        r = rollup_of(**{f"t{i}": "PASSED" for i in range(9)}, f="FAILED")

        card = svc.summarise(r)

        # A percentage without the count it was computed over lets "90% passed"
        # stand for both a thorough release and one where nine of ten tests
        # never ran.
        assert card["pass_rate"] == 90.0
        assert card["denominator"] == 10
        assert card["evidence_count"] == 10

    def test_the_scorecard_says_whether_it_was_measured_at_all(self):
        thin = svc.summarise(rollup_of(a="PASSED"))
        thick = svc.summarise(rollup_of(**{f"t{i}": "PASSED" for i in range(9)}))

        # Stated plainly rather than left to be inferred from a small
        # denominator, which readers do not do.
        assert thin["measured"] is False
        assert thick["measured"] is True

    def test_the_scorecard_reports_the_attribution_mix(self):
        r = rollup_of(a="PASSED")
        r.attribution_mix = {"explicit_name": 3, "active_release_fallback": 7}

        card = svc.summarise(r)

        # A verdict built mostly from the active-release fallback is weaker
        # evidence than one built from explicit client names, and a reader
        # cannot weigh the verdict without knowing which.
        assert card["attribution_mix"]["active_release_fallback"] == 7


class TestBuildRollupReadsTheDatabaseCorrectly:
    """The half that touches the database.

    The first version of this class used a fake session that ignored its
    argument and returned canned rows by call ordinal. Review showed the cost:
    EVERY SQL predicate in the module was unfalsifiable. Dropping
    ``primary_release_id == release_id`` — so the rollup reads every run in the
    database — survived the whole suite, as did dropping the case filter and the
    ``is_primary`` clause. Three of the most dangerous mutants, invisible.

    This fake dispatches on the STATEMENT and records it, so the predicates are
    under test rather than merely present.
    """

    @staticmethod
    def _run(run_id, start_time=None, created_at=None, project_id="p1"):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=run_id, start_time=start_time, created_at=created_at, project_id=project_id
        )

    @staticmethod
    def _case(run_id, fingerprint, status, suite="ui", name=None):
        # fingerprint and name deliberately DIFFER, so ``fingerprint or name``
        # has two distinguishable branches. The previous helper set them equal,
        # which let both mutations of that expression survive.
        return (run_id, fingerprint, name or f"name-of-{fingerprint}", suite, status)

    def _build(self, runs, links, cases, project_id="p1", release_id="rel-1"):
        import asyncio

        seen: list[str] = []

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

            def scalars(self):
                return self

            def scalar_one_or_none(self):
                return self._rows[0] if self._rows else None

        class _Session:
            async def execute(self, stmt, *a, **kw):
                sql = " ".join(str(stmt).split())
                seen.append(sql)
                if "FROM releases" in sql:
                    return _Rows([project_id] if project_id is not None else [])
                if "FROM test_runs" in sql:
                    return _Rows(runs)
                if "release_test_run_links" in sql:
                    return _Rows(links)
                if "FROM test_cases" in sql:
                    return _Rows(cases)
                raise AssertionError(f"unexpected statement: {sql[:120]}")

        result = asyncio.run(svc.build_rollup(_Session(), release_id))
        return result, seen

    # ── the predicates, which the old fake could not see ────────────────────

    def test_runs_are_scoped_to_the_release_and_the_project(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        _, seen = self._build([self._run("r1", start_time=now)], [], [])
        runs_sql = next(s for s in seen if "FROM test_runs" in s)
        where = runs_sql.split(" WHERE ")[1].split(" ORDER BY ")[0]

        # Without the release predicate the rollup reads every run in the
        # database. Without the project predicate, ONE bad cross-project link —
        # which migration 0151 deliberately left in place — pulls another
        # tenant's run into this verdict.
        assert "primary_release_id" in where
        assert "project_id" in where

    def test_cases_are_scoped_to_the_selected_runs(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        _, seen = self._build([self._run("r1", start_time=now)], [], [])
        cases_sql = next(s for s in seen if "FROM test_cases" in s)

        # Dropping this reads every test case in the database.
        assert "test_run_id IN" in cases_sql.replace("test_cases.", "")

    def test_the_attribution_query_asks_for_primary_links_only(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        _, seen = self._build([self._run("r1", start_time=now)], [], [])
        links_sql = next(s for s in seen if "release_test_run_links" in s)

        # A run's release is its PRIMARY one — what the run query selected on.
        # Counting secondary links would report rungs this verdict never used.
        assert "is_primary" in links_sql

    def test_both_reads_are_capped(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        _, seen = self._build([self._run("r1", start_time=now)], [], [])

        # Unbounded, a six-week release is millions of rows — enough to exhaust
        # a worker, which is why the structurally identical join in
        # flaky_score_service caps too.
        assert "LIMIT" in next(s for s in seen if "FROM test_runs" in s)
        assert "LIMIT" in next(s for s in seen if "FROM test_cases" in s)

    def test_an_unknown_release_yields_an_empty_rollup(self):
        result, _ = self._build([], [], [], project_id=None)

        assert result.denominator == 0
        assert svc.decide(result)[0] == "NOT_EVALUATED"

    # ── ordering ────────────────────────────────────────────────────────────

    def test_the_latest_result_wins_by_execution_time(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        # Supplied newest-first, so a rollup trusting row order keeps the STALE
        # result.
        result, _ = self._build(
            [
                self._run("r-new", start_time=now),
                self._run("r-old", start_time=now - timedelta(days=2)),
            ],
            [("r-old", "explicit_name"), ("r-new", "explicit_name")],
            [self._case("r-old", "t1", "FAILED"), self._case("r-new", "t1", "PASSED")],
        )

        # Failed on Monday, passed on Friday: the fix landed.
        assert result.latest_by_test["ui::t1"] == "PASSED"

    def test_equal_timestamps_do_not_hand_the_verdict_to_row_order(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # Two CI shards triggered together share a start_time — routine, not a
        # corner. ``list.sort`` is stable and the query has no inherent order,
        # so without a deterministic tie-break the winner is whatever the
        # planner returned: the same data could yield GO and then NO_GO with no
        # write in between.
        forward, _ = self._build(
            [self._run("r-aaa", start_time=now), self._run("r-bbb", start_time=now)],
            [("r-aaa", "explicit_name"), ("r-bbb", "explicit_name")],
            [self._case("r-aaa", "t1", "FAILED"), self._case("r-bbb", "t1", "PASSED")],
        )
        backward, _ = self._build(
            [self._run("r-bbb", start_time=now), self._run("r-aaa", start_time=now)],
            [("r-aaa", "explicit_name"), ("r-bbb", "explicit_name")],
            [self._case("r-bbb", "t1", "PASSED"), self._case("r-aaa", "t1", "FAILED")],
        )

        assert forward.latest_by_test == backward.latest_by_test

    def test_a_null_execution_time_falls_back_to_created_at(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        result, _ = self._build(
            [
                self._run("r-legacy", start_time=None, created_at=now - timedelta(days=1)),
                self._run("r-recent", start_time=None, created_at=now),
            ],
            [("r-legacy", "cutoff_window"), ("r-recent", "cutoff_window")],
            [self._case("r-legacy", "t1", "FAILED"), self._case("r-recent", "t1", "PASSED")],
        )

        # Rows predating S3a-1 carry no start_time; sorting must not raise or
        # reverse on them.
        assert result.latest_by_test["ui::t1"] == "PASSED"

    # ── identity ────────────────────────────────────────────────────────────

    def test_the_same_name_in_two_suites_stays_two_tests(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        # The fingerprint is sha256(class::name) with no project and no suite,
        # so two different tests both called test_login share one. Keyed on the
        # fingerprint alone they collapse: the denominator is short by one and
        # the later suite's PASS erases the earlier suite's FAIL.
        result, _ = self._build(
            [
                self._run("r1", start_time=now - timedelta(hours=1)),
                self._run("r2", start_time=now),
            ],
            [("r1", "explicit_name"), ("r2", "explicit_name")],
            [
                self._case("r1", "fp_login", "FAILED", suite="ui"),
                self._case("r2", "fp_login", "PASSED", suite="api"),
            ],
        )

        assert result.denominator == 2
        assert result.latest_by_test["ui::fp_login"] == "FAILED"
        assert result.latest_by_test["api::fp_login"] == "PASSED"

    def test_a_row_with_no_identity_at_all_is_skipped(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result, _ = self._build(
            [self._run("r1", start_time=now)],
            [("r1", "explicit_name")],
            [("r1", None, None, "ui", "PASSED"), self._case("r1", "t1", "PASSED")],
        )

        # Counting it under a shared empty key would merge every such row into
        # one phantom test.
        assert result.denominator == 1

    # ── attribution ─────────────────────────────────────────────────────────

    def test_the_attribution_mix_reads_the_link_table(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result, _ = self._build(
            [self._run("r1", start_time=now), self._run("r2", start_time=now)],
            [("r1", "explicit_name"), ("r2", "active_release_fallback")],
            [self._case("r1", "t1", "PASSED")],
        )

        assert result.attribution_mix == {"explicit_name": 1, "active_release_fallback": 1}

    def test_a_null_link_source_is_named_not_dropped(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # ``link_source`` is explicitly nullable. Without the fallback the key
        # would be None and the mix would carry a null bucket no reader can
        # interpret.
        result, _ = self._build([self._run("r1", start_time=now)], [("r1", None)], [])

        assert result.attribution_mix == {"unknown": 1}

    def test_a_run_with_no_primary_link_is_named_distinctly(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result, _ = self._build([self._run("r1", start_time=now)], [], [])

        # Distinguishable from a link that exists but carries no source.
        assert result.attribution_mix == {"unattributed_link": 1}

    def test_the_run_set_is_captured_for_the_snapshot(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result, _ = self._build(
            [self._run("r1", start_time=now)], [("r1", "explicit_name")], []
        )

        assert result.run_ids == ["r1"]

    def test_the_status_breakdown_from_a_real_rollup_is_exhaustive(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # Asserted on build_rollup's OWN counts. The earlier version asserted on
        # the test fixture's dict, so emptying the real initialiser survived —
        # the test was testing itself.
        result, _ = self._build(
            [self._run("r1", start_time=now)],
            [("r1", "explicit_name")],
            [self._case("r1", "t1", "PASSED")],
        )

        assert svc.statuses_are_exhaustive(result.status_counts)

    def test_a_release_with_no_runs_rolls_up_to_nothing(self):
        result, _ = self._build([], [], [])

        assert result.denominator == 0
        assert svc.decide(result)[0] == "NOT_EVALUATED"

    def test_a_row_with_no_fingerprint_still_counts_under_its_name(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # `test_fingerprint` is computed at ingest and can be absent on rows
        # from older writers. Keyed on the fingerprint ALONE such a row gets a
        # None key and is dropped, shrinking the denominator — the product
        # reports a smaller release than it measured. The name is the fallback
        # identity, and this is the only case that exercises it.
        result, _ = self._build(
            [self._run("r1", start_time=now)],
            [("r1", "explicit_name")],
            [("r1", None, "test_checkout", "ui", "FAILED")],
        )

        assert result.denominator == 1
        assert result.latest_by_test["ui::test_checkout"] == "FAILED"

    def test_an_unknown_release_reads_no_runs_at_all(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # Runs ARE available from the fake; the point is that an unresolvable
        # release must not reach the run query with a null project and pull
        # them in. Asserting on an already-empty run set could not tell the
        # short-circuit from its absence.
        result, seen = self._build(
            [self._run("r1", start_time=now)],
            [("r1", "explicit_name")],
            [self._case("r1", "t1", "PASSED")],
            project_id=None,
        )

        assert result.denominator == 0
        assert not any("FROM test_runs" in s for s in seen), (
            "an unresolvable release must not query runs with a null project scope"
        )

    def test_a_phase_scoped_rollup_filters_on_the_primary_link(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        import asyncio

        seen: list[str] = []

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

            def scalars(self):
                return self

            def scalar_one_or_none(self):
                return self._rows[0] if self._rows else None

        class _Session:
            async def execute(self, stmt, *a, **kw):
                sql = " ".join(str(stmt).split())
                seen.append(sql)
                if "FROM releases" in sql:
                    return _Rows(["p1"])
                if "FROM test_runs" in sql:
                    return _Rows([self_run])
                return _Rows([])

        self_run = self._run("r1", start_time=now)
        asyncio.run(svc.build_rollup(_Session(), "rel-1", phase_id="ph-1"))

        runs_sql = next(s for s in seen if "FROM test_runs" in s)
        # Phase membership is a property of HOW the run was attributed to this
        # release, so it is read from the link, not from a column on the run —
        # the same run attributed to a different release could sit in a
        # different phase. Without this predicate every phase is gated against
        # the whole release and each one inherits the others' results.
        assert "release_test_run_links" in runs_sql
        assert "phase_id" in runs_sql
        assert "is_primary" in runs_sql

    def test_an_unscoped_rollup_does_not_mention_phases(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        _, seen = self._build([self._run("r1", start_time=now)], [], [])
        runs_sql = next(s for s in seen if "FROM test_runs" in s)

        # NFR1 in miniature: omitting the phase must produce the statement this
        # function produced before phase gating existed.
        assert "phase_id" not in runs_sql.split(" WHERE ")[1]
