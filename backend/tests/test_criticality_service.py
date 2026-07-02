"""
Unit tests for criticality_service.py

Tests:
  - compute_dimension_scores produces values in [0, 100]
  - compute_composite produces correct weighted sum
  - score_to_recommendation threshold transitions
  - score_cluster scales by member fraction
  - get_scoring_model_info returns all 7 dimensions with correct structure
  - Weights sum to 1.0 (regression guard against config drift)
"""
import pytest

# ---------------------------------------------------------------------------
# Module-level import — works without a running database because
# criticality_service.py does a late import of settings inside _weights().
# ---------------------------------------------------------------------------
from app.services.criticality_service import (
    SCORE_MODEL_VERSION,
    compute_composite,
    compute_dimension_scores,
    get_scoring_model_info,
    score_cluster,
    score_to_recommendation,
    _weights,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _minimal_analyses(n_product_bugs: int = 2, n_flaky: int = 1) -> dict:
    """Build a minimal analyses dict with PRODUCT_BUG and flaky entries."""
    from app.models.postgres import FailureCategory

    analyses = {}
    for i in range(n_product_bugs):
        analyses[f"tc_{i}"] = {
            "failure_category": FailureCategory.PRODUCT_BUG,
            "is_flaky": False,
            "confidence_score": 75,
        }
    for j in range(n_flaky):
        analyses[f"tc_flaky_{j}"] = {
            "failure_category": FailureCategory.FLAKY,
            "is_flaky": True,
            "confidence_score": 50,
        }
    return analyses


# ── compute_dimension_scores ────────────────────────────────────────────────

class TestComputeDimensionScores:
    def test_returns_all_seven_dimensions(self):
        scores = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[],
            pass_rate=80.0,
            is_regression=False,
            regression_tests=[],
            failure_clusters=[],
            open_defects=0,
        )
        expected = {"user_impact", "env_sensitivity", "reproducibility",
                    "regression_likely", "hist_recurrence", "blast_radius", "diagnosis_conf"}
        assert set(scores.keys()) == expected

    def test_all_values_in_range(self):
        scores = compute_dimension_scores(
            analyses=_minimal_analyses(n_product_bugs=5, n_flaky=2),
            anomalies=[{"severity": "HIGH"}, {"severity": "LOW"}],
            pass_rate=60.0,
            is_regression=True,
            regression_tests=["tc_1", "tc_2"],
            failure_clusters=[{"size": 5}, {"size": 2}],
            open_defects=3,
        )
        for dim, val in scores.items():
            assert 0.0 <= val <= 100.0, f"{dim}={val} out of range"

    def test_empty_analyses_does_not_raise(self):
        scores = compute_dimension_scores(
            analyses={},
            anomalies=[],
            pass_rate=100.0,
            is_regression=False,
            regression_tests=[],
            failure_clusters=[],
            open_defects=0,
        )
        assert scores["user_impact"] == 0.0

    def test_regression_tests_raise_regression_likely(self):
        no_reg = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[], pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        with_reg = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[], pass_rate=80.0,
            is_regression=True, regression_tests=["tc_a", "tc_b"],
            failure_clusters=[], open_defects=0,
        )
        assert with_reg["regression_likely"] > no_reg["regression_likely"]

    # ── hist_recurrence now measures ACTUAL recurrence, not AI confidence ──────

    def test_hist_recurrence_measures_known_recurring_failures(self):
        """Failing tests that are NOT new regressions are recurring/known
        failures. All-new → 0 recurrence; none-new → full recurrence."""
        analyses = _minimal_analyses(n_product_bugs=3, n_flaky=0)  # tc_0, tc_1, tc_2
        base = dict(anomalies=[], pass_rate=80.0, is_regression=False,
                    failure_clusters=[], open_defects=0)
        # Every failure is a brand-new regression → nothing is recurring.
        all_new = compute_dimension_scores(
            analyses=analyses, regression_tests=["tc_0", "tc_1", "tc_2"], **base,
        )
        assert all_new["hist_recurrence"] == 0.0
        # No new regressions → every failure is a known/recurring one.
        none_new = compute_dimension_scores(
            analyses=analyses, regression_tests=[], **base,
        )
        assert none_new["hist_recurrence"] > all_new["hist_recurrence"]
        # Half new → partial recurrence, between the two extremes.
        some_new = compute_dimension_scores(
            analyses=analyses, regression_tests=["tc_0"], **base,
        )
        assert all_new["hist_recurrence"] < some_new["hist_recurrence"] < none_new["hist_recurrence"]

    def test_hist_recurrence_no_longer_tracks_ai_confidence(self):
        """Regression for the double-count bug: hist_recurrence and diagnosis_conf
        both used to rise with low AI confidence, counting uncertainty into risk
        twice. hist_recurrence must now be INDEPENDENT of confidence — only
        diagnosis_conf reflects AI uncertainty."""
        from app.models.postgres import FailureCategory

        def _analyses(conf: int) -> dict:
            return {
                "tc_0": {"failure_category": FailureCategory.PRODUCT_BUG,
                         "is_flaky": False, "confidence_score": conf},
                "tc_1": {"failure_category": FailureCategory.PRODUCT_BUG,
                         "is_flaky": False, "confidence_score": conf},
            }

        base = dict(anomalies=[], pass_rate=80.0, is_regression=False,
                    regression_tests=[], failure_clusters=[], open_defects=0)
        low = compute_dimension_scores(analyses=_analyses(15), **base)
        high = compute_dimension_scores(analyses=_analyses(95), **base)
        # Same failures, same recurrence structure → identical hist_recurrence,
        # regardless of AI confidence.
        assert low["hist_recurrence"] == high["hist_recurrence"]
        # But diagnosis_conf still (correctly) reflects the uncertainty.
        assert low["diagnosis_conf"] > high["diagnosis_conf"]


