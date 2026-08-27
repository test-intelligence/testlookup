"""A dependency outage must not stall the health report, or misattribute itself.

Both defects here were found by scaling Redis to zero against a live
deployment and timing each probe individually:

    redis      5017.1ms  UNBUDGETED  error     Timeout connecting to server
    minio      2001.6ms  budgeted    degraded  minio probe timed out after 2.0s
    chromadb   2003.4ms  budgeted    degraded  chromadb probe timed out after 2.0s

MinIO and ChromaDB were both healthy. They resolve their endpoint through
``get_effective_storage_config()``, whose first act is a Redis GET; the call is
wrapped in ``except Exception``, which catches a refusal but cannot shorten a
hang. So a Redis outage spent their whole 2s budget and reported them degraded.
The endpoint whose job is naming the dependency that died implicated three.

The 5017ms is the Redis client's own ``socket_connect_timeout=5``. The critical
probes were unbudgeted, so that landed on the response -- and on /health/ready,
whose kubelet ``timeoutSeconds`` is also 5. A dead heat: whoever won, the pod
went NotReady, but on a kubelet timeout there is no body, so the answer to
*which* dependency died was thrown away.
"""
from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("app.routers.health")

from app.routers import health as H  # noqa: E402

pytestmark = pytest.mark.regression


async def _hang(*_args, **_kwargs):
    """Stand-in for an unreachable dependency: blocks far past any budget."""
    await asyncio.sleep(30)
    return {"status": "ok"}


async def _ok(*_args, **_kwargs):
    return {"status": "ok"}


def _all_probes_ok(monkeypatch) -> None:
    """Stub every probe healthy, so a test's subject is the ONLY thing failing.

    Load-bearing: without this, Postgres and Mongo are unreachable in a local
    run, readiness returns 503 no matter what Redis does, and a test asserting
    503 passes without ever depending on its own subject. Two mutations that
    removed the budget entirely survived exactly that way.
    """
    for name in ("_check_postgres", "_check_mongo", "_check_redis",
                 "_check_minio", "_check_ollama", "_check_chromadb"):
        monkeypatch.setattr(H, name, _ok)


async def test_details_bounds_a_hanging_critical_probe(monkeypatch):
    """The 5017ms case, asserted where it happened: through the endpoint.

    The budget is shortened so this tests the mechanism rather than sleeping
    out the real one, which is pinned against the kubelet deadline below.
    """
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(H, "_CRITICAL_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(H, "_check_redis", _hang)

    started = time.perf_counter()
    body = await H.health_details()
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, (
        "/health/details ran " + str(round(elapsed, 2)) + "s on a hanging "
        "Redis -- the critical probes are not going through the budget"
    )
    assert body["checks"]["redis"]["status"] == "error", (
        "a hanging Redis was reported as " + repr(body["checks"]["redis"])
    )
    assert "timed out" in body["checks"]["redis"]["detail"]
    assert body["status"] == "degraded"


async def test_details_does_not_blame_the_healthy_services(monkeypatch):
    """The misattribution case, end to end: only the dependency that is
    actually down may be reported as anything other than ok."""
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(H, "_CRITICAL_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(H, "_check_redis", _hang)

    body = await H.health_details()

    for name in ("minio", "chromadb", "postgres", "mongo"):
        assert body["checks"][name]["status"] == "ok", (
            name + " was reported " + repr(body["checks"][name]) + " while only "
            "Redis was down. That is the bug: a Redis outage used to time out "
            "the MinIO and ChromaDB probes through the shared config cache."
        )


async def test_readiness_bounds_and_still_fails_closed(monkeypatch):
    """The budget must not soften the gate -- and must actually apply here."""
    import json

    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(H, "_CRITICAL_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(H, "_check_redis", _hang)

    started = time.perf_counter()
    response = await H.readiness()
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, (
        "/health/ready ran " + str(round(elapsed, 2)) + "s on a hanging Redis. "
        "The kubelet's timeout is 5s: past it there is no response body, so "
        "which dependency died is discarded."
    )
    assert response.status_code == 503, (
        "a hanging Redis returned " + str(response.status_code)
        + " -- the readiness gate stopped failing closed"
    )
    body = json.loads(bytes(response.body).decode())
    assert body["checks"]["redis"]["status"] == "error"
    assert body["checks"]["postgres"]["status"] == "ok", (
        "postgres was blamed too; the body must name only what actually failed"
    )


def test_the_critical_budget_stays_under_the_kubelet_deadline():
    """The budget is only useful if it expires FIRST. Read the kubelet's own
    timeout out of the manifest rather than restating it, so raising one
    without the other fails here."""
    import re
    from pathlib import Path

    manifest = (
        Path(__file__).resolve().parents[3]
        / "k8s" / "base" / "backend-deployment.yaml"
    ).read_text(encoding="utf-8")

    block = manifest.split("readinessProbe:", 1)[1].split("startupProbe:", 1)[0]
    match = re.search(r"timeoutSeconds:\s*(\d+)", block)
    assert match, "could not read readinessProbe.timeoutSeconds from the manifest"
    kubelet = int(match.group(1))

    assert H._CRITICAL_BUDGET_SECONDS < kubelet, (
        "the critical budget (" + str(H._CRITICAL_BUDGET_SECONDS) + "s) must "
        "expire before the kubelet's readinessProbe timeout (" + str(kubelet)
        + "s), or /health/ready is cut off before it can report which "
        "dependency failed"
    )


async def test_readiness_still_fails_closed_on_a_timed_out_dependency(monkeypatch):
    """The budget must not soften the gate: a probe that times out has to keep
    the pod out of the Service."""
    monkeypatch.setattr(H, "_CRITICAL_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(H, "_check_redis", _hang)

    response = await H.readiness()

    assert response.status_code == 503, (
        "a hanging Redis returned " + str(response.status_code)
        + " -- the readiness gate stopped failing closed"
    )


@pytest.mark.parametrize("probe", ["_check_minio", "_check_chromadb"])
async def test_the_optional_probes_do_not_touch_redis(probe, monkeypatch):
    """The misattribution case. These probes call the storage config resolver
    only to learn which endpoint to hit; going through its Redis cache made a
    Redis outage look like a MinIO and ChromaDB outage."""
    from app.services import storage_config_service as scs

    seen: dict = {}

    async def _spy(*, use_cache: bool = True):
        seen["use_cache"] = use_cache
        return {
            "minio_endpoint": "127.0.0.1:9", "minio_use_ssl": False,
            "chroma_host": "127.0.0.1", "chroma_port": 9,
        }

    monkeypatch.setattr(scs, "get_effective_storage_config", _spy)
    monkeypatch.setattr(
        "app.services.storage_config_service.get_effective_storage_config", _spy
    )

    await getattr(H, probe)()

    assert seen.get("use_cache") is False, (
        probe + " resolved its endpoint through the Redis-backed cache. That "
        "call cannot be shortened by its except-clause when Redis hangs, so a "
        "Redis outage reports this healthy service as degraded."
    )


async def test_the_resolver_skips_redis_entirely_when_asked(monkeypatch):
    """The other half: the flag must actually bypass Redis, not merely be
    accepted. A hanging client here would blow the probe budget again."""
    from app.services import storage_config_service as scs

    touched = []

    def _explode():
        touched.append(True)
        raise AssertionError("Redis was contacted despite use_cache=False")

    monkeypatch.setattr("app.db.redis_client.get_redis", _explode)

    await scs.get_effective_storage_config(use_cache=False)
    assert not touched, "use_cache=False still went to Redis"
