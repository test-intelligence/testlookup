"""Unit tests for ``metrics_service.classify_with_policy`` (2026-05-14 feature).

The classifier is the single source of truth for the per-project 4-band
pass-rate verdict used by both /overview (verdict colour) and /release-gate
(fail-closed downgrade). It's a pure function — no DB — so we test every
edge of the band table + every hard-cap path here without fixtures.

Cases covered:
  * Band edges (parametrised) — including ties at each boundary.
  * Each hard cap individually downgrades exactly one step.
  * Stacked caps cascade (yellow → orange → red).
  * Floor: stacked downgrades cannot go below red.
  * ``max_flaky_count=0`` and ``max_new_failures_24h=0`` disable those caps.
    ``max_p0_defects=0`` is the only cap where 0 means "zero allowed."
  * Custom bands reclassify the same input differently.
  * Defaults round-trip through ``PolicyDocument()``.
"""
from __future__ import annotations

import pytest

from app.models.schemas import PolicyDocument
from app.services.metrics_service import classify_with_policy


DEFAULT_BANDS = {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}
NO_CAPS = {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0}


# ── Band edges ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pass_rate, expected_band, expected_verdict",
    [
        # Green: pass_rate >= green_min (99.0)
        (100.0, "green",  "GO"),
        (99.5,  "green",  "GO"),
        (99.0,  "green",  "GO"),       # boundary inclusive
        # Yellow: yellow_min (95.0) <= pass_rate < green_min (99.0)
        (98.99, "yellow", "GO"),
        (95.0,  "yellow", "GO"),       # boundary inclusive
        # Orange: orange_min (90.0) <= pass_rate < yellow_min (95.0)
        (94.99, "orange", "CONDITIONAL"),
        (90.0,  "orange", "CONDITIONAL"),  # boundary inclusive
        # Red: pass_rate < orange_min (90.0)
        (89.99, "red",    "NO_GO"),
        (50.0,  "red",    "NO_GO"),
        (0.0,   "red",    "NO_GO"),
    ],
)
def test_band_edges(pass_rate, expected_band, expected_verdict):
    """Each boundary is inclusive on its lower edge — pin every transition."""
    result = classify_with_policy(
        pass_rate=pass_rate,
        active_defects_p0=0,
        flaky_count=0,
        new_failures_24h=0,
        bands=DEFAULT_BANDS,
        hard_caps=NO_CAPS,
    )
    assert result["band"] == expected_band, f"pr={pass_rate} expected {expected_band}, got {result['band']}"
    assert result["verdict"] == expected_verdict
    assert result["downgrades"] == []


# ── Individual hard caps ──────────────────────────────────────────────────


def test_p0_defect_cap_downgrades_one_step():
    """A single P0 defect over the cap downgrades green → yellow."""
    result = classify_with_policy(
        pass_rate=99.5,
        active_defects_p0=1,
        flaky_count=0,
        new_failures_24h=0,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    )
    assert result["band"] == "yellow"
    assert result["verdict"] == "GO"
    assert any("p0_defects" in d for d in result["downgrades"])


def test_flaky_cap_downgrades_one_step():
    """flaky_count > max_flaky_count downgrades the band."""
    result = classify_with_policy(
        pass_rate=99.5,
        active_defects_p0=0,
        flaky_count=11,
        new_failures_24h=0,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 5, "max_flaky_count": 10, "max_new_failures_24h": 0},
    )
    assert result["band"] == "yellow"
    assert any("flaky" in d for d in result["downgrades"])


def test_new_failures_cap_downgrades_one_step():
    """new_failures_24h > max_new_failures_24h downgrades the band."""
    result = classify_with_policy(
        pass_rate=99.5,
        active_defects_p0=0,
        flaky_count=0,
        new_failures_24h=21,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 5, "max_flaky_count": 0, "max_new_failures_24h": 20},
    )
    assert result["band"] == "yellow"
    assert any("new_failures_24h" in d for d in result["downgrades"])


# ── Stacked caps + floor ──────────────────────────────────────────────────


