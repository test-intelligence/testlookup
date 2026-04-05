"""
Unit tests for OPS-03: Load/Perf Tests for Large Runs and Search Indexing.

Covers:
 - Performance budgets: all defined, positive values, constraints
 - Load test harness: synthetic data generation
 - Config settings: search/indexing tunables
 - Scale scenarios: structure validation
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
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt",
                checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))
        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security",
            verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"),
            create_access_token=MagicMock(return_value="access_token"),
            create_refresh_token=MagicMock(return_value="refresh_token"),
            decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        from sqlalchemy.orm import DeclarativeBase
        class _Base(DeclarativeBase):
            pass
        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres",
            get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo",
            get_mongo_db=MagicMock(), close_mongo=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client",
            get_redis=MagicMock(), close_redis=MagicMock()))
        yield


# ── Performance Budgets ──────────────────────────────────────────────────────


class TestPerformanceBudgets:
    def test_latency_budgets_defined(self):
        from app.services.performance_budgets import LATENCY_BUDGETS

        assert len(LATENCY_BUDGETS) >= 10
        for b in LATENCY_BUDGETS:
            assert b.operation
            assert b.p50_ms > 0
            assert b.p95_ms >= b.p50_ms
            assert b.p99_ms >= b.p95_ms

    def test_throughput_budgets_defined(self):
        from app.services.performance_budgets import THROUGHPUT_BUDGETS

        assert len(THROUGHPUT_BUDGETS) >= 3
        for b in THROUGHPUT_BUDGETS:
            assert b.operation
            assert b.min_rps > 0

    def test_scale_scenarios_defined(self):
        from app.services.performance_budgets import SCALE_SCENARIOS

        assert len(SCALE_SCENARIOS) >= 3
        for s in SCALE_SCENARIOS:
            assert s["name"]
            assert s["projects"] > 0
            assert s["runs_per_day"] > 0
            assert s["tests_per_run"] > 0

    def test_get_all_budgets_serializable(self):
        import json

        from app.services.performance_budgets import get_all_budgets

        result = get_all_budgets()
        assert "latency_budgets" in result
        assert "throughput_budgets" in result
        assert "scale_scenarios" in result
        # Must be JSON serializable
        json.dumps(result)

    def test_search_budgets_exist(self):
        from app.services.performance_budgets import LATENCY_BUDGETS

        ops = {b.operation for b in LATENCY_BUDGETS}
        assert "keyword_search" in ops
        assert "semantic_search" in ops
        assert "hybrid_search" in ops
        assert "search_reindex_incremental" in ops

    def test_budgets_are_frozen_dataclass(self):
        from app.services.performance_budgets import LATENCY_BUDGETS

        budget = LATENCY_BUDGETS[0]
        with pytest.raises(AttributeError):
            budget.p50_ms = 999  # type: ignore


# ── Load Test Harness ────────────────────────────────────────────────────────


class TestLoadTestHarness:
    def test_generate_test_cases(self):
        from scripts.load_test_harness import generate_synthetic_test_cases

        cases = generate_synthetic_test_cases(100)
        assert len(cases) == 100
        assert all("test_name" in c for c in cases)
        assert all("suite_name" in c for c in cases)
        assert all("status" in c for c in cases)

    def test_generate_run(self):
        from scripts.load_test_harness import generate_synthetic_run

        run = generate_synthetic_run("proj-1", 50)
        assert run["total_tests"] == 50
        assert run["passed_tests"] + run["failed_tests"] + run["skipped_tests"] + run["broken_tests"] == 50
        assert 0 <= run["pass_rate"] <= 100

    def test_test_case_statuses_valid(self):
        from scripts.load_test_harness import generate_synthetic_test_cases

        cases = generate_synthetic_test_cases(200)
        valid = {"PASSED", "FAILED", "BROKEN", "SKIPPED"}
        for c in cases:
            assert c["status"] in valid

    def test_failure_category_only_on_failures(self):
        from scripts.load_test_harness import generate_synthetic_test_cases

        cases = generate_synthetic_test_cases(200)
        for c in cases:
            if c["status"] == "PASSED":
                assert c["failure_category"] is None


# ── Config Settings ──────────────────────────────────────────────────────────


class TestConfigSettings:
    def test_search_settings_have_defaults(self):
        from app.core.config import settings

        assert settings.SEARCH_INDEX_BATCH_SIZE == 200
        assert settings.SEARCH_INDEX_INCREMENTAL_LIMIT == 5000
        assert settings.SEARCH_QUERY_TIMEOUT_MS == 5000
        assert settings.SEARCH_MAX_RESULTS == 200

    def test_pool_settings_exist(self):
        from app.core.config import settings

        assert hasattr(settings, "PG_POOL_SIZE")
        assert hasattr(settings, "PG_MAX_OVERFLOW")
        assert settings.PG_POOL_RECYCLE > 0
        assert settings.PG_POOL_TIMEOUT > 0

    def test_celery_concurrency(self):
        from app.core.config import settings

        assert settings.CELERY_WORKER_CONCURRENCY >= 1


# ── Scale Scenario Constraints ───────────────────────────────────────────────


class TestScaleScenarios:
    def test_scenarios_scale_progressively(self):
        from app.services.performance_budgets import SCALE_SCENARIOS

        prev_users = 0
        for s in SCALE_SCENARIOS:
            assert s["concurrent_users"] >= prev_users
            prev_users = s["concurrent_users"]

    def test_large_enterprise_scenario(self):
        from app.services.performance_budgets import SCALE_SCENARIOS

        large = next(s for s in SCALE_SCENARIOS if s["name"] == "large_enterprise")
        assert large["projects"] >= 100
        assert large["runs_per_day"] >= 500
        assert large["tests_per_run"] >= 1000
