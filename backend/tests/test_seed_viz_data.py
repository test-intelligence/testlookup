"""VIZ-213 — the visualization demo seed keeps every guarantee it advertises.

All of these run on the PURE plan (``build_viz_seed_plan``): no database, no
session, no mocks. One test per acceptance criterion, named for it, so a
failure says which chart edge case the seed stopped producing.

Where a guarantee is about a product rule — error signatures, the pass-rate
denominator, environment normalisation, suite attribution — the assertion
calls the product's own function rather than restating the rule here.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from app.core.pass_rate import canonical_pass_rate, executed_count
from app.models.postgres import LaunchStatus, TestStatus
from app.services.flaky_signals import error_signature
from app.services.run_environment import normalize_environment
from scripts import seed_dev_data
from scripts.seed_viz_data import (
    ALWAYS_SKIPPED_SUITE,
    FAILURE_GROUPS,
    STALE_AFTER_DAYS,
    STALE_SUITE,
    VIZ_ENVIRONMENTS,
    VizSeedPlan,
    build_viz_seed_plan,
)

NOW = datetime(2026, 9, 21, 14, 30, tzinfo=timezone.utc)
SLUG = "ecommerce-platform"

_FAILING = (TestStatus.FAILED, TestStatus.BROKEN)


@pytest.fixture(scope="module")
def plan() -> VizSeedPlan:
    return build_viz_seed_plan(SLUG, NOW)


def _cases(plan: VizSeedPlan):
    for run in plan.runs:
        for case in run.cases:
            yield run, case


def _finished(plan: VizSeedPlan):
    return [run for run in plan.runs if run.status != LaunchStatus.IN_PROGRESS]


# ── 1. releases ──────────────────────────────────────────────────────────────


def test_guarantee_1_at_least_eight_releases_with_mixed_statuses(plan):
    # The plan alone reaches eight, so a project that never had the base
    # seed (the throwaway one in the Postgres test) still qualifies.
    assert len(plan.releases) >= 8

    names = [r.name.lower() for r in plan.releases]
    versions = [r.version for r in plan.releases]
    assert len(set(names)) == len(names), "release names must be distinct"
    assert len(set(versions)) == len(versions), "release versions must be distinct"

    # And clear of what seed_dev_data already writes — the same project gets both.
    base_names = {r["name"].lower() for r in seed_dev_data.RELEASES_TEMPLATE}
    base_versions = {r["version"] for r in seed_dev_data.RELEASES_TEMPLATE}
    assert not base_names & set(names)
    assert not base_versions & set(versions)

    # The declared vocabulary on Release.status, nothing drifted.
    statuses = {r.status for r in plan.releases}
    assert statuses <= {"planning", "in_progress", "released", "cancelled", "archived"}
    assert len(statuses) >= 3
    assert statuses & {"archived", "cancelled"}, "one retired release is required"


def test_guarantee_1_releases_are_spread_over_time(plan):
    created = sorted(r.created_at for r in plan.releases)
    assert created[-1] - created[0] >= timedelta(days=60)
    assert len(set(created)) >= 6
    for r in plan.releases:
        if r.status in ("released", "archived"):
            assert r.released_at is not None and r.released_at <= NOW
        else:
            assert r.released_at is None
        assert r.phases, f"{r.name} has no phases"


# ── 2. environments and branches ─────────────────────────────────────────────


def test_guarantee_2_exactly_three_normalised_environments(plan):
    environments = {run.environment for run in plan.runs if run.environment is not None}
    assert environments == set(VIZ_ENVIRONMENTS)
    assert len(environments) == 3
    for env in environments:
        # Stored as the write path would store it, so no read-time rewrite.
        assert normalize_environment(env) == env


def test_guarantee_2_at_least_three_branches_and_a_bare_run(plan):
    branches = {run.branch for run in plan.runs if run.branch is not None}
    assert len(branches) >= 3

    bare = [run for run in plan.runs if run.branch is None and run.environment is None]
    assert bare, "a run with neither branch nor environment is required"
    # Inside the default 30-day window too, or the charts never meet it.
    assert any(NOW - run.start_time < timedelta(days=STALE_AFTER_DAYS) for run in bare)


# ── 3. failure groups ────────────────────────────────────────────────────────


def test_guarantee_3_failures_collapse_into_exactly_five_signatures(plan):
    by_signature: dict[str, set[str]] = defaultdict(set)
    without_message = 0
    for _run, case in _cases(plan):
        if case.status not in _FAILING:
            continue
        if case.error_message is None:
            without_message += 1
            assert error_signature(case.error_message) == ""
            continue
        by_signature[error_signature(case.error_message)].add(case.error_message)

    # Exactly five, by the product's own function — the one the charts key on.
    assert len(by_signature) == 5, sorted(by_signature)
    assert all(sig for sig in by_signature), "a signature must never be empty"
    # Several raw messages per signature, so the collapse is real, not trivial.
    for signature, raw_messages in by_signature.items():
        assert (
            len(raw_messages) >= 3
        ), f"{signature!r} has only {len(raw_messages)} raw messages"
    # Plus the group that has no message at all — several of them.
    assert without_message >= 3


def test_guarantee_3_every_declared_group_is_emitted(plan):
    # FAILURE_GROUPS is the catalogue; a template nobody renders is a group
    # the charts never see.
    assert len(FAILURE_GROUPS) == 5
    seen = {
        error_signature(case.error_message)
        for _run, case in _cases(plan)
        if case.status in _FAILING and case.error_message
    }
    for key, group in FAILURE_GROUPS.items():
        rendered = group.template.format(
            ms=1,
            request_id="deadbeefcafe",
            code=500,
            order_id=1,
            address="0x0",
            sku=1,
            row=1,
            timestamp="2026-01-01T00:00:00",
            port=1,
            attempt=1,
        )
        assert error_signature(rendered) in seen, f"{key} is declared but never emitted"


def test_guarantee_3_passing_and_skipped_cases_carry_no_error(plan):
    for _run, case in _cases(plan):
        if case.status in _FAILING:
            assert case.failure_category is not None
        else:
            assert case.error_message is None
            assert case.stack_trace is None
            assert case.failure_category is None


# ── 4. in-progress run ───────────────────────────────────────────────────────


def test_guarantee_4_one_in_progress_run_dated_today_partially_filled(plan):
    live = [run for run in plan.runs if run.status == LaunchStatus.IN_PROGRESS]
    assert len(live) == 1
    run = live[0]
    assert run.start_time.date() == NOW.date()
    assert run.start_time <= NOW
    assert run.duration_ms is None
    assert run.release_key is None, "an in-flight run has no link yet, by design"
    # Partially filled: something has landed, but not the whole suite set.
    assert 0 < run.total_tests < min(r.total_tests for r in _finished(plan))
    assert run.total_tests == len(run.cases)


def test_guarantee_4_the_in_progress_run_has_the_shape_the_live_path_writes(plan):
    """stream_service.create_session writes the stub and live_session_drainer
    ._project refreshes it on every drain. Neither sets pass_rate or
    duration_ms (they arrive when the session completes); both write
    trigger_source="live_stream" and ingestion_source="live"; the drainer
    stamps end_time with the drain time — never NULL on a run that has
    projected results."""
    run = next(run for run in plan.runs if run.status == LaunchStatus.IN_PROGRESS)
    assert run.trigger_source == "live_stream"
    assert run.ingestion_source == "live"
    assert run.pass_rate is None
    assert run.duration_ms is None
    assert run.end_time == NOW, "the latest drain is the moment the plan was taken"
    assert run.start_time <= run.end_time
    # No finished run carries the live-only trigger.
    assert all(r.trigger_source != "live_stream" for r in _finished(plan))


def test_guarantee_4_the_live_run_adds_nothing_new_to_the_catalogue(plan):
    """The seed skips canonical sync for the unfinished run, as the product
    does (sync_canonical_test_cases runs from the finalize pipeline only). So
    every test the live run reports must already be catalogued by a finished
    run — otherwise its cases would point at no canonical row at all."""
    live = next(run for run in plan.runs if run.status == LaunchStatus.IN_PROGRESS)
    finished_fps = {
        case.test_fingerprint for run in _finished(plan) for case in run.cases
    }
    assert {case.test_fingerprint for case in live.cases} <= finished_fps


def test_guarantee_4_the_in_progress_run_stays_today_just_after_midnight():
    just_after_midnight = datetime(2026, 9, 21, 0, 0, 5, tzinfo=timezone.utc)
    plan = build_viz_seed_plan(SLUG, just_after_midnight)
    live = next(run for run in plan.runs if run.status == LaunchStatus.IN_PROGRESS)
    assert live.start_time.date() == just_after_midnight.date()
    assert live.start_time <= just_after_midnight
    assert live.end_time == just_after_midnight


# ── 5. UNKNOWN results ───────────────────────────────────────────────────────


def test_guarantee_5_unknown_cases_are_counted_on_their_run(plan):
    runs_with_unknown = [
        run
        for run in plan.runs
        if any(case.status == TestStatus.UNKNOWN for case in run.cases)
    ]
    assert runs_with_unknown
    for run in plan.runs:
        unknown = sum(1 for case in run.cases if case.status == TestStatus.UNKNOWN)
        assert run.unknown_tests == unknown
    # Ruled-out failures but uninterpretable results grade STOPPED, not PASSED.
    for run in runs_with_unknown:
        if (
            run.status != LaunchStatus.IN_PROGRESS
            and run.failed_tests + run.broken_tests == 0
        ):
            assert run.status == LaunchStatus.STOPPED


# ── 6. NULL durations ────────────────────────────────────────────────────────


def test_guarantee_6_some_cases_and_a_finished_run_have_null_duration(plan):
    untimed_cases = [case for _run, case in _cases(plan) if case.duration_ms is None]
    timed_cases = [case for _run, case in _cases(plan) if case.duration_ms is not None]
    assert untimed_cases and timed_cases
    # Executed cases too, not only skipped ones — a parser that reported no timings.
    assert any(case.status == TestStatus.PASSED for case in untimed_cases)

    finished_untimed = [run for run in _finished(plan) if run.duration_ms is None]
    assert finished_untimed, "a finished run with no measured duration is required"
    for run in finished_untimed:
        assert all(case.duration_ms is None for case in run.cases)
    for run in plan.runs:
        timed = [case.duration_ms for case in run.cases if case.duration_ms is not None]
        if run.status == LaunchStatus.IN_PROGRESS:
            assert run.duration_ms is None
        else:
            # NULL when nothing was measured, the sum of what was otherwise.
            assert run.duration_ms == (sum(timed) if timed else None)


# ── 7. always-skipped suite ──────────────────────────────────────────────────


def test_guarantee_7_one_suite_is_skipped_in_every_run(plan):
    statuses_by_suite: dict[str, Counter] = defaultdict(Counter)
    runs_by_suite: dict[str, int] = Counter()
    for run in plan.runs:
        for suite in {case.suite_name for case in run.cases}:
            runs_by_suite[suite] += 1
        for case in run.cases:
            statuses_by_suite[case.suite_name][case.status] += 1

    all_skipped = {
        suite
        for suite, counts in statuses_by_suite.items()
        if set(counts) == {TestStatus.SKIPPED}
    }
    assert all_skipped == {ALWAYS_SKIPPED_SUITE}
    assert runs_by_suite[ALWAYS_SKIPPED_SUITE] == len(
        _finished(plan)
    ), "present in every finished run"

    counts = statuses_by_suite[ALWAYS_SKIPPED_SUITE]
    # Not measured — nothing executed — rather than 0%.
    assert (
        executed_count(
            counts[TestStatus.PASSED],
            counts[TestStatus.FAILED],
            counts[TestStatus.BROKEN],
        )
        == 0
    )
    assert counts[TestStatus.SKIPPED] > 0
    # Every other suite has a measurable pass rate.
    for suite, other in statuses_by_suite.items():
        if suite != ALWAYS_SKIPPED_SUITE:
            assert (
                executed_count(
                    other[TestStatus.PASSED],
                    other[TestStatus.FAILED],
                    other[TestStatus.BROKEN],
                )
                > 0
            )


# ── 8. stale canonical test ──────────────────────────────────────────────────


def test_guarantee_8_a_suite_executed_before_but_not_in_the_last_30_days(plan):
    cutoff = NOW - timedelta(days=STALE_AFTER_DAYS)
    last_seen: dict[str, datetime] = {}
    first_seen: dict[str, datetime] = {}
    suite_of: dict[str, str] = {}
    for run, case in _cases(plan):
        fp = case.test_fingerprint
        suite_of[fp] = case.suite_name
        first_seen[fp] = min(first_seen.get(fp, run.start_time), run.start_time)
        last_seen[fp] = max(last_seen.get(fp, run.start_time), run.start_time)

    stale = {fp for fp, seen in last_seen.items() if seen < cutoff}
    assert stale, "a canonical test last executed before the cutoff is required"
    assert {suite_of[fp] for fp in stale} == {STALE_SUITE}
    # Executed before that — more than once, so it is history, not a one-off.
    for fp in stale:
        assert first_seen[fp] < last_seen[fp]
    # The whole suite is stale, so its suite-level card goes stale too.
    assert all(
        last_seen[fp] < cutoff for fp, suite in suite_of.items() if suite == STALE_SUITE
    )
    # And nothing else is: every other suite ran inside the window.
    for fp, suite in suite_of.items():
        if suite != STALE_SUITE:
            assert last_seen[fp] >= cutoff


# ── 9. release links and aggregates ──────────────────────────────────────────


def test_guarantee_9_every_run_has_one_primary_release_or_is_deliberately_unattributed(
    plan,
):
    keys = {r.key for r in plan.releases}
    unattributed = [run for run in plan.runs if run.release_key is None]
    for run in plan.runs:
        if run.release_key is None:
            assert run.link_source is None
        else:
            assert run.release_key in keys
            assert run.link_source is not None
    assert 1 <= len(unattributed) <= 6, "a deliberate handful, not a pattern"
    assert len(unattributed) < len(plan.runs) // 4
    # Every release except the plannable future one carries evidence.
    linked = {run.release_key for run in plan.runs if run.release_key}
    assert len(linked) >= 7


@pytest.mark.parametrize("slug", [SLUG, "mobile-banking", "api-gateway", "x"])
@pytest.mark.parametrize(
    "now",
    [
        NOW,
        datetime(2026, 9, 21, 0, 0, 5, tzinfo=timezone.utc),  # just after midnight UTC
        datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, 23, 59, 59, tzinfo=timezone.utc),
    ],
)
def test_guarantee_9_no_run_starts_before_its_release_exists(slug, now):
    """Attribution uses the window [created_at, planned_date) of the release —
    ``_RELEASE_DEFS``' starts/ends days — and the platform release by branch
    from its created_at on. The run's actual (jittered) start must lie inside
    that window: a run older than its release is evidence from before the
    release existed."""
    plan = build_viz_seed_plan(slug, now)
    releases = {r.key: r for r in plan.releases}
    attributed = [run for run in plan.runs if run.release_key is not None]
    assert attributed
    for run in attributed:
        release = releases[run.release_key]
        assert run.start_time >= release.created_at, (run.build_number, release.key)
        if run.branch != "feature/platform-v2":
            assert run.start_time < release.planned_date, (
                run.build_number,
                release.key,
            )


def test_guarantee_9_aggregates_equal_the_cases_and_use_the_canonical_pass_rate(plan):
    for run in plan.runs:
        counts = Counter(case.status for case in run.cases)
        assert run.total_tests == len(run.cases)
        assert run.passed_tests == counts[TestStatus.PASSED]
        assert run.failed_tests == counts[TestStatus.FAILED]
        assert run.skipped_tests == counts[TestStatus.SKIPPED]
        assert run.broken_tests == counts[TestStatus.BROKEN]
        assert run.unknown_tests == counts[TestStatus.UNKNOWN]
        assert (
            run.passed_tests
            + run.failed_tests
            + run.skipped_tests
            + run.broken_tests
            + run.unknown_tests
        ) == run.total_tests
        if run.status == LaunchStatus.IN_PROGRESS:
            # The live path writes no pass rate until the session completes.
            assert run.pass_rate is None
        else:
            assert run.pass_rate == canonical_pass_rate(
                run.passed_tests, run.failed_tests, run.broken_tests
            )


def test_guarantee_9_terminal_status_follows_the_counts(plan):
    for run in _finished(plan):
        if run.failed_tests + run.broken_tests > 0:
            assert run.status == LaunchStatus.FAILED
        elif run.unknown_tests > 0:
            assert run.status == LaunchStatus.STOPPED
        else:
            assert run.status == LaunchStatus.PASSED
        assert run.end_time is not None and run.end_time >= run.start_time
    assert any(run.status == LaunchStatus.PASSED for run in _finished(plan))
    assert any(run.status == LaunchStatus.FAILED for run in _finished(plan))


def test_suite_attribution_matches_the_ingestion_rule(plan):
    from app.services.ingestion import compute_suite_attribution

    for run in plan.runs:
        counts = Counter(case.suite_name for case in run.cases)
        primary, names = compute_suite_attribution(list(counts.items()))
        assert run.primary_suite_name == primary
        assert list(run.suite_names) == names


def test_fingerprints_are_unique_within_a_run(plan):
    for run in plan.runs:
        fps = [case.test_fingerprint for case in run.cases]
        assert len(set(fps)) == len(fps)


def test_fingerprints_are_file_ingestions_own(plan):
    """A real report ingested into a demo project must resolve to the same
    canonical tests, so the seed uses ingestion's recipe — by calling it."""
    from app.models.postgres import TestCase
    from app.services.ingestion import make_test_fingerprint

    width = TestCase.__table__.columns["test_fingerprint"].type.length
    cases = [case for _run, case in _cases(plan)]
    for case in cases[::7] + cases[-3:]:
        assert case.test_fingerprint == make_test_fingerprint(
            case.test_name, case.class_name
        )
        assert len(case.test_fingerprint) <= width


