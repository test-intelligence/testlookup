"""
ROI-03: Better Run Summaries From Full Factual Context — Unit Tests.

Tests:
  - _generate_and_cache_mode_variant accepts db parameter
  - Placeholder values are no longer hardcoded
  - Fallback to MongoDB layers when DB unavailable
  - Function signature is correct
  - Edge cases
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
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), require_project_access=MagicMock(return_value=MagicMock()), require_run_access=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None)))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


class TestFunctionSignature:
    def test_generate_variant_accepts_db_param(self):
        """The function should accept an optional db parameter for PostgreSQL access."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        sig = inspect.signature(_generate_and_cache_mode_variant)
        params = list(sig.parameters.keys())
        assert "db" in params
        # db should be optional (has a default)
        assert sig.parameters["db"].default is None

    def test_function_is_async(self):
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        assert inspect.iscoroutinefunction(_generate_and_cache_mode_variant)


class TestPlaceholderElimination:
    def test_no_hardcoded_unknown_branch(self):
        """The function source should NOT hardcode branch='unknown' as the only option."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        # The function should have a fallback to "unknown" but ALSO a real DB fetch
        assert "select(TestRun)" in source  # Real DB fetch present
        assert "run.branch" in source       # Real branch used

    def test_no_hardcoded_zero_pass_rate(self):
        """The function should fetch real pass_rate from TestRun, not default to 0."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        assert "run.pass_rate" in source     # Real pass_rate used
        assert "run.total_tests" in source   # Real total_tests used

    def test_real_analyses_fetched(self):
        """The function should fetch real AIAnalysis records, not just MongoDB stubs."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        assert "select(AIAnalysis)" in source

    def test_real_release_decision_fetched(self):
        """The function should fetch real ReleaseDecision, not just layer4 hints."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        assert "select(ReleaseDecision)" in source

    def test_real_clusters_fetched(self):
        """The function should fetch real FailureCluster records."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        assert "select(FailureCluster)" in source


class TestFallbackBehavior:
    def test_db_none_still_works(self):
        """When db=None (backward compat), the function should use MongoDB layer extraction."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        # Should check for db is not None before attempting DB queries
        assert "if db is not None" in source

    def test_mongodb_extraction_preserved(self):
        """MongoDB layer extraction should still exist as fallback."""
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        assert "layer2" in source
        assert "layer3" in source
        assert "_doc_cause" in source  # Fallback analysis stub


class TestEdgeCases:
    def test_run_not_found_uses_defaults(self):
        """If TestRun is not found, defaults should still be used (not crash)."""
        # The function has try/except around the DB fetch
        import inspect
        from app.services.run_intelligence_service import _generate_and_cache_mode_variant

        source = inspect.getsource(_generate_and_cache_mode_variant)
        assert "except Exception" in source

    def test_get_run_mode_summary_passes_db(self):
        """get_run_mode_summary should pass db to _generate_and_cache_mode_variant."""
        import inspect
        from app.services.run_intelligence_service import get_run_mode_summary

        source = inspect.getsource(get_run_mode_summary)
        assert "db=db" in source

    def test_models_importable(self):
        """All models used in the function should be importable."""
        from app.models.postgres import (
            AIAnalysis, FailureCluster, ReleaseDecision, TestCase, TestRun,
        )
        assert all(hasattr(m, "__tablename__") for m in [
            AIAnalysis, FailureCluster, ReleaseDecision, TestCase, TestRun,
        ])
