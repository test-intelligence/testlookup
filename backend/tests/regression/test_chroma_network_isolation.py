"""Only the backend and the Celery workers can reach ChromaDB (Dependabot triage 2026-10-12).

Four chromadb advisories have no fixed release (GHSA-f4j7-r4q5-qw2c and
GHSA-36p7-vc44-83pf, code injection; GHSA-2wm9-hf6c-p5cr and
GHSA-xph7-9rjv-w5fr, cross-tenant RBAC). They sit in chromadb's Python server
and its auth providers. The deployments run the ``chromadb/chroma`` image, a
Rust binary with no Python and no authentication of its own, so the control
that is left is who can connect to it.

That matters for more than the server. The backend's Python client builds each
collection's embedding function from the configuration Chroma returns
(``Collection._embed`` -> ``load_collection_configuration_from_json`` ->
``build_from_config``) whenever the caller passes no embedding function of its
own, which is every call site here. A peer that can write a collection's
configuration can name the ``openai`` embedding function with its own
``api_base`` and any ``api_key_env_var``, and the backend then posts the
documents, with that environment variable as the bearer token, to the peer.

Before this change the reachable set was wider than the two callers:

* ``docker-compose.yml`` and ``docker-compose.release.yml`` published Chroma
  as ``8001:8000``, i.e. on every host interface, and kept it on the shared
  ``testlookup_net`` with the frontend, MCP server, beat and seed jobs.
* the homelab overlay listed ``testlookup-chromadb`` in ``allow-data-stores``,
  which also admitted the MCP server and beat, on every port.

These tests hold the narrowed set. They read the manifests directly rather than
rendering kustomize, so they run on any machine.
"""
from __future__ import annotations

from typing import Any

import pytest
import yaml

from compose_topology_contract import load_compose_topology
from k8s_network_policy_contract import (
    BASE,
    REPO,
    admits as _admits,
    app_pods as _app_pods,
    policies_for as _policies_for,
    template_labels as _template_labels,
    yaml_docs as _yaml_docs,
)

OVERLAYS_WITH_CHROMA = ("homelab", "openshift-artifactory")
CHROMA_PORT = 8000
CHROMA_SERVICE = "chromadb"


# ── NetworkPolicy evaluation: k8s_network_policy_contract ───────────────────

def _chroma_labels(overlay: str) -> dict[str, str]:
    labels = _template_labels([REPO / "k8s" / "overlays" / overlay / "infra-chromadb.yaml"])
    assert "testlookup-chromadb" in labels, f"{overlay}: no testlookup-chromadb Deployment"
    return labels["testlookup-chromadb"]


@pytest.mark.parametrize("overlay", OVERLAYS_WITH_CHROMA)
def test_only_the_backend_and_workers_reach_chroma(overlay: str) -> None:
    policies = _policies_for(overlay)
    chroma = _chroma_labels(overlay)
    pods = _app_pods()
    allowed = {
        name for name, labels in pods.items()
        if name != "testlookup-chromadb" and _admits(policies, chroma, labels, CHROMA_PORT)
    }
    callers = {
        name for name, labels in pods.items()
        if name == "testlookup-backend" or labels.get("app.kubernetes.io/component") == "worker"
    }
    assert allowed == callers, (
        f"{overlay}: pods admitted to Chroma {sorted(allowed)}, expected exactly the "
        f"backend and the workers {sorted(callers)}"
    )


@pytest.mark.parametrize("overlay", OVERLAYS_WITH_CHROMA)
def test_the_mcp_server_and_beat_cannot_reach_chroma(overlay: str) -> None:
    """The specific regression: homelab's allow-data-stores admitted both."""
    policies = _policies_for(overlay)
    chroma = _chroma_labels(overlay)
    pods = _app_pods()
    for name in ("testlookup-mcp", "testlookup-beat", "testlookup-frontend"):
        assert not _admits(policies, chroma, pods[name], CHROMA_PORT), (
            f"{overlay}: {name} can connect to Chroma"
        )


@pytest.mark.parametrize("overlay", OVERLAYS_WITH_CHROMA)
def test_chroma_is_only_open_on_its_own_port(overlay: str) -> None:
    policies = _policies_for(overlay)
    chroma = _chroma_labels(overlay)
    backend = _app_pods()["testlookup-backend"]
    assert _admits(policies, chroma, backend, CHROMA_PORT)
    assert not _admits(policies, chroma, backend, 22), f"{overlay}: Chroma open on every port"


