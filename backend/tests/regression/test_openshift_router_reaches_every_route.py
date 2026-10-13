"""OpenShift's router reaches every Service an OpenShift Route points at.

Both OpenShift overlays publish three Routes: the UI (``testlookup-frontend``,
port ``http``), the API (``testlookup-backend``, port ``http``; the Artifactory
deploy script advertises it as ``https://<api host>/docs``) and MCP
(``testlookup-mcp``, port ``sse``). Under the base ``default-deny-all`` the
router needs an ingress allow on each target. ``allow-frontend`` and
``allow-mcp`` admitted the ``openshift-ingress`` namespace; ``allow-backend``
did not, so the API Route never reached the backend.

The router is matched two ways. Its namespace name covers a router on the pod
network. A router on the host network (HostNetwork endpoint publishing, common
on bare metal) is not traffic from that namespace's pods; OpenShift documents
the namespace label ``policy-group.network.openshift.io/ingress: ""`` for
allowing router traffic regardless of the router configuration, and the base
policies now carry both.
"""
from __future__ import annotations

import copy

import pytest

from k8s_network_policy_contract import (
    BASE,
    OVERLAYS,
    admits_from_namespace,
    policies_for,
    selects,
    template_labels,
    yaml_docs,
)

OPENSHIFT_OVERLAYS = ("openshift", "openshift-artifactory")
POLICY_GROUP = "policy-group.network.openshift.io/ingress"
# The router's namespace as the API server labels it.
ROUTER_ON_POD_NETWORK = {"kubernetes.io/metadata.name": "openshift-ingress", POLICY_GROUP: ""}
# A host-network router is matched by the policy group only.
ROUTER_ON_HOST_NETWORK = {POLICY_GROUP: ""}
ROUTERS = {"pod-network": ROUTER_ON_POD_NETWORK, "host-network": ROUTER_ON_HOST_NETWORK}
INGRESS_ALLOWS = ("allow-frontend", "allow-backend", "allow-mcp")


def _routes(overlay: str) -> list[tuple[str, str, str]]:
    """(route, target Service, target port name) for each Route in the overlay."""
    out = []
    for doc in yaml_docs(OVERLAYS / overlay / "route.yaml"):
        if doc.get("kind") == "Route":
            spec = doc["spec"]
            out.append((doc["metadata"]["name"], spec["to"]["name"], spec["port"]["targetPort"]))
    return out


def _target(service: str, port_name: str) -> tuple[dict[str, str], int]:
    """The pod labels a Service selects and the container port its named port maps to."""
    services = {
        d["metadata"]["name"]: d for d in yaml_docs(BASE / "services.yaml") if d.get("kind") == "Service"
    }
    spec = services[service]["spec"]
    port = next(p for p in spec["ports"] if p.get("name") == port_name)
    pods = template_labels(sorted(BASE.glob("*.yaml")))
    matches = [labels for labels in pods.values() if selects({"matchLabels": spec["selector"]}, labels)]
    assert len(matches) == 1, f"{service} selects {len(matches)} Deployments"
    return matches[0], int(port["targetPort"])


def test_the_route_scan_finds_the_api_route():
    """Everything below is parametrized over the Routes; an empty scan passes vacuously."""
    for overlay in OPENSHIFT_OVERLAYS:
        routes = {name: (svc, port) for name, svc, port in _routes(overlay)}
        assert routes.get("testlookup-api") == ("testlookup-backend", "http"), (overlay, routes)
        assert len(routes) == 3, (overlay, routes)


@pytest.mark.parametrize("router", sorted(ROUTERS))
@pytest.mark.parametrize("overlay", OPENSHIFT_OVERLAYS)
def test_the_router_reaches_every_route_target(overlay: str, router: str):
    policies = policies_for(overlay)
    unreachable = []
    for route, service, port_name in _routes(overlay):
        pod, port = _target(service, port_name)
        if not admits_from_namespace(policies, pod, ROUTERS[router], port):
            unreachable.append(f"{route} -> {service}:{port}")
    assert not unreachable, f"{overlay}: a {router} router cannot reach {unreachable}"


def test_no_openshift_overlay_patches_the_ingress_allows():
    """policies_for reads manifests, not kustomize patches: a patch here would
    change what the cluster gets without these tests seeing it."""
    for overlay in OPENSHIFT_OVERLAYS:
        text = (OVERLAYS / overlay / "kustomization.yaml").read_text(encoding="utf-8")
        assert not [name for name in INGRESS_ALLOWS if name in text], overlay


def test_without_the_router_peers_the_api_route_is_shut():
    """The regression, and proof the evaluator can say no: allow-backend as it
    was (no router peer) leaves the API Route unreachable, from either router."""
    policies = copy.deepcopy(policies_for("openshift-artifactory"))
    backend_allow = next(p for p in policies if p["metadata"]["name"] == "allow-backend")
    for rule in backend_allow["spec"]["ingress"]:
        rule["from"] = [
            peer for peer in rule.get("from") or []
            if "openshift-ingress" not in str(peer) and POLICY_GROUP not in str(peer)
        ]
    pod, port = _target("testlookup-backend", "http")
    for router in ROUTERS.values():
        assert not admits_from_namespace(policies, pod, router, port)
    assert not admits_from_namespace(
        policies_for("openshift-artifactory"), pod, {"kubernetes.io/metadata.name": "elsewhere"}, port
    )
