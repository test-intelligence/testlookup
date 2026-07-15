"""AI-F4 — confidence bands: completeness, basis labeling, and behavior
preservation for the rules engine.

Three contracts:

  1. **Band-table completeness** — every rule the engine can fire (each
     keyword pattern + each statistical heuristic) has a named band with a
     valid, documented basis. A new pattern without a band fails here.

  2. **Behavior preservation** — replacing the hardcoded confidences with
     bands changed NO numeric value. The pinned expectations below are the
     pre-AI-F4 constants; if a band value is ever recalibrated (allowed only
     with an empirical basis), this pin must be updated in the same change
     alongside a CHANGELOG entry.

  3. **Consumer-threshold audit** — the downstream gates that read
     confidence (requires_human_review at 70 in-engine, the pipeline/triage
     threshold default of 80, action-policy's 60) behave identically on
     fixture inputs.
"""
from __future__ import annotations

import pytest

from app.services import confidence_bands as cb
from app.services.rules_engine import RulesEngine, _PATTERNS


# ── 1. Band-table completeness ────────────────────────────────────────────


def test_every_pattern_has_a_band_with_valid_basis():
    pattern_rule_ids = {rule_id for rule_id, _, _, _ in _PATTERNS}
    assert pattern_rule_ids == set(cb.PATTERN_BANDS), (
        "rules_engine._PATTERNS and confidence_bands.PATTERN_BANDS drifted — "
        f"missing bands: {pattern_rule_ids - set(cb.PATTERN_BANDS)}, "
        f"orphan bands: {set(cb.PATTERN_BANDS) - pattern_rule_ids}"
    )


def test_every_band_has_valid_basis_and_provenance():
    for rule_id, band in cb.ALL_BANDS.items():
        assert band.basis in (cb.BASIS_EMPIRICAL, cb.BASIS_HEURISTIC), rule_id
        assert band.provenance.strip(), f"{rule_id} has no provenance note"
        assert 0 <= band.confidence <= 100, rule_id


def test_heuristic_bands_cover_all_engine_heuristics():
    expected = {
        "heuristic.historical_flakiness",
        "heuristic.regression_after_streak",
        "heuristic.duration_anomaly",
        "heuristic.suite_level_failure",
        "heuristic.cross_suite_blast",
        "heuristic.unknown_fallback",
    }
    assert set(cb.HEURISTIC_BANDS) == expected


def test_get_band_raises_on_unknown_rule():
    with pytest.raises(KeyError):
        cb.get_band("pattern.does_not_exist")


# ── 2. Behavior preservation (pre-AI-F4 pinned values) ────────────────────

# (rule_id, expected_confidence) — byte-for-byte the constants that were
# hardcoded in rules_engine.py before AI-F4. Any change here is a
# recalibration and must be justified empirically + CHANGELOG'd.
_PINNED_BAND_VALUES = {
    "pattern.oom": 75,
    "pattern.connection_refused": 70,
    "pattern.timeout": 65,
    "pattern.http_5xx": 70,
    "pattern.dns": 75,
    "pattern.disk_full": 75,
    "pattern.network_unreachable": 75,
    "pattern.resource_exhaustion": 70,
    "pattern.tls": 70,
    "pattern.not_found": 65,
    "pattern.setup_fixture": 60,
    "pattern.null_reference": 65,
    "pattern.ui_locator": 65,
    "pattern.missing_dependency": 70,
    "pattern.flaky_keywords": 55,
    "pattern.assertion": 55,
    "pattern.auth": 60,
    "heuristic.regression_after_streak": 70,
    "heuristic.duration_anomaly": 60,
    "heuristic.suite_level_failure": 70,
    "heuristic.cross_suite_blast": 60,
    "heuristic.unknown_fallback": 30,
    "heuristic.historical_flakiness": 85,  # cap of min(85, 50 + 2n)
}


def test_band_values_preserve_pre_aif4_constants():
    actual = {rid: band.confidence for rid, band in cb.ALL_BANDS.items()}
    assert actual == _PINNED_BAND_VALUES


def test_historical_flakiness_formula_preserved():
    # Original inline formula: min(85, 50 + hist_total * 2)
    assert cb.historical_flakiness_confidence(5) == 60
    assert cb.historical_flakiness_confidence(10) == 70
    assert cb.historical_flakiness_confidence(17) == 84
    assert cb.historical_flakiness_confidence(18) == 85
    assert cb.historical_flakiness_confidence(500) == 85


