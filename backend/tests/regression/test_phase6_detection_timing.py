"""Phase 6: detect sooner, and be honest about what actually limits it.

Roadmap Phase 6 (``architecture/TEST_INTELLIGENCE_PLAN.md``) — the last phase,
and the one whose headline justification did not survive review.

The refuted claim ("75% of flaky tests are already flaky at their introducing
commit", voted down 1–2) justifies nothing here. What survives is the weaker
**85/15 among order- and implementation-dependent flaky tests in 55 Java OSS
projects**, heavily qualified by its authors — enough to justify screening the
new and the directly-modified *first*, not enough to justify a constant.

So these tests guard three properties:

1. **Tier 1 never terminates the search.** Anything screening does not reach is
   still swept by tier 2 — which is where the environment- and
   dependency-induced flakiness this product mostly sees would land.
2. **Cadence is derived, not borrowed.** A commit-count cadence would never
   fire on this corpus; the Phase 0 census measured zero of four genuine
   projects clearing what one assumes.
3. **No fabricated latency.** A fingerprint whose first appearance was not
   observed is excluded and counted, never backfilled to a flattering zero.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app.services.flaky_detection_timing_service import (
    DOMINANT_TERMS,
    DOMINATED_BY_RUN_FREQUENCY,
    DOMINATED_BY_SWEEP_CADENCE,
    MAX_SCREEN_INTERVAL_MINUTES,
    MIN_SCREEN_INTERVAL_MINUTES,
    SWEEP_INTERVAL_MINUTES,
    dominant_term,
    expected_minutes_to_floor,
    percentiles,
    recommend_cadence,
)
from app.services.flaky_score_service import MIN_OBSERVATIONS
from app.services.flaky_screening_service import (
    DIRECT_MODIFICATION_OVERLAP,
    REASON_LABELS,
    SCREEN_CORPUS_SWEEP,
    SCREEN_MODIFIED_TEST,
    SCREEN_NEW_TEST,
    SCREEN_REASONS,
    ScreeningCandidate,
    classify_reason,
    is_directly_modified,
)


# ── The refuted claim must not have come back ────────────────────────────────

def test_the_refuted_75_percent_claim_justifies_nothing_in_this_phase():
    """It was voted down 1–2. A module that quietly reinstated it would be
    building on evidence the review rejected."""
    import app.services.flaky_detection_timing_service as timing
    import app.services.flaky_screening_service as screening

    for module in (screening, timing):
        source = inspect.getsource(module).lower()
        if "75%" in source or "introducing commit" in source:
            # Citing it is fine — that is how the record stays readable. Citing
            # it WITHOUT the refutation is how a rejected claim quietly becomes
            # load-bearing again.
            assert "refuted" in source
            assert "is not used here" in source or "justifies nothing" in source


def test_the_surviving_finding_is_cited_with_its_qualifications():
    """"85/15" as a bare constant would be a stronger claim than the corpus
    supports — 245 flaky tests, 55 Java OSS projects, async and concurrency
    flakiness under-sampled, authors say it may not generalize."""
    import app.services.flaky_screening_service as screening

    source = inspect.getsource(screening)
    assert "may not generalize" in source
    assert "55 Java OSS projects" in source


def test_no_borrowed_commit_count_cadence():
    """~150 commits is unusable here: the Phase 0 census measured zero of four
    genuine projects clearing what a commit-count cadence assumes."""
    import app.services.flaky_detection_timing_service as timing

    cadence = recommend_cadence(4.0)
    assert "commit" not in cadence.basis.lower()
    assert "census" in inspect.getsource(timing)


# ── Tier 1 screens; it does not conclude ─────────────────────────────────────

def test_screening_produces_a_population_not_a_verdict():
    """This product ingests results, it does not execute tests — so a test seen
    once supports no verdict at all. A screening tier that emitted one would be
    fabricating confidence."""
    candidate = ScreeningCandidate(
        test_fingerprint="fp",
        test_name="t",
        reason=SCREEN_NEW_TEST,
        first_seen_at=datetime.now(timezone.utc),
        first_seen_is_exact=True,
        observation_count=1,
    ).to_dict()
    for forbidden in ("verdict", "score", "flaky", "confidence"):
        assert forbidden not in candidate


def test_every_screen_reason_has_a_label():
    """The vocabulary-subset defect class: a reason added without a label
    renders as a raw enum string to a user."""
    assert set(REASON_LABELS) == set(SCREEN_REASONS)


def test_screen_reasons_are_unique():
    assert len(set(SCREEN_REASONS)) == len(SCREEN_REASONS)


def test_the_sweep_reason_is_part_of_the_vocabulary():
    """Tier 2 has to be able to record how it met a fingerprint, or everything
    it adopts is indistinguishable from something tier 1 screened."""
    assert SCREEN_CORPUS_SWEEP in SCREEN_REASONS


def test_new_beats_modified_when_a_test_is_both():
    """A new test's own file appearing in the diff is nearly tautological;
    "new" is the stronger and more specific statement."""
    assert classify_reason(is_new=True, is_modified=True) == SCREEN_NEW_TEST


def test_modified_is_reported_when_the_test_is_not_new():
    assert classify_reason(is_new=False, is_modified=True) == SCREEN_MODIFIED_TEST


def test_neither_population_declines_to_claim_one():
    """Tier 1 must not invent a population. Tier 2 still reaches it."""
    assert classify_reason(is_new=False, is_modified=False) is None


def test_directly_modified_means_the_test_itself_not_its_neighbourhood():
    """The finding is about tests whose own source was edited. Admitting a
    fuzzy same-directory match would quietly turn a screening tier into
    "most of the corpus"."""
    assert DIRECT_MODIFICATION_OVERLAP == 1.0
    assert is_directly_modified(1.0) is True
    # 0.8 is path_overlap's directory rung and 0.6 its fuzzy rung.
    assert is_directly_modified(0.8) is False
    assert is_directly_modified(0.6) is False


def test_absent_or_junk_overlap_is_not_a_modification():
    assert is_directly_modified(None) is False
    assert is_directly_modified("nonsense") is False  # type: ignore[arg-type]


def test_tier_one_never_terminates_the_search():
    """The 15% tail — flakiness introduced by changes elsewhere — is exactly
    what this product sees most. A screen that ended the search would miss it
    by construction."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.sweep_project)
    # The sweep adopts fingerprints tier 1 never had a reason to look at.
    assert "adopted" in source
    assert "first_seen_is_exact=False" in source


