"""The K3s deploy runs the exact images it pushed: by digest, never by tag.

The in-cluster registry of the K3s install is plain HTTP with no login, and a
stock registry lets a push replace an existing tag. While the Deployments named
their images by tag (and pulled on every pod start), a later push under a live
``build-...`` tag was picked up by the next pod restart; the deploy-time digest
check ran once and never saw it (security audit 2026-11-18).

The rule: the per-deploy overlay ``k3ssetup/deploy-k3s.sh`` renders names every
TestLookup image as ``<registry>/testlookup/<image>@sha256:<64 hex>``, the
script refuses to apply a render in which one of them is not, and the tracked
overlay no longer forces a registry round trip on every pod start.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.shell_utils import bash_environment

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "k3ssetup" / "deploy-k3s.sh"
OVERLAY = REPO / "k8s" / "overlays" / "k3s"

# Obviously fake digests: one repeated hex digit each.
DIGESTS = {name: "sha256:" + digit * 64 for name, digit in (("backend", "a"), ("frontend", "b"), ("mcp", "c"))}
APP_IMAGE = re.compile(r"/testlookup/(backend|frontend|mcp)([:@]|$)")


def _find_bash() -> str | None:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    return None if found and "system32" in found.lower() else found


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="requires POSIX bash")


def _function(name: str) -> str:
    source = DEPLOY.read_text(encoding="utf-8")
    match = re.search(rf"^([ \t]*){re.escape(name)}\(\) \{{$", source, re.M)
    assert match, f"{name}() is no longer in deploy-k3s.sh"
    indent = match.group(1)
    lines = source[match.start():].splitlines()
    for index, line in enumerate(lines[1:], 1):
        if line == f"{indent}}}":
            return textwrap.dedent("\n".join(lines[: index + 1]))
    raise AssertionError(f"no closing brace for {name}()")


def _bash(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / "runner.sh"
    runner.write_text("set -euo pipefail\n" + body, encoding="utf-8", newline="\n")
    assert BASH is not None
    return subprocess.run(
        [BASH, str(runner)], cwd=tmp_path, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=bash_environment(BASH), timeout=60, check=False,
    )


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


def _kustomize(directory: Path) -> str | None:
    """The rendered manifests, or None when no kustomize is installed here."""
    for argv in (["kustomize", "build"], ["kubectl", "kustomize"]):
        if shutil.which(argv[0]):
            done = subprocess.run(
                [*argv, str(directory)], capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            assert done.returncode == 0, done.stderr
            return done.stdout
    return None


def _images(manifests: str) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "image" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for doc in yaml.safe_load_all(manifests):
        walk(doc)
    return found


def _render_setup() -> str:
    return (
        "NAMESPACE=testlookup\nK3S_HOST=site.example.test\nREGISTRY=reg.example.test:30500\n"
        'K3S_PATCHES_DIR=""\nOVERLAY_IMAGE_PREFIX="registry.local:30500/testlookup"\n'
        + _function("render_overlay") + "\n"
    )


def _render_call(render_dir: Path, digests: dict[str, str]) -> str:
    return (
        f'render_overlay "{_posix(render_dir)}" build-20260101-000000 '
        f'"{digests["backend"]}" "{digests["frontend"]}" "{digests["mcp"]}"'
    )


@needs_bash
def test_the_rendered_overlay_names_every_app_image_by_digest(tmp_path: Path):
    # A private copy of the manifests, so the render never lands in the repo.
    shutil.copytree(REPO / "k8s", tmp_path / "k8s")
    render_dir = tmp_path / "k3ssetup" / ".render"
    render_dir.parent.mkdir()

    result = _bash(tmp_path, _render_setup() + _render_call(render_dir, DIGESTS) + "\n")
    assert result.returncode == 0, result.stderr

    kustomization = yaml.safe_load((render_dir / "kustomization.yaml").read_text(encoding="utf-8"))
    entries = {entry["newName"].rsplit("/", 1)[-1]: entry for entry in kustomization["images"]}
    assert set(entries) == set(DIGESTS)
    for name, entry in entries.items():
        assert entry["digest"] == DIGESTS[name], entry
        assert "newTag" not in entry, f"{name}: a tag beside the digest is what the cluster must not follow"
        assert entry["newName"] == f"reg.example.test:30500/testlookup/{name}"

    manifests = _kustomize(render_dir)
    if manifests is None:
        pytest.skip("no kustomize or kubectl on PATH: the render itself was checked above")
    app_images = [image for image in _images(manifests) if APP_IMAGE.search(image)]
    # backend + 6 workers/beat + frontend + mcp + the two backup jobs' object-store steps.
    assert len(app_images) >= 9, app_images
    assert "BUILD_TAG_PLACEHOLDER" not in manifests
    for image in app_images:
        name = APP_IMAGE.search(image).group(1)
        assert image == f"reg.example.test:30500/testlookup/{name}@{DIGESTS[name]}", image
    backend = next(
        doc for doc in yaml.safe_load_all(manifests)
        if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"] == "testlookup-backend"
    )
    [container] = backend["spec"]["template"]["spec"]["containers"]
    assert container["image"].endswith("@" + DIGESTS["backend"])
    # The image is content-addressed now: a cached copy is the right copy, and a
    # pod restart no longer needs the registry to be up.
    assert container["imagePullPolicy"] == "IfNotPresent"


@needs_bash
def test_a_tag_or_a_malformed_digest_is_refused(tmp_path: Path):
    """One bash run for both halves: the render refuses anything that is not a
    sha256 digest, and the pre-apply check names a tag-only image reference."""
    render_dir = tmp_path / "k3ssetup" / ".render"
    render_dir.parent.mkdir()
    manifest = textwrap.dedent(
        f"""\
        spec:
          containers:
            - name: backend
              image: reg.example.test:30500/testlookup/backend@{DIGESTS['backend']}
            - image: reg.example.test:30500/testlookup/frontend:build-20260101-000000
            - image: "reg.example.test:30500/testlookup/mcp"
            - image: reg.example.test:30500/testlookup/mcp:build-1@sha256:abc
            - image: valkey/valkey:8.1-alpine
            - image: busybox:1.36
        """
    )
    (tmp_path / "manifest.yaml").write_text(manifest, encoding="utf-8", newline="\n")
    cases = {
        "TAG": {**DIGESTS, "backend": "build-20260101-000000"},
        "SHORT": {**DIGESTS, "mcp": "sha256:abc"},
        "EMPTY": {**DIGESTS, "frontend": ""},
    }
    body = _render_setup() + _function("unpinned_app_images") + "\n"
    for label, digests in cases.items():
        body += (
            f"if {_render_call(render_dir, digests)} >/dev/null 2>&1; "
            f"then echo {label}=accepted; else echo {label}=refused; fi\n"
        )
    body += 'echo "--unpinned--"\nunpinned_app_images < manifest.yaml\n'
    result = _bash(tmp_path, body)
    assert result.returncode == 0, result.stderr
    head, _, unpinned = result.stdout.partition("--unpinned--\n")
    assert head.split() == ["TAG=refused", "SHORT=refused", "EMPTY=refused"]
    assert unpinned.split() == [
        "reg.example.test:30500/testlookup/frontend:build-20260101-000000",
        "reg.example.test:30500/testlookup/mcp",
        "reg.example.test:30500/testlookup/mcp:build-1@sha256:abc",
    ], "the digest reference and the two data-store images must not be reported"


def test_the_deploy_passes_the_resolved_digests_all_the_way_through():
    source = DEPLOY.read_text(encoding="utf-8")
    # Both ways of choosing the build end with three digests read from the registry.
    build_path = source[source.index('header "Step 2 — Build and Push Container Images"'):]
    reuse_path = build_path[build_path.index("Skipping image builds (--skip-build)"):]
    reuse_path = reuse_path[: reuse_path.index("# ── Step 3: Create Namespace")]
    for name in ("BACKEND", "FRONTEND", "MCP"):
        assert f'{name}_DIGEST=$(registry_manifest_digest "$PUSH_REGISTRY"' in build_path
        assert f'{name}_DIGEST=$(registry_manifest_digest "$LOOKUP_REGISTRY"' in reuse_path, (
            f"--skip-build must resolve the {name.lower()} digest too, or it would deploy by tag"
        )
    assert (
        'render_overlay "$RENDER_DIR" "$BUILD_TAG" "$BACKEND_DIGEST" "$FRONTEND_DIGEST" "$MCP_DIGEST"'
        in source
    )
    # The render is checked before anything is applied.
    check = source.index("unpinned_app_images)")
    assert source.index('render_overlay "$RENDER_DIR"') < check < source.index('kubectl apply -k "$RENDER_DIR"')
    # The migration Job and the post-rollout authority check use the digest as well.
    assert 'MIGRATION_IMAGE="${REGISTRY}/testlookup/backend@${BACKEND_DIGEST}"' in source
    assert 'expected_image="${REGISTRY}/testlookup/${image}@${digest}"' in source
    assert ":${BUILD_TAG}\"\n" not in source[source.index("# ── Step 3: Create Namespace"):], (
        "nothing after the build may name an image by its tag"
    )


def test_the_tracked_overlay_does_not_force_a_pull_on_every_pod_start():
    text = (OVERLAY / "kustomization.yaml").read_text(encoding="utf-8")
    assert not re.search(r"imagePullPolicy\s*\n\s*value:\s*Always", text)
    assert "value: Always" not in text
    # At rest the overlay still cannot be applied by hand (quality gate: k3s.build-tag-placeholder).
    assert text.count("newTag: BUILD_TAG_PLACEHOLDER") == 3
