"""VIZ-209 -- the bounded-read layer, without a server.

The integration twin (``tests/integration/test_analytics_read_layer_postgres.py``)
drives the same layer through the real app on real PostgreSQL and Redis. What
belongs here is the part a server cannot make sharper: the cache identity, the
ETag rules, the timeout SQL, the 429 body, and the three properties the layer
must have when its dependencies are broken (Redis down, a limiter that
explodes, telemetry that raises).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional

import pytest

pytest.importorskip("fastapi")

from app.core import analytics_read_layer as layer  # noqa: E402
from app.core.analytics_errors import AnalyticsQueryError  # noqa: E402

pytestmark = pytest.mark.regression


# ── doubles ─────────────────────────────────────────────────────────────────


def _request(
    path: str = "/api/v1/analytics/coverage",
    query: Optional[list[tuple[str, str]]] = None,
    headers: Optional[dict] = None,
    app: Any = None,
):
    """A REAL ``starlette.requests.Request``.

    Not a stand-in: slowapi refuses anything else ("parameter `request` must be
    an instance of starlette.requests.Request"), and the layer reads
    ``query_params`` and ``headers`` through Starlette's own parsing -- which
    is where repeated parameters and header case actually get decided.
    """
    from urllib.parse import urlencode

    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "query_string": urlencode(query or [], doseq=True).encode(),
        "headers": [
            (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
        ],
        "route": SimpleNamespace(path=path),
        "state": {},
    }
    if app is not None:
        scope["app"] = app
    return Request(scope)


def _scope(project_id=None, allowed=None, denied: bool = False):
    from app.services.analytics_scope import AnalyticsScope

    return AnalyticsScope(
        project_id=project_id,
        allowed_project_ids=None if allowed is None else frozenset(allowed),
        release_ids=(),
        suite_names=(),
        days=30,
        denied=denied,
    )


def _digest(request, scope, route="/api/v1/analytics/coverage") -> str:
    return layer.cache_digest(route=route, request=request, scope=scope)


# ── the cache key ───────────────────────────────────────────────────────────


class TestCacheIdentity:
    def test_the_same_scope_twice_is_the_same_key(self):
        scope = _scope(project_id=uuid.uuid4())
        query = [("project_id", "p"), ("days", "30")]
        assert _digest(_request(query=query), scope) == _digest(_request(query=query), scope)

    def test_repeated_values_are_sorted_so_filter_order_is_not_a_second_entry(self):
        """``?release_id=a&release_id=b`` and the reverse are one question."""
        scope = _scope(project_id=uuid.uuid4())
        forward = _request(query=[("release_id", "a"), ("release_id", "b")])
        backward = _request(query=[("release_id", "b"), ("release_id", "a")])
        assert _digest(forward, scope) == _digest(backward, scope)

    def test_a_different_value_is_a_different_key(self):
        scope = _scope(project_id=uuid.uuid4())
        assert _digest(_request(query=[("days", "30")]), scope) != _digest(
            _request(query=[("days", "7")]), scope
        )

    def test_two_routes_never_share_an_entry(self):
        scope = _scope(project_id=uuid.uuid4())
        request = _request()
        assert _digest(request, scope, route="/api/v1/analytics/coverage") != _digest(
            request, scope, route="/api/v1/analytics/top-failing"
        )

    def test_the_schema_version_is_in_the_key(self, monkeypatch):
        """A payload cached in an older envelope shape is never served as the
        new one -- the defect ``dashboard_summary_v2`` already had to fix."""
        scope = _scope(project_id=uuid.uuid4())
        request = _request()
        before = _digest(request, scope)
        monkeypatch.setattr(layer, "ANALYTICS_READ_SCHEMA_VERSION", 99)
        assert _digest(request, scope) != before

    def test_the_accessible_project_set_is_part_of_the_key(self):
        """All-projects answers mix exactly the projects the caller can read."""
        a, b = uuid.uuid4(), uuid.uuid4()
        request = _request(query=[("days", "30")])
        one = _digest(request, _scope(allowed=[a]))
        two = _digest(request, _scope(allowed=[a, b]))
        admin = _digest(request, _scope(allowed=None))
        assert len({one, two, admin}) == 3

    def test_the_accessible_set_is_order_independent(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        request = _request()
        assert _digest(request, _scope(allowed=[a, b])) == _digest(
            request, _scope(allowed=[b, a])
        )

    def test_a_pinned_project_is_its_own_class(self):
        project = uuid.uuid4()
        assert layer.user_scope_class(_scope(project_id=project)) == ["project", str(project)]

    def test_a_denied_scope_does_not_share_the_answer_of_an_allowed_one(self):
        """``on_denied="empty"`` routes answer with their empty shape. That is
        not the same payload as the real one for the same query string."""
        request = _request(query=[("project_id", "p")])
        project = uuid.uuid4()
        assert _digest(request, _scope(project_id=project)) != _digest(
            request, _scope(project_id=project, denied=True)
        )


# ── the ETag ────────────────────────────────────────────────────────────────


class TestEtag:
    def test_the_same_bytes_and_identity_give_the_same_etag(self):
        assert layer.etag_for("d1", '{"a":1}') == layer.etag_for("d1", '{"a":1}')

    def test_different_bytes_give_a_different_etag(self):
        assert layer.etag_for("d1", '{"a":1}') != layer.etag_for("d1", '{"a":2}')

    def test_a_new_epoch_gives_a_new_etag_even_for_identical_bytes(self):
        """A bump means a new snapshot. A client holding the old tag must not
        be told "not modified" about it, even when the numbers did not move."""
        assert layer.etag_for("epoch-1", '{"a":1}') != layer.etag_for("epoch-2", '{"a":1}')

    @pytest.mark.parametrize(
        "header,expected",
        [
            (None, False),
            ("", False),
            ('"abc"', True),
            ('W/"abc"', True),
            ('"zzz", "abc"', True),
            ("*", True),
            ('"zzz"', False),
        ],
    )
    def test_if_none_match(self, header, expected):
        assert layer.if_none_match(header, '"abc"') is expected

    def test_a_304_carries_the_tag_and_no_body(self):
        etag = layer.etag_for("d", '{"a":1}')
        response = layer._conditional(
            _request(headers={"If-None-Match": etag}), etag=etag, body='{"a":1}', cached=True
        )
        assert response.status_code == 304
        assert response.headers["ETag"] == etag
        assert response.body == b""

    def test_a_non_matching_tag_is_the_full_body(self):
        etag = layer.etag_for("d", '{"a":1}')
        response = layer._conditional(
            _request(headers={"If-None-Match": '"stale"'}),
            etag=etag, body='{"a":1}', cached=True,
        )
        assert response.status_code == 200
        assert response.body == b'{"a":1}'
        assert response.headers["X-Analytics-Cache"] == "hit"


def test_render_is_byte_identical_to_fastapis_own_json():
    """The cached bytes ARE the response, so caching must not reformat a
    payload (a client diffing two responses would see a change that is not
    one)."""
    from fastapi.responses import JSONResponse

    payload = {"b": 1, "a": [1.5, None, "é"], "n": {"x": True}}
    assert layer.render(payload).encode("utf-8") == JSONResponse(payload).body


# ── statement timeout ───────────────────────────────────────────────────────


class _FakeSession:
    def __init__(self, dialect: str = "postgresql", raises: Optional[Exception] = None) -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.statements: list[str] = []
        self.rolled_back = False
        self._raises = raises

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        if self._raises is not None:
            raise self._raises
        return None

    async def rollback(self):
        self.rolled_back = True


class TestStatementTimeout:
    @pytest.mark.asyncio
    async def test_it_is_set_local_inside_the_transaction(self):
        """``SET`` (session level) would survive the COMMIT and ride the
        pooled connection into whatever runs next on it -- an ingestion batch
        would inherit a 5 s ceiling nobody asked for."""
        db = _FakeSession()
        assert await layer.apply_statement_timeout(db, 5000) is True
        assert db.statements == ["SET LOCAL statement_timeout = 5000"]
        assert not any(
            s.startswith("SET statement_timeout") for s in db.statements
        ), "a session-level SET leaks to the next user of this connection"

    @pytest.mark.asyncio
    async def test_a_non_postgres_session_is_left_alone(self):
        db = _FakeSession(dialect="sqlite")
        assert await layer.apply_statement_timeout(db, 5000) is False
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_the_value_cannot_carry_sql(self):
        db = _FakeSession()
        with pytest.raises((ValueError, TypeError)):
            await layer.apply_statement_timeout(db, "5000; DROP TABLE test_runs")  # type: ignore[arg-type]
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_a_read_is_not_500d_by_its_own_guard(self):
        db = _FakeSession(raises=RuntimeError("no"))
        assert await layer.apply_statement_timeout(db, 5000) is False

    @pytest.mark.parametrize("attribute", ["sqlstate", "pgcode"])
    def test_57014_is_recognised_through_the_driver_wrapper(self, attribute):
        cancelled = type("Cancelled", (Exception,), {attribute: "57014"})()
        wrapped = type("DBAPIError", (Exception,), {})()
        wrapped.orig = cancelled  # type: ignore[attr-defined]
        assert layer.is_statement_timeout(wrapped) is True

    def test_another_database_error_is_not_a_timeout(self):
        other = type("Cancelled", (Exception,), {"sqlstate": "23505"})()
        wrapped = type("DBAPIError", (Exception,), {})()
        wrapped.orig = other  # type: ignore[attr-defined]
        assert layer.is_statement_timeout(wrapped) is False
        assert layer.is_statement_timeout(RuntimeError("boom")) is False

    def test_a_cycle_in_the_cause_chain_terminates(self):
        first = RuntimeError("a")
        second = RuntimeError("b")
        first.__cause__ = second
        second.__cause__ = first
        assert layer.is_statement_timeout(first) is False


# ── rate limit ──────────────────────────────────────────────────────────────


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_over_the_limit_is_429_with_retry_after(self, monkeypatch):
        from app.main import app

        route = f"/api/v1/analytics/probe-{uuid.uuid4().hex[:8]}"
        user = SimpleNamespace(id=uuid.uuid4())

        async def _call():
            # slowapi reads ``request.app.state.limiter``.
            request = _request(path=route, app=app)
            await layer.enforce_rate_limit(request, user, route=route, limit="2/minute")

        await _call()
        await _call()
        with pytest.raises(AnalyticsQueryError) as caught:
            await _call()
        error = caught.value
        assert error.status_code == 429
        assert error.code == "rate_limited"
        assert int(error.headers["Retry-After"]) > 0

    @pytest.mark.asyncio
    async def test_two_principals_do_not_share_a_budget(self):
        from app.main import app

        route = f"/api/v1/analytics/probe-{uuid.uuid4().hex[:8]}"

        async def _call(user):
            request = _request(path=route, app=app)
            await layer.enforce_rate_limit(request, user, route=route, limit="1/minute")

        await _call(SimpleNamespace(id=uuid.uuid4()))
        await _call(SimpleNamespace(id=uuid.uuid4()))  # a different user, own budget

    @pytest.mark.asyncio
    async def test_the_limiter_is_built_once_per_route_and_limit(self):
        """slowapi registers a limit under ``module.name`` and APPENDS: a
        limiter rebuilt per request makes request N evaluate N limits against
        one counter (``app.main._build_auth_limiters``)."""
        from app.main import app

        route = f"/api/v1/analytics/probe-{uuid.uuid4().hex[:8]}"
        user = SimpleNamespace(id=uuid.uuid4())
        for _ in range(5):
            request = _request(path=route, app=app)
            await layer.enforce_rate_limit(request, user, route=route, limit="20/minute")
        built = [key for key in layer._LIMITERS if key[0] == route]
        assert built == [(route, "20/minute")]

    @pytest.mark.asyncio
    async def test_a_broken_limiter_does_not_break_the_read(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("storage gone")

        monkeypatch.setattr(layer, "_limiter_for", _boom)
        await layer.enforce_rate_limit(
            _request(), SimpleNamespace(id=uuid.uuid4()), route="/r", limit="1/minute"
        )

    @pytest.mark.asyncio
    async def test_no_limit_means_no_limiter(self, monkeypatch):
        monkeypatch.setattr(
            layer, "_limiter_for", lambda *_a, **_kw: pytest.fail("built a limiter")
        )
        await layer.enforce_rate_limit(_request(), None, route="/r", limit=None)

    def test_the_principal_is_the_user_not_the_token(self):
        uid = uuid.uuid4()
        assert layer.principal_of(_request(), SimpleNamespace(id=uid)) == f"user:{uid}"

    def test_the_three_story_routes_carry_a_limit_before_they_exist(self):
        """VIZ-203/205/208 land in another change; the limit is configured for
        them here so the route is never live without one."""
        for route in (
            "/api/v1/analytics/chart-data",
            "/api/v1/analytics/chart-data/rows",
            "/api/v1/analytics/heatmap",
        ):
            assert layer.rate_limit_for(route, None)


# ── metrics ─────────────────────────────────────────────────────────────────


def _hist_count(metric, **labels) -> float:
    child = metric.labels(**labels) if labels else metric
    return sum(b.get() for b in child._buckets)


class TestMetrics:
    def test_every_outcome_can_be_observed(self):
        from app.core.metrics import analytics_query_duration_seconds

        for outcome in layer.ANALYTICS_OUTCOMES:
            before = _hist_count(
                analytics_query_duration_seconds, route="/t", outcome=outcome
            )
            layer.observe("/t", outcome, 0.0)
            assert _hist_count(
                analytics_query_duration_seconds, route="/t", outcome=outcome
            ) == before + 1, "a 0.0-second read is still a read"

    def test_telemetry_never_breaks_a_read(self, monkeypatch):
        from app.core import metrics

        class _Boom:
            def labels(self, **_kw):
                raise RuntimeError("registry exploded")

        monkeypatch.setattr(metrics, "analytics_query_duration_seconds", _Boom())
        layer.observe("/t", "miss", 1.0)  # must not raise

    def test_the_label_is_the_route_template_not_the_path(self):
        """A metric label carrying an id is an unbounded time series."""
        request = _request(path="/api/v1/runs/9e1f/intelligence")
        request.scope["route"] = SimpleNamespace(path="/api/v1/runs/{run_id}/intelligence")
        assert layer.route_of(request) == "/api/v1/runs/{run_id}/intelligence"

    def test_a_request_that_matched_no_route_still_has_a_label(self):
        request = _request(path="/api/v1/analytics/coverage")
        request.scope.pop("route")
        assert layer.route_of(request) == "/api/v1/analytics/coverage"


# ── the wrapper, end to end without a server ───────────────────────────────


class _Recorder:
    """A stand-in for a decorated endpoint."""

    def __init__(self, payload: Any = None, raises: Optional[Exception] = None) -> None:
        self.calls = 0
        self.payload = payload if payload is not None else {"value": 1}
        self.raises = raises

    async def __call__(self, *, scope=None, db=None):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.payload


async def _serve(endpoint, request, *, db=None, scope=None, endpoint_kwargs=None, **kwargs):
    return await layer._serve(
        endpoint,
        (),
        {"scope": scope, "db": db, **(endpoint_kwargs or {})},
        request=request,
        db=db,
        user=kwargs.pop("user", None) or SimpleNamespace(id=uuid.uuid4()),
        namespace=kwargs.pop("namespace", "unit"),
        ttl=kwargs.pop("ttl", 60),
        cache=kwargs.pop("cache", True),
        rate_limit=kwargs.pop("rate_limit", None),
        timeout_ms=kwargs.pop("timeout_ms", 5000),
        **kwargs,
    )


class _FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis down")
        self.store[key] = value

    async def incr(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._keys: list[str] = []

    def incr(self, key):
        self._keys.append(key)

    async def execute(self, raise_on_error: bool = True):
        return [await self._redis.incr(key) for key in self._keys]


@pytest.mark.asyncio
class TestWrapper:
    async def test_a_second_identical_request_is_served_from_the_cache(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder()
        scope = _scope(project_id=uuid.uuid4())
        request = _request(query=[("days", "30")])

        first = await _serve(endpoint, request, db=_FakeSession(), scope=scope)
        second = await _serve(endpoint, request, db=_FakeSession(), scope=scope)

        assert endpoint.calls == 1, "the second request re-ran the query"
        assert first.headers["X-Analytics-Cache"] == "miss"
        assert second.headers["X-Analytics-Cache"] == "hit"
        assert second.headers["ETag"] == first.headers["ETag"]
        assert second.body == first.body

    async def test_if_none_match_on_a_cached_entry_is_304(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder()
        scope = _scope(project_id=uuid.uuid4())

        first = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        again = await _serve(
            endpoint,
            _request(headers={"If-None-Match": first.headers["ETag"]}),
            db=_FakeSession(),
            scope=scope,
        )

        assert again.status_code == 304
        assert again.body == b""
        assert again.headers["ETag"] == first.headers["ETag"]

    async def test_an_epoch_bump_is_a_miss_with_a_new_etag(self, monkeypatch):
        from app.services.cache_service import bump_analytics_epoch

        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        project = uuid.uuid4()
        scope = _scope(project_id=project)
        endpoint = _Recorder()

        first = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        await bump_analytics_epoch(str(project))
        second = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)

        assert endpoint.calls == 2
        assert second.headers["X-Analytics-Cache"] == "miss"
        assert second.body == first.body
        assert second.headers["ETag"] != first.headers["ETag"], (
            "the payload happens to be identical, but it is a NEW snapshot: a "
            "client revalidating with the old tag must not get a 304"
        )

    async def test_redis_down_is_served_from_the_database(self, monkeypatch):
        redis = _FakeRedis(broken=True)
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder()
        scope = _scope(project_id=uuid.uuid4())

        first = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        second = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)

        assert first.status_code == 200 and second.status_code == 200
        assert endpoint.calls == 2, "an unreadable epoch must bypass the cache"
        assert first.headers["ETag"] == second.headers["ETag"], (
            "the ETag is still well defined without Redis"
        )

    async def test_one_projects_entries_cannot_be_served_to_another(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder()
        a = await _serve(endpoint, _request(), db=_FakeSession(), scope=_scope(project_id=uuid.uuid4()))
        b = await _serve(endpoint, _request(), db=_FakeSession(), scope=_scope(project_id=uuid.uuid4()))
        assert endpoint.calls == 2
        assert a.headers["ETag"] != b.headers["ETag"]

    async def test_the_timeout_is_a_503_with_the_story_s_code(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        cancelled = type("Cancelled", (Exception,), {"sqlstate": "57014"})()
        wrapped = type("DBAPIError", (Exception,), {})()
        wrapped.orig = cancelled  # type: ignore[attr-defined]
        db = _FakeSession()

        with pytest.raises(AnalyticsQueryError) as caught:
            await _serve(
                _Recorder(raises=wrapped), _request(), db=db, scope=_scope(project_id=uuid.uuid4())
            )

        error = caught.value
        assert (error.status_code, error.code) == (503, "analytics_timeout")
        assert int(error.headers["Retry-After"]) > 0
        assert "Retry" in error.message
        assert db.rolled_back, (
            "an aborted transaction left on the connection makes every later "
            "statement fail with 25P02"
        )

    async def test_a_failed_read_is_not_cached(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder(raises=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await _serve(endpoint, _request(), db=_FakeSession(), scope=_scope(project_id=uuid.uuid4()))
        assert not [k for k in redis.store if k.startswith("analytics:unit")]

    async def test_every_outcome_reaches_the_histogram(self, monkeypatch):
        from app.core.metrics import analytics_query_duration_seconds

        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        route = "/api/v1/analytics/coverage"
        scope = _scope(project_id=uuid.uuid4())
        before = {
            outcome: _hist_count(analytics_query_duration_seconds, route=route, outcome=outcome)
            for outcome in layer.ANALYTICS_OUTCOMES
        }
        endpoint = _Recorder()
        await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        with pytest.raises(RuntimeError):
            await _serve(
                _Recorder(raises=RuntimeError("boom")), _request(query=[("days", "7")]),
                db=_FakeSession(), scope=scope,
            )
        after = {
            outcome: _hist_count(analytics_query_duration_seconds, route=route, outcome=outcome)
            for outcome in layer.ANALYTICS_OUTCOMES
        }
        assert after["miss"] == before["miss"] + 1
        assert after["hit"] == before["hit"] + 1
        assert after["error"] == before["error"] + 1

    async def test_a_direct_call_bypasses_the_layer(self):
        """Regression tests call these handlers as plain functions
        (``analytics.flaky_scores(limit=50, scope=..., db=...)``); the layer is
        a transport concern and must not change what they get back."""

        @layer.analytics_read(namespace="unit_direct")
        async def endpoint(*, scope=None, db=None):
            return {"value": 1}

        assert await endpoint(scope=None, db=None) == {"value": 1}

    async def test_the_endpoints_own_signature_survives_the_wrapper(self):
        import inspect

        @layer.analytics_read(namespace="unit_signature")
        async def endpoint(limit: int = 20, *, scope=None, db=None):
            """docstring kept"""
            return {}

        params = inspect.signature(endpoint).parameters
        assert "limit" in params and "scope" in params
        assert layer._REQ in params and layer._DB in params
        assert endpoint.__name__ == "endpoint"
        assert "docstring kept" in inspect.getsource(endpoint), (
            "source-reading tests (test_phase3_systemic_clusters) must still "
            "see the endpoint, not the wrapper"
        )


def test_only_analytics_reads_wear_the_decorator():
    """It must never reach ingestion: a 5 s statement timeout on an ingestion
    transaction would fail a batch halfway through."""
    from app.main import app

    decorated = [
        (route.path, sorted(route.methods or ()))
        for route in app.routes
        if getattr(getattr(route, "endpoint", None), "__analytics_read__", None)
    ]
    assert decorated, "the layer is applied to no route at all"
    for path, methods in decorated:
        assert methods == ["GET"], f"{path} is not a read"
        assert path.startswith("/api/v1/analytics/"), path
    for path, _ in decorated:
        assert not any(
            part in path for part in ("/ingest", "/stream", "/live", "/ws")
        ), f"{path} is an ingestion path"


# ── VIZ-209 fix round: the cache identity is the RESOLVED request ──────────
#
# Reviewers' finding (BLOCKER): ``canonical_scope`` sorted the values of every
# repeated parameter. ``group_by`` is order-significant -- the first dimension
# is the x axis and the second keys the series -- so two DIFFERENT charts
# (transposes of each other) hashed to one digest, one cache entry and one
# ETag; a member was served an admin's transposed chart and 304'd on
# revalidation. And a repeated SCALAR binds "last wins" while keying "sorted",
# so a co-tenant could write the entry another caller reads.

#: The two orders, as a caller sends them.
_GROUP_BY_A = [("metric", "executions"), ("group_by", "status"), ("group_by", "environment")]
_GROUP_BY_B = [("metric", "executions"), ("group_by", "environment"), ("group_by", "status")]


def _chart_identity(resolved: dict) -> tuple:
    """A stand-in for ``chart_data_service.cache_identity_parts``: the parts
    of the RESOLVED request, group_by order preserved."""
    return (
        f"metric={resolved.get('metric')}",
        "group_by=" + ",".join(resolved.get("group_by") or ()),
    )


class _ChartRecorder:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list = []

    async def __call__(self, *, scope=None, db=None, metric=None, group_by=None):
        self.calls += 1
        self.seen.append(tuple(group_by or ()))
        return {"dimensions": list(group_by or ()), "call": self.calls}


class TestOrderSignificantIdentity:
    def test_the_two_group_by_orders_are_two_keys(self):
        """Without a hook the fallback keeps the ORDER of an order-significant
        parameter. Sorting them is what made two charts one entry."""
        scope = _scope(project_id=uuid.uuid4())
        forward = _digest(_request(query=_GROUP_BY_A), scope)
        backward = _digest(_request(query=_GROUP_BY_B), scope)
        assert forward != backward, (
            "group_by=status,environment and group_by=environment,status are "
            "different charts and must not share a cache key or an ETag"
        )

    def test_an_order_insignificant_filter_is_still_one_question(self):
        """``release_id`` is an OR set: the two orders ARE one answer, and
        keying them apart would double every entry for nothing."""
        scope = _scope(project_id=uuid.uuid4())
        assert _digest(_request(query=[("release_id", "a"), ("release_id", "b")]), scope) == (
            _digest(_request(query=[("release_id", "b"), ("release_id", "a")]), scope)
        )

    def test_the_identity_hook_replaces_the_query_string(self):
        scope = _scope(project_id=uuid.uuid4())
        request = _request(query=_GROUP_BY_A)
        forward = layer.cache_digest(
            route="/api/v1/analytics/chart-data", request=request, scope=scope,
            identity_parts=_chart_identity(
                {"metric": "executions", "group_by": ["status", "environment"]}
            ),
        )
        backward = layer.cache_digest(
            route="/api/v1/analytics/chart-data", request=request, scope=scope,
            identity_parts=_chart_identity(
                {"metric": "executions", "group_by": ["environment", "status"]}
            ),
        )
        assert forward != backward

    def test_the_hook_ignores_a_parameter_the_route_does_not_answer(self):
        """Keying on the resolved request also stops cache amplification: 25
        spellings of an ignored parameter were 25 entries for one answer."""
        scope = _scope(project_id=uuid.uuid4())
        parts = _chart_identity({"metric": "executions", "group_by": ["day"]})
        plain = layer.cache_digest(
            route="/r", request=_request(query=[("metric", "executions")]),
            scope=scope, identity_parts=parts,
        )
        junk = layer.cache_digest(
            route="/r", request=_request(query=[("metric", "executions"), ("zzz", "7")]),
            scope=scope, identity_parts=parts,
        )
        assert plain == junk

    def test_the_real_route_supplies_the_services_own_identity(self):
        """The seam, not a second recipe: ``/chart-data`` hands VIZ-209 what
        ``chart_data_service`` says the request IS."""
        from app.routers.analytics import _chart_data_identity
        from app.services import chart_data_service as svc

        scope = _scope(project_id=uuid.uuid4())
        resolved = {
            "metric": "executions", "group_by": ["status", "environment"],
            "top_n": None, "scope": scope,
        }
        spec = svc.parse_chart_spec("executions", ["status", "environment"], None, scope=scope)
        assert _chart_data_identity(resolved) == svc.cache_identity_parts(scope, spec)
        transposed = dict(resolved, group_by=["environment", "status"])
        assert _chart_data_identity(transposed) != _chart_data_identity(resolved)


@pytest.mark.asyncio
class TestTheSecondChartIsNotTheFirst:
    async def test_a_transposed_chart_is_not_served_the_first_charts_body(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())
        endpoint = _ChartRecorder()

        first = await _serve(
            endpoint, _request(query=_GROUP_BY_A), db=_FakeSession(), scope=scope,
            endpoint_kwargs={"metric": "executions", "group_by": ["status", "environment"]},
            identity=_chart_identity,
        )
        second = await _serve(
            endpoint, _request(query=_GROUP_BY_B), db=_FakeSession(), scope=scope,
            endpoint_kwargs={"metric": "executions", "group_by": ["environment", "status"]},
            identity=_chart_identity,
        )

        assert endpoint.calls == 2, (
            "the second, DIFFERENT chart was served from the first chart's "
            f"cache entry -- body {second.body!r}"
        )
        assert first.headers["ETag"] != second.headers["ETag"]
        assert b'["status","environment"]' in first.body
        assert b'["environment","status"]' in second.body
        assert endpoint.seen == [("status", "environment"), ("environment", "status")]

    async def test_a_revalidation_of_the_transposed_chart_is_not_a_304(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())
        endpoint = _ChartRecorder()

        first = await _serve(
            endpoint, _request(query=_GROUP_BY_A), db=_FakeSession(), scope=scope,
            endpoint_kwargs={"metric": "executions", "group_by": ["status", "environment"]},
            identity=_chart_identity,
        )
        again = await _serve(
            endpoint,
            _request(query=_GROUP_BY_B, headers={"If-None-Match": first.headers["ETag"]}),
            db=_FakeSession(), scope=scope,
            endpoint_kwargs={"metric": "executions", "group_by": ["environment", "status"]},
            identity=_chart_identity,
        )
        assert again.status_code == 200, (
            "the other order's ETag matched, so a client holding the first "
            "chart was told its transpose was 'not modified'"
        )


class TestRepeatedScalars:
    """``?metric=failed&metric=passed`` binds LAST WINS. A key built from the
    sorted values is the same for both orders, so a co-tenant sending the
    reverse writes the entry the first caller reads."""

    @pytest.mark.parametrize(
        "query,param",
        [
            ([("metric", "failed"), ("metric", "passed")], "metric"),
            ([("days", "5"), ("days", "300")], "days"),
            ([("top_n", "5"), ("top_n", "500")], "top_n"),
        ],
    )
    def test_a_repeated_scalar_is_refused(self, query, param):
        single = frozenset({"metric", "days", "top_n"})
        with pytest.raises(AnalyticsQueryError) as caught:
            layer.reject_repeated_scalars(_request(query=query), single)
        error = caught.value
        assert (error.status_code, error.code) == (422, "repeated_parameter")
        assert error.param == param

    def test_sending_it_once_is_fine(self):
        layer.reject_repeated_scalars(
            _request(query=[("metric", "failed"), ("group_by", "day")]),
            frozenset({"metric", "days"}),
        )

    def test_a_repeatable_parameter_is_not_refused(self):
        layer.reject_repeated_scalars(
            _request(query=[("release_id", "a"), ("release_id", "b")]),
            frozenset({"metric", "days"}),
        )

    def test_project_id_keeps_its_own_rule(self):
        """``parse_project_id(..., repeated=…)`` answers ``project_single_valued``
        with the comparison the caller actually wants (``group_by=project``).
        The generic refusal must not preempt it."""
        layer.reject_repeated_scalars(
            _request(query=[("project_id", "a"), ("project_id", "b")]),
            frozenset({"project_id", "metric"}),
        )

    def test_the_route_declares_which_is_which(self):
        """Read off the signature, the scope dependency included -- ``days``
        and ``release_id`` are as much chart-data's parameters as ``metric``."""
        from app.routers import analytics

        single, repeatable = layer.declared_params(analytics.chart_data.__wrapped__)
        assert {"metric", "top_n", "days", "project_id"} <= single
        assert {"group_by", "release_id", "suite_name"} <= repeatable
        assert not (single & repeatable)

    def test_an_authentication_header_is_not_read_as_a_query_parameter(self):
        """The auth chain declares ``x_api_key: Optional[str] = Header(...)``;
        reading that as a query parameter would refuse a query string that
        merely shares the name."""
        from app.routers import analytics

        single, repeatable = layer.declared_params(analytics.chart_data.__wrapped__)
        assert "x-api-key" not in single and "x_api_key" not in single
        assert "token" not in (single | repeatable)


# ── no scope, no cache ─────────────────────────────────────────────────────


def _counter(metric, **labels) -> float:
    return metric.labels(**labels)._value.get()


@pytest.mark.asyncio
class TestFailClosedWithoutAScope:
    async def test_a_handler_that_hands_over_no_scope_is_never_cached(self, monkeypatch):
        """``_scope_of`` returns None when the scope parameter is not named
        ``scope``. Every caller then shared ONE cache class -- a cross-tenant
        leak, with cache on by default."""
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder()

        await _serve(endpoint, _request(), db=_FakeSession(), scope=None)
        await _serve(endpoint, _request(), db=_FakeSession(), scope=None)

        assert endpoint.calls == 2, "an unscoped read was served from the cache"
        assert not [key for key in redis.store if key.startswith("analytics:unit")], (
            "an unscoped read wrote an entry every other caller would read"
        )

    async def test_the_bypass_is_counted(self, monkeypatch):
        from app.core.metrics import analytics_read_degraded_total

        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        before = _counter(analytics_read_degraded_total, reason="no_scope")
        await _serve(_Recorder(), _request(), db=_FakeSession(), scope=None)
        assert _counter(analytics_read_degraded_total, reason="no_scope") == before + 1

    async def test_a_scoped_read_is_still_cached(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        endpoint = _Recorder()
        scope = _scope(project_id=uuid.uuid4())
        await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        assert endpoint.calls == 1


def test_every_decorated_route_has_an_analytics_scope():
    """The guard behind the fail-closed rule.

    A decorated route whose scope reaches the layer under another name is not
    a cache miss, it is every caller sharing one entry. The authorization
    ratchet already knows how to see a REAL ``analytics_scope`` sub-dependency
    (marker attribute plus the factory's code object), so this asks it.
    """
    from fastapi.routing import APIRoute

    from app.main import app
    from tests import test_architectural_authorization as ratchet

    decorated = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and getattr(getattr(route, "endpoint", None), "__analytics_read__", None)
    ]
    assert decorated, "the layer is applied to no route at all"
    missing = []
    for route in decorated:
        _residual, has_scope = ratchet._ids_outside_analytics_scope(route)
        if not has_scope:
            missing.append(route.path)
        elif "scope" not in route.endpoint.__wrapped__.__code__.co_varnames:
            missing.append(f"{route.path} (the scope is not the `scope` parameter)")
    assert missing == [], (
        "these cached routes hand the read layer no AnalyticsScope, so every "
        f"caller shares one cache class: {missing}"
    )


# ── the limit is charged BEFORE the scope is resolved ──────────────────────


class TestTheGateRunsFirst:
    def test_the_gate_is_ordered_ahead_of_the_scope_dependency(self):
        """FastAPI awaits ``dependant.dependencies`` in signature order, so
        "before the scope" is a fact about this list and nothing else."""
        from fastapi.routing import APIRoute

        from app.main import app
        from tests import test_architectural_authorization as ratchet

        checked = 0
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            if not getattr(getattr(route, "endpoint", None), "__analytics_read__", None):
                continue
            checked += 1
            calls = [dep.call for dep in route.dependant.dependencies]
            gate_at = next(
                (i for i, call in enumerate(calls)
                 if getattr(call, "__name__", "").startswith("analytics_gate_")),
                None,
            )
            scope_at = next(
                (i for i, call in enumerate(calls)
                 if ratchet._is_analytics_scope_dependency(call)),
                None,
            )
            assert gate_at is not None, f"{route.path} has no rate-limit gate"
            assert scope_at is not None, f"{route.path} has no analytics_scope"
            assert gate_at < scope_at, (
                f"{route.path}: the limit is charged after the scope, so every "
                "403/404/422 is free, unlimited database work"
            )
        assert checked, "no decorated route was checked"

    @pytest.mark.asyncio
    async def test_a_request_refused_after_the_gate_still_costs_the_principal(self):
        """Proven cost of the old order: 12 forbidden and 6 malformed requests
        were served while the principal was over its limit."""
        from app.main import app

        route = f"/api/v1/analytics/probe-{uuid.uuid4().hex[:8]}"
        user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeSession()

        async def _open(query=None):
            await layer.open_read(
                _request(path=route, query=query, app=app), user, db,
                rate_limit="2/minute", timeout_ms=5000,
                single_valued=frozenset({"metric"}),
            )

        # Two requests that are refused AFTER the gate still spend the budget...
        for _ in range(2):
            with pytest.raises(AnalyticsQueryError) as refused:
                await _open([("metric", "failed"), ("metric", "passed")])
            assert refused.value.status_code == 422

        # ...so the third request, malformed or not, is a 429.
        with pytest.raises(AnalyticsQueryError) as caught:
            await _open()
        assert (caught.value.status_code, caught.value.code) == (429, "rate_limited")

    @pytest.mark.asyncio
    async def test_the_gate_bounds_the_scopes_own_queries(self):
        """``resolve_project_scope`` and the release ``IN`` lookup run inside
        the scope dependency. Applying the timeout in the handler left them
        unbounded."""
        db = _FakeSession()
        await layer.open_read(
            _request(), SimpleNamespace(id=uuid.uuid4()), db,
            rate_limit=None, timeout_ms=5000,
        )
        assert db.statements == ["SET LOCAL statement_timeout = 5000"]

    @pytest.mark.asyncio
    async def test_the_layer_does_not_charge_the_same_request_twice(self, monkeypatch):
        """The gate marks the request; ``_serve`` must not count it again."""
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        calls = []

        async def _spy(*args, **kwargs):
            calls.append(kwargs.get("route"))

        monkeypatch.setattr(layer, "enforce_rate_limit", _spy)
        request = _request()
        request.state.analytics_read_opened = True
        await _serve(_Recorder(), request, db=_FakeSession(), scope=_scope(project_id=uuid.uuid4()))
        assert calls == []


# ── a slow Redis is a miss, not a stall ────────────────────────────────────


class _SlowRedis(_FakeRedis):
    """Alive, answering, far too late -- the case an ``except Exception``
    around Redis never catches."""

    def __init__(self, delay: float = 1.0) -> None:
        super().__init__()
        self.delay = delay

    async def get(self, key):
        import asyncio

        await asyncio.sleep(self.delay)
        return self.store.get(key)


@pytest.mark.asyncio
class TestSlowRedis:
    async def test_a_slow_epoch_read_does_not_hold_the_request(self, monkeypatch):
        """Two unbounded reads at the client's 5 s socket timeout put up to
        10 s in front of a database the request never needed to wait for."""
        import time as _time

        from app.services import cache_service

        monkeypatch.setattr(cache_service, "ANALYTICS_READ_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _SlowRedis(1.0))
        endpoint = _Recorder()

        started = _time.perf_counter()
        response = await _serve(
            endpoint, _request(), db=_FakeSession(), scope=_scope(project_id=uuid.uuid4())
        )
        elapsed = _time.perf_counter() - started

        assert response.status_code == 200
        assert endpoint.calls == 1, "the read must fall through to the database"
        assert elapsed < 0.5, f"the request waited {elapsed:.2f}s on Redis"

    async def test_giving_up_on_redis_is_counted(self, monkeypatch):
        from app.core.metrics import analytics_read_degraded_total
        from app.services import cache_service

        monkeypatch.setattr(cache_service, "ANALYTICS_READ_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _SlowRedis(1.0))
        before = _counter(analytics_read_degraded_total, reason="epoch_timeout")
        assert await cache_service.get_analytics_epoch(str(uuid.uuid4())) is None
        assert _counter(analytics_read_degraded_total, reason="epoch_timeout") == before + 1

    async def test_a_slow_lookup_is_a_miss(self, monkeypatch):
        from app.core.metrics import analytics_read_degraded_total
        from app.services import cache_service

        monkeypatch.setattr(cache_service, "ANALYTICS_READ_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _SlowRedis(1.0))
        before = _counter(analytics_read_degraded_total, reason="cache_timeout")
        assert await cache_service.cache_get("unit", None, epoch=1, k="x") is None
        assert _counter(analytics_read_degraded_total, reason="cache_timeout") == before + 1


# ── a cache hit does not claim it was generated now ────────────────────────


def _meta_payload(as_of: str = "2026-09-22T10:00:00Z") -> dict:
    return {
        "series": [{"key": "all", "points": [{"x": "2026-09-22", "y": 1, "n": 1}]}],
        "meta": {"schema_version": 2, "as_of": as_of, "generated_at": as_of, "days": 30},
    }


@pytest.mark.asyncio
class TestCachedTimestamps:
    async def test_a_hit_restamps_generated_at_and_keeps_as_of(self, monkeypatch):
        """A hit replayed the stored bytes verbatim, so a five-minute-old body
        claimed it had just been generated. ``as_of`` is when the numbers were
        READ and must not move; ``generated_at`` is this response's."""
        import json as _json

        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())
        endpoint = _Recorder(payload=_meta_payload())

        miss = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        hit = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)

        assert endpoint.calls == 1 and hit.headers["X-Analytics-Cache"] == "hit"
        served = _json.loads(hit.body)["meta"]
        assert served["as_of"] == _json.loads(miss.body)["meta"]["as_of"], (
            "as_of is when the numbers were read; a hit must not move it"
        )
        assert served["as_of"] < served["generated_at"], (
            f"a cached body still claims generated_at={served['generated_at']}"
        )

    async def test_the_etag_still_304s_after_restamping(self, monkeypatch):
        """The ETag is over the payload WITHOUT ``generated_at``, so
        restamping cannot invalidate it: revalidation inside the TTL is still
        a 304 with no body."""
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())
        endpoint = _Recorder(payload=_meta_payload())

        miss = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        again = await _serve(
            endpoint, _request(headers={"If-None-Match": miss.headers["ETag"]}),
            db=_FakeSession(), scope=scope,
        )
        assert again.status_code == 304 and again.body == b""
        assert again.headers["ETag"] == miss.headers["ETag"]

    async def test_the_key_order_of_the_body_does_not_change_on_a_hit(self, monkeypatch):
        """``generated_at`` is rewritten IN PLACE. Appending it instead would
        reorder the JSON a client diffs between a miss and a hit."""
        import json as _json

        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())
        endpoint = _Recorder(payload=_meta_payload())
        miss = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        hit = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        assert list(_json.loads(miss.body)["meta"]) == list(_json.loads(hit.body)["meta"])

    async def test_the_validator_ignores_generated_at_and_nothing_else(self, monkeypatch):
        """Two reads of the SAME numbers, built a minute apart, must share a
        validator -- otherwise the tag moves on every response and no client
        can ever revalidate. Two reads of DIFFERENT numbers must not."""
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())

        class _Clocked:
            """Same numbers, a later ``generated_at`` every call."""

            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, *, scope=None, db=None):
                self.calls += 1
                payload = _meta_payload()
                payload["meta"]["generated_at"] = f"2026-09-22T10:0{self.calls}:00Z"
                return payload

        endpoint = _Clocked()
        one = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope, cache=False)
        two = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope, cache=False)
        assert endpoint.calls == 2
        assert one.headers["ETag"] == two.headers["ETag"], (
            "generated_at is in the ETag material, so the validator changes on "
            "every response and If-None-Match can never match"
        )
        assert one.body != two.body, "the two responses really do differ"

        moved = await _serve(
            _Recorder(payload=_meta_payload("2026-09-22T11:00:00Z")),
            _request(), db=_FakeSession(), scope=scope, cache=False,
        )
        assert moved.headers["ETag"] != one.headers["ETag"], (
            "as_of moved: these are different numbers and must not share a tag"
        )

    async def test_a_payload_without_an_envelope_is_untouched(self, monkeypatch):
        redis = _FakeRedis()
        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
        scope = _scope(project_id=uuid.uuid4())
        endpoint = _Recorder(payload={"value": 1})
        miss = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        hit = await _serve(endpoint, _request(), db=_FakeSession(), scope=scope)
        assert hit.body == miss.body