def test_screening_degrades_rather_than_failing_the_beat():
    """A locator failure must cost one fingerprint's classification, not the
    whole sweep — tier 2 still covers it."""
    import app.services.flaky_screening_service as screening

    source = inspect.getsource(screening._touches)
    assert "except Exception" in source
    assert "return False" in source


# ── Cadence is derived from measured arrival rate ────────────────────────────

def test_a_busy_project_screens_faster_than_a_quiet_one():
    busy = recommend_cadence(48.0)
    quiet = recommend_cadence(1.0)
    assert busy.screen_interval_minutes < quiet.screen_interval_minutes


def test_the_screen_interval_is_bounded_at_both_ends():
    """A floor so the beat cannot be scheduled faster than it finishes; a
    ceiling so screening does not degrade into the nightly sweep again."""
    for rate in (0.001, 0.5, 1, 10, 1000, 100_000):
        cadence = recommend_cadence(rate)
        assert MIN_SCREEN_INTERVAL_MINUTES <= cadence.screen_interval_minutes
        assert cadence.screen_interval_minutes <= MAX_SCREEN_INTERVAL_MINUTES


def test_a_silent_project_says_so_instead_of_reporting_an_interval():
    cadence = recommend_cadence(0)
    assert "no runs observed" in cadence.basis
    assert cadence.runs_per_day == 0.0


def test_cadence_survives_junk_input():
    assert recommend_cadence("not a number").runs_per_day == 0.0  # type: ignore[arg-type]
    assert recommend_cadence(None).runs_per_day == 0.0  # type: ignore[arg-type]
    assert recommend_cadence(-5).runs_per_day == 0.0


def test_the_sweep_stays_nightly_whatever_the_project_does():
    """The whole-corpus pass is measured against the score's own 30-day window,
    which does not move enough inside a day to justify re-reading the corpus."""
    for rate in (0, 1, 1000):
        assert recommend_cadence(rate).sweep_interval_minutes == SWEEP_INTERVAL_MINUTES


def test_the_payload_explains_why_the_cadence_is_not_commit_based():
    """A reader will ask; the answer is a measurement, and it should be next to
    the number rather than in a design document."""
    payload = recommend_cadence(4.0).to_dict()
    assert "census" in payload["why_not_commit_based"]


# ── The finding: run frequency, not sweep frequency ──────────────────────────

def test_a_test_below_the_floor_needs_that_many_more_runs():
    minutes = expected_minutes_to_floor(observation_count=1, runs_per_day=1.0)
    assert minutes == pytest.approx((MIN_OBSERVATIONS - 1) * 1440)


def test_a_test_at_the_floor_waits_no_longer():
    assert expected_minutes_to_floor(MIN_OBSERVATIONS, 1.0) == 0.0
    assert expected_minutes_to_floor(MIN_OBSERVATIONS + 10, 1.0) == 0.0


def test_an_unknown_arrival_rate_yields_no_number():
    """An unanswerable question gets no answer, not a zero that reads as
    "detected instantly"."""
    assert expected_minutes_to_floor(1, 0) is None
    assert expected_minutes_to_floor(1, "x") is None  # type: ignore[arg-type]