# ── 10. retries and flaky flags ──────────────────────────────────────────────


def test_guarantee_10_retries_and_flaky_runs_are_present(plan):
    retried = [case for _run, case in _cases(plan) if (case.retry_count or 0) > 0]
    flaky = [case for _run, case in _cases(plan) if case.is_flaky_run is True]
    assert retried and flaky
    # Both outcomes of a retry: passed on retry, and failed regardless.
    assert any(case.status == TestStatus.PASSED for case in retried)
    assert any(case.status == TestStatus.FAILED for case in retried)


# ── 11. determinism and isolation ────────────────────────────────────────────


def test_guarantee_11_same_inputs_same_plan():
    assert build_viz_seed_plan(SLUG, NOW) == build_viz_seed_plan(SLUG, NOW)
    assert build_viz_seed_plan(SLUG, NOW, seed=7) == build_viz_seed_plan(
        SLUG, NOW, seed=7
    )


def test_guarantee_11_different_inputs_different_plans():
    base = build_viz_seed_plan(SLUG, NOW)
    assert build_viz_seed_plan("mobile-banking", NOW) != base
    assert build_viz_seed_plan(SLUG, NOW, seed=7) != base
    assert build_viz_seed_plan(SLUG, NOW - timedelta(days=1)) != base


def test_guarantee_11_the_planner_never_touches_the_base_seed_rng():
    # seed_dev_data.RNG feeds every base value in sequence; one extra draw
    # would shift all the data seeded after it. The global RNG must stay
    # untouched too — that is what an accidental ``random.randint`` uses.
    base_before = seed_dev_data.RNG.getstate()
    global_before = random.getstate()
    build_viz_seed_plan(SLUG, NOW)
    build_viz_seed_plan("api-gateway", NOW, seed=99)
    assert seed_dev_data.RNG.getstate() == base_before
    assert random.getstate() == global_before


def test_the_planner_module_does_not_import_the_base_seed():
    import scripts.seed_viz_data as viz

    assert not hasattr(viz, "RNG")
    assert not hasattr(viz, "seed_dev_data")


def test_volume_stays_demo_sized(plan):
    assert 40 <= len(plan.runs) <= 60
    assert sum(run.total_tests for run in plan.runs) < 2000


def test_naive_now_is_treated_as_utc():
    naive = NOW.replace(tzinfo=None)
    assert build_viz_seed_plan(SLUG, naive) == build_viz_seed_plan(SLUG, NOW)
