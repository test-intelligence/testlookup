"""Phase 5: deliver the verdict where people are, and stop the pile growing.

Roadmap Phase 5 (``architecture/TEST_INTELLIGENCE_PLAN.md``).

**P5-A** — a bare failure count is mostly noise (roughly 84% of pass→fail
transitions involve a flaky test), and the surveyed adoption gap says the
verdict has to arrive *before* a human is paged. The PR comment now says what
the failures appear to be — **additively**, hiding nothing.

**P5-B** — quarantine already had an SLA, auto-defects and auto-promotion. What
it lacked was anything stopping the pile growing quietly: a cap, an unmasking
safeguard, and a visible population. Quarantine otherwise becomes "delay with
documentation".
"""
from __future__ import annotations

import inspect

from app.services.attribution_summary import (
    summarize,
    summary_labels,
    verdict_breakdown,
)
from app.services.failure_attribution_service import (
    Attribution,
    AttributionInputs,
    AttributionVerdict,
)
from app.services.quarantine_health_service import (
    DEFAULT_MAX_ACTIVE,
    SEVERITY_ALERT,
    SEVERITY_WARN,
    evaluate_cap,
    evaluate_unmasking,
    signature_changed,
    summarize_health,
)


def _attr(verdict: AttributionVerdict, **inputs) -> Attribution:
    return Attribution(
        verdict=verdict,
        confidence=0.8,
        rationale="",
        inputs=AttributionInputs(**inputs),
    )


# ── P5-A: the summary line ───────────────────────────────────────────────────

def test_no_failures_produces_no_line_rather_than_an_empty_sentence():
    assert summarize([]) == ""


def test_the_line_leads_with_what_is_attributable_to_the_change():
    """A reviewer wants "is any of this mine?" answered first."""
    line = summarize([
        _attr(AttributionVerdict.LIKELY_FLAKY),
        _attr(AttributionVerdict.LIKELY_YOUR_CHANGE),
    ])
    assert line.index("attributable to this change") < line.index("known-flaky")


def test_every_failure_is_accounted_for_in_the_sentence():
    """A failure that vanishes from the summary is the one outcome worse than
    an unhelpfully-labelled one."""
    attributions = (
        [_attr(AttributionVerdict.LIKELY_YOUR_CHANGE)] * 2
        + [_attr(AttributionVerdict.LIKELY_INFRA, cluster_key="sfc_001")] * 4
        + [_attr(AttributionVerdict.UNCERTAIN)]
    )
    line = summarize(attributions)
    assert line.startswith("7 failures:")
    assert "2 attributable to this change" in line
    assert "4 likely infrastructure" in line
    assert "1 uncertain" in line


def test_the_infrastructure_clause_names_the_cluster_a_reviewer_can_check():
    """A reviewer can go look at one shared dependency; they cannot check
    the word "infra"."""
    line = summarize([
        _attr(AttributionVerdict.LIKELY_INFRA,
              cluster_key="sfc_003", cluster_cause_family="external_dependency"),
        _attr(AttributionVerdict.LIKELY_INFRA,
              cluster_key="sfc_003", cluster_cause_family="external_dependency"),
    ])
    assert "sfc_003" in line
    assert "external dependency" in line


def test_a_single_failure_reads_as_singular():
    assert summarize([_attr(AttributionVerdict.UNCERTAIN)]).startswith("1 failure:")


def test_an_unrecognised_verdict_is_counted_not_dropped():
    class Bogus:
        verdict = "SOMETHING_NEW"
        inputs = None

    line = summarize([Bogus(), _attr(AttributionVerdict.UNCERTAIN)])
    assert line.startswith("2 failures:")


def test_the_breakdown_lists_every_verdict_including_zeros():
    """A consumer rendering the breakdown must not silently omit a category it
    has never seen — the vocabulary-subset defect class."""
    counts = verdict_breakdown([_attr(AttributionVerdict.LIKELY_FLAKY)])
    assert set(counts) == {v.value for v in AttributionVerdict}
    assert counts[AttributionVerdict.LIKELY_FLAKY.value] == 1
    assert counts[AttributionVerdict.LIKELY_INFRA.value] == 0


