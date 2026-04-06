"""
Tests for System-Wide Global Search (GS-1 through GS-8).

Covers:
  - GlobalSearchResult and GlobalSearchResponse schema validation
  - Entity type constants
  - Result structure for mixed entity types
  - Entity counts computation
  - Navigation URL format
  - Backward compatibility with test-case-only search
"""
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("asyncpg")

from pydantic import ValidationError  # noqa: E402

from app.models.schemas import GlobalSearchResult, GlobalSearchResponse  # noqa: E402


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GS-2: GlobalSearchResult Schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGlobalSearchResultSchema:
    def test_valid_test_case_result(self):
        result = GlobalSearchResult(
            entity_type="test_case",
            entity_id=str(uuid.uuid4()),
            title="testLoginSuccess",
            subtitle="AuthSuite · PASSED",
            navigation_url="/test-runs/abc123",
            relevance_score=0.85,
            match_reasons=["Matched test name"],
        )
        assert result.entity_type == "test_case"
        assert result.relevance_score == 0.85
        assert result.navigation_url.startswith("/test-runs/")

    def test_valid_test_run_result(self):
        result = GlobalSearchResult(
            entity_type="test_run",
            entity_id=str(uuid.uuid4()),
            title="Build 2029",
            subtitle="main · FAILED · 64.3% pass rate",
            project_id=str(uuid.uuid4()),
            navigation_url="/test-runs/xyz789",
        )
        assert result.entity_type == "test_run"
        assert result.project_id is not None

    def test_valid_suite_result(self):
        result = GlobalSearchResult(
            entity_type="suite",
            entity_id="payment-tests",
            title="payment-tests",
            subtitle="42 test executions",
            navigation_url="/coverage/suite?name=payment-tests",
        )
        assert result.entity_type == "suite"

    def test_valid_defect_result(self):
        result = GlobalSearchResult(
            entity_type="defect",
            entity_id=str(uuid.uuid4()),
            title="PROJ-123",
            subtitle="PRODUCT_BUG · Open",
            navigation_url="/defects",
        )
        assert result.entity_type == "defect"

    def test_valid_flaky_test_result(self):
        result = GlobalSearchResult(
            entity_type="flaky_test",
            entity_id="abc123deadbeef",
            title="testPaymentRetry",
            subtitle="45% failure rate over 20 runs",
            navigation_url="/failures",
            metadata={"failure_rate": 45.0, "total_runs": 20},
        )
        assert result.entity_type == "flaky_test"
        assert result.metadata["failure_rate"] == 45.0

    def test_valid_release_result(self):
        result = GlobalSearchResult(
            entity_type="release",
            entity_id=str(uuid.uuid4()),
            title="v2.3.0",
            subtitle="no version · active",
            navigation_url="/releases/abc",
        )
        assert result.entity_type == "release"

    def test_defaults(self):
        result = GlobalSearchResult(
            entity_type="test_case",
            entity_id="id",
            title="test",
            navigation_url="/path",
        )
        assert result.subtitle == ""
        assert result.project_id is None
        assert result.relevance_score == 0.0
        assert result.match_reasons == []
        assert result.metadata == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GS-2: GlobalSearchResponse Schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGlobalSearchResponseSchema:
    def test_mixed_entity_response(self):
        resp = GlobalSearchResponse(
            items=[
                GlobalSearchResult(entity_type="test_case", entity_id="1", title="test1", navigation_url="/a"),
                GlobalSearchResult(entity_type="test_run", entity_id="2", title="Build 42", navigation_url="/b"),
                GlobalSearchResult(entity_type="defect", entity_id="3", title="PROJ-1", navigation_url="/c"),
            ],
            total=3,
            query="payment",
            entity_counts={"test_case": 1, "test_run": 1, "defect": 1},
        )
        assert resp.total == 3
        assert len(resp.items) == 3
        assert resp.entity_counts["test_case"] == 1

    def test_empty_response(self):
        resp = GlobalSearchResponse(
            items=[],
            total=0,
            query="nonexistent",
        )
        assert resp.total == 0
        assert resp.entity_counts == {}

    def test_single_entity_type_response(self):
        resp = GlobalSearchResponse(
            items=[
                GlobalSearchResult(entity_type="suite", entity_id="s1", title="api-tests", navigation_url="/cov"),
                GlobalSearchResult(entity_type="suite", entity_id="s2", title="ui-tests", navigation_url="/cov"),
            ],
            total=2,
            query="tests",
            entity_counts={"suite": 2},
        )
        assert resp.entity_counts == {"suite": 2}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GS-3: Entity Adapter Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEntityAdapterRegistry:
    def test_all_entity_types_defined(self):
        from app.services.global_search_service import ALL_ENTITY_TYPES
        expected = {"test_case", "test_run", "suite", "defect", "flaky_test", "release"}
        assert ALL_ENTITY_TYPES == expected

    def test_all_adapters_registered(self):
        from app.services.global_search_service import _ADAPTERS, ALL_ENTITY_TYPES
        assert set(_ADAPTERS.keys()) == ALL_ENTITY_TYPES

    def test_adapters_are_callable(self):
        from app.services.global_search_service import _ADAPTERS
        for name, adapter in _ADAPTERS.items():
            assert callable(adapter), f"Adapter {name} is not callable"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GS-5: Navigation URL Format
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNavigationUrls:
    def test_test_case_url(self):
        result = GlobalSearchResult(
            entity_type="test_case", entity_id="tc1", title="t",
            navigation_url="/test-runs/run-1",
        )
        assert result.navigation_url.startswith("/test-runs/")

    def test_suite_url(self):
        result = GlobalSearchResult(
            entity_type="suite", entity_id="s1", title="api",
            navigation_url="/coverage/suite?name=api",
        )
        assert "name=" in result.navigation_url

    def test_defect_url(self):
        result = GlobalSearchResult(
            entity_type="defect", entity_id="d1", title="DEF",
            navigation_url="/defects",
        )
        assert result.navigation_url == "/defects"

    def test_release_url(self):
        result = GlobalSearchResult(
            entity_type="release", entity_id="r1", title="v1",
            navigation_url="/releases/r1",
        )
        assert result.navigation_url.startswith("/releases/")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GS-1: Backward Compatibility
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import SearchResult, SearchResponse  # noqa: E402


class TestBackwardCompatibility:
    def test_old_search_result_still_valid(self):
        """Existing test-case-only SearchResult schema still works."""
        result = SearchResult(
            test_case_id=uuid.uuid4(),
            test_run_id=uuid.uuid4(),
            test_name="testLogin",
            status="FAILED",
            last_run_date=datetime.now(timezone.utc),
            failure_count=3,
        )
        assert result.test_name == "testLogin"

    def test_old_search_response_still_valid(self):
        """Existing SearchResponse schema still works."""
        resp = SearchResponse(
            items=[],
            total=0,
            query="test",
            search_type="keyword",
        )
        assert resp.search_type == "keyword"
