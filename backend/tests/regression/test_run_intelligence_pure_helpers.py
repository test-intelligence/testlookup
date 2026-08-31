"""The Run Intelligence numbers and prose a human reads — none of it executed.

``services/run_intelligence_service.py`` is 33.6% covered with **261 missed
statements**. Per function, the same shape as the rest of this initiative: the
orchestrators (`get_run_mode_summary`, `_generate_and_cache_mode_variant`,
`_build_and_persist_defect_candidates`) show one executed line each. Their
bodies need a DB session, a Mongo client and an LLM to reach, and tests built
that way assert the mock.

The pure functions need none of that, and they are the ones whose output a
person actually reads:

* ``_criticality_from_cluster_size`` labels a failure cluster LOW/MEDIUM/HIGH/
  CRITICAL. Nothing but three thresholds, and an off-by-one mislabels severity
  on a release call.
* ``_build_dimension_scores`` turns raw scores into the weighted risk
  breakdown shown on the run page.
* ``_variant_to_markdown`` and the ``_render_*_markdown`` family turn a stored
  variant into the document a developer, manager or executive is handed.

A defect in any of them is silent — no exception, just a wrong number or a
missing section in something presented as a summary of the run.
"""
from __future__ import annotations

import pytest

from app.services.run_intelligence_service import (
    DIMENSION_METADATA,
    _build_dimension_scores,
    _criticality_from_cluster_size,
    _variant_to_markdown,
)

pytestmark = pytest.mark.regression


class TestCriticalityThresholds:
    """Three thresholds with `>=` semantics. Every boundary is checked on both
    sides, because "20 or more is critical" and "more than 20 is critical" are
    a one-character difference that no type checker sees."""

    @pytest.mark.parametrize(
        "size,expected",
        [
            (0, "LOW"), (1, "LOW"), (3, "LOW"),
            (4, "MEDIUM"), (5, "MEDIUM"), (9, "MEDIUM"),
            (10, "HIGH"), (11, "HIGH"), (19, "HIGH"),
            (20, "CRITICAL"), (21, "CRITICAL"), (5000, "CRITICAL"),
        ],
    )
    def test_each_band_and_both_sides_of_each_boundary(self, size, expected):
        assert _criticality_from_cluster_size(size).value == expected

    def test_the_bands_never_go_backwards(self):
        """Monotonic by construction — a bigger cluster can never be assigned a
        lower criticality. Guards against a reordered if-chain, where the first
        matching branch wins and an early `>= 4` would swallow everything."""
        order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        seen = [order.index(_criticality_from_cluster_size(n).value) for n in range(0, 40)]
        assert seen == sorted(seen)


class TestDimensionScores:
    def test_no_scores_yields_no_breakdown(self):
        """Distinct from "all dimensions are zero": an absent score dict means
        the run was never scored, and the UI must not render a full row of
        zeroes as if it had been."""
        assert _build_dimension_scores(None) == []
        assert _build_dimension_scores({}) == []

    def test_every_dimension_is_present_even_when_the_payload_is_partial(self):
        """A payload naming one dimension still produces the whole breakdown —
        a missing dimension reads 0, not absent, so the rows line up."""
        result = _build_dimension_scores({"user_impact": 80})

        assert len(result) == len(DIMENSION_METADATA)
        assert {d.name for d in result} == set(DIMENSION_METADATA)
        by_name = {d.name: d for d in result}
        assert by_name["user_impact"].score == 80
        assert all(
            by_name[k].score == 0 for k in DIMENSION_METADATA if k != "user_impact"
        )

    def test_the_weights_sum_to_one(self):
        """The contract that makes the weighted total a percentage. If the
        weights drift, every composite score silently changes scale and no
        single dimension looks wrong."""
        assert sum(w for _label, w in DIMENSION_METADATA.values()) == pytest.approx(1.0)

    def test_a_full_house_contributes_the_input_scale(self):
        """Follows from the weights summing to 1: all dimensions at 100 must
        total 100, not 700."""
        result = _build_dimension_scores({k: 100 for k in DIMENSION_METADATA})
        assert sum(d.contribution for d in result) == pytest.approx(100.0)

    def test_contribution_is_the_weighted_score_not_the_raw_one(self):
        result = _build_dimension_scores({"user_impact": 80})
        d = next(x for x in result if x.name == "user_impact")
        _label, weight = DIMENSION_METADATA["user_impact"]
        assert d.contribution == pytest.approx(round(80 * weight, 2))

    def test_values_are_rounded_for_display(self):
        """Score to 1dp, contribution to 2dp — these are rendered directly."""
        result = _build_dimension_scores({"user_impact": 66.666})
        d = next(x for x in result if x.name == "user_impact")
        assert d.score == 66.7
        assert d.contribution == round(66.666 * DIMENSION_METADATA["user_impact"][1], 2)

    def test_the_label_travels_with_the_score(self):
        """The UI renders `label`, not `name`; losing it shows raw keys."""
        result = _build_dimension_scores({"user_impact": 10})
        d = next(x for x in result if x.name == "user_impact")
        assert d.label == DIMENSION_METADATA["user_impact"][0]


