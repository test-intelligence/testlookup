"""R4 gap 1 / launch OD-08: every version string agrees with the root ``VERSION``.

Before 0.1.0 the repository said four different things: ``VERSION`` 0.1.0,
``APP_VERSION`` 0.0.1 (settings, ``.env.example``, the k8s ConfigMap), the
OpenAPI ``info.version`` 0.0.1 and k8s overlays pinning ``newTag: "0.0.1"``, an
image tag that was never published. The release workflow refuses a tag that
disagrees with ``VERSION`` (``.github/workflows/release.yml``), so ``VERSION``
is the source of truth and everything below follows it.

The k8s base names the images CI publishes, and a release promotes the verified
digests to ``:v<VERSION>`` (RELEASING.md), so a pinned app-image tag is
``v<VERSION>``. Overlays that a deploy script fills in (placeholders) and the
moving dev/staging channels are not release pins and are skipped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[3]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
IMAGE_TAG = f"v{VERSION}"
GHCR = "ghcr.io/test-intelligence/testlookup"
APP_COMPONENTS = ("backend", "frontend", "mcp")
# Tags a deploy script substitutes, and the moving channels of dev/staging.
NOT_A_RELEASE_PIN = {"BUILD_TAG_PLACEHOLDER", "APP_TAG_PLACEHOLDER", "dev-latest", "staging-latest"}


def _env_value(path: Path, key: str) -> str:
    match = re.search(rf"^{key}=(\S+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{key} missing from {path.relative_to(ROOT)}"
    return match.group(1)


def test_version_is_strict_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


def test_settings_default_app_version_matches() -> None:
    assert Settings.model_fields["APP_VERSION"].default == VERSION


def test_example_env_files_match() -> None:
    for rel in (".env.example", ".env.gcp-vm.example", "infra/cloudrun/backend.env.example"):
        assert _env_value(ROOT / rel, "APP_VERSION") == VERSION, rel
    assert _env_value(ROOT / "infra/cloudrun/frontend.env.example", "VITE_APP_VERSION") == VERSION


def test_compose_frontend_build_version_matches() -> None:
    for rel in ("docker-compose.yml", "docker-compose.dev-lite.yml"):
        values = re.findall(r"VITE_APP_VERSION=(\S+)", (ROOT / rel).read_text(encoding="utf-8"))
        assert values and set(values) == {VERSION}, (rel, values)


def test_openapi_reference_version_matches() -> None:
    spec = json.loads((ROOT / "docs/reference/openapi.json").read_text(encoding="utf-8"))
    assert spec["info"]["version"] == VERSION


def test_frontend_package_version_matches() -> None:
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == VERSION
    assert lock["version"] == VERSION
    assert lock["packages"][""]["version"] == VERSION


def test_k8s_configmap_and_version_label_match() -> None:
    configmap = yaml.safe_load((ROOT / "k8s/base/configmap.yaml").read_text(encoding="utf-8"))
    assert configmap["data"]["APP_VERSION"] == VERSION
    kustomization = yaml.safe_load((ROOT / "k8s/base/kustomization.yaml").read_text(encoding="utf-8"))
    labels = {k: v for entry in kustomization["labels"] for k, v in entry["pairs"].items()}
    assert labels["app.kubernetes.io/version"] == VERSION


def test_k8s_base_pulls_the_published_release_images() -> None:
    """R4 gap 3 (launch F-03): never the unclaimed Docker Hub ``testlookup/*``."""
    refs: list[str] = []
    for path in sorted((ROOT / "k8s/base").glob("*.yaml")) + sorted((ROOT / "k8s/components/backup").glob("*.yaml")):
        refs += re.findall(r"^\s*image:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    app_refs = [r for r in refs if any(f"/{c}:" in r for c in APP_COMPONENTS)]
    assert app_refs, "no app image found under k8s/base"
    assert {r.rsplit(":", 1)[1] for r in app_refs} == {IMAGE_TAG}, app_refs
    for ref in app_refs:
        assert ref.startswith(f"{GHCR}/"), ref


def test_every_overlay_keys_its_images_by_the_base_name_and_pins_this_release() -> None:
    overlays = sorted((ROOT / "k8s/overlays").glob("*/kustomization.yaml"))
    assert overlays
    for path in overlays:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = {e["name"]: e for e in doc.get("images", []) if e["name"].rsplit("/", 1)[-1] in APP_COMPONENTS}
        rel = path.relative_to(ROOT).as_posix()
        # An entry keyed by any other name matches nothing and is silently ignored by kustomize.
        assert set(entries) == {f"{GHCR}/{c}" for c in APP_COMPONENTS}, (rel, sorted(entries))
        for name, entry in entries.items():
            tag = str(entry.get("newTag", IMAGE_TAG))
            if tag not in NOT_A_RELEASE_PIN:
                assert tag == IMAGE_TAG, (rel, name, tag)
