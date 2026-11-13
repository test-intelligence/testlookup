"""The single-VM overlay runs its production images, not the host's source tree.

``docker-compose.gcp-vm.yml`` layers production targets over the development
``docker-compose.yml``. It cleared each app service's mounts with
``volumes: []``, but Compose merges ``volumes`` from every file by mount
target, so an empty list removes nothing: ``docker compose config`` showed the
"production" backend with the host's ``./backend`` mounted over ``/app`` (and
``app``, ``client``, ``infra``, ``cli``), the workers, seed-init and
db-migrate the same, and the nginx frontend with the dev ``src`` and
``public`` (OSS review 2026-11-12, M-13). The image that was built and tagged
was never what ran.

``volumes: !reset []`` drops the inherited mounts (Compose 2.24.4 and newer).
The contract loader used to replace lists wholesale, which is why no test saw
this; it now merges volumes the way Compose does.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from compose_topology_contract import _load_document, _merge_mapping, load_compose_topology

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
FILES = (ROOT / "docker-compose.yml", ROOT / "docker-compose.gcp-vm.yml")
OVERLAY_IMAGE = "gcr.io/"


def _bind_sources(service: dict) -> list[str]:
    """Host paths mounted into a service: long syntax by type, short syntax by its source."""
    sources = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            # Compose's own render: the source is absolute (C:\... on Windows).
            if volume.get("type") == "bind":
                sources.append(str(volume.get("source", "")))
            continue
        source = str(volume).split(":")[0]
        if source.startswith((".", "/", "~")):
            sources.append(source)
    return sources


def _overlay_services(services: dict) -> dict:
    return {name: s for name, s in services.items() if OVERLAY_IMAGE in str(s.get("image", ""))}


def test_no_production_service_mounts_the_host_source_tree():
    services = load_compose_topology(FILES)["services"]
    production = _overlay_services(services)
    assert {"backend", "frontend", "db-migrate", "seed-init"} <= set(production), sorted(production)
    mounted = {name: _bind_sources(s) for name, s in production.items() if _bind_sources(s)}
    assert not mounted, f"the single-VM overlay still runs these services on host files: {mounted}"


def test_every_overlay_volume_list_resets():
    """A plain list in an override file only adds mounts; it never removes one."""
    text = (ROOT / "docker-compose.gcp-vm.yml").read_text(encoding="utf-8")
    plain = [line.strip() for line in text.splitlines()
             if re.match(r"\s+volumes:\s*\[", line)]
    assert not plain, f"use `volumes: !reset []`: {plain}"


def test_the_loader_merges_volumes_the_way_compose_does():
    base = _load_document("services: {app: {volumes: ['./src:/app', 'data:/data']}}")
    plain = _merge_mapping(base, _load_document("services: {app: {volumes: []}}"))
    assert plain["services"]["app"]["volumes"] == ["./src:/app", "data:/data"]
    replaced = _merge_mapping(base, _load_document("services: {app: {volumes: ['./other:/app']}}"))
    assert replaced["services"]["app"]["volumes"] == ["./other:/app", "data:/data"]
    reset = _merge_mapping(base, _load_document("services: {app: {volumes: !reset []}}"))
    assert reset["services"]["app"]["volumes"] == []
    override = _merge_mapping(base, _load_document("services: {app: {volumes: !override ['x:/x']}}"))
    assert override["services"]["app"]["volumes"] == ["x:/x"]


def _compose_cli() -> list[str] | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    probe = subprocess.run([docker, "compose", "version"], capture_output=True, text=True, timeout=60, check=False)
    return [docker, "compose"] if probe.returncode == 0 else None


@pytest.mark.skipif(_compose_cli() is None, reason="needs the docker compose CLI (no daemon)")
def test_docker_compose_itself_renders_no_source_mount():
    """The same check through Compose's own merge, where the CLI is installed (CI runners)."""
    args = [*(_compose_cli() or []), "-f", str(FILES[0]), "-f", str(FILES[1]), "--profile", "*",
            "config", "--no-interpolate", "--format", "json"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=True, cwd=ROOT)
    services = json.loads(result.stdout[result.stdout.index("{"):])["services"]
    mounted = {name: _bind_sources(s) for name, s in _overlay_services(services).items() if _bind_sources(s)}
    assert not mounted, f"docker compose config shows host mounts in production services: {mounted}"