def test_every_verdict_has_a_label_for_the_summary_surface():
    assert set(summary_labels()) == {v.value for v in AttributionVerdict}


def test_the_pr_comment_adds_the_line_without_hiding_anything():
    """Additive only: the failure sections must still render. A comment that
    marked itself green because the failures looked flaky would be the
    suppression this roadmap refuses."""
    import app.services.github_pr_comment_service as module

    source = inspect.getsource(module._build_comment_body)
    assert "attribution_summary" in source
    # The failure sections are still built unconditionally.
    assert "part.newly_failed" in source
    for forbidden in ("suppress", "skip_comment", "mark_green"):
        assert forbidden not in source


def test_a_failing_attribution_does_not_block_the_comment():
    """A comment that says less beats a comment that never posts."""
    import app.services.github_pr_comment_service as module

    source = inspect.getsource(module)
    assert "pr_comment_attribution_failed" in source


# ── P5-B: the cap warns, never blocks ────────────────────────────────────────

def test_under_the_cap_is_silent():
    assert evaluate_cap(active_count=3, max_active=8) is None


def test_at_the_cap_warns():
    warning = evaluate_cap(active_count=8, max_active=8)
    assert warning is not None and warning.severity == SEVERITY_WARN


def test_over_the_cap_escalates():
    warning = evaluate_cap(active_count=20, max_active=8)
    assert warning is not None and warning.severity == SEVERITY_ALERT


def test_zero_means_unlimited_not_zero_allowed():
    """A team may want the lifecycle without the ceiling. Reading 0 as "none
    allowed" would warn on every single quarantine."""
    assert evaluate_cap(active_count=500, max_active=0) is None


def test_the_cap_never_blocks_a_quarantine():
    """Refusing to quarantine a genuinely broken test would push the noise back
    into the build. The service may only describe."""
    import app.services.quarantine_health_service as module

    source = inspect.getsource(module)
    for forbidden in ("raise ", "block", "reject", "deny"):
        assert f"def {forbidden}" not in source
    health = summarize_health("p", active_count=99, max_active=8)
    assert health.over_cap is True
    assert "not enforced" in health.to_dict()["policy"]


# ── P5-B: the unmasking safeguard ────────────────────────────────────────────

def test_a_changed_failure_signature_is_flagged():
    """Quarantine can mask a real bug. A quarantined test failing a DIFFERENT
    way is new information nobody is looking for, by construction."""
    warning = evaluate_unmasking(
        "test_checkout",
        previous_error="AssertionError: expected 200 got 500",
        current_error="java.net.UnknownHostException: payments.internal",
    )
    assert warning is not None
    assert warning.severity == SEVERITY_ALERT
    assert "test_checkout" in warning.message


def test_the_same_failure_is_not_flagged():
    assert evaluate_unmasking(
        "t", previous_error="Timeout after 30s", current_error="Timeout after 30s"
    ) is None


def test_signature_comparison_ignores_incidental_noise():
    """Compared on the normalised signature, not the raw message, so a changed
    line number or address is not mistaken for a new fault."""
    assert signature_changed(
        "AssertionError at line 42", "AssertionError at line 87"
    ) is False


def test_missing_data_is_not_a_change():
    """A warning nobody can act on is noise."""
    assert signature_changed(None, "boom") is False
    assert signature_changed("boom", None) is False


# ── P5-B: the population is visible ──────────────────────────────────────────

def test_health_reports_the_population_and_the_limit_it_judged_against():
    health = summarize_health("p", active_count=5, max_active=8).to_dict()
    assert health["active_count"] == 5
    assert health["max_active"] == 8


def test_a_breached_sla_is_surfaced():
    """A deadline nobody surfaces is a deadline nobody meets."""
    health = summarize_health("p", active_count=2, max_active=8, stale_count=3)
    kinds = {w.kind for w in health.warnings}
    assert "sla_breached" in kinds


def test_a_healthy_quarantine_produces_no_warnings():
    health = summarize_health("p", active_count=1, max_active=DEFAULT_MAX_ACTIVE)
    assert health.warnings == []


def test_summarize_health_never_raises_on_junk():
    health = summarize_health(
        "p", active_count=-4, max_active=None, stale_count=None, unmasking=[None]
    )
    assert health.active_count == 0