class TestVariantMarkdown:
    """The document handed to a person. Sections are emitted only when their
    field is populated, so a partial variant must not leave empty headings."""

    def test_a_developer_variant_renders_its_sections_in_order(self):
        md = _variant_to_markdown(
            {
                "headline": "Checkout suite regressed",
                "root_cause_analysis": "Null session token",
                "fix_recommendations": ["Guard the token", "Add a retry"],
                "validation_steps": ["Re-run checkout"],
                "similar_historical_context": "Seen in build 41",
            },
            "developer",
        )

        assert md.index("## Summary") < md.index("## Root Cause")
        assert md.index("## Root Cause") < md.index("## Fix Recommendations")
        assert md.index("## Fix Recommendations") < md.index("## Validation Steps")
        assert "- Guard the token\n- Add a retry" in md
        assert "## Historical Context" in md

    def test_a_manager_variant_renders_its_own_sections(self):
        md = _variant_to_markdown(
            {
                "executive_summary": "One suite is failing",
                "release_recommendation": "CONDITIONAL_GO",
                "key_risks": ["Checkout"],
                "recommended_decisions": ["Ship behind a flag"],
            },
            "manager",
        )

        assert "## Executive Summary" in md
        assert "## Release Recommendation\nCONDITIONAL_GO" in md
        assert "- Checkout" in md
        assert "- Ship behind a flag" in md

    def test_developer_fields_do_not_leak_into_the_manager_document(self):
        """The modes exist to show different people different things. A
        developer's root-cause detail appearing in the manager document defeats
        the split."""
        md = _variant_to_markdown(
            {"executive_summary": "ok", "root_cause_analysis": "Null token"},
            "manager",
        )
        assert "Null token" not in md
        assert "## Root Cause" not in md

    def test_an_absent_field_leaves_no_empty_heading(self):
        """A heading with nothing under it reads as missing data rather than an
        omitted section."""
        md = _variant_to_markdown({"headline": "Only this"}, "developer")

        assert "## Summary" in md
        for absent in ("## Root Cause", "## Fix Recommendations",
                       "## Validation Steps", "## Historical Context"):
            assert absent not in md

    def test_an_empty_variant_renders_nothing_at_all(self):
        assert _variant_to_markdown({}, "developer") == ""
        assert _variant_to_markdown({}, "manager") == ""

    def test_an_unrecognised_mode_renders_empty_rather_than_raising(self):
        """DOCUMENTED BEHAVIOUR. Neither branch matches, so a typo'd or new
        mode yields a blank document instead of an error. That is the safe
        failure for a renderer, but it means a caller adding a mode gets an
        empty summary with nothing to explain it — worth knowing before
        blaming the model."""
        assert _variant_to_markdown({"headline": "present"}, "executive") == ""
        assert _variant_to_markdown({"headline": "present"}, "") == ""

    def test_sections_are_separated_by_a_blank_line(self):
        """Markdown needs the gap; without it the next heading is swallowed
        into the previous paragraph."""
        md = _variant_to_markdown(
            {"headline": "A", "root_cause_analysis": "B"}, "developer"
        )
        assert md == "## Summary\nA\n\n## Root Cause\nB"
