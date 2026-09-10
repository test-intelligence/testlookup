"""The release deploy script must verify Deployments that actually exist.

Re-audit finding N1. ``scripts/deploy-k8s.sh`` verified the rollout of five
``testlookup-celery-*`` Deployments that exist in no manifest. The script runs
under ``set -euo pipefail``, so the first ``kubectl rollout status`` against a
missing Deployment aborted it — *after* ``kubectl apply -k`` had already rolled
the release out. Every verified cloud deploy (GKE/AKS/EKS all call this one
script) therefore reported failure on a successful apply, and the per-Deployment
image-digest check the loop exists to perform never ran against the workers.

The defect is name drift between a hand-typed shell list and the manifests, so
the guard is a cross-check between exactly those two sources.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy-k8s.sh"
K8S_BASE = ROOT / "k8s" / "base"

# Deployments the release script is not expected to gate on: optional
# components that a given overlay may not schedule at all.
OPTIONAL_DEPLOYMENTS = {"testlookup-ollama"}

# In-cluster datastores. Overlays may replace these with managed services, so a
# release rollout must not block on them.
DATASTORE_DEPLOYMENTS = {
    "testlookup-postgres",
    "testlookup-mongo",
    "testlookup-redis",
    "testlookup-minio",
    "testlookup-chromadb",
}


def _script_deployments() -> list[str]:
    """Names listed in the script's RELEASE_DEPLOYMENTS array."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"RELEASE_DEPLOYMENTS=\((.*?)\)", text, re.DOTALL)
    assert match, "deploy-k8s.sh no longer declares a RELEASE_DEPLOYMENTS array"
    return [line.strip() for line in match.group(1).split() if line.strip()]


def _manifest_deployments() -> dict[str, str]:
    """Map every base Deployment name to its container image."""
    found: dict[str, str] = {}
    for path in sorted(K8S_BASE.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
                continue
            name = doc["metadata"]["name"]
            containers = doc["spec"]["template"]["spec"]["containers"]
            found[name] = containers[0]["image"]
    return found


def test_every_verified_deployment_exists_in_the_manifests():
    """The exact defect: a name in the script that no manifest defines."""
    manifests = _manifest_deployments()
    assert manifests, "no Deployments parsed from k8s/base — the guard would vacuously pass"

    unknown = [name for name in _script_deployments() if name not in manifests]
    assert not unknown, (
        "scripts/deploy-k8s.sh waits on Deployments that do not exist: "
        f"{sorted(unknown)}. Known Deployments: {sorted(manifests)}. "
        "Under 'set -e' this aborts the deploy AFTER the apply has already "
        "rolled out."
    )


def test_every_released_workload_is_verified_by_the_script():
    """A new worker Deployment must not silently escape digest verification."""
    manifests = _manifest_deployments()
    listed = set(_script_deployments())

    expected = {
        name
        for name in manifests
        if name not in OPTIONAL_DEPLOYMENTS and name not in DATASTORE_DEPLOYMENTS
    }
    missing = expected - listed
    assert not missing, (
        f"Deployments ship a released image but are never rollout/digest "
        f"verified by scripts/deploy-k8s.sh: {sorted(missing)}"
    )


@pytest.mark.parametrize(
    "component,expected_fragment",
    [("frontend", "frontend"), ("mcp", "mcp")],
)
def test_component_digest_mapping_matches_the_image_it_deploys(component, expected_fragment):
    """The script maps frontend/mcp to their own digests and the rest to backend.

    A name in the wrong branch of that ``case`` would compare a worker against
    the frontend digest and fail a correct deploy.
    """
    manifests = _manifest_deployments()
    name = f"testlookup-{component}"
    assert name in manifests
    assert expected_fragment in manifests[name]


def test_backend_image_workloads_fall_through_to_the_backend_digest():
    """Workers and beat run the backend image, so the default branch is right."""
    manifests = _manifest_deployments()
    backend_image = manifests["testlookup-backend"]
    for name in _script_deployments():
        if name in {"testlookup-frontend", "testlookup-mcp"}:
            continue
        assert name in manifests, (
            f"{name} is listed in RELEASE_DEPLOYMENTS but no manifest defines it"
        )
        assert manifests[name] == backend_image, (
            f"{name} does not run the backend image ({manifests[name]}), so the "
            "script's default digest branch would compare it against the wrong "
            "manifest entry"
        )
