"""Regression tests for the image-manifest drift guard (US-13.1).

Run:  python -m pytest scripts/release/test_image_drift.py -v

Two jobs:
  1. pin the *live* repository — every deployment surface agrees with
     deploy/images.manifest.txt right now;
  2. prove the guard actually fails when a surface drifts. A guard that can
     only pass is a guard that will silently rot, which is precisely how the
     compose/k8s/mirror-script image lists diverged in the first place.

Each drift test copies the handful of real files the checker reads into a
tmp_path, mutates one of them, and asserts the specific error surfaces.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_image_drift import (  # noqa: E402
    check,
    check_latest_tags,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

COPIED_FILES = (
    "deploy/images.manifest.txt",
    "docker-compose.yml",
    "docker-compose.release.yml",
    "docker-compose.dev-lite.yml",
    "docker-compose.airgap.yml",
    "openshiftsetup/mirror-images.sh",
    "openshiftsetup/deploy-openshift-artifactory.sh",
)
COPIED_TREES = ("k8s",)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A minimal copy of the real repo containing only what the checker reads."""
    for rel in COPIED_FILES:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, dst)
    for rel in COPIED_TREES:
        shutil.copytree(REPO_ROOT / rel, tmp_path / rel)
    return tmp_path


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture text not found in {path}: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _joined(errors: list[str]) -> str:
    return "\n".join(errors)


# ── 1. The live repo ────────────────────────────────────────────────────────


def test_live_repo_has_no_image_drift():
    assert check(REPO_ROOT) == []


def test_live_repo_has_no_floating_compose_tags():
    assert check_latest_tags(REPO_ROOT) == []


def test_manifest_shape():
    rows = load_manifest(REPO_ROOT / "deploy" / "images.manifest.txt")
    app = {r.name for r in rows if r.role == "app"}
    # worker/beat/seed-init reuse the backend image — exactly three app images.
    assert app == {"testlookup/backend", "testlookup/frontend", "testlookup/mcp"}
    core = {r.ref for r in rows if r.bundle == "core"}
    # Everything a default `make offline-bundle` must carry.
    assert "postgres:16-alpine" in core
    assert "busybox:1.36" in core
    # The deliberate MinIO skew: both server tags ship in the bundle.
    minio = sorted(r.ref for r in rows if r.name == "minio/minio")
    assert len(minio) == 2, "the compose/k8s MinIO skew must stay explicit in the manifest"
    # The optional LLM images are opt-in, never core.
    llm = {r.ref for r in rows if r.bundle == "llm"}
    assert llm == {"ollama/ollama:0.5.4", "chromadb/chroma:1.5.9"}


# ── 2. Drift is actually detected ───────────────────────────────────────────


def test_compose_tag_bump_without_manifest_update_fails(repo: Path):
    _sub(repo / "docker-compose.yml", "postgres:16-alpine", "postgres:17-alpine")
    errors = _joined(check(repo))
    assert "postgres:17-alpine" in errors
    assert "images.manifest.txt" in errors


def test_k8s_tag_bump_without_manifest_update_fails(repo: Path):
    _sub(
        repo / "k8s/overlays/openshift-artifactory/infra-postgres.yaml",
        "postgres:16-alpine",
        "postgres:17-alpine",
    )
    errors = _joined(check(repo))
    assert "postgres:17-alpine" in errors
    assert "surface 'k8s'" in errors


def test_manifest_entry_nothing_references_fails(repo: Path):
    manifest = repo / "deploy/images.manifest.txt"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\nnobody/uses-me:1.0   infra  compose,k8s   core\n",
        encoding="utf-8",
    )
    errors = _joined(check(repo))
    assert "nobody/uses-me:1.0" in errors
    assert "no compose file" in errors


def test_airgap_override_missing_a_service_fails(repo: Path):
    _sub(
        repo / "docker-compose.airgap.yml",
        "  redis:\n    image: ${TESTLOOKUP_REGISTRY:?TESTLOOKUP_REGISTRY must be set}/redis:7-alpine\n",
        "",
    )
    errors = _joined(check(repo))
    assert "redis:7-alpine" in errors
    assert "docker.io" in errors, "the operator must be told what actually breaks"


def test_openshift_overlay_missing_a_remap_fails(repo: Path):
    _sub(
        repo / "k8s/overlays/openshift-artifactory/kustomization.yaml",
        '  - name: busybox\n    newName: ARTIFACTORY_REGISTRY_PLACEHOLDER/busybox\n    newTag: "1.36"\n',
        "",
    )
    errors = _joined(check(repo))
    assert "busybox:1.36" in errors
    assert "does NOT remap" in errors


def test_openshift_overlay_real_app_tag_fails(repo: Path):
    _sub(
        repo / "k8s/overlays/openshift-artifactory/kustomization.yaml",
        "    newName: ARTIFACTORY_REGISTRY_PLACEHOLDER/testlookup/backend\n    newTag: APP_TAG_PLACEHOLDER",
        "    newName: ARTIFACTORY_REGISTRY_PLACEHOLDER/testlookup/backend\n    newTag: 0.1.0",
    )
    errors = _joined(check(repo))
    assert "APP_TAG_PLACEHOLDER" in errors


def test_hardcoded_infra_images_in_mirror_script_fails(repo: Path):
    script = repo / "openshiftsetup/mirror-images.sh"
    _sub(
        script,
        'INFRA_IMAGES="${INFRA_IMAGES:-$(manifest_refs k8s infra core,llm | tr \'\\n\' \' \')}"',
        'INFRA_IMAGES="postgres:16-alpine redis:7-alpine"',
    )
    errors = _joined(check(repo))
    assert "hardcoded INFRA_IMAGES" in errors


def test_script_not_sourcing_the_manifest_fails(repo: Path):
    script = repo / "openshiftsetup/mirror-images.sh"
    script.write_text(
        script.read_text(encoding="utf-8").replace("image-manifest.sh", "somewhere-else.sh"),
        encoding="utf-8",
    )
    errors = _joined(check(repo))
    assert "does not source" in errors


# ── 3. Floating tags ────────────────────────────────────────────────────────


def test_latest_tag_in_any_compose_file_fails(repo: Path):
    _sub(repo / "docker-compose.dev-lite.yml", "minio/mc:RELEASE.2024-11-05T11-29-45Z", "minio/mc:latest")
    errors = _joined(check_latest_tags(repo))
    assert "floating ':latest'" in errors


def test_untagged_image_in_compose_fails(repo: Path):
    _sub(repo / "docker-compose.dev-lite.yml", "image: mongo:7", "image: mongo")
    errors = _joined(check_latest_tags(repo))
    assert "unpinned image" in errors


def test_interpolated_release_tag_is_not_flagged(repo: Path):
    # docker-compose.release.yml legitimately ends app image lines with
    # `:${TESTLOOKUP_VERSION:-latest}` — a deploy-time choice, not a pin bug.
    assert check_latest_tags(repo) == []
    assert "TESTLOOKUP_VERSION:-latest" in (repo / "docker-compose.release.yml").read_text(
        encoding="utf-8"
    )
