"""Adoption regression — the release artifacts that enable no-clone self-host.

CI can't run `docker compose up`, so these pin the *contract* of the release
stack instead: `docker-compose.release.yml` must pull pinned pre-built images
(never `build:` from source, never bind-mount host source), keep parity with the
dev compose's service set, and `install.sh` must be syntactically valid and fetch
exactly the files the release stack needs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_RELEASE = _ROOT / "docker-compose.release.yml"
_DEV = _ROOT / "docker-compose.yml"
_INSTALL = _ROOT / "install.sh"
_VERSION = _ROOT / "VERSION"

# App services run from pre-built images; the rest are stock infra images.
_APP_SERVICES = {"backend", "worker", "beat", "frontend", "mcp", "seed-init"}
_REGISTRY_PREFIX = "ghcr.io/anandtopu/testlookup"


@pytest.fixture(scope="module")
def release() -> dict:
    assert _RELEASE.exists(), "docker-compose.release.yml is missing"
    return yaml.safe_load(_RELEASE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dev() -> dict:
    return yaml.safe_load(_DEV.read_text(encoding="utf-8"))


def test_release_compose_is_valid_yaml_with_services(release):
    assert isinstance(release.get("services"), dict) and release["services"]


def test_app_services_use_pinned_images_not_build(release):
    services = release["services"]
    for name in _APP_SERVICES:
        assert name in services, f"release compose missing app service {name!r}"
        svc = services[name]
        assert "build" not in svc, f"{name} must NOT build from source in the release stack"
        image = svc.get("image", "")
        assert _REGISTRY_PREFIX in image, f"{name} image must point at {_REGISTRY_PREFIX}: {image!r}"
        # Version must be parametrized so users can pin a tag / SHA.
        assert "${TESTLOOKUP_VERSION" in image, f"{name} image tag must be parametrized: {image!r}"


def test_no_app_service_bind_mounts_host_source(release):
    """Pre-built images carry the code — no ./backend, ./frontend, ./mcp mounts."""
    for name in _APP_SERVICES:
        for vol in release["services"][name].get("volumes", []) or []:
            host = vol.split(":", 1)[0] if isinstance(vol, str) else ""
            assert not host.startswith(("./backend", "./frontend", "./mcp", "./cli", "./client", "./infra")), (
                f"{name} bind-mounts host source {host!r} — the release image must be self-contained"
            )


def test_infra_services_present_and_pinned(release):
    services = release["services"]
    for name in ("postgres", "mongo", "redis", "minio"):
        assert name in services, f"release compose missing infra service {name!r}"
        image = services[name].get("image", "")
        assert image and ":" in image, f"{name} must use a pinned image tag: {image!r}"


def test_service_parity_with_dev_compose(dev, release):
    """The release stack runs the same app services the dev stack builds."""
    dev_app = {n for n in dev["services"] if "build" in dev["services"][n]}
    missing = dev_app - set(release["services"])
    assert not missing, f"release compose is missing dev app services: {missing}"


def test_optional_services_are_profile_gated(release):
    services = release["services"]
    # Demo seed and the local-LLM services must not start by default.
    assert "demo" in (services["seed-init"].get("profiles") or [])
    for name in ("ollama", "chromadb"):
        assert "local-llm" in (services[name].get("profiles") or [])


def test_frontend_maps_host_3000_to_container_80(release):
    ports = release["services"]["frontend"].get("ports", [])
    assert any(str(p).startswith("3000:80") for p in ports), f"frontend ports: {ports}"


def test_version_file_is_a_sane_semver_ish_string():
    assert _VERSION.exists(), "VERSION file is missing"
    raw = _VERSION.read_text(encoding="utf-8").strip()
    assert raw, "VERSION is empty"
    parts = raw.lstrip("v").split(".")
    assert len(parts) >= 2 and all(p.isdigit() for p in parts[:2]), f"VERSION not version-like: {raw!r}"


def test_install_script_is_syntactically_valid():
    """bash -n parses install.sh without executing it."""
    assert _INSTALL.exists(), "install.sh is missing"
    bash = _resolve_bash()
    if bash is None:
        pytest.skip("no bash available to syntax-check install.sh")
    proc = subprocess.run([bash, "-n", str(_INSTALL)], capture_output=True, text=True)
    assert proc.returncode == 0, f"install.sh has a syntax error:\n{proc.stderr}"


def test_install_script_fetches_the_required_files():
    text = _INSTALL.read_text(encoding="utf-8")
    for needed in ("docker-compose.release.yml", ".env.example",
                   "scripts/gen-dev-env.sh", "scripts/init-db.sql"):
        assert needed in text, f"install.sh never references {needed!r}"
    # It must actually bring the stack up via compose.
    assert "up -d" in text
    assert "raw.githubusercontent.com/anandtopu/testlookup" in text


def _resolve_bash() -> str | None:
    import os
    import shutil

    override = os.environ.get("TL_TEST_BASH")
    if override:
        return override
    return shutil.which("bash")
