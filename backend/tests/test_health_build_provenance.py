"""Regression tests for build-provenance surfacing on /health/details.

A self-host operator hitting ``GET /health/details`` should be able to see
exactly which commit + build the running container is — the ``build`` block
carries ``revision`` (git SHA) and ``built_at`` (build timestamp), injected
at image-build time (backend/Dockerfile ARGs, wired from CI).

These are network-free unit tests: the pure ``build_provenance`` helper is
exercised directly, and ``health_details`` is awaited with its dependency
probes monkeypatched so no live services are required.
"""
from __future__ import annotations

from pathlib import Path

import yaml

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


async def test_health_version_surfaces_build_without_probes(monkeypatch):
    """GET /health/version reports version + provenance and runs no dep probes.

    The endpoint exists precisely so a CD smoke test / uptime monitor can
    verify the running build fast, without paying for the six-service probe
    that /health/details runs. Guard that intent: fail any probe loudly if it
    is ever called from this path.
    """
    def _boom(*_a, **_kw):  # pragma: no cover - only runs on regression
        raise AssertionError("/health/version must not run dependency probes")

    monkeypatch.setattr(health, "_check_postgres", _boom)
    monkeypatch.setattr(health, "_check_mongo", _boom)
    monkeypatch.setattr(health, "_check_redis", _boom)
    monkeypatch.setattr(health, "_check_minio", _boom)
    monkeypatch.setattr(health, "_check_ollama", _boom)
    monkeypatch.setattr(health, "_check_chromadb", _boom)

    monkeypatch.setattr(settings, "BUILD_REVISION", "cafe1234", raising=False)
    monkeypatch.setattr(settings, "BUILD_DATE", "2026-08-01T09:30:00Z", raising=False)

    result = await health.version()

    assert result["status"] == "ok"
    assert result["version"] == settings.APP_VERSION
    assert result["build"] == {
        "revision": "cafe1234",
        "built_at": "2026-08-01T09:30:00Z",
    }
    assert result["env"] == settings.APP_ENV
    assert isinstance(result["uptime_seconds"], int)
    assert result["uptime_seconds"] >= 0
    # A timestamp is present and ISO-ish (has the date/time separator).
    assert "T" in result["timestamp"]


async def test_health_version_defaults_provenance_to_unknown(monkeypatch):
    """Local/dev runs with no image-build env report 'unknown', not empty."""
    monkeypatch.setattr(settings, "BUILD_REVISION", "", raising=False)
    monkeypatch.setattr(settings, "BUILD_DATE", "", raising=False)

    result = await health.version()

    assert result["build"] == {"revision": "unknown", "built_at": "unknown"}


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


def test_ci_backend_image_receives_exact_build_provenance():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["build-images"]["steps"]
    provenance = next(step for step in steps if step.get("id") == "build-provenance")
    backend_build = next(step for step in steps if step.get("id") == "build-backend")

    assert "date -u +'%Y-%m-%dT%H:%M:%SZ'" in provenance["run"]
    assert "$GITHUB_OUTPUT" in provenance["run"]
    build_args = backend_build["with"]["build-args"]
    assert "BUILD_REVISION=${{ github.sha }}" in build_args
    assert (
        "BUILD_DATE=${{ steps.build-provenance.outputs.build_date }}" in build_args
    )

    dockerfile = (root / "backend/Dockerfile").read_text(encoding="utf-8")
    assert 'ARG BUILD_REVISION=""' in dockerfile
    assert 'ARG BUILD_DATE=""' in dockerfile
    assert "BUILD_REVISION=$BUILD_REVISION" in dockerfile
    assert "BUILD_DATE=$BUILD_DATE" in dockerfile


def test_homelab_backend_image_receives_exact_build_provenance():
    """A homelab build must identify the commit that its image contains.

    The health endpoint cannot prove that the requested candidate is serving
    when the deploy script leaves both Docker build arguments empty.  Refuse a
    tracked dirty tree, derive the values once, and pass both arguments to the
    backend production build.
    """
    root = Path(__file__).resolve().parents[2]
    deploy = (root / "homelabsetup/deploy-homelab.sh").read_text(encoding="utf-8")

    assert 'BUILD_REVISION="$(git rev-parse HEAD)"' in deploy
    assert 'BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in deploy
    assert 'git -C "$repo_root" diff --quiet --ignore-submodules --' in deploy
    assert 'git -C "$repo_root" diff --cached --quiet --ignore-submodules --' in deploy
    assert 'git -C "$repo_root" status --porcelain' in deploy
    assert '--untracked-files=all --ignored=matching --' in deploy

    backend_build = deploy.split('log "Building backend image', 1)[1].split(
        'log "Building frontend image', 1
    )[0]
    assert '--build-arg BUILD_REVISION="$BUILD_REVISION"' in backend_build
    assert '--build-arg BUILD_DATE="$BUILD_DATE"' in backend_build