def test_allow_chromadb_names_pods_only() -> None:
    """No namespaceSelector or ipBlock peer: Chroma is never reachable from outside."""
    policy = next(
        d for d in _yaml_docs(BASE / "networkpolicy.yaml")
        if d.get("kind") == "NetworkPolicy" and d["metadata"]["name"] == "allow-chromadb"
    )
    for rule in policy["spec"]["ingress"]:
        assert rule.get("from"), "allow-chromadb has a rule with no `from` (every source)"
        for peer in rule["from"]:
            assert set(peer) == {"podSelector"}, f"allow-chromadb peer {peer} is not a pod selector"


def test_no_overlay_patches_allow_chromadb() -> None:
    """The cloud overlays widen allow-mcp with ipBlock patches; none may do that here."""
    offenders = [
        str(path.relative_to(REPO))
        for path in (REPO / "k8s" / "overlays").glob("*/kustomization.yaml")
        if "allow-chromadb" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_evaluator_catches_the_old_homelab_policy() -> None:
    """Without this, an evaluator that admitted nothing would pass the tests above."""
    old = yaml.safe_load(
        """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-data-stores}
spec:
  podSelector:
    matchExpressions:
      - {key: app, operator: In, values: [testlookup-postgres, testlookup-chromadb]}
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector: {matchLabels: {app: testlookup-backend}}
        - podSelector: {matchLabels: {app: testlookup-mcp}}
        - podSelector: {matchLabels: {app: testlookup-beat}}
"""
    )
    policies = _policies_for("homelab") + [old]
    chroma = _chroma_labels("homelab")
    pods = _app_pods()
    assert _admits(policies, chroma, pods["testlookup-mcp"], CHROMA_PORT)
    assert _admits(policies, chroma, pods["testlookup-beat"], 22)


# ── Docker Compose ──────────────────────────────────────────────────────────

def _env(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


def _networks(service: dict[str, Any]) -> set[str]:
    nets = service.get("networks") or ["default"]
    return set(nets) if isinstance(nets, (list, dict)) else {str(nets)}


def _chroma_neighbours(topology: dict[str, Any]) -> set[str]:
    services = topology["services"]
    chroma_nets = _networks(services[CHROMA_SERVICE])
    return {
        name for name, svc in services.items()
        if name != CHROMA_SERVICE and _networks(svc) & chroma_nets
    }


def _chroma_callers(topology: dict[str, Any]) -> set[str]:
    return {
        name for name, svc in topology["services"].items()
        if _env(svc).get("CHROMA_HOST") == CHROMA_SERVICE
    }


COMPOSE_STACKS = {
    "release": ["docker-compose.release.yml"],
    "airgap": ["docker-compose.release.yml", "docker-compose.airgap.yml"],
    "dev": ["docker-compose.yml"],
    "gcp-vm": ["docker-compose.yml", "docker-compose.gcp-vm.yml"],
}


@pytest.mark.parametrize("stack", sorted(COMPOSE_STACKS))
def test_compose_chroma_shares_a_network_only_with_its_callers(stack: str) -> None:
    topology = load_compose_topology(REPO / f for f in COMPOSE_STACKS[stack])
    callers = _chroma_callers(topology)
    assert callers >= {"backend", "worker"}, f"{stack}: CHROMA_HOST=chromadb callers moved: {callers}"
    assert _chroma_neighbours(topology) == callers, (
        f"{stack}: services sharing a network with chromadb {sorted(_chroma_neighbours(topology))}, "
        f"expected only the ones that use it {sorted(callers)}"
    )


@pytest.mark.parametrize("stack", sorted(COMPOSE_STACKS))
def test_compose_never_publishes_chroma_beyond_loopback(stack: str) -> None:
    topology = load_compose_topology(REPO / f for f in COMPOSE_STACKS[stack])
    for port in topology["services"][CHROMA_SERVICE].get("ports") or []:
        spec = port if isinstance(port, str) else str(port.get("host_ip", "")) + ":"
        assert str(spec).startswith("127.0.0.1:"), f"{stack}: chromadb publishes {port!r} beyond loopback"


@pytest.mark.parametrize("stack", ["release", "airgap"])
def test_release_chroma_is_unpublished_and_internal(stack: str) -> None:
    topology = load_compose_topology(REPO / f for f in COMPOSE_STACKS[stack])
    chroma = topology["services"][CHROMA_SERVICE]
    assert not chroma.get("ports"), f"{stack}: chromadb publishes a host port"
    for net in _networks(chroma):
        assert topology["networks"][net].get("internal") is True, f"{stack}: {net} is not internal"