def test_stacked_downgrades_cascade():
    """The smoke test from the feature ship: pr=97 + all three caps fire
    cascades yellow → orange → red."""
    result = classify_with_policy(
        pass_rate=97.0,
        active_defects_p0=5,
        flaky_count=50,
        new_failures_24h=100,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 0, "max_flaky_count": 10, "max_new_failures_24h": 20},
    )
    # yellow (97 in [95, 99)) → orange (P0 cap) → red (flaky cap) → red (floor)
    assert result["band"] == "red"
    assert result["verdict"] == "NO_GO"
    assert len(result["downgrades"]) == 3


def test_downgrade_floor_at_red():
    """Stacked downgrades never wrap or underflow — floor stays at red."""
    result = classify_with_policy(
        pass_rate=50.0,                          # already red
        active_defects_p0=99,
        flaky_count=99,
        new_failures_24h=99,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 0, "max_flaky_count": 10, "max_new_failures_24h": 20},
    )
    assert result["band"] == "red"
    assert result["verdict"] == "NO_GO"


# ── Disabled caps ─────────────────────────────────────────────────────────


def test_flaky_cap_disabled_when_zero():
    """``max_flaky_count=0`` means "disable the cap" — any flaky count is fine.

    Pins the asymmetric semantic noted in the feature memory: P0 and flaky
    treat 0 differently because P0=0 is the only intent the user actually
    wants. ``flaky=0`` would over-fire on real projects."""
    result = classify_with_policy(
        pass_rate=99.5,
        active_defects_p0=0,
        flaky_count=9999,                        # would fire if 0 weren't "disabled"
        new_failures_24h=0,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    )
    assert result["band"] == "green"
    assert result["downgrades"] == []


def test_new_failures_cap_disabled_when_zero():
    """Same disable semantic for the new-failures cap."""
    result = classify_with_policy(
        pass_rate=99.5,
        active_defects_p0=0,
        flaky_count=0,
        new_failures_24h=10_000,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    )
    assert result["band"] == "green"


def test_p0_cap_zero_means_zero_allowed():
    """Contrast with the flaky/new_failures caps: P0=0 fires on ANY P0 defect.

    Most QA teams want "no P0 defects allowed when shipping" as the default,
    so 0 here means strictness, not disable."""
    result = classify_with_policy(
        pass_rate=99.5,
        active_defects_p0=1,
        flaky_count=0,
        new_failures_24h=0,
        bands=DEFAULT_BANDS,
        hard_caps={"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    )
    assert result["band"] == "yellow"
    assert result["downgrades"] != []


# ── Custom bands ──────────────────────────────────────────────────────────


def test_custom_bands_reclassify_input():
    """Lowering the green floor reclassifies a 92% run from orange → yellow."""
    custom = {"orange_min": 85.0, "yellow_min": 90.0, "green_min": 95.0}
    result = classify_with_policy(
        pass_rate=92.0,
        active_defects_p0=0,
        flaky_count=0,
        new_failures_24h=0,
        bands=custom,
        hard_caps=NO_CAPS,
    )
    assert result["band"] == "yellow"  # 92 in [90, 95) under custom bands


# ── Defaults round-trip ───────────────────────────────────────────────────


def test_policy_document_defaults_match_classifier_expectations():
    """``PolicyDocument().pass_rate_bands.model_dump()`` must produce the
    exact keys the classifier reads (orange_min/yellow_min/green_min) and
    the defaults must match the values DEFAULT_BANDS above uses for tests.
    A drift here would make this test file silently test stale behaviour."""
    doc = PolicyDocument()
    bands = doc.pass_rate_bands.model_dump()
    caps = doc.hard_caps.model_dump()
    assert bands == {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}
    # Default caps allow up to 0 P0 (strict), 10 flaky, 20 new failures.
    assert caps["max_p0_defects"] == 0
    assert caps["max_flaky_count"] == 10
    assert caps["max_new_failures_24h"] == 20

    # And the classifier still produces the expected verdict using those
    # defaults — guards against schema-only changes breaking the integration.
    result = classify_with_policy(
        pass_rate=99.5, active_defects_p0=0, flaky_count=0, new_failures_24h=0,
        bands=bands, hard_caps=caps,
    )
    assert result["band"] == "green"
    assert result["verdict"] == "GO"
