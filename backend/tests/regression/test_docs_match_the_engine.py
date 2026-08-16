"""The in-app documentation must state the constants the engine actually uses.

The docs page (B-2) tells users that flakiness weights are 0.45 / 0.25 / 0.20 /
0.10, that five observations are needed before a score exists, and that the
release gate hard-floors at 70% of the configured bar. Those are not
illustrations — they are the real values, read out of the implementation.

A doc page that keeps quoting a threshold the engine no longer uses is the same
defect class this codebase keeps producing: something published to a reader
that nothing in the system produces. `_fallback_used`, `events_received` and
`anthropic_key_set` were all that shape. Prose is harder to notice, not less
wrong — nobody gets a stack trace from a stale sentence.

So the constants are asserted from BOTH sides. The frontend test
(`frontend/src/pages/DocsPage.test.tsx`) pins that the page renders them; this
one pins that they still match Python. Change a weight in the engine and this
fails until the documentation is updated too.
"""
from __future__ import annotations

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
DOCS_PAGE = REPO / "frontend" / "src" / "pages" / "DocsPage.tsx"

pytestmark = pytest.mark.skipif(
    not DOCS_PAGE.exists(), reason="frontend not present in this checkout"
)


def _docs() -> str:
    return DOCS_PAGE.read_text(encoding="utf-8")


def test_the_docs_page_is_readable_and_non_trivial():
    """Every assertion below is a substring search. If the page moved or shrank
    to a stub they would all pass vacuously."""
    src = _docs()
    assert len(src) > 4_000, f"docs page looks like a stub ({len(src)} bytes)"
    for marker in ("flaky", "GO / NO-GO", "AI reports"):
        assert marker in src, f"section marker {marker!r} missing"


# ── Flakiness ───────────────────────────────────────────────────────────────


def test_documented_flakiness_weights_match_the_scorer():
    from app.services.flaky_score_service import DEFAULT_WEIGHTS

    src = _docs()
    missing = [
        f"{name}={weight}"
        for name, weight in DEFAULT_WEIGHTS.items()
        if f"{weight:.2f}" not in src
    ]
    assert not missing, (
        "documentation does not state the weights the scorer uses: "
        f"{missing} (engine has {DEFAULT_WEIGHTS})"
    )


def test_documented_weights_still_sum_to_one():
    """The docs present them as a weighted blend. If they stopped summing to 1
    the page's framing would be wrong even with every number copied across."""
    from app.services.flaky_score_service import DEFAULT_WEIGHTS

    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_documented_observation_floor_matches_the_scorer():
    from app.services.flaky_score_service import MIN_OBSERVATIONS

    src = _docs()
    assert f"Fewer than {MIN_OBSERVATIONS}" in src, (
        f"docs do not state the real observation floor ({MIN_OBSERVATIONS})"
    )


def test_documented_confidence_bands_match_the_scorer():
    from app.services.flaky_score_service import (
        LOW_CONFIDENCE_MAX,
        MEDIUM_CONFIDENCE_MAX,
        MIN_OBSERVATIONS,
    )

    src = _docs()
    assert f"{MIN_OBSERVATIONS} – {LOW_CONFIDENCE_MAX}" in src
    assert f"{LOW_CONFIDENCE_MAX + 1} – {MEDIUM_CONFIDENCE_MAX}" in src
    assert f"{MEDIUM_CONFIDENCE_MAX + 1} or more" in src


def test_the_docs_do_not_promise_a_score_below_the_floor():
    """The engine returns score=None below MIN_OBSERVATIONS. Documenting that as
    0.0 would invent stability nobody measured."""
    src = _docs()
    assert "insufficient" in src.lower()


# ── Release gate ────────────────────────────────────────────────────────────


def test_documented_hard_floor_matches_the_engine():
    from app.services.criticality_service import HARD_FLOOR_FACTOR

    src = _docs()
    pct = int(round(HARD_FLOOR_FACTOR * 100))
    assert f"{pct}% of your configured bar" in src, (
        f"docs do not state the real hard-floor factor ({HARD_FLOOR_FACTOR})"
    )


def test_every_risk_dimension_is_documented():
    """Seven dimensions drive the composite. One missing from the page means a
    user cannot account for part of their own score."""
    from app.services.criticality_service import _weights

    # Engine key -> the phrase the page uses for it.
    labels = {
        "user_impact": "User impact",
        "env_sensitivity": "Environment sensitivity",
        "reproducibility": "Reproducibility",
        "regression_likely": "Regression likelihood",
        "hist_recurrence": "Historical recurrence",
        "blast_radius": "Blast radius",
        "diagnosis_conf": "Diagnosis confidence",
    }
    src = _docs()
    dimensions = set(_weights())
    unmapped = sorted(dimensions - set(labels))
    assert not unmapped, f"new risk dimension with no documented label: {unmapped}"

    missing = sorted(k for k in dimensions if labels[k] not in src)
    assert not missing, f"risk dimensions absent from the documentation: {missing}"


def test_the_documented_verdicts_are_the_ones_the_engine_can_return():
    """All three states must be described — CONDITIONAL_GO in particular was
    once unreachable, and a page that omitted it would have looked correct."""
    src = _docs().upper().replace("-", "_").replace(" ", "_")
    for verdict in ("GO", "CONDITIONAL_GO", "NO_GO"):
        assert verdict in src, f"documentation never mentions {verdict}"


def test_the_docs_state_that_bands_only_tighten():
    """release_council_service layers bands fail-closed: they can downgrade a
    verdict but never upgrade one. A reader who assumes otherwise will
    mis-predict their own gate."""
    src = _docs().lower()
    assert "never unblock" in src or "can never unblock" in src
