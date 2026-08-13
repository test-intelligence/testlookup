"""
TestLookup — Enhanced health check endpoints.

GET /health/live    — Kubernetes liveness probe.
                      Only checks that the process is running; never checks dependencies.
                      A failed check triggers a pod restart.

GET /health/ready   — Kubernetes readiness probe.
                      Verifies that critical dependencies (PostgreSQL, MongoDB, Redis)
                      are reachable before traffic is routed to the pod.
                      Returns 503 when any critical dependency is unavailable.

GET /health/version — Cheap build-identity probe: version + git revision +
                      build date + env, with NO dependency probes. Answers
                      "which build is this pod running?" fast enough for a CD
                      smoke test or an uptime monitor, where /health/details
                      (which probes six services) is too slow.

GET /health/details — Full dependency status report for operations dashboards.
                      Checks all services: PostgreSQL, MongoDB, Redis, MinIO, Ollama, ChromaDB.
                      Not intended for K8s probes (too slow).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, cast

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter(prefix="/health", tags=["Health"])
logger = logging.getLogger(__name__)

_START_TIME = time.time()


# ── Individual dependency probes ──────────────────────────────────────────────

async def _check_postgres() -> dict[str, Any]:
    try:
        import sqlalchemy  # noqa: PLC0415
        from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415

        async with AsyncSessionLocal() as db:
            await db.execute(sqlalchemy.text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("Postgres health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)[:200]}


async def _check_mongo() -> dict[str, Any]:
    try:
        from app.db.mongo import get_mongo_db  # noqa: PLC0415

        db = cast(Any, get_mongo_db())
        await db.command("ping")
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("MongoDB health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)[:200]}


async def _check_redis() -> dict[str, Any]:
    try:
        from app.db.redis_client import get_redis  # noqa: PLC0415

        redis = cast(Any, get_redis())
        await redis.ping()
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)[:200]}


# Per-probe timeout budget for the non-critical /health/details checks.
# Tight on purpose — the endpoint is meant to be fast even when optional
# services are degraded. If a probe can't answer in this window it's
# reported as ``degraded`` and the dashboard decides what to do.
#
# A bare ``timeout=1.5`` float passed into httpx does NOT override every
# phase: the shared pool's ``connect=10.0`` default still applies because
# the float overrides only the read/write phases. We build a proper
# ``httpx.Timeout`` object with all four phases pinned so an unreachable
# service can't stall the whole probe.
_PROBE_BUDGET_SECONDS = 1.5
_PROBE_TIMEOUT = httpx.Timeout(
    _PROBE_BUDGET_SECONDS,
    connect=_PROBE_BUDGET_SECONDS,
    read=_PROBE_BUDGET_SECONDS,
    write=_PROBE_BUDGET_SECONDS,
    pool=_PROBE_BUDGET_SECONDS,
)


async def _check_minio() -> dict[str, Any]:
    try:
        from app.core.http_client import get_http_client  # noqa: PLC0415

        # MinIO health endpoint — available without auth
        endpoint = settings.MINIO_ENDPOINT
        scheme = "https" if settings.MINIO_USE_SSL else "http"
        client = get_http_client()
        resp = await client.get(f"{scheme}://{endpoint}/minio/health/live", timeout=_PROBE_TIMEOUT)
        if resp.status_code in (200, 204):
            return {"status": "ok"}
        return {"status": "degraded", "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "degraded", "detail": str(exc)[:200]}


async def _check_ollama() -> dict[str, Any]:
    # The skip is keyed on AI_OFFLINE_MODE because offline mode means
    # "local models only" — a cloud-LLM deployment has no Ollama worth
    # reporting on. It is NOT inverted, but it is keyed on the setting
    # next door to the decisive one: LLM_PROVIDER. A deployment running
    # ollama with AI_OFFLINE_MODE=false is reported as "skipped" here even
    # though Ollama is on the path. Left as-is deliberately — ops
    # dashboards consume this shape — and covered instead by
    # GET /api/v1/settings/ai/model-status (US-13.2), which keys off the
    # effective provider. See services/model_status_service.py.
    if not settings.AI_OFFLINE_MODE:
        return {"status": "skipped", "detail": "AI_OFFLINE_MODE=false — using cloud LLM"}
    # Single shared probe with the model-status endpoint — one place knows
    # how to ask Ollama what it has installed.
    from app.services.model_status_service import probe_ollama  # noqa: PLC0415

    probe = await probe_ollama(settings.OLLAMA_BASE_URL, timeout=_PROBE_TIMEOUT)
    if probe["reachable"]:
        return {"status": "ok", "models": probe["models"]}
    return {"status": "degraded", "detail": probe["error"]}


async def _check_chromadb() -> dict[str, Any]:
    try:
        from app.core.http_client import get_http_client  # noqa: PLC0415

        client = get_http_client()
        resp = await client.get(f"{settings.chroma_host_url}/api/v2/heartbeat", timeout=_PROBE_TIMEOUT)
        if resp.status_code == 200:
            return {"status": "ok"}
        return {"status": "degraded", "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "degraded", "detail": str(exc)[:200]}


async def _with_budget(coro, label: str) -> dict[str, Any]:
    """Run a probe with a hard wall-clock budget.

    ``asyncio.wait_for`` enforces the deadline at the Python level so a
    probe that hangs past its httpx timeout (e.g. DNS retry loop under
    load) cannot stall the whole ``/health/details`` response. The wrap
    is an extra safety net on top of the per-request ``timeout`` argument
    passed into httpx.
    """
    try:
        return await asyncio.wait_for(coro, timeout=_PROBE_BUDGET_SECONDS + 0.5)
    except asyncio.TimeoutError:
        return {"status": "degraded", "detail": f"{label} probe timed out after {_PROBE_BUDGET_SECONDS + 0.5}s"}
    except Exception as exc:
        return {"status": "degraded", "detail": str(exc)[:200]}


def build_provenance() -> dict[str, str]:
    """Self-reported build provenance for the running image.

    Sourced from ``BUILD_REVISION`` / ``BUILD_DATE``, injected at
    image-build time (``backend/Dockerfile`` ARGs, wired from
    ``release.yml``). Lets a self-host operator confirm which commit and
    build a container is running — matching a pinned image digest back to
    its source — without shelling into the pod. Falls back to ``"unknown"``
    in local/dev runs where the env is unset.
    """
    return {
        "revision": settings.BUILD_REVISION or "unknown",
        "built_at": settings.BUILD_DATE or "unknown",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/live", summary="Liveness probe — is the process alive?")
async def liveness():
    """
    Kubernetes liveness probe.
    Returns 200 as long as the process is running and the event loop is responsive.
    Never checks external dependencies — a database outage must NOT restart the pod.
    """
    return {
        "status": "alive",
        "uptime_seconds": int(time.time() - _START_TIME),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready", summary="Readiness probe — are critical dependencies up?")
async def readiness():
    """
    Kubernetes readiness probe.
    Checks PostgreSQL, MongoDB, and Redis connectivity concurrently.
    Returns 503 if any of those is unavailable so the load balancer stops sending traffic.
    """
    pg, mongo, redis = await asyncio.gather(
        _check_postgres(),
        _check_mongo(),
        _check_redis(),
    )
    critical_ok = all(d["status"] == "ok" for d in (pg, mongo, redis))
    return JSONResponse(
        status_code=200 if critical_ok else 503,
        content={
            "status": "ready" if critical_ok else "not_ready",
            "checks": {"postgres": pg, "mongo": mongo, "redis": redis},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.get("/version", summary="Build identity — version, revision, build date (no probes)")
async def version():
    """
    Cheap build-identity report — the running image's version and provenance
    with **no** dependency probes, so it answers in microseconds.

    ``/health/details`` also carries the ``build`` block, but it probes six
    services (Postgres, Mongo, Redis, MinIO, Ollama, ChromaDB) and is
    documented as "not intended for K8s probes (too slow)". A post-deploy CD
    smoke test, an uptime monitor, or an operator confirming a rollout landed
    all want a single fast answer to "which commit + build is this pod?" —
    that is what this endpoint is for. Root ``GET /`` returns the version but
    not the git revision or build date, so it cannot verify a specific build.

    Always returns 200 (the process is alive if it can answer at all).
    ``build.revision`` / ``build.built_at`` fall back to ``"unknown"`` in
    local/dev runs where the image-build env is unset.
    """
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "build": build_provenance(),
        "env": settings.APP_ENV,
        "uptime_seconds": int(time.time() - _START_TIME),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/details", summary="Full dependency status — for ops dashboards")
async def health_details():
    """
    Full health report across all infrastructure dependencies.
    Runs all checks concurrently; returns 200 even when non-critical services are degraded.
    Not intended for K8s probes — use /health/live and /health/ready instead.

    Each optional (non-critical) probe runs inside ``_with_budget`` so a
    hung or unreachable dependency can't stall the whole response beyond
    the configured per-probe timeout. Critical checks (pg/mongo/redis)
    are not wrapped because the readiness probe depends on their honest
    result; if postgres is truly down we want the probe to expose that.
    """
    pg, mongo, redis, minio, ollama, chroma = await asyncio.gather(
        _check_postgres(),
        _check_mongo(),
        _check_redis(),
        _with_budget(_check_minio(), "minio"),
        _with_budget(_check_ollama(), "ollama"),
        _with_budget(_check_chromadb(), "chromadb"),
    )
    critical_ok = all(d["status"] == "ok" for d in (pg, mongo, redis))
    return {
        "status": "healthy" if critical_ok else "degraded",
        "version": settings.APP_VERSION,
        "build": build_provenance(),
        "env": settings.APP_ENV,
        "uptime_seconds": int(time.time() - _START_TIME),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "postgres": pg,
            "mongo": mongo,
            "redis": redis,
            "minio": minio,
            "ollama": ollama,
            "chromadb": chroma,
        },
    }


@router.get(
    "/ingestion",
    summary="Live-stream ingestion health — gate + queue + memory",
)
async def health_ingestion() -> dict:
    """Surface the load + backpressure signals used by the live-stream
    admission gates.

    The output is consumed by the future Grafana panel (Phase 1 of the
    scalable-ingestion plan in ``docs/SCALABLE_INGESTION_DESIGN.md``)
    and by support / on-call humans when a customer reports "my runs
    are slow / 429ing". A single GET answers:

    * Is Redis approaching the memory threshold that triggers 503?
    * What's the per-minute ingest vs reject rate?
    * How many live sessions are active right now?
    * How deep are the Celery queues backing ingestion + AI analysis?
    * What thresholds is the gate using today (env-tuned, useful for
      verifying the deployment matches the docs)?

    Everything is best-effort — a degraded Redis returns partial data
    rather than 500'ing the health endpoint itself.
    """
    from datetime import datetime as _dt, timezone as _tz

    from app.db.redis_client import get_redis
    from app.services.ingestion_backpressure import get_redis_memory_snapshot

    redis = get_redis()
    snapshot = await get_redis_memory_snapshot(force_refresh=True)

    bucket = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M")

    # Per-bucket ingest counter: sum across every project's bucket key.
    # This is O(K) where K = number of projects with ingest in the last
    # minute. Bounded by project count, fine.
    ingest_count = 0
    project_count = 0
    try:
        async for key in redis.scan_iter(match=f"testlookup:rate:ingest:*:{bucket}", count=200):
            project_count += 1
            try:
                val = await redis.get(key)
                ingest_count += int(val or 0)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("health_ingestion_scan_failed: %s", exc)

    # Global reject counter for this minute.
    reject_count = 0
    try:
        val = await redis.get(f"testlookup:rate:reject:{bucket}")
        reject_count = int(val or 0)
    except Exception:
        pass

    # Celery queue depths. Each queue lives as a Redis List under its
    # routing key — ``LLEN`` is O(1). Falls back to None on error so
    # the rest of the payload still renders. Phase 2.4 adds the
    # ingestion shards; the base queues stay so monitoring tools that
    # alert on the legacy names still work.
    from app.worker.ingestion_routing import all_shard_queues

    queue_depths: dict[str, int | None] = {}
    base_queues = ("critical", "ingestion", "ai_analysis", "default")
    for queue in (*base_queues, *all_shard_queues()):
        try:
            queue_depths[queue] = int(await redis.llen(queue))
        except Exception:
            queue_depths[queue] = None

    # Active live sessions — count of state-key hashes. Same pattern as
    # the per-project scan; bounded by live-run count.
    active_sessions = 0
    try:
        async for _ in redis.scan_iter(match="testlookup:live:state:*", count=200):
            active_sessions += 1
    except Exception:
        active_sessions = -1  # surface "unknown"

    # Phase 3 — AI pipeline debouncer state. ``pending`` = runs waiting
    # in the SortedSet; ``degraded_projects`` = projects currently
    # over their daily LLM-cost budget (rules+ML fallback in effect).
    debouncer_pending: int | None = None
    try:
        debouncer_pending = int(await redis.zcard("testlookup:ai_pipeline_debounce"))
    except Exception:
        debouncer_pending = None
    from app.services.ai_pipeline_debouncer import get_degraded_project_count
    degraded_projects = await get_degraded_project_count()

    # Phase 4.3 — DLQ depth for permanently-failed persist_live_session
    # tasks. Surfaces "N tasks failed after exhausting retries in the
    # last 7 days" without an operator having to LRANGE the Redis list.
    from app.services.ingestion_dlq import get_dlq_count
    dlq_persist_count = await get_dlq_count("persist_live_session")

    # Status flag for the dashboard. ``overload`` when backpressure is
    # actively rejecting; ``degraded`` when the reject rate is non-zero
    # but we're not over the memory threshold (i.e. one project is
    # being rate-limited, but global memory is fine).
    over_threshold = False
    used_pct = 0.0
    if snapshot is not None:
        used_pct = round(snapshot.used_pct, 1)
        if snapshot.max_bytes > 0 and snapshot.used_pct >= settings.INGEST_REDIS_MEMORY_THRESHOLD_PCT:
            over_threshold = True
        elif (
            settings.INGEST_REDIS_MEMORY_ABSOLUTE_BYTES > 0
            and snapshot.used_bytes >= settings.INGEST_REDIS_MEMORY_ABSOLUTE_BYTES
        ):
            over_threshold = True

    if over_threshold:
        status_flag = "overload"
    elif reject_count > 0:
        status_flag = "degraded"
    else:
        status_flag = "ok"

    return {
        "status": status_flag,
        "redis": {
            "used_bytes": snapshot.used_bytes if snapshot else None,
            "max_bytes": snapshot.max_bytes if snapshot else None,
            "used_pct": used_pct,
        },
        "queues": queue_depths,
        "live_sessions": {"active": active_sessions},
        "ai_pipeline": {
            "pending_in_debouncer": debouncer_pending,
            "degraded_projects": degraded_projects,
            "debounce_window_seconds": settings.AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS,
            "debouncer_enabled": settings.AI_PIPELINE_DEBOUNCE_ENABLED,
        },
        "dlq": {
            "persist_live_session": dlq_persist_count,
        },
        "recent": {
            "ingest_count_this_minute": ingest_count,
            "reject_count_this_minute": reject_count,
            "active_projects_this_minute": project_count,
        },
        "thresholds": {
            "rate_limit_per_minute": settings.INGEST_RATE_LIMIT_PER_MINUTE,
            "redis_memory_threshold_pct": settings.INGEST_REDIS_MEMORY_THRESHOLD_PCT,
            "redis_memory_absolute_bytes": settings.INGEST_REDIS_MEMORY_ABSOLUTE_BYTES,
        },
        "timestamp": _dt.now(_tz.utc).isoformat(),
    }
