"""Read-only Compose proof must bind every role and exclude shared networks."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inspect_compose_candidate as proof  # noqa: E402


PROJECT = "tl-e2e-" + "a" * 12
NETWORKS = {PROJECT + "_testlookup_net", PROJECT + "_telemetry_ingress"}
CONFIG = "sha256:" + "c" * 64
CANDIDATE = "example.invalid/release/backend@sha256:" + "d" * 64


@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_runtime_proof_requires_candidate_digest_and_private_networks(monkeypatch, engine):
    state = [CANDIDATE, True, CONFIG]
    attached = set(NETWORKS)

    def command(arguments, **kwargs):
        if arguments[1] == "ps":
            assert f"label=com.docker.compose.project={PROJECT}" in arguments
            assert "label=com.docker.compose.service=worker-children" in arguments
            return b"112233445566\n"
        assert "--format" in arguments and "inspect" in arguments
        assert ".Config.Env" not in arguments[-2]
        return ("|".join(json.dumps(item) for item in state) + "|" + " ".join(attached)).encode()

    monkeypatch.setattr(proof, "run", command)
    accepted = {CONFIG, CANDIDATE.rsplit("@", 1)[1]}
    observed = proof.inspect_role(engine, PROJECT, "worker-children", CANDIDATE, accepted, NETWORKS)
    assert observed["running"] == "example.invalid/release/backend@" + CONFIG
    assert set(observed["verified_runtime_digests"]) == accepted
    state[2] = "sha256:" + "f" * 64
    with pytest.raises(proof.RolloutError, match="running digest"):
        proof.inspect_role(engine, PROJECT, "worker-children", CANDIDATE, accepted, NETWORKS)
    state[2] = CONFIG
    attached.add("shared-network")
    with pytest.raises(proof.RolloutError, match="isolated networks"):
        proof.inspect_role(engine, PROJECT, "worker-children", CANDIDATE, accepted, NETWORKS)