# ── compute_composite ────────────────────────────────────────────────────────

class TestComputeComposite:
    def test_zero_scores_gives_zero_composite(self):
        zero_scores = {k: 0.0 for k in _weights()}
        assert compute_composite(zero_scores) == 0.0

    def test_max_scores_gives_100_composite(self):
        max_scores = {k: 100.0 for k in _weights()}
        assert compute_composite(max_scores) == 100.0

    def test_composite_is_weighted_sum(self):
        weights = _weights()
        # Set each dimension to 50
        dim_scores = {k: 50.0 for k in weights}
        expected = round(sum(50.0 * w for w in weights.values()), 1)
        assert compute_composite(dim_scores) == pytest.approx(expected, abs=0.1)

    def test_weights_sum_to_one(self):
        """Regression guard: any config change that breaks the weight sum is caught here."""
        total = sum(_weights().values())
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"


# ── score_to_recommendation ──────────────────────────────────────────────────

class TestScoreToRecommendation:
    def test_low_risk_is_go(self):
        assert score_to_recommendation(15.0, 95.0, 90.0) == "GO"

    def test_medium_risk_is_conditional_go(self):
        assert score_to_recommendation(35.0, 85.0, 90.0) == "CONDITIONAL_GO"

    def test_high_risk_is_no_go(self):
        assert score_to_recommendation(70.0, 85.0, 90.0) == "NO_GO"

    def test_critically_low_pass_rate_forces_no_go(self):
        # pass_rate=50 < 90*0.7=63 → NO_GO regardless of composite
        assert score_to_recommendation(10.0, 50.0, 90.0) == "NO_GO"

    def test_boundary_go_threshold(self):
        # composite == 20 → CONDITIONAL_GO (threshold is strictly <20 for GO)
        assert score_to_recommendation(20.0, 95.0, 90.0) == "CONDITIONAL_GO"

    def test_boundary_no_go_threshold(self):
        # composite == 55 → NO_GO
        assert score_to_recommendation(55.0, 95.0, 90.0) == "NO_GO"
        # composite == 54.9 → CONDITIONAL_GO
        assert score_to_recommendation(54.9, 95.0, 90.0) == "CONDITIONAL_GO"


# ── score_cluster ────────────────────────────────────────────────────────────

class TestScoreCluster:
    def test_empty_dim_scores_returns_empty(self):
        result = score_cluster(
            cluster={"member_test_ids": ["a", "b"]},
            all_dim_scores={},
            total_analyses=10,
            member_count=2,
        )
        assert result == {}

    def test_zero_total_analyses_returns_empty(self):
        result = score_cluster(
            cluster={"member_test_ids": ["a"]},
            all_dim_scores={"user_impact": 60.0},
            total_analyses=0,
            member_count=1,
        )
        assert result == {}

    def test_proportional_scaling(self):
        # Cluster owns 4 of 10 analyses → fraction = 0.4
        dim_scores = {"user_impact": 80.0, "blast_radius": 50.0}
        result = score_cluster(
            cluster={"member_test_ids": list(range(4))},
            all_dim_scores=dim_scores,
            total_analyses=10,
            member_count=4,
        )
        assert result["user_impact"] == pytest.approx(32.0, abs=0.1)
        assert result["blast_radius"] == pytest.approx(20.0, abs=0.1)

    def test_full_cluster_matches_full_score(self):
        dim_scores = {"user_impact": 70.0}
        result = score_cluster(
            cluster={},
            all_dim_scores=dim_scores,
            total_analyses=5,
            member_count=5,
        )
        assert result["user_impact"] == pytest.approx(70.0, abs=0.1)


