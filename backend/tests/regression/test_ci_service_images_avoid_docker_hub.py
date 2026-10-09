"""CI's service containers are not pulled from Docker Hub anonymously.

GitHub-hosted runners pull without credentials and share outbound IPs, so they
share Docker Hub's unauthenticated pull limit. On 2026-10-09 both backend jobs
of PR #225 failed at "Initialize containers" with "toomanyrequests: You have
reached your unauthenticated pull rate limit", on the first run and on the
re-run, before a single test ran. The jobs now pull the same official images
from AWS's mirror (``public.ecr.aws/docker/library/...``).

A bare ``postgres:16-alpine`` (or ``docker.io/...``) here would bring that back.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _registry(image: str) -> str:
    """The registry host of an image reference ("docker.io" when it has none)."""
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return first
    return "docker.io"


def test_service_images_come_from_a_registry_other_than_docker_hub():
    if not CI_WORKFLOW.exists():
        pytest.skip("ci.yml not present in this checkout")
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    images = {
        f"{job}.{name}": service["image"]
        for job, spec in jobs.items()
        for name, service in (spec.get("services") or {}).items()
    }
    assert images, "ci.yml declares no service containers: this test no longer guards anything"
    pulled_from_hub = {where: image for where, image in images.items() if _registry(image) in ("docker.io", "registry-1.docker.io")}
    assert pulled_from_hub == {}


def test_every_buildx_setup_pulls_buildkit_and_base_images_through_mirrors():
    """The image-scan jobs died the same way at "Set up Docker Buildx":
    buildkit's own image (moby/buildkit) and the Dockerfiles' python/node/nginx
    bases are Docker Hub pulls too."""
    if not CI_WORKFLOW.exists():
        pytest.skip("ci.yml not present in this checkout")
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    setups = {
        f"{job}: {step.get('name')}": step.get("with") or {}
        for job, spec in jobs.items()
        for step in spec.get("steps") or []
        if str(step.get("uses", "")).startswith("docker/setup-buildx-action")
    }
    assert setups, "ci.yml sets up no buildx: this test no longer guards anything"
    for where, options in setups.items():
        driver_image = str(options.get("driver-opts", "")).partition("image=")[2].split()[0:1]
        assert driver_image and _registry(driver_image[0]) != "docker.io", where
        assert 'mirrors = ["mirror.gcr.io"]' in str(options.get("buildkitd-config-inline", "")), where


@pytest.mark.parametrize("image, registry", [
    ("postgres:16-alpine", "docker.io"),
    ("library/postgres:16-alpine", "docker.io"),
    ("docker.io/library/postgres:16-alpine", "docker.io"),
    ("public.ecr.aws/docker/library/postgres:16-alpine", "public.ecr.aws"),
    ("quay.io/minio/minio:RELEASE.2025", "quay.io"),
    ("localhost:5000/postgres:16", "localhost:5000"),
])
def test_registry_of_an_image_reference(image, registry):
    assert _registry(image) == registry
