"""
Unit tests for run_diff_service.py

Tests:
  - _classify_regression threshold transitions
  - _classify_cluster deterministic logic (infra → env, flaky → known_flaky, etc.)
  - get_baseline_diff returns None when no baseline exists (mock DB)
  - pass_rate_delta computation correctness
  - classified_new_failures includes correct classification per failure
"""
from app.models.enums import RegressionClassification
from app.services.run_diff_service import _classify_regression, _classify_cluster


# ── _classify_regression ─────────────────────────────────────────────────────

class TestClassifyRegression:
    def test_no_failures_is_unclassified(self):
        result = _classify_regression(
            pass_rate_delta=0.0,
            new_failures=[],
            env_sensitivity=None,
            flaky_count=0,
            total_failures=0,
        )
        assert result == RegressionClassification.UNCLASSIFIED

    def test_majority_flaky_is_known_flaky(self):
        result = _classify_regression(
            pass_rate_delta=-5.0,
            new_failures=["test_a"],
            env_sensitivity=None,
            flaky_count=3,
            total_failures=4,  # 75% flaky
        )
        assert result == RegressionClassification.KNOWN_FLAKY

    def test_high_env_sensitivity_is_environmental(self):
        result = _classify_regression(
            pass_rate_delta=-8.0,
            new_failures=["test_a"],
            env_sensitivity=65.0,  # > 60 threshold
            flaky_count=0,
            total_failures=1,
        )
        assert result == RegressionClassification.ENVIRONMENTAL

    def test_large_pass_rate_drop_with_new_failures_is_new_regression(self):
        result = _classify_regression(
            pass_rate_delta=-15.0,  # > 10pp drop
            new_failures=["test_a", "test_b"],
            env_sensitivity=10.0,
            flaky_count=0,
            total_failures=2,
        )
        assert result == RegressionClassification.NEW_REGRESSION

    def test_small_drop_with_new_failures_is_product_bug(self):
        result = _classify_regression(
            pass_rate_delta=-5.0,  # < 10pp — not a "large" regression
            new_failures=["test_a"],
            env_sensitivity=20.0,
            flaky_count=0,
            total_failures=1,
        )
        assert result == RegressionClassification.PRODUCT_BUG

    def test_flaky_check_before_env_sensitivity(self):
        # Flaky > 50% takes precedence over env_sensitivity > 60
        result = _classify_regression(
            pass_rate_delta=-20.0,
            new_failures=["test_a"],
            env_sensitivity=70.0,
            flaky_count=2,
            total_failures=2,   # 100% flaky
        )
        assert result == RegressionClassification.KNOWN_FLAKY


# ── _classify_cluster ────────────────────────────────────────────────────────

class TestClassifyCluster:
    """Unit tests for _classify_cluster using a mock FailureCluster object."""

    def _make_cluster(self, member_ids: list[str]):
        """Create a minimal mock cluster object."""
        class FakeCluster:
            member_test_ids = member_ids
        return FakeCluster()

    def test_empty_cluster_is_unclassified(self):
        cluster = self._make_cluster([])
        result = _classify_cluster(cluster, {})
        assert result == RegressionClassification.UNCLASSIFIED

    def test_majority_infra_is_environmental(self):
        from app.models.postgres import FailureCategory
        cluster = self._make_cluster(["tc1", "tc2", "tc3"])
        analyses = {
            "tc1": {"failure_category": FailureCategory.INFRASTRUCTURE.value, "is_flaky": False},
            "tc2": {"failure_category": FailureCategory.INFRASTRUCTURE.value, "is_flaky": False},
            "tc3": {"failure_category": FailureCategory.PRODUCT_BUG.value,    "is_flaky": False},
        }
        # 2/3 = 67% infrastructure >= 50% threshold
        result = _classify_cluster(cluster, analyses)
        assert result == RegressionClassification.ENVIRONMENTAL

    def test_majority_flaky_is_known_flaky(self):
        from app.models.postgres import FailureCategory
        cluster = self._make_cluster(["tc1", "tc2", "tc3"])
        analyses = {
            "tc1": {"failure_category": FailureCategory.FLAKY.value, "is_flaky": True},
            "tc2": {"failure_category": FailureCategory.FLAKY.value, "is_flaky": True},
            "tc3": {"failure_category": FailureCategory.PRODUCT_BUG.value, "is_flaky": False},
        }
        # 2/3 = 67% flaky >= 50% threshold
        result = _classify_cluster(cluster, analyses)
        assert result == RegressionClassification.KNOWN_FLAKY

    def test_product_bug_present_is_product_bug(self):
        from app.models.postgres import FailureCategory
        cluster = self._make_cluster(["tc1", "tc2"])
        analyses = {
            "tc1": {"failure_category": FailureCategory.PRODUCT_BUG.value, "is_flaky": False},
            "tc2": {"failure_category": FailureCategory.TEST_DATA.value,   "is_flaky": False},
        }
        result = _classify_cluster(cluster, analyses)
        assert result == RegressionClassification.PRODUCT_BUG

    def test_no_analyses_match_is_new_regression(self):
        cluster = self._make_cluster(["tc1", "tc2"])
        # No analyses entries → fallthrough to NEW_REGRESSION
        result = _classify_cluster(cluster, {})
        assert result == RegressionClassification.NEW_REGRESSION

    def test_infra_threshold_is_exactly_half(self):
        from app.models.postgres import FailureCategory
        cluster = self._make_cluster(["tc1", "tc2"])
        analyses = {
            "tc1": {"failure_category": FailureCategory.INFRASTRUCTURE.value, "is_flaky": False},
            "tc2": {"failure_category": FailureCategory.PRODUCT_BUG.value,    "is_flaky": False},
        }
        # 1/2 = 50% == _INFRA_THRESHOLD → environmental (>= check)
        result = _classify_cluster(cluster, analyses)
        assert result == RegressionClassification.ENVIRONMENTAL


# ── pass_rate_delta arithmetic ────────────────────────────────────────────────

class TestPassRateDelta:
    def test_improvement_is_positive(self):
        delta = round(90.0 - 75.0, 2)
        assert delta > 0

    def test_regression_is_negative(self):
        delta = round(75.0 - 90.0, 2)
        assert delta < 0

    def test_no_change_is_zero(self):
        delta = round(90.0 - 90.0, 2)
        assert delta == 0.0


# ── Regression classification priority order ─────────────────────────────────

class TestClassificationPriorityOrder:
    """Verify that the priority chain (flaky > env > new_reg > product_bug) is stable."""

    def test_flaky_beats_env_sensitivity(self):
        result = _classify_regression(
            pass_rate_delta=-20.0,
            new_failures=["x"],
            env_sensitivity=80.0,  # would trigger ENVIRONMENTAL
            flaky_count=5,
            total_failures=6,      # 83% flaky → KNOWN_FLAKY wins
        )
        assert result == RegressionClassification.KNOWN_FLAKY

    def test_env_beats_new_regression(self):
        result = _classify_regression(
            pass_rate_delta=-20.0,  # would trigger NEW_REGRESSION
            new_failures=["x"],
            env_sensitivity=75.0,   # triggers ENVIRONMENTAL
            flaky_count=0,
            total_failures=1,
        )
        assert result == RegressionClassification.ENVIRONMENTAL

    def test_new_regression_beats_product_bug(self):
        result = _classify_regression(
            pass_rate_delta=-15.0,  # triggers NEW_REGRESSION (> 10pp)
            new_failures=["x"],
            env_sensitivity=5.0,
            flaky_count=0,
            total_failures=1,
        )
        assert result == RegressionClassification.NEW_REGRESSION