def test_on_a_thin_corpus_run_frequency_dominates():
    """The Phase 0 census: 12–15 fingerprints, median 5–12 runs each over the
    window. At roughly a run a day, clearing a 5-observation floor is days
    away — so shortening a 30-minute screen changes nothing, and the product
    must say that rather than sell the cadence."""
    wait = expected_minutes_to_floor(observation_count=1, runs_per_day=1.0)
    assert dominant_term(30, wait) == DOMINATED_BY_RUN_FREQUENCY


def test_on_a_busy_corpus_the_cadence_dominates():
    wait = expected_minutes_to_floor(observation_count=4, runs_per_day=480.0)
    assert dominant_term(30, wait) == DOMINATED_BY_SWEEP_CADENCE


def test_an_unknown_wait_names_no_bottleneck():
    """Defaulting would pick the answer that flatters the cadence."""
    assert dominant_term(30, None) is None


def test_the_bottleneck_vocabulary_is_enumerable():
    assert set(DOMINANT_TERMS) == {
        DOMINATED_BY_RUN_FREQUENCY,
        DOMINATED_BY_SWEEP_CADENCE,
    }


# ── No fabricated latency ────────────────────────────────────────────────────

def test_no_samples_reports_nothing_measured_not_zero():
    """"Nothing measurable yet" and "measured, instant" are different claims."""
    assert percentiles([]) == {"p50": None, "p90": None, "max": None}


def test_percentiles_are_nearest_rank_over_the_measurable_subset():
    stats = percentiles([1.0, 2.0, 3.0, 4.0, 10.0])
    assert stats["p50"] == 3.0
    assert stats["p90"] == 10.0
    assert stats["max"] == 10.0


def test_a_single_sample_is_reported_as_itself():
    assert percentiles([7.0]) == {"p50": 7.0, "p90": 7.0, "max": 7.0}


def test_unobserved_first_appearances_are_excluded_from_latency():
    """A fingerprint that predates screening has no honest latency. Counting it
    as zero would make rollout day look like instant detection forever."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.detection_timing)
    assert "if not row.first_seen_is_exact" in source
    assert "excluded += 1" in source
    assert "continue" in source


def test_the_exclusion_is_reported_not_silent():
    """A dropped population that nobody can see reads as full coverage."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.DetectionTiming.to_dict)
    assert "excluded_first_appearance_not_observed" in source


def test_the_retention_artefact_is_disclosed():
    """A purge can delete the older runs that marked a fingerprint as
    pre-existing, making it look newly-seen and latency look better."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.detection_timing)
    assert "Retention purges" in source


def test_negative_latency_is_dropped_rather_than_reported():
    """Clock skew can produce one. A negative detection latency is not a fast
    detection."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.detection_timing)
    assert "total_seconds() >= 0" in source


def test_first_seen_is_written_once_and_never_recomputed():
    """A re-derived first-seen tracks the reader's window, not the test's
    history — which is the entire reason this state is persisted."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.record_screening)
    # The established-row branch must not touch either field.
    established = source.split("continue", 1)[1]
    assert "first_seen_at" not in established
    assert "first_seen_is_exact" not in established


def test_an_unscreened_project_says_so_rather_than_reporting_zeros():
    """The same honesty contract as /metrics/flaky-readiness."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.detection_timing)
    assert "available=False" in source
    assert "insufficient_data_reason" in source


# ── Wiring ───────────────────────────────────────────────────────────────────

def test_both_tiers_are_scheduled_and_the_sweep_follows_the_scorer():
    """The sweep closes a latency clock from the score. Running it before the
    recompute would record every detection a day late."""
    import app.worker.celery_app as celery_app

    beats = celery_app.celery_app.conf.beat_schedule
    assert "screen-new-test-fingerprints" in beats
    assert "nightly-flaky-detection-sweep" in beats

    score = beats["nightly-flaky-score-recompute"]["schedule"]
    sweep = beats["nightly-flaky-detection-sweep"]["schedule"]
    assert (min(sweep.hour), min(sweep.minute)) > (min(score.hour), min(score.minute))


def test_screening_stays_off_the_ingest_path():
    """Screening buys nothing by being synchronous — there is no re-run to
    trigger — and a screening bug must not be able to cost an ingestion."""
    import app.services.ingestion_pipeline as pipeline

    source = inspect.getsource(pipeline)
    assert "flaky_screening_service" not in source
    assert "screen_project" not in source


def test_both_tasks_are_gated_on_a_flag_that_is_off_by_default():
    """A project that has not opted in should not accumulate detection state.
    An absent flag row resolves to disabled."""
    import app.worker.tasks as tasks

    for task in (tasks.screen_new_test_fingerprints, tasks.sweep_flaky_detection):
        source = inspect.getsource(task)
        assert "flaky_detection_timing" in source
        assert "skipped += 1" in source


