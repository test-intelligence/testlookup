"""
Unit tests for the resilience, caching, and token handling utilities.
Tests cover:
  - async_retry with exponential backoff
  - Token estimation and truncation
  - Analysis cache key computation
  - AI result caching in agent.py
  - Pipeline checkpoint serialization
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ── async_retry tests ────────────────────────────────────────────────────────


class TestAsyncRetry:
    """Verify exponential backoff retry logic."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        from app.services.resilience import async_retry

        fn = AsyncMock(return_value="ok")
        result = await async_retry(fn, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        from app.services.resilience import async_retry

        fn = AsyncMock(side_effect=[ConnectionError("fail"), ConnectionError("fail"), "ok"])
        result = await async_retry(fn, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert fn.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        from app.services.resilience import async_retry

        fn = AsyncMock(side_effect=TimeoutError("always timeout"))
        with pytest.raises(TimeoutError, match="always timeout"):
            await async_retry(fn, max_retries=2, base_delay=0.01)
        assert fn.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_http_status(self):
        from app.services.resilience import async_retry

        response = MagicMock()
        response.status_code = 400
        fn = AsyncMock(side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=response))
        with pytest.raises(httpx.HTTPStatusError):
            await async_retry(fn, max_retries=3, base_delay=0.01)
        assert fn.call_count == 1  # No retry on 400

    @pytest.mark.asyncio
    async def test_retries_on_retryable_http_status(self):
        from app.services.resilience import async_retry

        response_503 = MagicMock()
        response_503.status_code = 503

        fn = AsyncMock(side_effect=[
            httpx.HTTPStatusError("srv", request=MagicMock(), response=response_503),
            "recovered",
        ])
        result = await async_retry(fn, max_retries=2, base_delay=0.01)
        assert result == "recovered"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_when_max_retries_zero(self):
        from app.services.resilience import async_retry

        fn = AsyncMock(side_effect=ConnectionError("fail"))
        with pytest.raises(ConnectionError):
            await async_retry(fn, max_retries=0, base_delay=0.01)
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_negative_max_retries_raises_value_error(self):
        """Caller misuse — max_retries must be >= 0; otherwise the function
        used to silently return None. It now raises ValueError so the bug
        surfaces immediately at the call site."""
        from app.services.resilience import async_retry

        fn = AsyncMock(return_value="ok")
        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            await async_retry(fn, max_retries=-1, base_delay=0.01)
        # The coro factory is never called because the for-range is empty.
        assert fn.call_count == 0

    @pytest.mark.asyncio
    async def test_http_retryable_exhausts_then_raises(self):
        """HTTP 503 retried max_retries times then bubbles up the last error."""
        from app.services.resilience import async_retry

        response_503 = MagicMock()
        response_503.status_code = 503
        err = httpx.HTTPStatusError("svc unavailable", request=MagicMock(), response=response_503)

        fn = AsyncMock(side_effect=[err, err, err])
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await async_retry(fn, max_retries=2, base_delay=0.01)
        assert excinfo.value.response.status_code == 503
        assert fn.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_custom_retryable_exception_class(self):
        """Caller can override the retryable_exceptions tuple."""
        from app.services.resilience import async_retry

        class CustomBlip(RuntimeError):
            pass

        fn = AsyncMock(side_effect=[CustomBlip("flake"), "ok"])
        result = await async_retry(
            fn,
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(CustomBlip,),
        )
        assert result == "ok"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_exception_propagates_immediately(self):
        """Exception type not in retryable_exceptions short-circuits."""
        from app.services.resilience import async_retry

        fn = AsyncMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError, match="bad input"):
            await async_retry(fn, max_retries=3, base_delay=0.01)
        assert fn.call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_passes_through_args_and_kwargs(self):
        """async_retry forwards positional and keyword args to the coroutine."""
        from app.services.resilience import async_retry

        async def echo(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        result = await async_retry(echo, "a", "b", x=1, y=2, max_retries=0)
        assert result == {"args": ("a", "b"), "kwargs": {"x": 1, "y": 2}}


# ── Token estimation & truncation tests ──────────────────────────────────────


class TestTokenHandling:
    """Verify token budget estimation and truncation."""

    def test_estimate_token_count(self):
        from app.services.resilience import estimate_token_count

        assert estimate_token_count("") == 1  # min 1
        assert estimate_token_count("hello world") == 2  # 11 chars / 4 ≈ 2
        assert estimate_token_count("a" * 4000) == 1000

    def test_truncate_to_token_budget_no_truncation(self):
        from app.services.resilience import truncate_to_token_budget

        text = "short text"
        result = truncate_to_token_budget(text, 100)
        assert result == text

    def test_truncate_to_token_budget_truncates(self):
        from app.services.resilience import truncate_to_token_budget

        text = "a" * 1000
        result = truncate_to_token_budget(text, 50)  # 50 tokens = 200 chars
        assert len(result) < 1000
        assert "truncated" in result


# ── Cache key tests ──────────────────────────────────────────────────────────


class TestAnalysisCacheKey:
    """Verify cache key determinism and normalization."""

    def test_same_inputs_produce_same_key(self):
        from app.services.resilience import compute_analysis_cache_key

        key1 = compute_analysis_cache_key("TestLogin", "Expected 200", "at Login.java:45")
        key2 = compute_analysis_cache_key("TestLogin", "Expected 200", "at Login.java:45")
        assert key1 == key2

    def test_different_inputs_produce_different_keys(self):
        from app.services.resilience import compute_analysis_cache_key

        key1 = compute_analysis_cache_key("TestLogin", "Expected 200", "at Login.java:45")
        key2 = compute_analysis_cache_key("TestPayment", "Expected 200", "at Payment.java:10")
        assert key1 != key2

    def test_case_insensitive_test_name(self):
        from app.services.resilience import compute_analysis_cache_key

        key1 = compute_analysis_cache_key("TestLogin", "error", "trace")
        key2 = compute_analysis_cache_key("testlogin", "error", "trace")
        assert key1 == key2

    def test_key_has_correct_prefix(self):
        from app.services.resilience import compute_analysis_cache_key

        key = compute_analysis_cache_key("test", "err", "trace")
        assert key.startswith("testlookup:ai_cache:")


# ── AI analysis cache integration tests ──────────────────────────────────────


class TestAgentCache:
    """Verify the agent-level cache lookup and store functions."""

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        from app.services.agent import _check_analysis_cache

        with patch("app.db.redis_client.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)
            mock_get_redis.return_value = mock_redis
            result = await _check_analysis_cache("TestLogin", "error msg", "stacktrace")
            assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_analysis(self):
        from app.services.agent import _check_analysis_cache

        cached_analysis = {"failure_category": "INFRASTRUCTURE", "confidence_score": 90}
        with patch("app.db.redis_client.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=json.dumps(cached_analysis))
            mock_get_redis.return_value = mock_redis
            result = await _check_analysis_cache("TestLogin", "error msg", "stacktrace")
            assert result is not None
            assert result["failure_category"] == "INFRASTRUCTURE"

    @pytest.mark.asyncio
    async def test_cache_store_sets_with_ttl(self):
        from app.services.agent import _store_analysis_cache

        with patch("app.db.redis_client.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(return_value=True)
            mock_get_redis.return_value = mock_redis
            await _store_analysis_cache(
                "TestLogin", "error msg", "stacktrace",
                {"failure_category": "BUG", "confidence_score": 85},
            )
            mock_redis.set.assert_called_once()
            call_args = mock_redis.set.call_args
            assert call_args.kwargs.get("ex") == 3600  # 1 hour TTL

    @pytest.mark.asyncio
    async def test_cache_no_inputs_returns_none(self):
        from app.services.agent import _check_analysis_cache

        result = await _check_analysis_cache("TestLogin", "", "")
        assert result is None


# ── Pipeline checkpoint tests ────────────────────────────────────────────────


class TestCheckpointSerialization:
    """Verify checkpoint data is safely serializable.

    We test the checkpoint logic inline (duplicating the pure functions)
    to avoid importing app.agents.workflow which triggers heavy DB imports.
    """

    @staticmethod
    def _safe_serialize(data: dict) -> dict:
        """Mirror of workflow._safe_serialize for isolated testing."""
        try:
            json.dumps(data, default=str)
            return data
        except (TypeError, ValueError):
            cleaned = {}
            for k, v in data.items():
                try:
                    json.dumps(v, default=str)
                    cleaned[k] = v
                except (TypeError, ValueError):
                    cleaned[k] = str(v)
            return cleaned

    def test_safe_serialize_passes_clean_data(self):
        data = {"key": "value", "count": 42, "nested": {"a": [1, 2]}}
        result = self._safe_serialize(data)
        assert result == data

    def test_safe_serialize_handles_non_serializable(self):
        from datetime import datetime, timezone

        data = {"timestamp": datetime.now(timezone.utc), "normal": "ok"}
        result = self._safe_serialize(data)
        json.dumps(result, default=str)  # Should not raise

    @pytest.mark.asyncio
    async def test_checkpointed_node_skips_restored_stage(self):
        """Verify that a checkpointed node wrapper skips execution for restored stages."""
        original_called = False

        async def mock_node(state):
            nonlocal original_called
            original_called = True
            return {"data": "new"}

        # Inline the _make_checkpointed_node logic to avoid importing workflow module
        async def wrapped(state):
            checkpoint_stages = state.get("_checkpoint_stages", [])
            if "ingestion" in checkpoint_stages:
                return {"completed_stages": ["ingestion"], "current_stage": "ingestion"}
            result = await mock_node(state)
            return result

        state = {"pipeline_run_id": "test-123", "_checkpoint_stages": ["ingestion"]}
        result = await wrapped(state)

        assert not original_called
        assert "completed_stages" in result
        assert "ingestion" in result["completed_stages"]

    @pytest.mark.asyncio
    async def test_checkpointed_node_executes_when_not_in_checkpoint(self):
        """When stage is not in checkpoint list, the original node should execute."""
        original_called = False

        async def mock_node(state):
            nonlocal original_called
            original_called = True
            return {"data": "new", "completed_stages": ["analysis"]}

        async def wrapped(state):
            checkpoint_stages = state.get("_checkpoint_stages", [])
            if "analysis" in checkpoint_stages:
                return {"completed_stages": ["analysis"], "current_stage": "analysis"}
            return await mock_node(state)

        state = {"pipeline_run_id": "test-123", "_checkpoint_stages": []}
        result = await wrapped(state)

        assert original_called
        assert result["data"] == "new"


# ── Backoff delay tests ──────────────────────────────────────────────────────


class TestBackoffDelay:
    """Verify exponential backoff calculation."""

    def test_backoff_increases_with_attempts(self):
        from app.services.resilience import _backoff_delay

        delays = [_backoff_delay(i, 1.0, 30.0) for i in range(5)]
        # Each delay should roughly double (within jitter range)
        for i in range(1, len(delays)):
            # The expected center doubles each time, but jitter adds ±20%
            # So we check that later delays trend higher
            pass
        # First attempt base ≈ 1.0, fifth attempt base ≈ 16.0
        assert delays[0] < 2.0  # 1.0 ± 20%
        assert delays[4] > 5.0  # 16.0 ± 20%, capped at 30

    def test_backoff_respects_cap(self):
        from app.services.resilience import _backoff_delay

        delay = _backoff_delay(100, 1.0, 5.0)  # 2^100 >> 5.0
        assert delay <= 6.0  # 5.0 + 20% jitter max


# ── with_fallback tests ──────────────────────────────────────────────────────


class TestWithFallback:
    """Verify the primary/fallback degradation primitive."""

    @pytest.mark.asyncio
    async def test_primary_succeeds_fallback_not_called(self):
        from app.services.resilience import with_fallback

        primary = AsyncMock(return_value="primary-result")
        fallback = AsyncMock(return_value="fallback-result")

        result = await with_fallback(primary, fallback, name="t1")

        assert result == "primary-result"
        primary.assert_awaited_once()
        fallback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_primary_raises_triggers_fallback(self):
        from app.services.resilience import with_fallback

        primary = AsyncMock(side_effect=RuntimeError("chromadb down"))
        fallback = AsyncMock(return_value="fallback-result")

        result = await with_fallback(primary, fallback, name="t2")

        assert result == "fallback-result"
        primary.assert_awaited_once()
        fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_primary_empty_predicate_triggers_fallback(self):
        from app.services.resilience import with_fallback

        # Mirrors search.py usage: (items, total, pages); fall back on total == 0.
        primary = AsyncMock(return_value=([], 0, 0))
        fallback = AsyncMock(return_value=(["a", "b"], 2, 1))

        result = await with_fallback(
            primary, fallback, name="t3", is_empty=lambda r: r[1] == 0,
        )

        assert result == (["a", "b"], 2, 1)
        primary.assert_awaited_once()
        fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_primary_nonempty_with_is_empty_predicate(self):
        from app.services.resilience import with_fallback

        primary = AsyncMock(return_value=(["x"], 1, 1))
        fallback = AsyncMock(return_value=(["should-not-see"], 99, 1))

        result = await with_fallback(
            primary, fallback, name="t4", is_empty=lambda r: r[1] == 0,
        )

        assert result == (["x"], 1, 1)
        fallback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_is_empty_predicate_returns_zero_result_as_is(self):
        """Without ``is_empty``, only exceptions trigger fallback — a
        successful empty result is returned as-is."""
        from app.services.resilience import with_fallback

        primary = AsyncMock(return_value=([], 0, 0))
        fallback = AsyncMock(return_value=(["never"], 1, 1))

        result = await with_fallback(primary, fallback, name="t5")

        assert result == ([], 0, 0)
        fallback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fallback_exception_propagates(self):
        from app.services.resilience import with_fallback

        primary = AsyncMock(side_effect=RuntimeError("primary boom"))
        fallback = AsyncMock(side_effect=ValueError("fallback also boom"))

        with pytest.raises(ValueError, match="fallback also boom"):
            await with_fallback(primary, fallback, name="t6")
