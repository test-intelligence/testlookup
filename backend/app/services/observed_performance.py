"""Observed request latency and slow queries, measured in-app (W17b ADM-06).

The Performance page showed only the codified budgets (``performance_budgets``)
and nothing measured. No Prometheus server ships with ``k8s/base`` and
``pg_stat_statements`` is not enabled anywhere, so the numbers are measured
here, where every deployment has them:

* **Latency** — ``ObservedLatencyMiddleware`` times every request whose route
  has a budget (``LatencyBudget.http_handler`` + method; nothing else, so the
  label set stays tiny) into fixed millisecond buckets, per 5-minute slot, in
  process memory. ``flush_loop`` (an API lifespan task) adds each process's
  counts to Redis every ``FLUSH_SECONDS`` (``HINCRBY`` — every gunicorn worker
  on every pod sums into the same slot) and the endpoint reads the slots of the
  window and interpolates p50/p95/p99 from the cumulative buckets, the way
  Prometheus' ``histogram_quantile`` does. When ``PROMETHEUS_URL`` is set the
  instrumentator's histogram is queried there instead.
* **Slow queries** — an ``after_cursor_execute`` hook on the API engine records
  statements slower than ``SLOW_QUERY_MS`` as **statement text only** (bound
  values never: asyncpg statements carry ``$1`` placeholders, and literals that
  were inlined are normalised away), fingerprinted, aggregated (count / total /
  max / last seen) and flushed into a capped Redis set. Below the threshold the
  hook costs one ``perf_counter`` subtraction and a compare. When
  ``pg_stat_statements`` is readable its top statements are shown instead.

Absence is reported as absence: a budget with no requests in the window reads
``measured: false`` and ``null`` quantiles, never 0 ms.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from collections import defaultdict
from typing import Any, Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Upper bounds (ms) of the latency buckets; a final +Inf bucket follows.
BUCKETS_MS: tuple[int, ...] = (
    10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000,
)
SLOT_SECONDS = 300
RETAIN_SECONDS = 25 * 3600
FLUSH_SECONDS = 30
WINDOWS: dict[str, int] = {"15m": 900, "1h": 3600, "24h": 86400}

SLOW_INDEX_KEY = "perf:slowq:index"
SLOW_KEY_PREFIX = "perf:slowq:"
SLOW_CAP = 200
SLOW_TTL_SECONDS = 7 * 86400
SQL_MAX_CHARS = 2048
LAT_KEY_PREFIX = "perf:lat:"

_lock = threading.Lock()
_latency: dict[tuple[int, str], list[int]] = {}
_slow: dict[str, dict[str, Any]] = {}
_route_ops: Optional[dict[tuple[Any, str], str]] = None


# ── latency recording ───────────────────────────────────────────────────────


def _budgeted() -> dict[tuple[str, str], str]:
    from app.services.performance_budgets import LATENCY_BUDGETS

    return {(b.http_method.upper(), b.http_handler): b.operation for b in LATENCY_BUDGETS if b.http_handler}


def _build_route_ops(app: Any) -> dict[tuple[Any, str], str]:
    """``(endpoint, METHOD) -> operation`` for every route that has a budget."""
    wanted = _budgeted()
    out: dict[tuple[Any, str], str] = {}
    for route in getattr(app, "routes", []) or []:
        path, endpoint = getattr(route, "path", None), getattr(route, "endpoint", None)
        for method in getattr(route, "methods", None) or ():
            op = wanted.get((str(method).upper(), str(path)))
            if op and endpoint is not None:
                out[(endpoint, method.upper())] = op
    return out


def _bucket_index(elapsed_ms: float) -> int:
    for i, bound in enumerate(BUCKETS_MS):
        if elapsed_ms <= bound:
            return i
    return len(BUCKETS_MS)


def record_latency(operation: str, elapsed_ms: float, now: Optional[float] = None) -> None:
    slot = int((now if now is not None else time.time()) // SLOT_SECONDS)
    idx = _bucket_index(elapsed_ms)
    with _lock:
        counts = _latency.get((slot, operation))
        if counts is None:
            counts = _latency[(slot, operation)] = [0] * (len(BUCKETS_MS) + 1)
        counts[idx] += 1


class ObservedLatencyMiddleware:
    """Pure ASGI: times a budgeted request from arrival to its last body chunk."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()

        async def _send(message: Message) -> None:
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                try:
                    _observe(scope, (time.perf_counter() - start) * 1000.0)
                except Exception:  # noqa: BLE001 - measurement never breaks a response
                    pass
            await send(message)

        await self.app(scope, receive, _send)


