"""
TestLookup — Enhanced health check endpoints.

GET /health/live    — Kubernetes liveness probe.
                      Only checks that the process is running; never checks dependencies.
                      A failed check triggers a pod restart.

GET /health/ready   — Kubernetes readiness probe.
                      Verifies that critical dependencies (PostgreSQL, MongoDB, Redis)
                      are reachable before traffic is routed to the pod.
                      Returns 503 when any critical dependency is unavailable.

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
    if not settings.AI_OFFLINE_MODE:
        return {"status": "skipped", "detail": "AI_OFFLINE_MODE=false — using cloud LLM"}
    try:
        from app.core.http_client import get_http_client  # noqa: PLC0415

        client = get_http_client()
        resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=_PROBE_TIMEOUT)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"status": "ok", "models": models}
        return {"status": "degraded", "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "degraded", "detail": str(exc)[:200]}


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
