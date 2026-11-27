"""A pod that never calls the Kubernetes API gets no service-account token.

Kubernetes mounts the namespace's default service-account token into every pod
unless told not to. The frontend, the MCP server, Ollama and the bundled data
stores never call the API, so a compromise of any of them handed the attacker a
credential for nothing (OSS review 2026-11-12, L-8). Pods on ``testlookup-sa``
keep theirs: the backend and workers use it for Vault's Kubernetes auth
(``secret_providers/vault.py``) and to tell a cluster from Compose.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
# Service accounts whose token a workload really uses.
TOKEN_USERS = {"testlookup-sa"}


def _patch_paths():
    """Identify patches by their Kustomization registration, not container shape."""
    paths = set()
    resources = set()
    for kustomization in (ROOT / "k8s").rglob("kustomization.yaml"):
        document = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
        resources.update((kustomization.parent / resource).resolve()
                         for resource in document.get("resources", []))
        for patch in document.get("patches", []):
            if isinstance(patch, dict) and patch.get("path"):
                paths.add((kustomization.parent / patch["path"]).resolve())
        for patch in document.get("patchesStrategicMerge", []):
            if isinstance(patch, str) and "\n" not in patch:
                paths.add((kustomization.parent / patch).resolve())
    # A manifest registered as a real resource anywhere remains checked.
    return paths - resources


def _pod_specs():
    patches = _patch_paths()
    for path in sorted((ROOT / "k8s").rglob("*.yaml")):
        if path.resolve() in patches:
            continue
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or doc.get("kind") not in WORKLOADS:
                continue
            spec = doc.get("spec") or {}
            if doc["kind"] == "CronJob":
                spec = (spec.get("jobTemplate") or {}).get("spec") or {}
            pod = (spec.get("template") or {}).get("spec") or {}
            if pod.get("containers"):
                yield f"{path.relative_to(ROOT).as_posix()}:{doc['metadata']['name']}", pod


def test_pods_without_an_api_identity_mount_no_token():
    pods = dict(_pod_specs())
    assert len(pods) >= 15, f"expected the app, worker and data-store workloads, found {len(pods)}"
    mounting = sorted(name for name, pod in pods.items()
                      if pod.get("serviceAccountName") not in TOKEN_USERS
                      and pod.get("automountServiceAccountToken") is not False)
    assert not mounting, (
        "these pods get the default service-account token although nothing in them calls the "
        "Kubernetes API; set automountServiceAccountToken: false: " + ", ".join(mounting)
    )


def test_the_api_users_keep_their_token():
    """Vault's Kubernetes auth reads the token; turning it off breaks secret loading."""
    users = {name: pod for name, pod in _pod_specs() if pod.get("serviceAccountName") in TOKEN_USERS}
    assert any("testlookup-backend" in name for name in users), sorted(users)
    assert not [name for name, pod in users.items() if pod.get("automountServiceAccountToken") is False]


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.parametrize("profile", ("k3s", "openshift", "prod"))
def test_rendered_telemetry_overlay_preserves_profile_identity_images_and_proxy_boundary(profile):
    def render(name):
        result = subprocess.run(
            ["kubectl", "kustomize", str(ROOT / "k8s/overlays" / name)],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, f"profile {name} did not render successfully"
        return [document for document in yaml.safe_load_all(result.stdout) if isinstance(document, dict)]

    baseline, telemetry = render(profile), render(profile + "-telemetry")

    def deployment_contract(documents):
        return {
            document["metadata"]["name"]: {
                "service_account": document["spec"]["template"]["spec"].get("serviceAccountName"),
                "automount": document["spec"]["template"]["spec"].get("automountServiceAccountToken"),
                "images": {container["name"]: container["image"]
                           for container in document["spec"]["template"]["spec"]["containers"]},
            }
            for document in documents if document.get("kind") == "Deployment"
        }

    expected = deployment_contract(baseline)
    actual = deployment_contract(telemetry)
    assert actual and actual == expected
    for pod in actual.values():
        if pod["service_account"] in TOKEN_USERS:
            assert pod["automount"] is not False
        else:
            assert pod["automount"] is False

    def boundaries(documents):
        return {document["metadata"]["name"]: document["data"]["TRUSTED_PROXY_IPS"]
                for document in documents if document.get("kind") == "ConfigMap"
                and "TRUSTED_PROXY_IPS" in document.get("data", {})}

    assert boundaries(baseline) and boundaries(telemetry) == boundaries(baseline)
