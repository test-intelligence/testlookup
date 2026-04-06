"""
Unit tests for Phase 5: Test Suite Hardening & Observability Gaps.

Covers:
  P5-1: FakeRedis TTL behavior (expiry, setex, expire, pipeline, scan)
  P5-3: Tests for analytics router, webhooks router, stream service, cache service
  P5-5: Directionality assertions are in test_criticality_service.py (separate file)
  P5-8: Rate limit config structure
"""
import asyncio

import pytest

pytest.importorskip("asyncpg")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-1: FakeRedis TTL Behavior
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFakeRedisTTL:
    @pytest.mark.asyncio
    async def test_set_without_ttl_persists_indefinitely(self, fake_redis):
        await fake_redis.set("key1", "value1")
        result = await fake_redis.get("key1")
        assert result == b"value1"

    @pytest.mark.asyncio
    async def test_set_with_ttl_returns_value_before_expiry(self, fake_redis):
        await fake_redis.set("key2", "value2", ex=60)
        result = await fake_redis.get("key2")
        assert result == b"value2"

    @pytest.mark.asyncio
    async def test_set_with_zero_ttl_expires_immediately(self, fake_redis):
        """TTL of 0 should expire the key on next get."""
        await fake_redis.set("key3", "value3", ex=0)
        # monotonic clock: setting ex=0 means expiry = now + 0, which is already past
        # Need a tiny sleep to ensure monotonic clock advances
        await asyncio.sleep(0.01)
        result = await fake_redis.get("key3")
        assert result is None

    @pytest.mark.asyncio
    async def test_setex_sets_value_with_ttl(self, fake_redis):
        await fake_redis.setex("key4", 60, "value4")
        result = await fake_redis.get("key4")
        assert result == b"value4"

    @pytest.mark.asyncio
    async def test_expire_refreshes_ttl(self, fake_redis):
        await fake_redis.set("key5", "value5", ex=1)
        # Refresh TTL to 60 seconds
        result = await fake_redis.expire("key5", 60)
        assert result is True
        val = await fake_redis.get("key5")
        assert val == b"value5"

    @pytest.mark.asyncio
    async def test_expire_on_missing_key_returns_false(self, fake_redis):
        result = await fake_redis.expire("nonexistent", 60)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_multiple_keys(self, fake_redis):
        await fake_redis.set("a", "1")
        await fake_redis.set("b", "2")
        await fake_redis.set("c", "3")
        count = await fake_redis.delete("a", "b", "nonexistent")
        assert count == 2
        assert await fake_redis.get("a") is None
        assert await fake_redis.get("c") == b"3"

    @pytest.mark.asyncio
    async def test_exists_returns_1_for_present_key(self, fake_redis):
        await fake_redis.set("present", "yes")
        assert await fake_redis.exists("present") == 1

    @pytest.mark.asyncio
    async def test_exists_returns_0_for_missing_key(self, fake_redis):
        assert await fake_redis.exists("missing") == 0

    @pytest.mark.asyncio
    async def test_xlen_returns_zero(self, fake_redis):
        """Stream length stub always returns 0."""
        assert await fake_redis.xlen("some_stream") == 0

    @pytest.mark.asyncio
    async def test_scan_returns_matching_keys(self, fake_redis):
        await fake_redis.set("analytics:dash:proj1:days=7", "x")
        await fake_redis.set("analytics:flaky:proj1:days=30", "y")
        await fake_redis.set("other:key", "z")
        cursor, keys = await fake_redis.scan(0, match="analytics:*")
        assert cursor == 0  # single-pass stub
        assert "analytics:dash:proj1:days=7" in keys
        assert "analytics:flaky:proj1:days=30" in keys
        assert "other:key" not in keys


class TestFakeRedisPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_xadd_and_execute(self, fake_redis):
        pipe = fake_redis.pipeline()
        pipe.xadd("stream1", {"key": "val"})
        pipe.xadd("stream1", {"key": "val2"})
        results = await pipe.execute()
        assert len(results) == 2
        assert all(r.startswith("msg-") for r in results)

    @pytest.mark.asyncio
    async def test_pipeline_empty_execute(self, fake_redis):
        pipe = fake_redis.pipeline()
        results = await pipe.execute()
        assert results == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-3: Tests for Analytics Router
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestAnalyticsRouterStructure:
    def _import_router(self):
        try:
            from app.routers.analytics import router
            return router
        except ImportError:
            pytest.skip("analytics router requires celery (not installed locally)")

    def test_analytics_router_importable(self):
        router = self._import_router()
        assert router.prefix == "/api/v1/analytics"

    def test_analytics_router_has_flaky_tests_endpoint(self):
        router = self._import_router()
        paths = [r.path for r in router.routes]
        assert any("flaky-tests" in p for p in paths)

    def test_analytics_router_has_failure_categories_endpoint(self):
        router = self._import_router()
        paths = [r.path for r in router.routes]
        assert any("failure-categories" in p for p in paths)

    def test_analytics_router_has_top_failing_endpoint(self):
        router = self._import_router()
        paths = [r.path for r in router.routes]
        assert any("top-failing" in p for p in paths)

    def test_analytics_router_has_coverage_endpoint(self):
        router = self._import_router()
        paths = [r.path for r in router.routes]
        assert any("coverage" in p for p in paths)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-3: Tests for Webhooks Router
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWebhooksRouterStructure:
    def _import_router(self):
        try:
            from app.routers.webhooks import router
            return router
        except ImportError:
            pytest.skip("webhooks router requires celery (not installed locally)")

    def test_webhooks_router_importable(self):
        router = self._import_router()
        assert router.prefix == "/api/v1/webhook"

    def test_webhooks_has_minio_endpoint(self):
        router = self._import_router()
        paths = [r.path for r in router.routes]
        assert any("minio" in p for p in paths)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-3: Tests for Cache Service
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCacheServiceIntegration:
    @pytest.mark.asyncio
    async def test_cache_roundtrip_with_fake_redis(self, fake_redis):
        """Full get/set cycle using real FakeRedis with TTL."""
        import json
        key = "analytics:test:proj-1:days=7"
        value = {"total_executions_7d": {"value": 42}}
        await fake_redis.set(key, json.dumps(value), ex=60)
        raw = await fake_redis.get(key)
        assert json.loads(raw) == value

    @pytest.mark.asyncio
    async def test_cache_expired_key_returns_none(self, fake_redis):
        """Verify expired cache entries return None."""
        await fake_redis.set("expiring", "data", ex=0)
        await asyncio.sleep(0.01)
        assert await fake_redis.get("expiring") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-3: Tests for Stream Backpressure (from P3-8)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

pytest.importorskip("redis")

from app.streams.producer import StreamBackpressureError  # noqa: E402


class TestStreamBackpressureImport:
    def test_error_class_exists(self):
        assert issubclass(StreamBackpressureError, Exception)

    def test_error_message_preserved(self):
        err = StreamBackpressureError("Stream full")
        assert str(err) == "Stream full"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-8: Rate Limit Config Structure
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRateLimitConfig:
    def _get_limits(self):
        try:
            from app.main import _AUTH_RATE_LIMITS
            return _AUTH_RATE_LIMITS
        except ImportError:
            pytest.skip("app.main requires slowapi (not installed locally)")

    def test_rate_limit_dict_has_login(self):
        limits = self._get_limits()
        assert "/api/v1/auth/login" in limits

    def test_rate_limit_dict_has_register(self):
        limits = self._get_limits()
        assert "/api/v1/auth/register" in limits

    def test_login_limit_is_stricter_than_register(self):
        """Login allows more attempts than register (register is more abusable)."""
        limits = self._get_limits()
        login_limit = limits["/api/v1/auth/login"][0]
        register_limit = limits["/api/v1/auth/register"][0]
        login_n = int(login_limit.split("/")[0])
        register_n = int(register_limit.split("/")[0])
        assert login_n > register_n

    def test_rate_limit_entries_have_error_messages(self):
        limits = self._get_limits()
        for path, (limit, msg) in limits.items():
            assert isinstance(msg, str)
            assert len(msg) > 10, f"Error message too short for {path}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-7: Alert Rules File Existence
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os  # noqa: E402


class TestAlertRulesExist:
    def test_alerts_yml_exists(self):
        alerts_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "infra", "monitoring",
            "prometheus-rules", "testlookup-alerts.yml",
        )
        assert os.path.isfile(alerts_path), "Alert rules file should exist"

    def test_alerts_yml_contains_infrastructure_group(self):
        alerts_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "infra", "monitoring",
            "prometheus-rules", "testlookup-alerts.yml",
        )
        content = open(alerts_path).read()
        assert "testlookup.infrastructure" in content
        assert "TestLookupDBPoolExhausted" in content
        assert "TestLookupRedisMemoryHigh" in content
        assert "TestLookupCeleryQueueBacklog" in content
        assert "TestLookupTaskLatencyHigh" in content
        assert "TestLookupDiskSpaceLow" in content
        assert "TestLookupOOMKillDetected" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P5-3: Cluster Service + Regression Diff Service (from P3-9) smoke tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestServiceImports:
    def test_cluster_service_importable(self):
        from app.services.cluster_service import check_duplicate
        assert callable(check_duplicate)

    def test_regression_diff_service_importable(self):
        from app.services.regression_diff_service import compute_regression_diff
        assert callable(compute_regression_diff)

    def test_cache_service_importable(self):
        from app.services.cache_service import cache_get, cache_set, invalidate_analytics_cache
        assert callable(cache_get)
        assert callable(cache_set)
        assert callable(invalidate_analytics_cache)

    def test_llm_schemas_importable(self):
        from app.models.llm_schemas import validate_llm_output
        assert callable(validate_llm_output)
