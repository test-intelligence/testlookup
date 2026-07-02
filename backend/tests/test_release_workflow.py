"""Release-pipeline regression — the semver release workflow contract.

`docker-compose.release.yml` lets users pin ``TESTLOOKUP_VERSION`` to a
published tag instead of the moving ``latest``. That promise is only real if
pushing a ``v*.*.*`` git tag actually publishes versioned GHCR images for all
three app images. CI can't run the workflow, so these tests pin its *contract*
by parsing ``.github/workflows/release.yml``: it must trigger on semver tags,
build the three images, and tag them with the semver derived from the git tag.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_VERSION = _ROOT / "VERSION"

_REGISTRY_PREFIX = "ghcr.io/anandtopu/testlookup"
_IMAGES = ("backend", "frontend", "mcp")


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert _WORKFLOW.exists(), "release workflow .github/workflows/release.yml is missing"
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_is_valid_yaml(workflow):
    assert isinstance(workflow, dict) and workflow.get("jobs"), "workflow has no jobs"


def test_triggers_on_semver_tag_push(workflow):
    # PyYAML parses the bare `on:` key as the boolean True, so accept either.
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), f"`on:` is not a mapping: {on!r}"
    tags = on.get("push", {}).get("tags", [])
    assert tags, "release workflow must trigger on tag push"
    assert any("v*" in t for t in tags), f"tag filter must match v* semver tags: {tags}"


def test_builds_all_three_app_images(workflow_text):
    for image in _IMAGES:
        ref = f"{_REGISTRY_PREFIX}/{image}"
        # The workflow templates the prefix via env, so accept either the
        # literal prefix or the metadata-action image line for each app image.
        assert ref in workflow_text or f"/{image}\n" in workflow_text, (
            f"release workflow never references the {image} image"
        )


def test_tags_images_with_semver(workflow_text):
    # metadata-action semver patterns are what derive :v1.2.3 / :1.2.3 / :1.2 / :1
    assert "type=semver" in workflow_text, "workflow must derive tags from the semver git tag"
    assert "pattern={{version}}" in workflow_text, "workflow must emit the full {{version}} tag"
    # Major/minor moving aliases make `:1` / `:1.2` usable for self-hosters.
    assert "{{major}}.{{minor}}" in workflow_text, "workflow should emit the {major}.{minor} alias"


def test_pushes_to_ghcr(workflow_text):
    assert "ghcr.io" in workflow_text, "release images must publish to GHCR"
    assert "push: true" in workflow_text, "build-push steps must push"


def test_grants_packages_write_to_build_job(workflow):
    jobs = workflow["jobs"]
    build_jobs = [
        name for name, job in jobs.items()
        if (job.get("permissions") or {}).get("packages") == "write"
    ]
    assert build_jobs, "no job grants packages:write — GHCR push would 403"


def test_logs_into_ghcr_with_github_token(workflow_text):
    assert "docker/login-action" in workflow_text, "workflow must log in to the registry"
    assert "secrets.GITHUB_TOKEN" in workflow_text, "GHCR login should use GITHUB_TOKEN"


def test_uses_docker_build_push_action(workflow_text):
    # One build-push step per app image.
    assert workflow_text.count("docker/build-push-action") >= len(_IMAGES), (
        "expected a build-push step per app image"
    )


def test_guards_tag_against_version_file(workflow_text):
    assert "VERSION" in workflow_text, "workflow should reference the VERSION file as a guard"


def test_version_file_is_semver_taggable():
    raw = _VERSION.read_text(encoding="utf-8").strip()
    parts = raw.lstrip("v").split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"VERSION must be a 3-part semver for the v*.*.* tag trigger: {raw!r}"
    )


# ── Supply-chain: SBOM + provenance + digest surfacing ────────────────────────
#
# The images build with `sbom: true` + `provenance: mode=max`, but that metadata
# is only actionable if an operator can find the content-addressable digest to
# pin and inspect — a moving tag can be re-pushed to point at different bits, a
# digest cannot. These tests pin that the workflow both *produces* the metadata
# and *surfaces* the per-image digest so a self-hoster can pin by `@sha256:…`.

def _build_push_steps() -> list[dict]:
    """Every docker/build-push-action step across the workflow's jobs."""
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict] = []
    for job in doc["jobs"].values():
        for step in job.get("steps", []) or []:
            if "docker/build-push-action" in str(step.get("uses", "")):
                steps.append(step)
    return steps


def test_every_build_push_step_ships_sbom_and_provenance():
    steps = _build_push_steps()
    assert len(steps) == len(_IMAGES), f"expected one build-push step per app image, got {len(steps)}"
    for step in steps:
        with_ = step.get("with", {})
        assert with_.get("sbom") is True, f"{step.get('name')} must build with sbom: true"
        assert with_.get("provenance") == "mode=max", (
            f"{step.get('name')} must build with provenance: mode=max"
        )


def test_every_build_push_step_has_an_id_for_the_digest_output():
    # build-push-action exposes its pushed digest via steps.<id>.outputs.digest;
    # without an id the digest can't be referenced downstream.
    for step in _build_push_steps():
        assert step.get("id"), f"build-push step {step.get('name')!r} needs an id to expose outputs.digest"


def test_summary_surfaces_each_image_digest(workflow_text):
    for image in _IMAGES:
        assert f"steps.build-{image}.outputs.digest" in workflow_text, (
            f"release summary must surface the {image} image digest"
        )


def test_summary_pins_and_verifies_by_digest(workflow_text):
    # The digest is only useful if the operator is shown the pin form and how to
    # verify the attestation it enables.
    assert "@${BACKEND_DIGEST}" in workflow_text, "summary must show the digest pin form (image@sha256:…)"
    assert "buildx imagetools inspect" in workflow_text, (
        "summary must show how to inspect the SBOM/provenance attestation"
    )