def _observe(scope: Scope, elapsed_ms: float) -> None:
    global _route_ops
    if _route_ops is None:
        _route_ops = _build_route_ops(scope.get("app"))
    op = _route_ops.get((scope.get("endpoint"), str(scope.get("method", "")).upper()))
    if op:
        record_latency(op, elapsed_ms)


# ── slow queries ────────────────────────────────────────────────────────────

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w$])-?\d+(?:\.\d+)?\b")
_IN_LIST = re.compile(r"\bIN\s*\(\s*\?(?:\s*,\s*\?)*\s*\)", re.IGNORECASE)
_PARAM = re.compile(r"\$\d+|%\(\w+\)s")
_SPACE = re.compile(r"\s+")


def normalise_sql(statement: str) -> str:
    """Shape only: literals -> ``?``, placeholders -> ``?``, ``IN (...)`` collapsed, whitespace folded."""
    text = _STRING_LITERAL.sub("?", statement)
    text = _NUMBER.sub("?", text)
    text = _PARAM.sub("?", text)
    text = _IN_LIST.sub("IN (...)", text)
    text = _SPACE.sub(" ", text).strip()
    return text[:SQL_MAX_CHARS]


def fingerprint(shape: str) -> str:
    return hashlib.sha256(shape.encode("utf-8")).hexdigest()[:16]


def record_slow_query(statement: str, elapsed_ms: float, now: Optional[float] = None) -> None:
    shape = normalise_sql(statement)
    fp = fingerprint(shape)
    ts = now if now is not None else time.time()
    with _lock:
        row = _slow.get(fp)
        if row is None:
            row = _slow[fp] = {"sql": shape, "count": 0, "total_ms": 0.0, "max_ms": 0.0, "last_seen": ts}
        row["count"] += 1
        row["total_ms"] += elapsed_ms
        row["max_ms"] = max(row["max_ms"], elapsed_ms)
        row["last_seen"] = max(row["last_seen"], ts)


def install_slow_query_hook(sync_engine: Any) -> bool:
    """Attach the timing hook once per engine. Returns False when already installed."""
    from sqlalchemy import event

    if getattr(sync_engine, "_tl_slow_query_hook", False):
        return False
    threshold = float(settings.SLOW_QUERY_MS)

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info.setdefault("_tl_query_start", []).append(time.perf_counter())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        starts = conn.info.get("_tl_query_start")
        if not starts:
            return
        elapsed = (time.perf_counter() - starts.pop()) * 1000.0
        if elapsed >= threshold:
            record_slow_query(statement, elapsed)

    sync_engine._tl_slow_query_hook = True
    return True


# ── flushing to Redis ───────────────────────────────────────────────────────

_RAISE_MAX = (
    "local cur = tonumber(redis.call('HGET', KEYS[1], 'max_ms') or '0') "
    "if cur < tonumber(ARGV[1]) then redis.call('HSET', KEYS[1], 'max_ms', ARGV[1]) end return 1"
)


def _drain() -> tuple[dict[tuple[int, str], list[int]], dict[str, dict[str, Any]]]:
    global _latency, _slow
    with _lock:
        lat, slow = _latency, _slow
        _latency, _slow = {}, {}
    return lat, slow


async def flush(redis: Any) -> None:
    """Add this process's counts to Redis and clear them. On failure the counts are dropped
    (a measurement gap, logged) rather than kept growing without bound."""
    lat, slow = _drain()
    if not lat and not slow:
        return
    pipe = redis.pipeline()
    for (slot, op), counts in lat.items():
        key = f"{LAT_KEY_PREFIX}{slot}"
        for i, n in enumerate(counts):
            if n:
                pipe.hincrby(key, f"{op}|{i}", n)
        pipe.expire(key, RETAIN_SECONDS)
    for fp, row in slow.items():
        key = f"{SLOW_KEY_PREFIX}{fp}"
        pipe.hset(key, mapping={"sql": row["sql"], "last_seen": f"{row['last_seen']:.3f}"})
        pipe.hincrby(key, "count", int(row["count"]))
        pipe.hincrbyfloat(key, "total_ms", round(float(row["total_ms"]), 3))
        pipe.eval(_RAISE_MAX, 1, key, f"{row['max_ms']:.3f}")
        pipe.expire(key, SLOW_TTL_SECONDS)
        pipe.zadd(SLOW_INDEX_KEY, {fp: float(row["last_seen"])})
    await pipe.execute()
    if slow:
        await _cap_slow_index(redis)


