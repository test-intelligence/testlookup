"""Phase 0 must measure honestly, or it is worse than not measuring.

Roadmap Phase 0 (``architecture/TEST_INTELLIGENCE_PLAN.md``). Every test here
pins an *honesty* property rather than a happy path, because the failure mode
this phase exists to prevent is a confident-looking number computed from data
that cannot support it.

Three classes of that failure are guarded:

1. **Fabricated environment grouping** — coalescing an unrecorded environment to
   a shared literal would make unrelated historical runs look like one
   environment, and the flakiness score's environment-consistency signal would
   read "perfectly consistent" for a corpus that simply never recorded it.
2. **Fabricated specificity** — reporting a number from a handful of samples.
3. **Fabricated readiness** — declaring a project scoreable when its tests have
   almost no run history.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.flaky_classifier_calibration import (
    MIN_SAMPLES,
    compute_calibration,
)
from app.services.flaky_readiness_service import (
    MIN_QUALIFYING_FINGERPRINTS,
    MIN_RUNS_PER_FINGERPRINT,
    summarize_readiness,
)
from app.services.run_environment import (
    SOURCE_DERIVED,
    SOURCE_EXPLICIT,
    SOURCE_UNKNOWN,
    normalize_environment,
    resolve_environment,
)


# ── 1. Environment: unknown must stay unknown ────────────────────────────────

def _run(**kwargs):
    base = {"environment": None, "oc_namespace": None, "ci_provider": None, "branch": None}
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n", 123, [], {}])
def test_blank_environment_never_becomes_a_group(blank):
    """THE bug this guards: a falsy environment must not normalize to a label.

    If ``""`` became ``"default"``, every run that never recorded an
    environment would join one enormous synthetic group and look perfectly
    environment-consistent.
    """
    assert normalize_environment(blank) is None


def test_run_with_nothing_to_derive_from_is_unknown_not_default():
    resolved = resolve_environment(_run())
    assert resolved.key is None
    assert resolved.source == SOURCE_UNKNOWN
    assert resolved.is_known is False


def test_explicit_environment_wins_and_is_marked_explicit():
    resolved = resolve_environment(_run(environment="  STAGING  ", ci_provider="github_actions"))
    assert resolved.key == "staging"
    assert resolved.source == SOURCE_EXPLICIT


def test_derived_environment_is_marked_derived_so_it_can_be_weighted_down():
    """A derived key is a guess. Consumers must be able to tell."""
    resolved = resolve_environment(_run(ci_provider="github_actions", branch="main"))
    assert resolved.source == SOURCE_DERIVED
    assert resolved.key == "github_actions:mainline"


def test_openshift_namespace_derivation_uses_the_real_column_name():
    """Regression: this read ``oc_namespace``, but the column is
    ``ocp_namespace`` (siblings ocp_pod_name / ocp_node / ocp_metadata).
    ``getattr(..., None)`` swallowed the typo, so the branch was dead code that
    failed silently on every OpenShift run. Pin the attribute name."""
    resolved = resolve_environment(_run(ocp_namespace="qa-tenant-3"))
    assert resolved.key == "ocp:qa-tenant-3"
    assert resolved.source == SOURCE_DERIVED


def test_openshift_namespace_outranks_the_ci_provider_guess():
    """A namespace is literally an environment; a CI provider is a proxy."""
    resolved = resolve_environment(
        _run(ocp_namespace="staging", ci_provider="jenkins", branch="main")
    )
    assert resolved.key == "ocp:staging"


def test_real_test_run_model_exposes_every_attribute_the_resolver_reads():
    """Guard the CLASS, not the instance: resolve_environment() reads run
    attributes by name, and every one of them must exist on the real ORM model.
    A renamed or mistyped column would otherwise degrade silently to
    'unknown' instead of raising anywhere."""
    from app.models.postgres import TestRun

    for attribute in ("environment", "ocp_namespace", "ci_provider", "branch"):
        assert hasattr(TestRun, attribute), (
            f"resolve_environment() reads TestRun.{attribute}, which no longer exists"
        )


def test_feature_branches_collapse_to_one_class_not_one_group_each():
    """Keying on the raw branch would create thousands of one-run environments,
    each trivially 'inconsistent'."""
    a = resolve_environment(_run(ci_provider="gitlab_ci", branch="feature/add-widget"))
    b = resolve_environment(_run(ci_provider="gitlab_ci", branch="feature/other-thing"))
    assert a.key == b.key == "gitlab_ci:topic"


def test_environment_never_exceeds_the_column_width():
    """String(100) — a longer label must be truncated, not raise or overflow."""
    resolved = resolve_environment(_run(environment="e" * 500))
    assert resolved.key is not None
    assert len(resolved.key) <= 100


def test_environment_threads_end_to_end_through_every_ingest_hop():
    """Regression: the column and the service parameter existed, but no caller
    passed ``environment``, so the dimension was inert — it would have shipped
    and stayed NULL forever.

    Walks the seam rather than one hop: the wire schema must declare it, the
    pipeline must accept it, and every ingestion entry point must forward it.
    A change that drops any single hop fails here.
    """
    import inspect

    from app.models.schemas import IngestPayload
    from app.services.ingestion_pipeline import create_run_from_payload

    # 1. JSON batch wire shape declares it (reaches the task via model_dump()).
    assert "environment" in IngestPayload.model_fields

    # 2. The pipeline accepts it and stores it on the run.
    assert "environment" in inspect.signature(create_run_from_payload).parameters
    assert "environment=normalize_environment(environment)" in inspect.getsource(
        create_run_from_payload
    ), "the pipeline must normalize before persisting, not store raw input"

    # 3. Every ingestion entry point forwards it.
    from app.routers import ingest as ingest_router
    from app.worker import tasks as worker_tasks

    upload_task = inspect.getsource(worker_tasks.ingest_uploaded_file)
    assert "environment=environment" in upload_task, "file-upload path drops environment"

    batch_task = inspect.getsource(worker_tasks.ingest_uploaded_results)
    assert 'environment=payload.get("environment")' in batch_task, (
        "SDK/JSON batch path drops environment"
    )

    router_source = inspect.getsource(ingest_router)
    assert "environment=environment" in router_source, "upload router drops environment"


# ── 2. Calibration: below the sample floor is "insufficient", not a number ───

def _failure(fingerprint, commit, status, error):
    return SimpleNamespace(
        test_fingerprint=fingerprint,
        commit_hash=commit,
        status=status,
        error_message=error,
    )


def test_thin_corpus_reports_insufficient_rather_than_a_specificity():
    rows = [_failure("fp1", "c1", "FAILED", "boom") for _ in range(MIN_SAMPLES - 1)]
    result = compute_calibration(rows, project_id="p", window_days=90)
    assert result.specificity is None, "a number here would be fabricated confidence"
    assert result.is_measured is False
    assert result.insufficient_reason and str(MIN_SAMPLES) in result.insufficient_reason


def test_specificity_is_reported_with_its_sample_count():
    """A specificity without a denominator is unreviewable."""
    rows = [_failure(f"fp{i}", "c1", "FAILED", f"unique error {i}") for i in range(MIN_SAMPLES)]
    result = compute_calibration(rows, project_id="p", window_days=90)
    assert result.is_measured
    assert result.sample_count == MIN_SAMPLES
    assert result.true_negatives + result.false_positives == result.sample_count


def test_specificity_is_a_probability():
    rows = [_failure(f"fp{i}", "c1", "FAILED", f"e{i}") for i in range(MIN_SAMPLES * 2)]
    result = compute_calibration(rows, project_id="p", window_days=90)
    assert 0.0 <= result.specificity <= 1.0


def test_rows_without_a_commit_anchor_are_excluded_not_assumed_stable():
    """Same-code flakiness cannot be established without a commit, so those
    rows are not evidence — and must not silently inflate the sample count."""
    rows = [_failure("fp1", None, "FAILED", "boom") for _ in range(MIN_SAMPLES * 2)]
    result = compute_calibration(rows, project_id="p", window_days=90)
    assert result.sample_count == 0
    assert result.specificity is None


def test_a_genuinely_flaky_fingerprint_is_excluded_from_the_negative_class():
    """Specificity is about failures that are NOT flaky. A fingerprint that
    passed and failed on the same commit is flaky by definition and must not
    be counted as a true negative."""
    rows = [
        _failure("flaky", "c1", "FAILED", "timeout"),
        _failure("flaky", "c1", "PASSED", None),
    ]
    result = compute_calibration(rows, project_id="p", window_days=90, min_samples=1)
    assert result.sample_count == 0


def test_compute_calibration_never_raises_on_malformed_rows():
    junk = [None, object(), SimpleNamespace(), _failure(None, None, None, None), 42]
    result = compute_calibration(junk, project_id="p", window_days=90)
    assert result.specificity is None


# ── 3. Readiness: the gate must actually gate ────────────────────────────────

def test_empty_project_is_not_available_and_says_why():
    readiness = summarize_readiness([], project_id="p", window_days=90)
    assert readiness.available is False
    assert readiness.insufficient_data_reason
    assert readiness.median_runs_per_fingerprint == 0.0


def test_many_shallow_fingerprints_do_not_qualify():
    """The exact scenario the census exists to catch: a project with plenty of
    tests but almost no history per test. Volume is not depth."""
    counts = [3] * 5000
    readiness = summarize_readiness(counts, project_id="p", window_days=90)
    assert readiness.available is False
    assert readiness.qualifying_fingerprints == 0
    assert readiness.total_fingerprints == 5000
    assert "median" in (readiness.insufficient_data_reason or "")


def test_deep_corpus_qualifies():
    counts = [MIN_RUNS_PER_FINGERPRINT] * MIN_QUALIFYING_FINGERPRINTS
    readiness = summarize_readiness(counts, project_id="p", window_days=90)
    assert readiness.available is True
    assert readiness.insufficient_data_reason is None


def test_one_run_below_either_threshold_still_fails_closed():
    """Boundary: the gate must not round in favour of availability."""
    just_under = [MIN_RUNS_PER_FINGERPRINT] * (MIN_QUALIFYING_FINGERPRINTS - 1)
    assert summarize_readiness(just_under, project_id="p", window_days=90).available is False

    shallow = [MIN_RUNS_PER_FINGERPRINT - 1] * (MIN_QUALIFYING_FINGERPRINTS * 10)
    assert summarize_readiness(shallow, project_id="p", window_days=90).available is False


def test_readiness_counts_runs_not_rows():
    """Regression: the census grouped by fingerprint and counted test_case
    ROWS. A parameterised test, a retry, or a duplicated test name inside one
    run inflated the count, so a shallow project could clear a gate named
    'runs per fingerprint' on volume it did not have — fabricated readiness in
    the gate that exists to prevent fabricated readiness.

    Pinned at the SQL level because the arithmetic below it is already correct;
    what regressed was which thing got counted.
    """
    from app.services.flaky_readiness_service import get_flaky_readiness
    import inspect

    source = inspect.getsource(get_flaky_readiness)
    assert "func.distinct(TestCase.test_run_id)" in source, (
        "readiness must count DISTINCT runs per fingerprint, not test_case rows"
    )


def test_readiness_payload_publishes_the_thresholds_it_judged_against():
    """A verdict whose thresholds are invisible cannot be argued with."""
    payload = summarize_readiness([1], project_id="p", window_days=90).to_dict()
    thresholds = payload["thresholds"]
    assert thresholds["min_runs_per_fingerprint"] == MIN_RUNS_PER_FINGERPRINT
    assert thresholds["min_qualifying_fingerprints"] == MIN_QUALIFYING_FINGERPRINTS
    for key in ("available", "insufficient_data_reason", "median_runs_per_fingerprint"):
        assert key in payload
