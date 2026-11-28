"""The release gate must fail on missing roles, stale rollout and unknown digests."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_kubernetes_rollout as gate  # noqa: E402


def test_runtime_identities_follow_hashed_linux_manifest_and_exclude_attestations(monkeypatch):
    config_digest = "sha256:" + "c" * 64
    manifest_type = "application/vnd.oci.image.manifest.v1+json"
    platform = json.dumps({"schemaVersion": 2, "mediaType": manifest_type, "config": {"digest": config_digest}}).encode()
    platform_digest = "sha256:" + hashlib.sha256(platform).hexdigest()
    attestation_digest = "sha256:" + "0" * 64
    index = json.dumps({
        "schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [
            {"mediaType": manifest_type, "digest": platform_digest, "platform": {"os": "linux", "architecture": "amd64"}},
            {"mediaType": manifest_type, "digest": attestation_digest, "platform": {"os": "unknown", "architecture": "unknown"}},
        ],
    }).encode()
    index_digest = "sha256:" + hashlib.sha256(index).hexdigest()
    supplied = {index_digest: index, platform_digest: platform}
    monkeypatch.setattr(gate, "run", lambda command, **kwargs: supplied[command[-1].split("@")[1]])
    assert gate.runtime_digests("example.invalid/release/backend@" + index_digest) == {
        config_digest, platform_digest, index_digest,
    }
    supplied[platform_digest] += b" "
    with pytest.raises(gate.RolloutError, match="requested manifest digest"):
        gate.runtime_digests("example.invalid/release/backend@" + index_digest)


@pytest.fixture
def fleet():
    images = {component: f"example.invalid/release/{component}@sha256:{character * 64}"
              for component, character in zip(("backend", "frontend", "mcp"), "abc")}
    expected = {component: {ref.split("@")[1]} for component, ref in images.items()}
    deployments, replicasets, pods = [], [], []
    for role, component in gate.ROLES.items():
        desired = int(role != "beat")
        labels, uid = {"app": f"testlookup-{role}"}, f"{role}-deployment"
        containers = [{"name": "app", "image": images[component]}]
        deployments.append({
            "metadata": {"name": f"testlookup-{role}", "uid": uid, "generation": 2},
            "spec": {"replicas": desired, "selector": {"matchLabels": labels},
                     "template": {"spec": {"containers": containers}}},
            "status": {key: desired for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")}
                      | {"observedGeneration": 2},
        })
        if desired:
            rs_uid = f"{role}-rs"
            replicasets.append({"metadata": {
                "uid": rs_uid, "ownerReferences": [{"uid": uid, "kind": "Deployment", "controller": True}],
            }})
            pods.append({
                "metadata": {"labels": labels, "ownerReferences": [{"uid": rs_uid, "kind": "ReplicaSet", "controller": True}]},
                "spec": {"containers": deepcopy(containers)},
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                           "containerStatuses": [{"name": "app", "imageID": images[component], "ready": True}]},
            })
    return deployments, replicasets, pods, images, expected, {"beat"}


def test_every_application_role_checked_with_explicit_disabled_beat(fleet):
    result = gate.verify_roles(*fleet)
    assert set(result) == set(gate.ROLES)
    assert result["beat"]["state"] == "inactive" and result["beat"]["observed_digests"] == []
    assert all(result[role]["replicas"] == 1 for role in result if role != "beat")


def test_absent_mcp_or_unexpected_worker_generation_is_rejected(fleet):
    deployments = fleet[0]
    mcp = next(item for item in deployments if item["metadata"]["name"] == "testlookup-mcp")
    deployments.remove(mcp)
    with pytest.raises(gate.RolloutError, match="mcp: deployment is absent"):
        gate.verify_roles(*fleet)
    deployments.append(mcp)
    worker = next(item for item in deployments if item["metadata"]["name"] == "testlookup-worker-children")
    worker["metadata"]["generation"] += 1
    with pytest.raises(gate.RolloutError, match="worker-children: controller has not observed"):
        gate.verify_roles(*fleet)


def test_matching_service_pod_cannot_hide_behind_an_unrelated_owner_or_stale_image(fleet):
    pods = fleet[2]
    worker = next(item for item in pods if item["metadata"]["labels"]["app"] == "testlookup-worker-children")
    worker["status"]["containerStatuses"][0]["imageID"] = "docker://sha256:" + "f" * 64
    with pytest.raises(gate.RolloutError, match="worker-children: running image identity"):
        gate.verify_roles(*fleet)
    worker["status"]["containerStatuses"][0]["imageID"] = "docker://" + fleet[3]["backend"].split("@")[1]
    worker["metadata"]["ownerReferences"][0]["uid"] = "some-other-replicaset"
    with pytest.raises(gate.RolloutError, match="worker-children: selected pod has no matching rollout owner"):
        gate.verify_roles(*fleet)


def test_required_role_cannot_be_silently_scaled_to_zero(fleet):
    worker = next(item for item in fleet[0] if item["metadata"]["name"] == "testlookup-worker-ai")
    worker["spec"]["replicas"] = 0
    with pytest.raises(gate.RolloutError, match="worker-ai: desired replicas"):
        gate.verify_roles(*fleet)