async def _cap_slow_index(redis: Any) -> None:
    size = int(await redis.zcard(SLOW_INDEX_KEY) or 0)
    if size <= SLOW_CAP:
        return
    stale = await redis.zrange(SLOW_INDEX_KEY, 0, size - SLOW_CAP - 1)
    if stale:
        names = [s.decode() if isinstance(s, bytes) else str(s) for s in stale]
        await redis.delete(*[f"{SLOW_KEY_PREFIX}{n}" for n in names])
        await redis.zrem(SLOW_INDEX_KEY, *names)


async def flush_loop() -> None:
    """API lifespan task: flush every ``FLUSH_SECONDS`` until cancelled (a last flush on the way out)."""
    from app.db.redis_client import get_redis

    try:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            try:
                await flush(get_redis())
            except Exception as exc:  # noqa: BLE001
                logger.warning("observed_performance_flush_failed: %s", type(exc).__name__)
    except asyncio.CancelledError:
        try:
            await flush(get_redis())
        except Exception:  # noqa: BLE001
            pass
        raise


# ── reading ─────────────────────────────────────────────────────────────────


def quantile_ms(q: float, counts: list[int]) -> Optional[float]:
    """Linear interpolation inside the bucket that holds rank ``q * total`` (Prometheus style).

    The +Inf bucket answers its lower bound (the largest finite bound): beyond
    it nothing is known, and a made-up number would read as a measurement.
    """
    total = sum(counts)
    if total <= 0:
        return None
    rank = q * total
    cumulative = 0
    for i, n in enumerate(counts):
        if n and cumulative + n >= rank:
            if i >= len(BUCKETS_MS):
                return float(BUCKETS_MS[-1])
            lower = 0.0 if i == 0 else float(BUCKETS_MS[i - 1])
            upper = float(BUCKETS_MS[i])
            return round(lower + (upper - lower) * ((rank - cumulative) / n), 1)
        cumulative += n
    return float(BUCKETS_MS[-1])


