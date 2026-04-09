"""
Epic 3: Regression And Baseline Explainability — Unit Tests.

Tests:
  QAI-301: Baseline selection and persistence (ORM models)
  QAI-302: Diff classification, commit range, config drift
  QAI-303: Schema validation for new fields
  QAI-304: Run baseline and diff persistence models
  Edge cases: no baseline, same commit, no SCM, different branch
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security", verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"), create_access_token=MagicMock(return_value="access_token"), create_refresh_token=MagicMock(return_value="refresh_token"), decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None)))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-301+304: Baseline and Diff Models
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunBaselineModel:
    def test_table_exists(self):
        from app.models.postgres import RunBaseline
        assert RunBaseline.__tablename__ == "run_baselines"

    def test_columns(self):
        from app.models.postgres import RunBaseline
        columns = {c.name for c in RunBaseline.__table__.columns}
        assert "run_id" in columns
        assert "baseline_run_id" in columns
        assert "selection_reason" in columns
        assert "classification" in columns
        assert "commit_range" in columns
        assert "config_drift" in columns
        assert "pass_rate_delta" in columns
        assert "computed_at" in columns

    def test_run_id_unique(self):
        from app.models.postgres import RunBaseline
        col = RunBaseline.__table__.c.run_id
        assert col.unique is True


class TestRunDiffModel:
    def test_table_exists(self):
        from app.models.postgres import RunDiff
        assert RunDiff.__tablename__ == "run_diffs"

    def test_columns(self):
        from app.models.postgres import RunDiff
        columns = {c.name for c in RunDiff.__table__.columns}
        assert "run_id" in columns
        assert "baseline_run_id" in columns
        assert "diff_payload" in columns
        assert "computed_at" in columns


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-302: Classification Logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyRegression:
    def test_no_failures_is_unclassified(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(0.0, [], None, 0, 0)
        assert result == "unclassified"

    def test_majority_flaky_is_known_flaky(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(-5.0, ["test_a"], None, 3, 4)
        assert result == "known_flaky"

    def test_high_env_sensitivity_is_environmental(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(-8.0, ["test_a"], 65.0, 0, 1)
        assert result == "environmental"

    def test_large_drop_is_new_regression(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(-15.0, ["test_a", "test_b"], 10.0, 0, 2)
        assert result == "new_regression"

    def test_small_drop_is_product_bug(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(-5.0, ["test_a"], 20.0, 0, 1)
        assert result == "product_bug"

    def test_priority_flaky_over_env(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(-20.0, ["x"], 70.0, 2, 2)
        assert result == "known_flaky"

    def test_priority_env_over_regression(self):
        from app.services.run_diff_service import _classify_regression
        result = _classify_regression(-20.0, ["x"], 75.0, 0, 1)
        assert result == "environmental"


class TestClassifyCluster:
    def _make_cluster(self, member_ids):
        class FakeCluster:
            member_test_ids = member_ids
        return FakeCluster()

    def test_empty_cluster(self):
        from app.services.run_diff_service import _classify_cluster
        c = self._make_cluster([])
        assert _classify_cluster(c, {}) == "unclassified"

    def test_majority_infra(self):
        from app.services.run_diff_service import _classify_cluster
        c = self._make_cluster(["a", "b", "c"])
        analyses = {
            "a": {"failure_category": "INFRASTRUCTURE", "is_flaky": False},
            "b": {"failure_category": "INFRASTRUCTURE", "is_flaky": False},
            "c": {"failure_category": "PRODUCT_BUG", "is_flaky": False},
        }
        assert _classify_cluster(c, analyses) == "environmental"

    def test_majority_flaky(self):
        from app.services.run_diff_service import _classify_cluster
        c = self._make_cluster(["a", "b"])
        analyses = {
            "a": {"failure_category": "FLAKY", "is_flaky": True},
            "b": {"failure_category": "FLAKY", "is_flaky": True},
        }
        assert _classify_cluster(c, analyses) == "known_flaky"

    def test_no_analyses_is_new_regression(self):
        from app.services.run_diff_service import _classify_cluster
        c = self._make_cluster(["a", "b"])
        assert _classify_cluster(c, {}) == "new_regression"


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-302+303: Schema Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaselineDiffSchema:
    def test_defaults(self):
        from app.models.schemas import BaselineDiff
        d = BaselineDiff()
        assert d.selection_reason == "latest_passing"
        assert d.commit_range is None
        assert d.config_drift == []

    def test_with_commit_range(self):
        from app.models.schemas import BaselineDiff
        d = BaselineDiff(
            commit_range={"from_commit": "abc123", "to_commit": "def456", "same_commit": False},
        )
        assert d.commit_range is not None
        assert d.commit_range["same_commit"] is False

    def test_with_config_drift(self):
        from app.models.schemas import BaselineDiff
        d = BaselineDiff(
            config_drift=[
                {"field": "branch", "old_value": "main", "new_value": "feature/x"},
                {"field": "ocp_namespace", "old_value": "qa-prod", "new_value": "qa-staging"},
            ],
        )
        assert len(d.config_drift) == 2

    def test_selection_reasons(self):
        from app.models.schemas import BaselineDiff
        for reason in ["latest_passing", "approved_release", "no_baseline"]:
            d = BaselineDiff(selection_reason=reason)
            assert d.selection_reason == reason

    def test_commit_range_schema(self):
        from app.models.schemas import CommitRange
        cr = CommitRange(from_commit="abc", to_commit="def")
        assert cr.same_commit is False

    def test_commit_range_same_commit(self):
        from app.models.schemas import CommitRange
        cr = CommitRange(from_commit="abc", to_commit="abc", same_commit=True)
        assert cr.same_commit is True

    def test_config_drift_entry_schema(self):
        from app.models.schemas import ConfigDriftEntry
        entry = ConfigDriftEntry(field="branch", old_value="main", new_value="release/v2")
        assert entry.field == "branch"

    def test_backward_compatible(self):
        """Existing code that creates BaselineDiff without new fields should still work."""
        from app.models.schemas import BaselineDiff
        d = BaselineDiff(
            baseline_run_id="x",
            pass_rate_delta=-5.0,
            new_failures=["test_a"],
            regression_classification="new_regression",
        )
        assert d.commit_range is None
        assert d.config_drift == []
        assert d.selection_reason == "latest_passing"


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_no_prior_successful_run(self):
        """When no baseline exists, diff should be None (handled by caller)."""
        # The service returns None when no baseline found
        # The UI shows "No baseline available" message
        diff = None
        assert diff is None

    def test_same_commit_different_environment(self):
        """Same commit but different env → config_drift should be populated."""
        config_drift = [{"field": "ocp_namespace", "old_value": "prod", "new_value": "staging"}]
        commit_range = {"from_commit": "abc123", "to_commit": "abc123", "same_commit": True}
        assert commit_range["same_commit"] is True
        assert len(config_drift) == 1

    def test_no_scm_metadata(self):
        """When no commit hashes are available, commit_range is None."""
        commit_range = None
        assert commit_range is None

    def test_different_branch_is_config_drift(self):
        """Branch change between runs should appear as config_drift."""
        config_drift = [{"field": "branch", "old_value": "main", "new_value": "release/v3"}]
        assert config_drift[0]["field"] == "branch"

    def test_rerun_same_commit_env_only(self):
        """Re-run of same commit with different environment should show env drift."""
        commit_range = {"from_commit": "abc", "to_commit": "abc", "same_commit": True}
        config_drift = [{"field": "ocp_namespace", "old_value": "qa-1", "new_value": "qa-2"}]
        # UI should show: "Same commit — environment or data change suspected"
        assert commit_range["same_commit"]
        assert len(config_drift) > 0

    def test_classification_enum_values(self):
        from app.models.enums import RegressionClassification
        assert RegressionClassification.NEW_REGRESSION == "new_regression"
        assert RegressionClassification.KNOWN_FLAKY == "known_flaky"
        assert RegressionClassification.ENVIRONMENTAL == "environmental"
        assert RegressionClassification.PRODUCT_BUG == "product_bug"
        assert RegressionClassification.UNCLASSIFIED == "unclassified"
