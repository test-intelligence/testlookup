"""Offline regressions for authenticated release image transport."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
import release_manifest as manifests  # noqa: E402


SOURCE_SHA = "a" * 40
DIGESTS = {
    component: format(index, "x") * 64
    for index, component in enumerate(manifests.COMPONENTS, start=1)
}


def _source_ref(component: str) -> str:
    return f"{manifests.EXPECTED_SOURCE_NAMES[component]}@sha256:{DIGESTS[component]}"


def _release_manifest() -> dict:
    return manifests.create_manifest(
        source_sha=SOURCE_SHA,
        version="v1.2.3",
        images={component: _source_ref(component) for component in manifests.COMPONENTS},
    )


def _stub(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _file_snapshot(directory: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _rendered_document(rendered: str, *, kind: str, name: str) -> str:
    for document in re.split(r"^---\s*$", rendered, flags=re.MULTILINE):
        if re.search(rf"^kind:\s*{re.escape(kind)}\s*$", document, flags=re.MULTILINE) and re.search(
            rf"^\s+name:\s*{re.escape(name)}\s*$", document, flags=re.MULTILINE
        ):
            return document
    raise AssertionError(f"rendered output has no {kind}/{name}")


@pytest.mark.parametrize("skip_mirror", [False, True], ids=["copy", "skip-mirror"])
def test_openshift_release_authenticates_and_uses_destination_digests(tmp_path: Path, skip_mirror: bool):
    """The signed complete set reaches copies, rendering and skip-mirror verification unchanged."""
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(_release_manifest()), encoding="utf-8")
    provenance_path = tmp_path / "release.intoto.jsonl"
    provenance_path.write_text('{"stub":"attestation bundle"}\n', encoding="utf-8")
    config_path = tmp_path / "artifactory.env"
    config_path.write_text(
        'RELEASE_MANIFEST=""\nRELEASE_PROVENANCE_BUNDLE=""\n', encoding="utf-8"
    )

    push_prefix = "push.example.test/releases"
    pull_prefix = "pull.internal.test/releases"
    push_refs = manifests.materialize_destination_refs(
        _release_manifest(), destination_prefix=push_prefix
    )["destination_refs"]
    pull_refs = manifests.materialize_destination_refs(
        _release_manifest(), destination_prefix=pull_prefix
    )["destination_refs"]

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "commands.log"
    rendered_path = tmp_path / "rendered.yaml"
    real_kustomize = shutil.which("kustomize")
    assert real_kustomize is not None, "release transport tests require the CI-pinned Kustomize"
    _stub(stubs, "gh", "printf 'gh\\t%s\\n' \"$*\" >> \"$STUB_LOG\"")
    _stub(stubs, "skopeo", "printf 'skopeo\\t%s\\n' \"$*\" >> \"$STUB_LOG\"")
    _stub(
        stubs,
        "kustomize",
        "printf 'kustomize\\t%s\\n' \"$*\" >> \"$STUB_LOG\"\n"
        "if [ \"${1:-}\" = build ]; then\n"
        "  \"$REAL_KUSTOMIZE\" \"$@\" | tee \"$STUB_RENDER_CAPTURE\"\n"
        "else\n"
        "  exec \"$REAL_KUSTOMIZE\" \"$@\"\n"
        "fi",
    )
    _stub(
        stubs,
        "oc",
        "printf 'oc\\t%s\\n' \"$*\" >> \"$STUB_LOG\"\n"
        "if [ \"${1:-}\" = whoami ]; then printf 'stub-user\\n'; fi",
    )
    _stub(stubs, "docker", "printf 'docker\\t%s\\n' \"$*\" >> \"$STUB_LOG\"; exit 99")

    k8s_tree = tmp_path / "k8s"
    shutil.copytree(ROOT / "k8s", k8s_tree)
    overlay = k8s_tree / "overlays" / "openshift-artifactory"
    overlay_before = _file_snapshot(overlay)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(stubs) + os.pathsep + env["PATH"],
            "STUB_LOG": str(log),
            "REAL_KUSTOMIZE": real_kustomize,
            "STUB_RENDER_CAPTURE": str(rendered_path),
            "ARTIFACTORY_ENV_FILE": str(config_path),
            "ARTIFACTORY_REGISTRY": push_prefix,
            "ARTIFACTORY_PULL_REGISTRY": pull_prefix,
            "ARTIFACTORY_USERNAME": "",
            "ARTIFACTORY_PASSWORD": "",
            "RELEASE_MANIFEST": str(manifest_path),
            "RELEASE_PROVENANCE_BUNDLE": str(provenance_path),
            "OPENSHIFT_OVERLAY_DIR": str(overlay),
            "NAMESPACE": "release-test",
            "STORAGE_CLASS": "stub-storage",
            "ROUTE_HOST_FRONTEND": "app.example.test",
            "ROUTE_HOST_API": "api.example.test",
            "ROUTE_HOST_MCP": "mcp.example.test",
            "GRANT_NONROOT": "false",
            "GRANT_ANYUID": "false",
        }
    )
    command = [
        "bash",
        str(ROOT / "openshiftsetup" / "deploy-openshift-artifactory.sh"),
        "--dry-run",
        "--skip-admin",
        "--no-anyuid",
    ]
    if skip_mirror:
        command.append("--skip-mirror")

    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    rendered = rendered_path.read_text(encoding="utf-8")
    redis_pvc = _rendered_document(rendered, kind="PersistentVolumeClaim", name="redis-data")
    assert re.search(r"^\s+storageClassName:\s*stub-storage\s*$", redis_pvc, flags=re.MULTILINE)
    assert "STORAGE_CLASS_PLACEHOLDER" not in rendered

    rendered_images = re.findall(r"^\s+image:\s*(\S+)\s*$", rendered, flags=re.MULTILINE)
    assert set(rendered_images) == set(pull_refs.values())
    assert all(re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", ref) for ref in rendered_images)
    assert _rendered_document(rendered, kind="Deployment", name="testlookup-backend")
    assert _rendered_document(rendered, kind="Route", name="testlookup-frontend")

    assert _file_snapshot(overlay) == overlay_before
    assert (overlay / "infra-redis.yaml").read_text(encoding="utf-8").count(
        "STORAGE_CLASS_PLACEHOLDER"
    ) == 1
    commands = log.read_text(encoding="utf-8").splitlines()
    assert commands[0].startswith("gh\tattestation verify "), commands
    inspections = [line for line in commands if line.startswith("skopeo\tinspect --raw ")]
    assert len(inspections) == len(manifests.COMPONENTS)
    assert all(any(push_refs[component] in line for line in inspections) for component in manifests.COMPONENTS)

    copies = [line for line in commands if line.startswith("skopeo\tcopy ")]
    assert len(copies) == (0 if skip_mirror else len(manifests.COMPONENTS))
    assert all("--all --preserve-digests" in line and "@sha256:" in line for line in copies)
    edits = [line for line in commands if line.startswith("kustomize\tedit set image ")]
    assert len(edits) == len(manifests.COMPONENTS)
    assert max(commands.index(line) for line in inspections) < min(commands.index(line) for line in edits)
    assert all(any(pull_refs[component] in line for line in edits) for component in manifests.COMPONENTS)
    builds = [line for line in commands if line == f"kustomize\tbuild {overlay}"]
    assert len(builds) == 1
    assert not any(line.startswith("oc\tkustomize ") for line in commands)
    assert not any(line.startswith("docker\t") for line in commands)

    deploy_source = (ROOT / "openshiftsetup" / "deploy-openshift-artifactory.sh").read_text(encoding="utf-8")
    assert 'MIGRATION_IMAGE="${RELEASE_PULL_REFS[backend]}"' in deploy_source
    assert "printf '%s\\n' \"$RENDERED_OVERLAY\" | \"$KCLI\" apply -f -" in deploy_source


def test_deploy_k8s_rejects_schema_one_before_cluster_or_overlay_work(tmp_path: Path):
    legacy = _release_manifest()
    legacy["schema_version"] = manifests.LEGACY_SCHEMA_VERSION
    legacy["images"] = {
        component: legacy["images"][component]
        for component in manifests.APPLICATION_COMPONENTS
    }
    materialized = manifests.materialize_destination_refs(
        legacy, destination_prefix="registry.example.test/releases"
    )
    manifest_path = tmp_path / "legacy-materialized.json"
    manifest_path.write_text(json.dumps(materialized), encoding="utf-8")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "cluster.log"
    _stub(stubs, "kubectl", "printf 'kubectl\\t%s\\n' \"$*\" >> \"$STUB_LOG\"")
    _stub(stubs, "kustomize", "printf 'kustomize\\t%s\\n' \"$*\" >> \"$STUB_LOG\"")
    env = os.environ.copy()
    env.update({"PATH": str(stubs) + os.pathsep + env["PATH"], "STUB_LOG": str(log)})

    result = subprocess.run(
        ["bash", "scripts/deploy-k8s.sh", "--cloud=self-hosted",
         f"--release-manifest={manifest_path}", "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "inspection only" in result.stderr
    assert "complete schema-v2 deployment manifest" in result.stderr
    assert not log.exists(), "schema rejection must happen before kubectl or kustomize is invoked"


def test_tagged_telemetry_preload_pulls_only_redistributable_digests(tmp_path: Path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "docker.log"
    docker = _stub(
        stubs,
        "docker",
        "printf '%s\\n' \"$*\" >> \"$STUB_LOG\"\n"
        "if [ \"${1:-}\" = image ] && [ \"${2:-}\" = inspect ]; then printf 'sha256:stub-image-id\\n'; fi",
    )
    env = os.environ.copy()
    env.update({"DOCKER": str(docker), "STUB_LOG": str(log)})

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "release" / "pull-offline-telemetry-images.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rows = []
    for line in (ROOT / "deploy" / "images.manifest.txt").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        ref, role, surfaces, bundle = stripped.split()
        if role == "infra" and "telemetry" in surfaces.split(","):
            rows.append((ref, bundle))
    expected = [ref for ref, bundle in rows if bundle == "telemetry"]
    external = {ref for ref, bundle in rows if bundle == "telemetry-external"}
    docker_commands = log.read_text(encoding="utf-8").splitlines()
    pulled = [line.removeprefix("pull ") for line in docker_commands if line.startswith("pull ")]
    assert pulled == expected
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in pulled)
    assert external.isdisjoint(pulled)
    archive_tags = [line for line in docker_commands if line.startswith("tag ") and "localhost/testlookup-telemetry/" in line]
    assert len(archive_tags) == len(expected)

    workflow = (ROOT / ".github" / "workflows" / "offline-bundle.yml").read_text(encoding="utf-8")
    preload = workflow.index("bash scripts/release/pull-offline-telemetry-images.sh")
    skip_pull = workflow.index("args+=(--skip-build --skip-pull)")
    assert preload < skip_pull
