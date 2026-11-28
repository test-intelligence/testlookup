#!/usr/bin/env python3
"""Verify every TestLookup application role against one authorized release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from release_manifest import APPLICATION_COMPONENTS, ManifestError, validate_manifest


ROLES = {
    "backend": "backend", "frontend": "frontend", "mcp": "mcp", "beat": "backend",
    "worker-critical": "backend", "worker-ingestion": "backend", "worker-ai": "backend",
    "worker-default": "backend", "worker-children": "backend",
}
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_NAMESPACE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_MANIFEST_TYPES = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}
_INDEX_TYPES = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}


class RolloutError(RuntimeError):
    pass


def run(command: list[str], *, error: str, timeout: int) -> bytes:
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        raise RolloutError(error) from None


def read_oci_manifest(reference: str) -> dict:
    raw = run(["docker", "buildx", "imagetools", "inspect", "--raw", "--", reference],
              error="registry manifest retrieval failed", timeout=30)
    expected = reference.rsplit("@", 1)[1]
    if len(raw) > 1024 * 1024 or "sha256:" + hashlib.sha256(raw).hexdigest() != expected:
        raise RolloutError("the registry response does not match the requested manifest digest")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schemaVersion") != 2:
        raise RolloutError("registry returned an unsupported manifest")
    return value


def runtime_digests(reference: str) -> set[str]:
    """Accept only the verified index, Linux amd64 manifest and its config."""
    name, digest = reference.rsplit("@", 1)
    image = read_oci_manifest(reference)
    allowed = {digest}
    if image.get("mediaType") in _INDEX_TYPES:
        runnable = [entry for entry in image.get("manifests", [])
                    if entry.get("mediaType") in _MANIFEST_TYPES
                    and entry.get("platform", {}).get("os") == "linux"
                    and entry.get("platform", {}).get("architecture") == "amd64"
                    and entry.get("annotations", {}).get("vnd.docker.reference.type") != "attestation-manifest"]
        if len(runnable) != 1 or not _DIGEST.fullmatch(runnable[0].get("digest", "")):
            raise RolloutError("release index must contain one Linux amd64 image manifest")
        platform_digest = runnable[0]["digest"]
        allowed.add(platform_digest)
        image = read_oci_manifest(f"{name}@{platform_digest}")
    config = image.get("config", {}).get("digest", "")
    if image.get("mediaType") not in _MANIFEST_TYPES or not _DIGEST.fullmatch(config):
        raise RolloutError("release manifest is missing a valid runtime image config digest")
    return allowed | {config}


def _controlled_by(metadata: dict, kind: str, uid: str) -> bool:
    return any(owner.get("controller") is True and owner.get("kind") == kind and owner.get("uid") == uid
               for owner in metadata.get("ownerReferences", []))


def verify_roles(deployments: list[dict], replicasets: list[dict], pods: list[dict], image_refs: dict[str, str],
                 allowed_digests: dict[str, set[str]], inactive_roles: set[str]) -> dict:
    by_name = {item["metadata"]["name"]: item for item in deployments}
    evidence = {}
    for role, component in ROLES.items():
        deployment = by_name.get(f"testlookup-{role}")
        if deployment is None:
            raise RolloutError(f"{role}: deployment is absent")
        metadata, spec, status = deployment["metadata"], deployment["spec"], deployment.get("status", {})
        desired = spec.get("replicas", 1)
        inactive = role in inactive_roles
        if not isinstance(desired, int) or (desired != 0 if inactive else desired < 1):
            raise RolloutError(f"{role}: desired replicas do not match the selected profile")
        if status.get("observedGeneration", 0) < metadata["generation"]:
            raise RolloutError(f"{role}: controller has not observed the current deployment generation")
        if any(status.get(key, 0) != desired for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")):
            raise RolloutError(f"{role}: rollout has not reached its desired available replica count")
        expected = image_refs[component]
        containers = spec["template"]["spec"]["containers"]
        names = [item["name"] for item in containers if item.get("image") == expected]
        if len(names) != 1:
            raise RolloutError(f"{role}: deployment template does not select exactly one candidate application image")
        name = names[0]
        selector = spec["selector"].get("matchLabels", {})
        if not selector or spec["selector"].get("matchExpressions"):
            raise RolloutError(f"{role}: expected the reference profile's label selector")
        owned = [rs["metadata"]["uid"] for rs in replicasets if _controlled_by(rs["metadata"], "Deployment", metadata["uid"])]
        active = [pod for pod in pods if not pod["metadata"].get("deletionTimestamp")
                  and pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
                  and all(pod["metadata"].get("labels", {}).get(key) == value for key, value in selector.items())]
        if len(active) != desired:
            raise RolloutError(f"{role}: active selected pod count does not match the deployment")
        observed = set()
        for pod in active:
            if not any(_controlled_by(pod["metadata"], "ReplicaSet", uid) for uid in owned):
                raise RolloutError(f"{role}: selected pod has no matching rollout owner")
            pod_state = pod.get("status", {})
            if pod_state.get("phase") != "Running" or not any(
                state.get("type") == "Ready" and state.get("status") == "True" for state in pod_state.get("conditions", [])
            ):
                raise RolloutError(f"{role}: selected pod is not ready")
            configured = [item.get("image") for item in pod["spec"]["containers"] if item.get("name") == name]
            runtime = [item for item in pod_state.get("containerStatuses", []) if item.get("name") == name]
            if configured != [expected] or len(runtime) != 1 or runtime[0].get("ready") is not True:
                raise RolloutError(f"{role}: selected pod's application container is not ready on its candidate reference")
            image_id = runtime[0].get("imageID", "").rsplit("@", 1)[-1].split("://", 1)[-1]
            if image_id not in allowed_digests[component]:
                raise RolloutError(f"{role}: running image identity is outside the candidate digest set")
            observed.add(image_id)
        evidence[role] = {"state": "inactive" if inactive else "ready", "replicas": desired,
                          "image": expected, "observed_digests": sorted(observed)}
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--context")
    parser.add_argument("--inactive-role", action="append", choices=tuple(ROLES), default=[])
    parser.add_argument("--wait-seconds", type=int, default=180)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not _NAMESPACE.fullmatch(args.namespace) or not 1 <= args.wait_seconds <= 300:
        parser.error("use a valid namespace and a wait of 1–300 seconds")
    if set(args.inactive_role) & {"backend", "frontend", "mcp"}:
        parser.error("the API, frontend and MCP are required roles")
    try:
        source = args.manifest.read_bytes()
        manifest = validate_manifest(json.loads(source), expected_source_sha=args.source_sha)
        image_refs = {
            name: manifest["images"][name]["source_ref"]
            for name in APPLICATION_COMPONENTS
        }
        allowed = {name: runtime_digests(ref) for name, ref in image_refs.items()}
        command = ["kubectl", f"--namespace={args.namespace}", "--request-timeout=10s"]
        if args.context:
            command.append(f"--context={args.context}")

        def items(resource: str) -> list[dict]:
            return json.loads(run([*command, "get", resource, "-o", "json"],
                                  error=f"could not read Kubernetes {resource}", timeout=15))["items"]

        until = time.monotonic() + args.wait_seconds
        while True:
            try:
                roles = verify_roles(items("deployments"), items("replicasets"), items("pods"),
                                     image_refs, allowed, set(args.inactive_role))
                break
            except RolloutError:
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(3, remaining))
        evidence = {"schema_version": 1, "source_sha": args.source_sha, "namespace": args.namespace,
                    "release_manifest_sha256": hashlib.sha256(source).hexdigest(), "roles": roles}
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(evidence, output, indent=2)
            output.write("\n")
    except RolloutError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ManifestError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Rollout input or evidence could not be validated.", file=sys.stderr)
        return 1
    print(f"Verified {len(roles)} application roles for {args.source_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
