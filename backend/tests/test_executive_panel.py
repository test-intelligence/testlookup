"""
Tests for the Executive Summary Panel (ES-2).

Covers:
  - build_executive_panel(): all-green, partial failure, severe failure,
    with/without baseline, empty categories
  - RulesEngine.generate_summary() includes executive_panel
  - MLSummaryGenerator.generate() includes executive_panel
  - ExecutivePanel Pydantic validation
"""
import pytest

pytest.importorskip("asyncpg")

from app.services.executive_panel_builder import build_executive_panel  # noqa: E402
from app.models.llm_schemas import ExecutivePanel  # noqa: E402


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_executive_panel — Core Builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBuildExecutivePanelAllGreen:
    def test_all_green_headline(self):
        panel = build_executive_panel(
            run_data={"build_number": "42", "branch": "main", "total_tests": 100,
                      "passed_tests": 100, "failed_tests": 0, "skipped_tests": 0, "pass_rate": 100.0},
            category_counts={},
        )
        assert "All 100 tests passed" in panel["headline"]
        assert panel["status_signal"] == "CONDITIONAL_GO"  # default, enriched later by release decision
        assert panel["dominant_failure"] is None
        assert panel["metrics"]["failed"] == 0
        assert panel["metrics"]["pass_rate"] == 100.0

    def test_all_green_takeaways(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 50,
                      "passed_tests": 50, "failed_tests": 0, "skipped_tests": 0, "pass_rate": 100.0},
            category_counts={},
        )
        assert any("passed" in t.lower() for t in panel["key_takeaways"])


class TestBuildExecutivePanelPartialFailure:
    def test_partial_failure_headline(self):
        panel = build_executive_panel(
            run_data={"build_number": "43", "branch": "develop", "total_tests": 100,
                      "passed_tests": 88, "failed_tests": 12, "skipped_tests": 0, "pass_rate": 88.0},
            category_counts={"INFRASTRUCTURE": 8, "PRODUCT_BUG": 3, "FLAKY": 1},
            flaky_count=1,
            anomaly_count=2,
            cluster_count=3,
            release_impact="CONDITIONAL_GO",
        )
        assert "12 failures" in panel["headline"]
        assert panel["dominant_failure"] is not None
        assert panel["dominant_failure"]["category"] == "INFRASTRUCTURE"
        assert panel["dominant_failure"]["count"] == 8
        assert abs(panel["dominant_failure"]["percentage"] - 66.7) < 1.0

    def test_partial_failure_metrics(self):
        panel = build_executive_panel(
            run_data={"build_number": "43", "branch": "develop", "total_tests": 100,
                      "passed_tests": 88, "failed_tests": 12, "skipped_tests": 0, "pass_rate": 88.0},
            category_counts={"INFRASTRUCTURE": 8},
            cluster_count=3,
            anomaly_count=2,
        )
        m = panel["metrics"]
        assert m["total_tests"] == 100
        assert m["failed"] == 12
        assert m["failure_clusters"] == 3
        assert m["anomaly_count"] == 2

    def test_flaky_takeaway(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 50,
                      "passed_tests": 45, "failed_tests": 5, "skipped_tests": 0, "pass_rate": 90.0},
            category_counts={"FLAKY": 3, "PRODUCT_BUG": 2},
            flaky_count=3,
        )
        assert any("flaky" in t.lower() for t in panel["key_takeaways"])


class TestBuildExecutivePanelSevereFailure:
    def test_severe_failure_with_no_go(self):
        panel = build_executive_panel(
            run_data={"build_number": "99", "branch": "main", "total_tests": 100,
                      "passed_tests": 40, "failed_tests": 60, "skipped_tests": 0, "pass_rate": 40.0},
            category_counts={"PRODUCT_BUG": 50, "INFRASTRUCTURE": 10},
            release_impact="NO_GO",
            risk_score=85,
        )
        assert panel["status_signal"] == "NO_GO"
        assert panel["risk_score"] == 85
        assert panel["dominant_failure"]["category"] == "PRODUCT_BUG"


class TestBuildExecutivePanelBaseline:
    def test_with_baseline(self):
        panel = build_executive_panel(
            run_data={"build_number": "10", "branch": "main", "total_tests": 50,
                      "passed_tests": 47, "failed_tests": 3, "skipped_tests": 0, "pass_rate": 94.0},
            category_counts={"PRODUCT_BUG": 3},
            baseline_diff={
                "pass_rate_delta": -3.2,
                "new_failures": ["test_a", "test_b"],
                "resolved_failures": ["test_c"],
                "regression_classification": "minor_regression",
            },
        )
        bl = panel["baseline_comparison"]
        assert bl is not None
        assert bl["pass_rate_delta"] == -3.2
        assert bl["new_failures"] == 2
        assert bl["resolved"] == 1
        assert bl["classification"] == "minor_regression"

    def test_without_baseline(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 50,
                      "passed_tests": 50, "failed_tests": 0, "skipped_tests": 0, "pass_rate": 100.0},
            category_counts={},
        )
        assert panel["baseline_comparison"] is None


