"""Regression tests for build-provenance surfacing on /health/details.

A self-host operator hitting ``GET /health/details`` should be able to see
exactly which commit + build the running container is — the ``build`` block
carries ``revision`` (git SHA) and ``built_at`` (build timestamp), injected
at image-build time (backend/Dockerfile ARGs, wired from release.yml).

These are network-free unit tests: the pure ``build_provenance`` helper is
exercised directly, and ``health_details`` is awaited with its dependency
probes monkeypatched so no live services are required.
"""
from __future__ import annotations

from app.core.config import settings
from app.routers import health


def test_build_provenance_defaults_to_unknown(monkeypatch):
    """Unset BUILD_* env (local/dev) is reported as 'unknown', not empty."""
    monkeypatch.setattr(settings, "BUILD_REVISION", "", raising=False)
    monkeypatch.setattr(settings, "BUILD_DATE", "", raising=False)

    assert health.build_provenance() == {
        "revision": "unknown",
        "built_at": "unknown",
    }


def test_build_provenance_reflects_injected_values(monkeypatch):
    """Values injected at build time flow through verbatim."""
    monkeypatch.setattr(settings, "BUILD_REVISION", "abc1234def", raising=False)
    monkeypatch.setattr(settings, "BUILD_DATE", "2026-07-01T12:00:00Z", raising=False)

    assert health.build_provenance() == {
        "revision": "abc1234def",
        "built_at": "2026-07-01T12:00:00Z",
    }


async def test_health_details_includes_build_block(monkeypatch):
    """/health/details surfaces the build provenance block."""
    async def _ok():
        return {"status": "ok"}

    async def _budget_ok(coro, label):
        return await coro

    # Stub every dependency probe so the endpoint needs no live services.
    monkeypatch.setattr(health, "_check_postgres", _ok)
    monkeypatch.setattr(health, "_check_mongo", _ok)
    monkeypatch.setattr(health, "_check_redis", _ok)
    monkeypatch.setattr(health, "_check_minio", _ok)
    monkeypatch.setattr(health, "_check_ollama", _ok)
    monkeypatch.setattr(health, "_check_chromadb", _ok)
    monkeypatch.setattr(health, "_with_budget", _budget_ok)

    monkeypatch.setattr(settings, "BUILD_REVISION", "deadbeef", raising=False)
    monkeypatch.setattr(settings, "BUILD_DATE", "2026-07-01T00:00:00Z", raising=False)

    result = await health.health_details()

    assert result["status"] == "healthy"
    assert result["build"] == {
        "revision": "deadbeef",
        "built_at": "2026-07-01T00:00:00Z",
    }