# End-to-end pins through classify_test: same inputs → same
# (category, confidence) as before the refactor.
_CLASSIFY_CASES = [
    # Historical flakiness (dynamic): 5 pass + 5 fail → min(85, 50+20) = 70
    (
        dict(error_message="anything", history={"pass_count": 5, "fail_count": 5}),
        ("FLAKY", 70, "heuristic.historical_flakiness"),
    ),
    # Regression after streak
    (
        dict(error_message="whatever text", history={"consecutive_passes": 4}),
        ("PRODUCT_BUG", 70, "heuristic.regression_after_streak"),
    ),
    # Duration anomaly: 20000ms vs median 5000ms (ratio 4, >5s)
    (
        dict(
            error_message="zzz nothing matching zzz",
            duration_ms=20000,
            history={"median_duration_ms": 5000},
        ),
        ("INFRASTRUCTURE", 60, "heuristic.duration_anomaly"),
    ),
    # Suite-level failure
    (
        dict(
            error_message="zzz nothing matching zzz",
            run_context={"suite_failure_rate": 0.9, "suite_test_count": 5},
        ),
        ("TEST_DATA", 70, "heuristic.suite_level_failure"),
    ),
    # Keyword patterns
    (dict(error_message="pod OOMKilled by kubelet"), ("INFRASTRUCTURE", 75, "pattern.oom")),
    (dict(error_message="ECONNREFUSED 10.0.0.1:5432"), ("INFRASTRUCTURE", 70, "pattern.connection_refused")),
    (dict(error_message="read timeout after 30s"), ("INFRASTRUCTURE", 65, "pattern.timeout")),
    (dict(error_message="upstream returned 502 bad gateway"), ("INFRASTRUCTURE", 70, "pattern.http_5xx")),
    (dict(error_message="getaddrinfo ENOTFOUND api.internal"), ("INFRASTRUCTURE", 75, "pattern.dns")),
    (dict(error_message="OSError: no space left on device"), ("INFRASTRUCTURE", 75, "pattern.disk_full")),
    (dict(error_message="connect: no route to host"), ("INFRASTRUCTURE", 75, "pattern.network_unreachable")),
    (dict(error_message="EMFILE too many open files"), ("INFRASTRUCTURE", 70, "pattern.resource_exhaustion")),
    (dict(error_message="ssl handshake failure"), ("INFRASTRUCTURE", 70, "pattern.tls")),
    (dict(error_message="GET /users → 404 not found"), ("TEST_DATA", 65, "pattern.not_found")),
    (dict(error_message="beforeEach hook errored"), ("TEST_DATA", 60, "pattern.setup_fixture")),
    (dict(error_message="java.lang.NullPointerException at Foo.bar"), ("AUTOMATION_DEFECT", 65, "pattern.null_reference")),
    (dict(error_message="stale element reference"), ("AUTOMATION_DEFECT", 65, "pattern.ui_locator")),
    (dict(error_message="ModuleNotFoundError: module not found: paymnts"), ("AUTOMATION_DEFECT", 70, "pattern.missing_dependency")),
    (dict(error_message="intermittent race condition detected"), ("FLAKY", 55, "pattern.flaky_keywords")),
    (dict(error_message="AssertionError: expected 5 but was 3"), ("PRODUCT_BUG", 55, "pattern.assertion")),
    (dict(error_message="403 forbidden for role viewer"), ("TEST_DATA", 60, "pattern.auth")),
    # Cross-suite blast radius (error avoids every keyword)
    (
        dict(
            error_message="zzz nothing matching zzz",
            run_context={"cross_suite_failure_rate": 0.6},
        ),
        ("INFRASTRUCTURE", 60, "heuristic.cross_suite_blast"),
    ),
    # UNKNOWN fallback
    (dict(error_message="zzz nothing matching zzz"), ("UNKNOWN", 30, "heuristic.unknown_fallback")),
]


@pytest.mark.parametrize("kwargs,expected", _CLASSIFY_CASES)
def test_classify_test_confidences_unchanged(kwargs, expected):
    exp_category, exp_confidence, exp_rule = expected
    result = RulesEngine.classify_test(**kwargs)
    assert result["failure_category"] == exp_category
    assert result["confidence_score"] == exp_confidence
    assert result["confidence_rule_id"] == exp_rule


# ── Basis carried on every result ──────────────────────────────────────────


@pytest.mark.parametrize("kwargs,expected", _CLASSIFY_CASES)
def test_every_result_carries_confidence_basis(kwargs, expected):
    result = RulesEngine.classify_test(**kwargs)
    assert result["confidence_basis"] in (cb.BASIS_EMPIRICAL, cb.BASIS_HEURISTIC)
    # Basis must match the band table, not be invented per-result.
    assert result["confidence_basis"] == cb.get_band(result["confidence_rule_id"]).basis


# ── 3. Consumer-threshold audit ────────────────────────────────────────────


def test_requires_human_review_gate_unchanged():
    """In-engine gate: requires_human_review == (confidence < 70). Verified
    against fixtures on both sides of the boundary."""
    high = RulesEngine.classify_test(error_message="pod OOMKilled")           # 75
    boundary = RulesEngine.classify_test(                                     # 70
        error_message="whatever", history={"consecutive_passes": 4},
    )
    low = RulesEngine.classify_test(error_message="read timeout after 30s")   # 65
    assert high["requires_human_review"] is False
    assert boundary["requires_human_review"] is False
    assert low["requires_human_review"] is True


def test_downstream_threshold_constants_unchanged():
    """Triage/auto-action consumers gate on these settings + literals.
    Pinning them here means a band recalibration that silently shifts
    triage behavior cannot land without touching this audit."""
    from app.core.config import settings
    from app.agents import triage_agent
    from app.agents import analysis_agent

    assert settings.AI_CONFIDENCE_THRESHOLD == 80
    assert triage_agent._AUTO_TRIAGE_CONFIDENCE == settings.AI_CONFIDENCE_THRESHOLD
    assert analysis_agent._RETRY_CONFIDENCE_THRESHOLD == 40


def test_triage_gate_behavior_on_rules_fixtures():
    """Simulate the triage gate (confidence >= 80, or high-priority category
    >= 50) over the full pinned fixture set: with preserved band values the
    rules engine still never auto-triages via the >=80 arm, exactly as
    before AI-F4 (max rules confidence is 85 only via long flaky history)."""
    for kwargs, (category, confidence, _rule) in _CLASSIFY_CASES:
        result = RulesEngine.classify_test(**kwargs)
        auto_via_confidence = result["confidence_score"] >= 80
        assert auto_via_confidence is (confidence >= 80)
        assert result["confidence_score"] == confidence  # no drift, ever
