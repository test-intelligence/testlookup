"""Regression contracts for the verified, digest-only release path.

The release workflows used to build and deploy directly from a tag.  A tag can
refer to a revision which has never completed CI, and rebuilding separately in
each cloud makes "the release" three different artifact sets.  These tests
make the new boundary executable: CI proves one source SHA, the release path
promotes only its immutable image manifest, and cloud deploy workflows consume
that manifest rather than rebuilding images.

The tests intentionally inspect workflow YAML.  GitHub Actions syntax is not
executed during pytest, so this is the closest deterministic regression check
for the control-plane contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
VERIFIER = WORKFLOWS / "require-verified-sha.yml"
RELEASE = WORKFLOWS / "release.yml"
CLOUD_DEPLOYS = tuple(
    WORKFLOWS / name
    for name in ("deploy-eks.yml", "deploy-gke.yml", "deploy-aks.yml")
)
ALL_PROMOTION_WORKFLOWS = (RELEASE, *CLOUD_DEPLOYS)
COMPONENTS = ("backend", "frontend", "mcp")
MANIFEST_ARTIFACT_PREFIX = "verified-images-"


def _yaml(path: Path) -> dict:
    assert path.exists(), f"missing release-control workflow: {path.relative_to(ROOT)}"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{path.relative_to(ROOT)} must be a YAML mapping"
    return parsed


def _on(workflow: dict) -> dict:
    # PyYAML treats an unquoted `on` as YAML 1.1 boolean True.
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict), "workflow must declare an event mapping"
    return value


def _executable_steps(workflow: dict) -> str:
    """Return only executable action/run values, excluding explanatory comments."""
    commands: list[str] = []
    for job in workflow.get("jobs", {}).values():
        commands.append(str(job.get("uses", "")))
        for step in job.get("steps", []) or []:
            commands.extend(str(step.get(key, "")) for key in ("uses", "run"))
    return "\n".join(commands).lower()


def _called_verifier_jobs(workflow: dict) -> list[dict]:
    return [
        job
        for job in workflow.get("jobs", {}).values()
        if str(job.get("uses", "")).endswith("/.github/workflows/require-verified-sha.yml")
        or str(job.get("uses", "")) == "./.github/workflows/require-verified-sha.yml"
    ]


def _job_needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs or [])


def test_exact_sha_verifier_is_a_reusable_workflow_with_a_required_input():
    workflow = _yaml(VERIFIER)
    call = _on(workflow).get("workflow_call")
    assert isinstance(call, dict), "exact-SHA verifier must be reusable via workflow_call"
    source_sha = call.get("inputs", {}).get("source_sha")
    assert isinstance(source_sha, dict) and source_sha.get("required") is True, (
        "the verifier must require the source SHA it is authorizing"
    )


def test_exact_sha_verifier_queries_ci_for_the_requested_sha():
    text = VERIFIER.read_text(encoding="utf-8")
    assert "inputs.source_sha" in text, "verifier must use the caller's source SHA"
    assert "head_sha" in text, "verifier must match CI results by head_sha, not branch or tag name"
    assert "success" in text, "verifier must require a successful CI conclusion"
    assert "verified-images-${{ inputs.source_sha }}" in text, (
        "verified artifact name must be bound to the verified SHA"
    )


def test_release_tag_path_calls_the_exact_sha_verifier_before_promotion():
    workflow = _yaml(RELEASE)
    verifier_jobs = _called_verifier_jobs(workflow)
    assert verifier_jobs, "release.yml must call require-verified-sha.yml before publishing"
    assert any("source_sha" in (job.get("with") or {}) for job in verifier_jobs), (
        "release verifier call must pass an explicit source SHA"
    )

    push = _on(workflow).get("push", {})
    assert push.get("tags"), "release.yml must remain tag-triggered"


def test_release_resolves_an_annotated_tag_to_its_commit_before_verifying():
    """``github.sha`` can name a tag object; CI must authorize the commit it tags."""
    text = RELEASE.read_text(encoding="utf-8")
    assert "git fetch --tags --force" in text, (
        "release must fetch the tag object before resolving the release revision"
    )
    assert 'git rev-list -n 1 "$GITHUB_REF_NAME"' in text, (
        "release must resolve an annotated tag to its target commit"
    )
    assert "source_sha" in text, "resolved commit SHA must flow into the verification gate"
    assert "merge-base --is-ancestor" in text and "origin/main" in text, (
        "release must reject a tag whose resolved commit is outside main's verified history"
    )


@pytest.mark.parametrize("workflow_path", ALL_PROMOTION_WORKFLOWS)
def test_release_and_cloud_deploys_do_not_rebuild_or_push_images(workflow_path: Path):
    text = _executable_steps(_yaml(workflow_path))
    forbidden = ("docker build", "docker push", "docker/build-push-action")
    found = [token for token in forbidden if token in text]
    assert not found, (
        f"{workflow_path.name} rebuilds/pushes images ({', '.join(found)}); "
        "promotion must use the already verified immutable manifest"
    )


@pytest.mark.parametrize("workflow_path", ALL_PROMOTION_WORKFLOWS)
def test_each_promotion_consumer_uses_the_sha_named_verified_manifest(workflow_path: Path):
    workflow = _yaml(workflow_path)
    text = workflow_path.read_text(encoding="utf-8")
    verifier_jobs = _called_verifier_jobs(workflow)
    assert verifier_jobs, (
        f"{workflow_path.name} may deploy/publish only after calling the exact-SHA verifier"
    )
    assert MANIFEST_ARTIFACT_PREFIX in text, (
        f"{workflow_path.name} must retrieve the verified-images artifact, not reconstruct tags"
    )
    assert "release_manifest.py" in text, (
        f"{workflow_path.name} must validate/materialize the immutable release manifest"
    )


@pytest.mark.parametrize("workflow_path", ALL_PROMOTION_WORKFLOWS)
def test_promotion_consumers_pass_an_explicit_sha_to_the_verifier(workflow_path: Path):
    workflow = _yaml(workflow_path)
    jobs = _called_verifier_jobs(workflow)
    assert any("source_sha" in (job.get("with") or {}) for job in jobs), (
        f"{workflow_path.name} must pass a source_sha to require-verified-sha.yml"
    )


@pytest.mark.parametrize("workflow_path", ALL_PROMOTION_WORKFLOWS)
def test_manifest_consumption_is_ordered_after_exact_sha_verification(workflow_path: Path):
    """A verifier job that is not a dependency is only a decorative gate."""
    workflow = _yaml(workflow_path)
    verifier_names = {
        name
        for name, job in workflow.get("jobs", {}).items()
        if job in _called_verifier_jobs(workflow)
    }
    assert verifier_names, f"{workflow_path.name} has no exact-SHA verifier job"

    manifest_consumers = [
        (name, job)
        for name, job in workflow.get("jobs", {}).items()
        if "release_manifest.py" in "\n".join(
            str(step.get("run", "")) for step in job.get("steps", []) or []
        )
    ]
    assert manifest_consumers, f"{workflow_path.name} never consumes a release manifest"
    for name, job in manifest_consumers:
        assert verifier_names <= _job_needs(job), (
            f"{workflow_path.name}:{name} consumes deployment inputs without depending on "
            f"the exact-SHA verifier jobs {sorted(verifier_names)}"
        )


def test_legacy_gke_tag_build_workflow_is_removed():
    assert not (WORKFLOWS / "deploy-production.yml").exists(), (
        "deploy-production.yml is a second tag-build/deploy path; remove it so it cannot "
        "bypass the verified manifest promotion path"
    )


def test_manifest_tool_is_present_for_workflow_validation_and_materialization():
    tool = ROOT / "scripts" / "release" / "release_manifest.py"
    assert tool.exists(), "promotion workflows need scripts/release/release_manifest.py"
    text = tool.read_text(encoding="utf-8")
    for command in ("create", "validate", "materialize"):
        assert command in text, f"release manifest tool must expose {command!r}"
    for component in COMPONENTS:
        assert component in text, f"release manifest tool must require the {component} digest"
    assert "@sha256:" in text, "release manifest tool must reject mutable tag-only references"


def test_release_workflow_has_no_build_job_named_as_a_promotion_step():
    """Keep the naming/graph honest: a release must promote, never rebuild."""
    workflow = _yaml(RELEASE)
    bad_jobs = [
        name for name in workflow.get("jobs", {})
        if "build" in name.lower() or "push" in name.lower()
    ]
    assert not bad_jobs, f"release.yml contains rebuild jobs: {bad_jobs}"