class TestBuildExecutivePanelNextActions:
    def test_custom_actions(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 10,
                      "passed_tests": 8, "failed_tests": 2, "skipped_tests": 0, "pass_rate": 80.0},
            category_counts={"INFRASTRUCTURE": 2},
            recommended_actions=["Check servers", "Review logs", "Restart pods", "Extra action"],
        )
        assert len(panel["next_actions"]) <= 3
        assert panel["next_actions"][0] == "Check servers"

    def test_fallback_actions_when_none_provided(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 10,
                      "passed_tests": 8, "failed_tests": 2, "skipped_tests": 0, "pass_rate": 80.0},
            category_counts={"INFRASTRUCTURE": 2},
        )
        assert len(panel["next_actions"]) >= 1


class TestBuildExecutivePanelEdgeCases:
    def test_empty_category_counts(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 0,
                      "passed_tests": 0, "failed_tests": 0, "skipped_tests": 0, "pass_rate": 0.0},
            category_counts={},
        )
        assert panel["dominant_failure"] is None
        assert isinstance(panel["key_takeaways"], list)

    def test_single_failure(self):
        panel = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 10,
                      "passed_tests": 9, "failed_tests": 1, "skipped_tests": 0, "pass_rate": 90.0},
            category_counts={"PRODUCT_BUG": 1},
        )
        assert "1 failure" in panel["headline"]
        assert "failures" not in panel["headline"]

    def test_pydantic_validation(self):
        """Output must validate through the ExecutivePanel Pydantic model."""
        raw = build_executive_panel(
            run_data={"build_number": "1", "branch": "main", "total_tests": 50,
                      "passed_tests": 45, "failed_tests": 5, "skipped_tests": 0, "pass_rate": 90.0},
            category_counts={"INFRASTRUCTURE": 5},
            flaky_count=1,
            anomaly_count=2,
            cluster_count=1,
            release_impact="CONDITIONAL_GO",
            risk_score=35,
            baseline_diff={"pass_rate_delta": -2.0, "new_failures": ["a"], "resolved_failures": [],
                           "regression_classification": "minor_regression"},
            recommended_actions=["Fix infra", "Re-run tests"],
        )
        validated = ExecutivePanel(**raw)
        assert validated.headline == raw["headline"]
        assert validated.status_signal == "CONDITIONAL_GO"
        assert validated.dominant_failure is not None
        assert validated.baseline_comparison is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RulesEngine integration — executive_panel in generate_summary()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.rules_engine import RulesEngine  # noqa: E402


class TestRulesEnginePanelIntegration:
    def test_summary_includes_executive_panel(self):
        classifications = {
            "tc1": {"failure_category": "PRODUCT_BUG", "is_flaky": False, "recommended_actions": ["Fix bug"]},
            "tc2": {"failure_category": "INFRASTRUCTURE", "is_flaky": False, "recommended_actions": []},
        }
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 50, "failed_tests": 2, "passed_tests": 48,
                      "skipped_tests": 0, "pass_rate": 96.0, "build_number": "42", "branch": "main"},
            classifications=classifications,
        )
        assert "executive_panel" in summary
        panel = summary["executive_panel"]
        assert panel["headline"]
        assert panel["metrics"]["total_tests"] == 50
        assert panel["metrics"]["failed"] == 2

    def test_all_green_summary_has_panel(self):
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 100, "failed_tests": 0, "passed_tests": 100,
                      "skipped_tests": 0, "pass_rate": 100.0, "build_number": "1", "branch": "main"},
            classifications={},
        )
        panel = summary["executive_panel"]
        assert panel["dominant_failure"] is None
        assert "passed" in panel["headline"].lower()

    def test_panel_validates_as_pydantic(self):
        summary = RulesEngine.generate_summary(
            run_data={"total_tests": 10, "failed_tests": 3, "passed_tests": 7,
                      "skipped_tests": 0, "pass_rate": 70.0, "build_number": "99", "branch": "develop"},
            classifications={
                "tc1": {"failure_category": "FLAKY", "is_flaky": True},
                "tc2": {"failure_category": "PRODUCT_BUG", "is_flaky": False},
                "tc3": {"failure_category": "PRODUCT_BUG", "is_flaky": False},
            },
        )
        validated = ExecutivePanel(**summary["executive_panel"])
        assert validated.status_signal in ("GO", "CONDITIONAL_GO", "NO_GO")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pydantic Schema Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestExecutivePanelSchema:
    def test_status_signal_normalization(self):
        panel = ExecutivePanel(status_signal="go")
        assert panel.status_signal == "GO"

    def test_invalid_status_defaults_to_conditional(self):
        panel = ExecutivePanel(status_signal="MAYBE")
        assert panel.status_signal == "CONDITIONAL_GO"

    def test_defaults(self):
        panel = ExecutivePanel()
        assert panel.headline == ""
        assert panel.status_signal == "CONDITIONAL_GO"
        assert panel.risk_score is None
        assert panel.dominant_failure is None
        assert panel.baseline_comparison is None
        assert panel.key_takeaways == []
        assert panel.next_actions == []