def test_a_new_as_of_is_a_new_etag():
    """Only ``generated_at`` is out of the ETag material. Recomputed numbers
    read at a different moment ARE a different answer."""
    from app.services.analytics_meta import without_generated_at

    one = _meta_payload("2026-09-22T10:00:00Z")
    two = _meta_payload("2026-09-22T10:05:00Z")
    assert layer.etag_for("d", layer.render(without_generated_at(one))) != (
        layer.etag_for("d", layer.render(without_generated_at(two)))
    )


# ── headers, and the guard that fails open ─────────────────────────────────


class TestHeaders:
    def test_every_response_varies_on_the_credential(self):
        """The body is one caller's data horizon. ``private`` tells a shared
        cache not to store it; ``Vary`` names what the answer depends on for
        the caches that store private responses anyway."""
        response = layer._conditional(_request(), etag='"e"', body="{}", cached=False)
        assert response.headers["Vary"] == "Authorization, Cookie"
        assert "private" in response.headers["Cache-Control"]

    def test_a_304_varies_too(self):
        response = layer._conditional(
            _request(headers={"If-None-Match": '"e"'}), etag='"e"', body="{}", cached=True
        )
        assert response.status_code == 304
        assert response.headers["Vary"] == "Authorization, Cookie"

    def test_the_cache_header_is_a_diagnostic_and_not_sent_in_production(self, monkeypatch):
        """In production it is an oracle: the class is shared by every caller
        with the same project horizon, so a hit on a request you never made
        says someone with your access made it."""
        from app.core import config

        monkeypatch.setattr(type(config.settings), "is_production", property(lambda _s: True))
        assert layer.cache_header_enabled() is False
        assert "X-Analytics-Cache" not in layer._conditional(
            _request(), etag='"e"', body="{}", cached=True
        ).headers
        monkeypatch.setattr(type(config.settings), "is_production", property(lambda _s: False))
        assert layer.cache_header_enabled() is True

    def test_the_browser_can_read_the_etag_and_the_cache_header(self):
        """A header the CORS policy does not expose does not exist to the SPA:
        without ``ETag`` no revalidation is possible at all."""
        from starlette.middleware.cors import CORSMiddleware

        from app.main import app

        exposed: set = set()
        for middleware in app.user_middleware:
            if middleware.cls is CORSMiddleware:
                exposed = set(middleware.kwargs.get("expose_headers", ()))
        assert {"ETag", "X-Analytics-Cache", "Retry-After"} <= exposed, exposed

    @pytest.mark.asyncio
    async def test_a_statement_timeout_that_could_not_be_applied_is_counted(self):
        """It fails open on purpose -- a guard must not break the read -- but
        the query that follows is then unbounded, and a log line pages nobody."""
        from app.core.metrics import analytics_read_degraded_total

        reason = "statement_timeout_not_applied"
        before = _counter(analytics_read_degraded_total, reason=reason)
        assert await layer.apply_statement_timeout(
            _FakeSession(raises=RuntimeError("no")), 5000
        ) is False
        assert _counter(analytics_read_degraded_total, reason=reason) == before + 1

    def test_the_degraded_vocabulary_matches_the_metric(self):
        for reason in layer.ANALYTICS_DEGRADED_REASONS:
            layer.count_degraded(reason)  # must not raise