def test_the_tasks_own_their_transactions_and_the_services_do_not():
    """Transaction-boundary discipline: services flush, callers commit."""
    import app.services.flaky_detection_timing_service as timing
    import app.worker.tasks as tasks

    service_source = inspect.getsource(timing)
    assert "db.commit()" not in service_source
    assert "db.rollback()" not in service_source
    for task in (tasks.screen_new_test_fingerprints, tasks.sweep_flaky_detection):
        assert "await db.commit()" in inspect.getsource(task)


def test_one_bad_project_does_not_stop_either_sweep():
    import app.worker.tasks as tasks

    for task in (tasks.screen_new_test_fingerprints, tasks.sweep_flaky_detection):
        source = inspect.getsource(task)
        assert "except Exception" in source
        assert "await db.rollback()" in source


def test_the_endpoint_requires_a_project_and_scopes_it():
    """Detection latency is a claim about one project's own history, and
    test_fingerprint is unique only within a project."""
    import app.routers.metrics as metrics

    source = inspect.getsource(metrics.detection_timing)
    assert "get_accessible_project_ids" in source
    assert "_project_in_scope" in source
    assert "status_code=422" in source


def test_reads_are_bounded_so_a_busy_project_cannot_exhaust_a_worker():
    import app.services.flaky_screening_service as screening

    source = inspect.getsource(screening.screen_project)
    assert "max_rows" in source
    assert "truncated" in source
    assert "flaky_screening_window_truncated" in source


def test_the_screening_query_is_project_scoped():
    """test_fingerprint is not globally unique — an unscoped read blends
    tenants."""
    import app.services.flaky_screening_service as screening

    source = inspect.getsource(screening.screen_project)
    assert source.count("TestRun.project_id == project_id") >= 2


def test_earliest_seen_is_measured_over_the_corpus_not_the_window():
    """A window-local minimum would call every fingerprint new again each time
    the window rolled forward — an infinite supply of fake new tests."""
    import app.services.flaky_screening_service as screening

    source = inspect.getsource(screening.screen_project)
    earliest = source.split("Earliest surviving run per fingerprint", 1)[1]
    head = earliest.split(").all()", 1)[0]
    assert "created_at >= window_start" not in head


def test_structlog_calls_use_keyword_fields():
    """BoundLogger takes (event, **kw); a stdlib-style positional %s raises
    mid-call inside an except block and kills the enclosing feature."""
    import app.services.flaky_detection_timing_service as timing
    import app.services.flaky_screening_service as screening

    for module in (screening, timing):
        for line in inspect.getsource(module).splitlines():
            stripped = line.strip()
            if stripped.startswith("logger.") and "%s" in stripped:
                pytest.fail(f"positional structlog arg: {stripped}")


def test_the_migration_has_a_real_downgrade():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0135_flaky_detection_state.py"
    )
    source = path.read_text(encoding="utf-8")
    assert 'down_revision = "0134"' in source
    body = source.split("def downgrade()", 1)[1]
    assert "drop_table" in body
    # Every index created is dropped again.
    for index in (
        "ux_flaky_detection_state_project_fingerprint",
        "ix_flaky_detection_state_project_scored",
        "ix_flaky_detection_state_project_swept",
    ):
        assert body.count(index) == 1


def test_the_orm_model_matches_the_migration():
    """Migration↔ORM drift is silent until a query hits a column that is not
    there."""
    from app.models.postgres import FlakyDetectionState

    columns = set(FlakyDetectionState.__table__.columns.keys())
    assert {
        "project_id",
        "test_fingerprint",
        "first_seen_at",
        "first_seen_is_exact",
        "screen_reason",
        "first_screened_at",
        "first_scored_at",
        "first_scored_confidence",
        "observation_count",
        "last_swept_at",
    } <= columns


def test_stale_coverage_tolerates_one_missed_beat():
    """Reporting rot after a single skipped run would train people to ignore
    the signal."""
    from app.services.flaky_detection_timing_service import STALE_SWEEP_MULTIPLIER

    assert STALE_SWEEP_MULTIPLIER >= 2


def test_coverage_is_measured_from_work_done_not_from_the_beat_running():
    """A project can appear swept because the task executed while every row it
    should have touched was skipped."""
    import app.services.flaky_detection_timing_service as timing

    source = inspect.getsource(timing.detection_timing)
    assert "row.last_swept_at is not None" in source


def test_naive_timestamps_do_not_crash_the_measurement():
    """Postgres can hand back a naive datetime depending on the driver path;
    subtracting one from an aware datetime raises."""
    from app.services.flaky_detection_timing_service import _aware

    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert _aware(naive).tzinfo is timezone.utc
    aware = datetime.now(timezone.utc)
    assert _aware(aware) is aware
    assert (_aware(aware) - _aware(naive)) > timedelta(0)
