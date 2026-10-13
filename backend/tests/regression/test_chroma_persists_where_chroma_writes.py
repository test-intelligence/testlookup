"""Chroma's volume is mounted where Chroma 1.x writes.

chromadb/chroma 1.x runs ``chroma run /config.yaml``, whose ``persist_path``
is ``/data``. The manifests still carried Chroma 0.x's ``IS_PERSISTENT`` /
``PERSIST_DIRECTORY=/chroma/chroma`` and mounted the volume there, so the live
vectors lived in the container's writable layer and were lost whenever the
container was recreated (homelab, 2026-10-12: ``/data`` held the October
collections, the volume only August's 0.x files).

The Compose healthcheck also called ``curl``, which the image does not ship,
so the service never reported healthy; it asks the heartbeat over bash's
``/dev/tcp`` instead.
"""
from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.release.yml"]
OVERLAYS = ["homelab", "openshift-artifactory"]
CHROMA_1X_PERSIST_PATH = "/data"


def _compose_chroma(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    doc = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
    return doc["services"]["chromadb"], doc.get("volumes") or {}


def _overlay_chroma(overlay: str) -> dict[str, Any]:
    path = REPO / "k8s" / "overlays" / overlay / "infra-chromadb.yaml"
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(d, dict)]
    deployment = next(d for d in docs if d.get("kind") == "Deployment")
    return deployment["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_compose_mounts_the_named_volume_at_chromas_persist_path(name):
    service, volumes = _compose_chroma(name)
    mounts = [m.split(":") for m in service["volumes"]]
    targets = {target: source for source, target, *_ in mounts}
    assert CHROMA_1X_PERSIST_PATH in targets, service["volumes"]
    assert targets[CHROMA_1X_PERSIST_PATH] in volumes, "the mounted volume must be declared"
    assert "/chroma/chroma" not in targets


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_compose_drops_the_chroma_0x_persistence_settings(name):
    service, _ = _compose_chroma(name)
    env = service.get("environment") or {}
    assert "PERSIST_DIRECTORY" not in env and "IS_PERSISTENT" not in env


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_compose_healthcheck_uses_only_what_the_image_ships(name):
    service, _ = _compose_chroma(name)
    test = service["healthcheck"]["test"]
    assert test[:3] == ["CMD", "bash", "-c"], test
    assert "/api/v2/heartbeat" in test[3] and "/dev/tcp/127.0.0.1/8000" in test[3]
    assert not any(tool in " ".join(test) for tool in ("curl", "wget", "python"))


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_k8s_mounts_the_claim_at_chromas_persist_path_on_a_fresh_subpath(overlay):
    container = _overlay_chroma(overlay)
    mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
    assert CHROMA_1X_PERSIST_PATH in mounts, container["volumeMounts"]
    # A fresh directory on the same claim: Chroma 1.x never opens the 0.x files at its root.
    assert mounts[CHROMA_1X_PERSIST_PATH].get("subPath") == "chroma-1x"
    assert "/chroma/chroma" not in mounts
    names = {e["name"] for e in container.get("env") or []}
    assert not names & {"PERSIST_DIRECTORY", "IS_PERSISTENT"}
