"""Confidence bands for the rules engine — named, documented, auditable (AI-F4).

Every confidence number the rules engine emits comes from this table instead
of an inline magic number. Each band carries a ``basis`` that is surfaced all
the way to the API/UI (``confidence_basis`` on analysis results):

  - ``empirical``           — the confidence equals measured precision of the
                              rule on a labeled evaluation corpus; provenance
                              must name the corpus and sample count.
  - ``heuristic_estimate``  — no labeled corpus exercises this rule; the value
                              is an engineering estimate.

Provenance audit (2026-07-10, AI-F4):
    The golden datasets in ``app/services/golden_datasets.py`` contain
    pre-labeled *analysis summaries* (``root_cause_summary`` text with an
    already-assigned category) used to score classification *agreement*.
    They do not contain raw test error messages, so they cannot measure the
    per-rule precision of ``RulesEngine.classify_test``. No other eval
    fixture in the repo feeds raw error messages through the rules engine
    with ground-truth labels. Consequently every band below is honestly
    labeled ``heuristic_estimate`` and its numeric value is byte-for-byte
    the pre-AI-F4 hardcoded constant (behavior preservation first — see
    ``tests/services/test_confidence_bands.py`` for the pinned values).
    When a labeled raw-error corpus lands, recompute precision per rule,
    flip the basis to ``empirical``, and record corpus + N here.
"""
from dataclasses import dataclass

BASIS_EMPIRICAL = "empirical"
BASIS_HEURISTIC = "heuristic_estimate"

_VALID_BASES = frozenset({BASIS_EMPIRICAL, BASIS_HEURISTIC})


@dataclass(frozen=True)
class ConfidenceBand:
    """A named confidence level with a documented basis."""

    rule_id: str
    confidence: int          # 0-100; for dynamic rules this is the cap
    basis: str               # "empirical" | "heuristic_estimate"
    provenance: str          # where the number comes from

    def __post_init__(self) -> None:
        if self.basis not in _VALID_BASES:
            raise ValueError(f"invalid basis {self.basis!r} for {self.rule_id}")
        if not (0 <= self.confidence <= 100):
            raise ValueError(f"confidence out of range for {self.rule_id}")


_LEGACY = "pre-AI-F4 hardcoded constant, preserved unchanged; no labeled raw-error corpus exists to calibrate against"


# ── Statistical heuristics (rules_engine.classify_test steps 1-4, 6, 7) ─────

# Historical flakiness (step 1) is the one *dynamic* band: confidence grows
# with the amount of history backing the verdict —
#   min(HISTORICAL_FLAKINESS_CAP, BASE + PER_RUN * total_runs)
# preserved exactly from the original ``min(85, 50 + hist_total * 2)``.
HISTORICAL_FLAKINESS_BASE = 50
HISTORICAL_FLAKINESS_PER_RUN = 2
HISTORICAL_FLAKINESS_CAP = 85

HEURISTIC_BANDS: dict[str, ConfidenceBand] = {
    band.rule_id: band
    for band in (
        ConfidenceBand(
            "heuristic.historical_flakiness", HISTORICAL_FLAKINESS_CAP, BASIS_HEURISTIC,
            "dynamic: min(85, 50 + 2*history_size); " + _LEGACY,
        ),
        ConfidenceBand("heuristic.regression_after_streak", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("heuristic.duration_anomaly", 60, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("heuristic.suite_level_failure", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("heuristic.cross_suite_blast", 60, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("heuristic.unknown_fallback", 30, BASIS_HEURISTIC, _LEGACY),
    )
}


def historical_flakiness_confidence(history_size: int) -> int:
    """Dynamic confidence for the historical-flakiness heuristic.

    Preserved formula: min(85, 50 + 2 * history_size).
    """
    return min(
        HISTORICAL_FLAKINESS_CAP,
        HISTORICAL_FLAKINESS_BASE + int(history_size) * HISTORICAL_FLAKINESS_PER_RUN,
    )


# ── Keyword patterns (rules_engine step 5) ───────────────────────────────────
# One band per pattern; the rules engine references these by rule_id so the
# keyword list and the confidence live in exactly one place each.

PATTERN_BANDS: dict[str, ConfidenceBand] = {
    band.rule_id: band
    for band in (
        ConfidenceBand("pattern.oom", 75, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.connection_refused", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.timeout", 65, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.http_5xx", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.dns", 75, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.disk_full", 75, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.network_unreachable", 75, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.resource_exhaustion", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.tls", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.not_found", 65, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.setup_fixture", 60, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.null_reference", 65, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.ui_locator", 65, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.missing_dependency", 70, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.flaky_keywords", 55, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.assertion", 55, BASIS_HEURISTIC, _LEGACY),
        ConfidenceBand("pattern.auth", 60, BASIS_HEURISTIC, _LEGACY),
    )
}


ALL_BANDS: dict[str, ConfidenceBand] = {**HEURISTIC_BANDS, **PATTERN_BANDS}


def get_band(rule_id: str) -> ConfidenceBand:
    """Look up a band; raises KeyError for unknown rule ids (fail loud —
    a rules-engine result without a band is a table-completeness bug)."""
    return ALL_BANDS[rule_id]
