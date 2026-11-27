#!/usr/bin/env python3
"""Collect sanitized runtime image proof from an isolated Compose test project."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release"))
from release_manifest import ManifestError, validate_manifest  # noqa: E402
from verify_kubernetes_rollout import RolloutError, run, runtime_digests  # noqa: E402


SERVICES = {"api": "backend", "worker": "worker", "worker-children": "worker-children", "mcp": "mcp"}
_PROJECT = re.compile(r"tl-e2e-[a-f0-9]{12}\Z")
_CONTAINER = re.compile(r"[a-f0-9]{12,64}\Z")
_NETWORKS_FORMAT = '{{range $name, $value := .NetworkSettings.Networks}}{{$name}} {{end}}'


def inspect_role(engine: str, project: str, role: str, candidate: str, allowed: set[str], networks: set[str]) -> dict:
    service = SERVICES[role]
    identifiers = run([engine, "ps", "--filter", f"label=com.docker.compose.project={project}",
                       "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}"],
                      error=f"{role}: could not list disposable containers", timeout=15).decode().split()
    if len(identifiers) != 1 or not _CONTAINER.fullmatch(identifiers[0]):
        raise RolloutError(f"{role}: expected one running disposable container")
    selector = ".ImageDigest" if engine == "podman" else ".Image"
    fields = '{{json .Config.Image}}|{{json .State.Running}}|{{json ' + selector + '}}|' + _NETWORKS_FORMAT
    raw = run([engine, "container", "inspect", "--format", fields, identifiers[0]],
              error=f"{role}: could not inspect the disposable runtime", timeout=15).decode().strip().split("|")
    if len(raw) != 4:
        raise RolloutError(f"{role}: unexpected container observation")
    configured, running, observed = (json.loads(field) for field in raw[:3])
    attached = set(raw[3].split())
    if project + "_testlookup_net" not in attached or not attached <= networks:
        raise RolloutError(f"{role}: container is outside the isolated networks")
    if not isinstance(observed, str) or configured != candidate or running is not True:
        raise RolloutError(f"{role}: container is not ready on the declared candidate reference")
    observed = observed.rsplit("@", 1)[-1]
    if re.fullmatch(r"[a-f0-9]{64}", observed):
        observed = "sha256:" + observed
    if observed not in allowed:
        raise RolloutError(f"{role}: running digest does not match the candidate")
    repository = candidate.rsplit("@", 1)[0]
    return {"role": role, "component": "mcp" if role == "mcp" else "backend", "candidate": candidate,
            "running": f"{repository}@{observed}", "verified_runtime_digests": sorted(allowed)}


def build_proof(engine: str, project: str, project_id: str, manifest: dict) -> dict:
    networks = set()
    for name in ("testlookup_net", "telemetry_ingress", "telemetry_metrics", "telemetry_storage"):
        network = f"{project}_{name}"
        scoped = run([engine, "network", "inspect", "--format", "{{json .Internal}}", network],
                     error="the isolated Compose project network could not be read", timeout=15)
        if json.loads(scoped) is not True:
            raise RolloutError("the disposable Compose networks must be internal")
        networks.add(network)
    refs = {key: manifest["images"][key]["source_ref"] for key in ("backend", "mcp")}
    allowed = {key: runtime_digests(ref) for key, ref in refs.items()}
    roles = []
    for role in SERVICES:
        component = "mcp" if role == "mcp" else "backend"
        roles.append(inspect_role(engine, project, role, refs[component], allowed[component], networks))
    return {"schema_version": 1, "profile": "compose", "observed_at": datetime.now(timezone.utc).isoformat(),
            "source_sha": manifest["source_sha"], "disposable": True, "project_id": project_id, "roles": roles}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("podman", "docker"), required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--project-id", required=True, type=uuid.UUID)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not _PROJECT.fullmatch(args.compose_project):
        parser.error("use the generated name of a disposable TestLookup release project")
    try:
        manifest = validate_manifest(json.loads(args.manifest.read_bytes()), expected_source_sha=args.source_sha)
        proof = build_proof(args.engine, args.compose_project, str(args.project_id), manifest)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(proof, output, indent=2)
            output.write("\n")
    except RolloutError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ManifestError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Disposable runtime proof could not be validated.", file=sys.stderr)
        return 1
    print("Verified candidate image identities for four disposable signal roles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