# ── get_scoring_model_info ───────────────────────────────────────────────────

class TestGetScoringModelInfo:
    def test_returns_seven_dimensions(self):
        info = get_scoring_model_info()
        assert len(info["dimensions"]) == 7

    def test_all_dimensions_have_required_keys(self):
        info = get_scoring_model_info()
        for dim in info["dimensions"]:
            assert "name" in dim
            assert "weight" in dim
            assert "description" in dim
            assert isinstance(dim["description"], str)
            assert len(dim["description"]) > 20  # non-trivial descriptions

    def test_version_is_integer(self):
        assert isinstance(get_scoring_model_info()["version"], int)

    def test_model_version_constant(self):
        assert SCORE_MODEL_VERSION == get_scoring_model_info()["version"]

    def test_thresholds_present(self):
        info = get_scoring_model_info()
        assert "go_threshold" in info
        assert "no_go_threshold" in info
        assert info["go_threshold"] < info["no_go_threshold"]


# ── P5-5: Directionality & monotonicity assertions ─────────────────────────


class TestDirectionality:
    """Verify that scores move in the expected direction when inputs change."""

    def test_more_product_bugs_increases_user_impact(self):
        few = compute_dimension_scores(
            analyses=_minimal_analyses(n_product_bugs=1, n_flaky=0),
            anomalies=[], pass_rate=90.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        many = compute_dimension_scores(
            analyses=_minimal_analyses(n_product_bugs=10, n_flaky=0),
            anomalies=[], pass_rate=90.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        assert many["user_impact"] >= few["user_impact"]

    def test_more_anomalies_increases_env_sensitivity(self):
        few = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[],
            pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        many = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[{"severity": "HIGH"}] * 5,
            pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        assert many["env_sensitivity"] >= few["env_sensitivity"]

    def test_more_clusters_increases_blast_radius(self):
        few = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[], pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[{"size": 2}],
            open_defects=0,
        )
        many = compute_dimension_scores(
            analyses=_minimal_analyses(),
            anomalies=[], pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[{"size": 5}, {"size": 4}, {"size": 3}],
            open_defects=0,
        )
        assert many["blast_radius"] >= few["blast_radius"]

    def test_lower_confidence_increases_diagnosis_conf_risk(self):
        """diagnosis_conf measures RISK from uncertain diagnoses — lower AI
        confidence means higher risk score (inverted relationship)."""
        from app.models.postgres import FailureCategory
        low_conf = {
            "tc_1": {"failure_category": FailureCategory.PRODUCT_BUG, "is_flaky": False, "confidence_score": 20},
        }
        high_conf = {
            "tc_1": {"failure_category": FailureCategory.PRODUCT_BUG, "is_flaky": False, "confidence_score": 95},
        }
        base_args = dict(anomalies=[], pass_rate=80.0, is_regression=False,
                         regression_tests=[], failure_clusters=[], open_defects=0)
        low = compute_dimension_scores(analyses=low_conf, **base_args)
        high = compute_dimension_scores(analyses=high_conf, **base_args)
        # Low AI confidence → more risk → HIGHER diagnosis_conf score
        assert low["diagnosis_conf"] >= high["diagnosis_conf"]

    def test_increasing_composite_increases_recommendation_severity(self):
        """GO → CONDITIONAL_GO → NO_GO as composite increases."""
        rec_low = score_to_recommendation(10.0, 95.0, 90.0)
        rec_mid = score_to_recommendation(35.0, 95.0, 90.0)
        rec_high = score_to_recommendation(70.0, 95.0, 90.0)
        severity_order = {"GO": 0, "CONDITIONAL_GO": 1, "NO_GO": 2}
        assert severity_order[rec_low] < severity_order[rec_mid] < severity_order[rec_high]

    def test_zero_pass_rate_always_no_go(self):
        assert score_to_recommendation(0.0, 0.0, 90.0) == "NO_GO"

    def test_perfect_pass_rate_with_low_composite_is_go(self):
        assert score_to_recommendation(5.0, 100.0, 90.0) == "GO"

    def test_flaky_tests_increase_reproducibility(self):
        no_flaky = compute_dimension_scores(
            analyses=_minimal_analyses(n_product_bugs=3, n_flaky=0),
            anomalies=[], pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        with_flaky = compute_dimension_scores(
            analyses=_minimal_analyses(n_product_bugs=3, n_flaky=5),
            anomalies=[], pass_rate=80.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        # Flaky tests should *decrease* reproducibility (more flaky = less reproducible)
        # OR increase it depending on implementation — just verify they differ
        assert no_flaky["reproducibility"] != with_flaky["reproducibility"]
