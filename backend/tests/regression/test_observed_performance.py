"""W17b ADM-06: Settings > Performance shows MEASURED latency and slow queries.

The page showed only the codified budgets; nothing read a measurement back.
Pinned here, with an in-memory Redis stand-in:

* the quantile math (Prometheus-style interpolation in the cumulative buckets;
  the +Inf bucket answers its lower bound, never an invented number);
* two processes (two pods) flushing into the same 5-minute slot sum;
* a window only reads its own slots; an unrequested route reads
  ``measured: false`` with null quantiles (absence is not 0 ms); a p95 over the
  budget is a breach;
* the slow-query hook records the statement SHAPE: a bound email or an inlined
  literal never reaches Redis; a fast statement is not recorded;
* the capped slow-query set drops the oldest shapes past ``SLOW_CAP``;
* the Prometheus path parses a recorded ``/api/v1/query`` answer;
* the routes: observed is QA_LEAD+, slow queries ADMIN-only.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.services import observed_performance as op


class _Pipe:
    def __init__(self, redis):
        self.redis, self.calls = redis, []

    def __getattr__(self, name):
        def _queue(*a, **k):
            self.calls.append((name, a, k))
            return self
        return _queue

    async def execute(self):
        return [await getattr(self.redis, name)(*a, **k) for name, a, k in self.calls]


class FakeRedis:
    def __init__(self):
        self.h: dict[str, dict[str, str]] = {}
        self.z: dict[str, dict[str, float]] = {}

    def pipeline(self):
        return _Pipe(self)

    async def hincrby(self, key, field, n):
        row = self.h.setdefault(key, {})
        row[field] = str(int(row.get(field, "0")) + int(n))

    async def hincrbyfloat(self, key, field, n):
        row = self.h.setdefault(key, {})
        row[field] = str(float(row.get(field, "0")) + float(n))

    async def hset(self, key, mapping):
        self.h.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def expire(self, key, seconds):
        return True

    async def eval(self, script, numkeys, key, value):  # the raise-max script
        row = self.h.setdefault(key, {})
        if float(row.get("max_ms", "0")) < float(value):
            row["max_ms"] = value

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zcard(self, key):
        return len(self.z.get(key, {}))

    def _ordered(self, key):
        return [m for m, _ in sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])]

    async def zrange(self, key, start, stop):
        return self._ordered(key)[start: stop + 1]

    async def zrevrange(self, key, start, stop):
        return list(reversed(self._ordered(key)))[start: stop + 1]

    async def zrem(self, key, *members):
        for m in members:
            self.z.get(key, {}).pop(m, None)

    async def delete(self, *keys):
        for k in keys:
            self.h.pop(k, None)


@pytest.fixture(autouse=True)
def _clean_state():
    op._drain()
    yield
    op._drain()


def _counts(**by_bound):
    """{bound_ms: n} -> bucket counts; key 'inf' for the +Inf bucket."""
    c = [0] * (len(op.BUCKETS_MS) + 1)
    for bound, n in by_bound.items():
        c[len(op.BUCKETS_MS) if bound == "inf" else op.BUCKETS_MS.index(int(bound[1:]))] += n
    return c


def test_quantiles_interpolate_inside_the_bucket():
    # 100 requests: 50 in (75,100], 45 in (100,150], 5 in (150,200]
    c = _counts(b100=50, b150=45, b200=5)
    assert op.quantile_ms(0.5, c) == 100.0
    assert op.quantile_ms(0.95, c) == 150.0
    assert op.quantile_ms(0.99, c) == pytest.approx(190.0)
    assert op.quantile_ms(0.25, c) == pytest.approx(87.5)
    assert op.quantile_ms(0.95, [0] * (len(op.BUCKETS_MS) + 1)) is None
    assert op.quantile_ms(0.99, _counts(inf=3)) == float(op.BUCKETS_MS[-1])


def test_two_processes_sum_and_a_window_reads_only_its_slots():
    redis = FakeRedis()
    now = 1_800_000_000.0
    op.record_latency("run_list", 120, now=now)
    op.record_latency("run_list", 140, now=now)
    asyncio.run(op.flush(redis))  # pod A
    op.record_latency("run_list", 130, now=now)
    op.record_latency("run_list", 900, now=now - 2 * 3600)  # two hours ago: outside 1h, inside 24h
    asyncio.run(op.flush(redis))  # pod B

    hour = asyncio.run(op.observed("1h", redis=redis, now=now + 1))
    row = next(r for r in hour["budgets"] if r["operation"] == "run_list")
    assert row["count"] == 3 and row["measured"] is True and hour["source"] == "in_app"
    assert 100 < row["observed_p95_ms"] <= 150 and row["breach"] is False
    day = asyncio.run(op.observed("24h", redis=redis, now=now + 1))
    row24 = next(r for r in day["budgets"] if r["operation"] == "run_list")
    assert row24["count"] == 4 and row24["observed_p99_ms"] > 500

    quiet = next(r for r in hour["budgets"] if r["operation"] == "project_list")
    assert quiet == {**quiet, "count": 0, "measured": False, "observed_p95_ms": None, "breach": False}


def test_a_p95_over_the_budget_is_a_breach():
    redis = FakeRedis()
    now = 1_800_000_000.0
    for _ in range(20):
        op.record_latency("project_list", 450, now=now)  # budget p95 200 ms
    asyncio.run(op.flush(redis))
    row = next(r for r in asyncio.run(op.observed("15m", redis=redis, now=now))["budgets"] if r["operation"] == "project_list")
    assert row["breach"] is True and row["observed_p95_ms"] > row["budget_p95_ms"]


def test_the_middleware_times_only_budgeted_routes():
    from app.main import app

    op._route_ops = None
    seen = {}
    real = op.record_latency
    op.record_latency = lambda name, ms, now=None: seen.setdefault(name, ms)
    try:
        routes = op._build_route_ops(app)
        assert "run_list" in routes.values() and "project_list" in routes.values()
        endpoint = next(e for (e, m), name in routes.items() if name == "run_list")
        op._route_ops = routes
        op._observe({"endpoint": endpoint, "method": "GET"}, 42.0)
        op._observe({"endpoint": object(), "method": "GET"}, 42.0)
        assert seen == {"run_list": 42.0}
    finally:
        op.record_latency = real
        op._route_ops = None


def test_slow_query_shape_carries_no_values():
    redis = FakeRedis()
    op.record_slow_query(
        "SELECT users.id FROM users WHERE users.email = 'alice.secret@corp.example' AND users.id IN ($1, $2, $3) "
        "AND failed_login_attempts > 5",
        812.4,
    )
    op.record_slow_query("SELECT users.id FROM users WHERE users.email = 'bob@corp.example' AND users.id IN ($1) AND failed_login_attempts > 9", 640.0)
    asyncio.run(op.flush(redis))
    dump = repr(redis.h) + repr(redis.z)
    assert "alice.secret" not in dump and "bob@corp" not in dump and "> 5" not in dump
    result = asyncio.run(op.slow_queries(_NoPgStat(), redis=redis))
    assert result["source"] == "in_app" and result["total"] == 1
    (item,) = result["items"]
    assert item["sql"] == "SELECT users.id FROM users WHERE users.email = ? AND users.id IN (...) AND failed_login_attempts > ?"
    assert item["count"] == 2 and item["max_ms"] == 812.4 and item["mean_ms"] == pytest.approx(726.2)


def test_the_hook_ignores_a_fast_statement_and_records_a_slow_one(monkeypatch):
    from sqlalchemy import create_engine, text

    monkeypatch.setattr(op.settings, "SLOW_QUERY_MS", 0)
    engine = create_engine("sqlite://")
    assert op.install_slow_query_hook(engine) is True
    assert op.install_slow_query_hook(engine) is False  # once per engine
    with engine.connect() as conn:
        conn.execute(text("SELECT :email"), {"email": "carol@corp.example"})
    _, slow = op._drain()
    assert len(slow) == 1 and "carol" not in repr(slow)

    engine2 = create_engine("sqlite://")
    monkeypatch.setattr(op.settings, "SLOW_QUERY_MS", 60_000)
    op.install_slow_query_hook(engine2)
    with engine2.connect() as conn:
        conn.execute(text("SELECT 1"))
    assert op._drain()[1] == {}


def test_the_slow_set_is_capped(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(op, "SLOW_CAP", 3)
    for i in range(5):
        op.record_slow_query(f"SELECT * FROM t{i}x", 600.0, now=1000.0 + i)
    asyncio.run(op.flush(redis))
    left = asyncio.run(op.slow_queries(_NoPgStat(), redis=redis))
    assert [r["sql"] for r in left["items"]] and len(left["items"]) == 3
    assert sum(1 for k in redis.h if k.startswith(op.SLOW_KEY_PREFIX)) == 3
    assert not any("t0x" in r["sql"] or "t1x" in r["sql"] for r in left["items"])


def test_prometheus_answers_are_parsed(monkeypatch):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        value = "0.14" if "0.95" in q else ("812" if q.startswith("sum(increase") else "0.09")
        return httpx.Response(200, json={"status": "success", "data": {"result": [{"value": [0, value]}]}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: real(transport=httpx.MockTransport(handler), **k))
    monkeypatch.setattr(op.settings, "PROMETHEUS_URL", "http://prometheus.internal:9090")
    result = asyncio.run(op.observed("1h", redis=FakeRedis()))
    row = next(r for r in result["budgets"] if r["operation"] == "run_list")
    assert result["source"] == "prometheus" and row["observed_p95_ms"] == 140.0 and row["count"] == 812


def test_route_roles():
    from app.main import app

    def source(path):
        route = next(r for r in app.routes if getattr(r, "path", "") == path)
        return inspect.getsource(route.endpoint)

    assert "require_role(UserRole.QA_LEAD)" in source("/api/v1/performance/observed")
    assert "require_role(UserRole.ADMIN)" in source("/api/v1/performance/slow-queries")


class _NoPgStat:
    """A session where pg_stat_statements is not installed."""

    async def execute(self, *a, **k):
        class _R:
            def scalar(self):
                return False
        return _R()