async def _in_app_counts(redis: Any, window_seconds: int, now: Optional[float] = None) -> dict[str, list[int]]:
    now = now if now is not None else time.time()
    last = int(now // SLOT_SECONDS)
    first = int((now - window_seconds) // SLOT_SECONDS) + 1
    pipe = redis.pipeline()
    for slot in range(first, last + 1):
        pipe.hgetall(f"{LAT_KEY_PREFIX}{slot}")
    totals: dict[str, list[int]] = defaultdict(lambda: [0] * (len(BUCKETS_MS) + 1))
    for row in await pipe.execute():
        for field, value in (row or {}).items():
            f = field.decode() if isinstance(field, bytes) else str(field)
            op, _, idx = f.rpartition("|")
            try:
                i = int(idx)
            except ValueError:
                continue
            if 0 <= i <= len(BUCKETS_MS):
                totals[op][i] += int(value)
    return dict(totals)


async def _prometheus_rows(budgets: list, window: str) -> Optional[dict[str, dict[str, Any]]]:
    """Per operation ``{p50, p95, p99, count}`` from Prometheus, or None when it cannot answer."""
    import httpx

    from app.core.http_client import http_verify

    base = (settings.PROMETHEUS_URL or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, verify=http_verify()) as client:
            for b in budgets:
                sel = f'handler="{b.http_handler}",method="{b.http_method.upper()}"'
                row: dict[str, Any] = {}
                for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
                    expr = f"histogram_quantile({q}, sum by (le) (rate(http_request_duration_seconds_bucket{{{sel}}}[{window}])))"
                    row[name] = await _prom_scalar(client, base, expr, scale=1000.0)
                row["count"] = await _prom_scalar(client, base, f"sum(increase(http_request_duration_seconds_count{{{sel}}}[{window}]))")
                out[b.operation] = row
    except Exception as exc:  # noqa: BLE001 - fall back to the in-app numbers
        logger.warning("prometheus_observed_failed: %s", type(exc).__name__)
        return None
    return out


async def _prom_scalar(client: Any, base: str, expr: str, scale: float = 1.0) -> Optional[float]:
    resp = await client.get(f"{base}/api/v1/query", params={"query": expr})
    resp.raise_for_status()
    result = (resp.json().get("data") or {}).get("result") or []
    if not result:
        return None
    value = float(result[0]["value"][1])
    if value != value or value in (float("inf"), float("-inf")):  # NaN / Inf: no data
        return None
    return round(value * scale, 1)


async def observed(window: str, redis: Any = None, now: Optional[float] = None) -> dict[str, Any]:
    from app.services.performance_budgets import LATENCY_BUDGETS

    budgets = [b for b in LATENCY_BUDGETS if b.http_handler]
    seconds = WINDOWS[window]
    source = "in_app"
    prom = await _prometheus_rows(budgets, window) if settings.PROMETHEUS_URL else None
    if prom is not None:
        source = "prometheus"
        counts: dict[str, list[int]] = {}
    else:
        if redis is None:
            from app.db.redis_client import get_redis

            redis = get_redis()
        counts = await _in_app_counts(redis, seconds, now=now)

    rows = []
    for b in budgets:
        if prom is not None:
            p = prom.get(b.operation, {})
            n = int(p.get("count") or 0)
            p50, p95, p99 = p.get("p50"), p.get("p95"), p.get("p99")
        else:
            c = counts.get(b.operation, [0] * (len(BUCKETS_MS) + 1))
            n = sum(c)
            p50, p95, p99 = quantile_ms(0.5, c), quantile_ms(0.95, c), quantile_ms(0.99, c)
        measured = n > 0 and p95 is not None
        rows.append({
            "operation": b.operation,
            "http_handler": b.http_handler,
            "http_method": b.http_method,
            "budget_p50_ms": b.p50_ms,
            "budget_p95_ms": b.p95_ms,
            "budget_p99_ms": b.p99_ms,
            "observed_p50_ms": p50 if measured else None,
            "observed_p95_ms": p95 if measured else None,
            "observed_p99_ms": p99 if measured else None,
            "count": n,
            "measured": measured,
            "breach": bool(measured and p95 is not None and p95 > b.p95_ms),
        })
    return {
        "window": window,
        "source": source,
        "measured": any(r["measured"] for r in rows),
        "slot_seconds": SLOT_SECONDS,
        "budgets": rows,
    }


async def slow_queries(db: Any, redis: Any = None, limit: int = 50) -> dict[str, Any]:
    """The slowest statement shapes: ``pg_stat_statements`` when readable, else the in-app capture."""
    threshold = int(settings.SLOW_QUERY_MS)
    pg = await _pg_stat_statements(db, threshold, limit)
    if pg is not None:
        return {"source": "pg_stat_statements", "threshold_ms": threshold, "items": pg, "total": len(pg)}
    if redis is None:
        from app.db.redis_client import get_redis

        redis = get_redis()
    names = await redis.zrevrange(SLOW_INDEX_KEY, 0, SLOW_CAP - 1)
    fps = [n.decode() if isinstance(n, bytes) else str(n) for n in names or []]
    items = []
    if fps:
        pipe = redis.pipeline()
        for fp in fps:
            pipe.hgetall(f"{SLOW_KEY_PREFIX}{fp}")
        for fp, row in zip(fps, await pipe.execute()):
            if not row:
                continue
            r = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in row.items()}
            count = int(float(r.get("count", 0)))
            total = float(r.get("total_ms", 0.0))
            items.append({
                "fingerprint": fp,
                "sql": r.get("sql", ""),
                "count": count,
                "total_ms": round(total, 1),
                "mean_ms": round(total / count, 1) if count else None,
                "max_ms": round(float(r.get("max_ms", 0.0)), 1),
                "last_seen": float(r.get("last_seen", 0.0)),
            })
    items.sort(key=lambda r: float(r["max_ms"] or 0.0), reverse=True)
    return {"source": "in_app", "threshold_ms": threshold, "items": items[:limit], "total": len(items)}


async def _pg_stat_statements(db: Any, threshold: int, limit: int) -> Optional[list[dict[str, Any]]]:
    from sqlalchemy import text

    try:
        present = (await db.execute(text("SELECT to_regclass('pg_stat_statements') IS NOT NULL"))).scalar()
        if not present:
            return None
        async with db.begin_nested():
            rows = (await db.execute(text(
                "SELECT queryid::text AS fp, query, calls, total_exec_time, max_exec_time "
                "FROM pg_stat_statements WHERE max_exec_time >= :t ORDER BY max_exec_time DESC LIMIT :n"
            ), {"t": threshold, "n": limit})).all()
    except Exception as exc:  # noqa: BLE001 - not installed / not readable: use the in-app capture
        logger.info("pg_stat_statements_unreadable: %s", type(exc).__name__)
        return None
    return [
        {
            "fingerprint": r.fp,
            "sql": normalise_sql(r.query or ""),
            "count": int(r.calls or 0),
            "total_ms": round(float(r.total_exec_time or 0.0), 1),
            "mean_ms": round(float(r.total_exec_time or 0.0) / r.calls, 1) if r.calls else None,
            "max_ms": round(float(r.max_exec_time or 0.0), 1),
            "last_seen": None,
        }
        for r in rows
    ]
