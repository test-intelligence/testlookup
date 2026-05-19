"""Regression: docker push failed with ``/app/__client_sdks_staged: not found``.

Bug pinned (2026-05-19): GitHub Actions docker build-push step
failed with ``failed to compute cache key: failed to calculate
checksum of ref ...: "/app/__client_sdks_staged": not found``. The
production stage of ``backend/Dockerfile`` did
``COPY --from=builder /app/__client_sdks_staged ./client_sdks``,
but the source path didn't exist unless the caller's CI staged
SDKs into ``./backend/__client_sdks_staged/`` beforehand. The five
cloud-deploy workflows (deploy-{aks,gke,eks,staging,production}.yml)
skipped that staging step entirely.

Fix:
  1. ``backend/Dockerfile`` builder stage now runs
     ``mkdir -p /app/__client_sdks_staged && touch
     /app/__client_sdks_staged/.keep`` so the path always exists.
  2. All five cloud-deploy workflows added a "Stage client SDKs
     into backend context" step that rsyncs ``./client/`` to
     ``./backend/__client_sdks_staged/`` so the resulting image
     actually carries SDK artifacts.

What this file pins:

  * Dockerfile builder stage creates the ``__client_sdks_staged``
    directory + ``.keep`` sentinel.
  * Production stage's COPY line uses ``--from=builder``.
  * Every deploy workflow that builds the backend image has a
    "Stage client SDKs" step BEFORE the docker build.

The check is intentionally a string match on file content — this
test guards against an accidental revert.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def test_dockerfile_builder_creates_sdk_staging_sentinel():
    """Without this, BuildKit fails to compute the cache key for the
    cross-stage COPY when the caller hasn't staged SDKs."""
    src = DOCKERFILE.read_text(encoding="utf-8")
    assert "mkdir -p /app/__client_sdks_staged" in src, (
        "Builder stage must create /app/__client_sdks_staged so the "
        "production stage's cross-stage COPY always finds the path."
    )
    assert "/app/__client_sdks_staged/.keep" in src, (
        "Builder must touch a .keep sentinel so the directory has at "
        "least one file — BuildKit's checksum step has historically "
        "tripped on empty cross-stage source directories."
    )


def test_dockerfile_production_copy_references_builder_stage():
    src = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --from=builder /app/__client_sdks_staged ./client_sdks" in src


@pytest.mark.parametrize("workflow", [
    "deploy-aks.yml",
    "deploy-gke.yml",
    "deploy-eks.yml",
    "deploy-staging.yml",
    "deploy-production.yml",
])
def test_deploy_workflow_has_sdk_staging_step(workflow: str):
    """Every cloud-deploy workflow that builds the backend image MUST
    stage SDKs into the build context — otherwise the resulting image
    builds successfully (thanks to the .keep sentinel) but serves an
    empty /api/v1/sdk/{lang}."""
    path = WORKFLOWS_DIR / workflow
    assert path.exists(), f"{workflow} missing from .github/workflows/"
    text = path.read_text(encoding="utf-8")
    assert "Stage client SDKs into backend context" in text, (
        f"{workflow}: missing the 'Stage client SDKs into backend "
        "context' step — the image will build but ship empty SDKs."
    )
    assert "__client_sdks_staged" in text, (
        f"{workflow}: SDK staging step must reference the "
        "__client_sdks_staged destination."
    )
