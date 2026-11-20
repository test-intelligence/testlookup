"""OpenShift's router reaches every Service an OpenShift Route points at.

Both OpenShift overlays publish the UI (``testlookup-frontend``, port
``http``), MCP (``testlookup-mcp``, port ``sse``) and, on the API host, one
Route per backend path (``testlookup-backend``, and each published health
endpoint to ``testlookup-backend-diagnostics``, port ``http``; the Artifactory
deploy script advertises ``https://<api host>/api-docs``). Under the base ``default-deny-all`` the
router needs an ingress allow on each target. ``allow-frontend`` and
``allow-mcp`` admitted the ``openshift-ingress`` namespace; ``allow-backend``
did not, so the API Route never reached the backend.

The router is matched two ways. Its namespace name covers a router on the pod
network. A router on the host network (HostNetwork endpoint publishing, common
on bare metal) is not traffic from that namespace's pods; OpenShift documents
the namespace label ``policy-group.network.openshift.io/ingress: ""`` for
allowing router traffic regardless of the router configuration, and the base
policies now carry both.

A Route ``path`` is a prefix match and has no exact mode. One Route for
``/health/`` published every health endpoint, including ones added later; so
each published endpoint (live, ready, version, details) has its own Route and
no Route covers the ``/health`` prefix as a whole.
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
DIAGNOSTICS = "testlookup-backend-diagnostics"
# Route name -> path, for the health endpoints an installation publishes.
HEALTH_ROUTES = {
    "testlookup-api-health-live": "/health/live",
    "testlookup-api-health-ready": "/health/ready",
    "testlookup-api-health-version": "/health/version",
    "testlookup-api-health-details": "/health/details",
}


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
    matches = {name: labels for name, labels in pods.items() if selects({"matchLabels": spec["selector"]}, labels)}
    assert len(matches) == 1, f"{service} selects {len(matches)} Deployments"
    workload, labels = next(iter(matches.items()))
    return labels, _container_port(workload, port["targetPort"])


def _container_port(workload: str, target) -> int:
    """A Service targetPort is a number, or the NAME of a container port of the pod."""
    if isinstance(target, int) or str(target).isdigit():
        return int(target)
    for path in sorted(BASE.glob("*.yaml")):
        for doc in yaml_docs(path):
            if doc.get("kind") in ("Deployment", "StatefulSet") and doc["metadata"]["name"] == workload:
                for container in doc["spec"]["template"]["spec"]["containers"]:
                    for container_port in container.get("ports") or []:
                        if container_port.get("name") == target:
                            return int(container_port["containerPort"])
    raise AssertionError(f"{workload} has no container port named {target!r}")


def test_the_route_scan_finds_the_api_route():
    """Everything below is parametrized over the Routes; an empty scan passes vacuously."""
    for overlay in OPENSHIFT_OVERLAYS:
        routes = {name: (svc, port) for name, svc, port in _routes(overlay)}
        assert routes.get("testlookup-api") == ("testlookup-backend", "http"), (overlay, routes)
        for name in HEALTH_ROUTES:
            assert routes.get(name) == (DIAGNOSTICS, "http"), (overlay, name, routes)
        assert len(routes) == 6 + len(HEALTH_ROUTES), (overlay, routes)


def _route_docs(overlay: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in yaml_docs(OVERLAYS / overlay / "route.yaml") if d.get("kind") == "Route"}


@pytest.mark.parametrize("overlay", OPENSHIFT_OVERLAYS)
def test_no_route_publishes_the_whole_health_prefix(overlay: str):
    """Refusal case: a Route at /health or /health/ also serves /health/ingestion
    and every health endpoint added later."""
    routes = _route_docs(overlay)
    api_host = routes["testlookup-api"]["spec"]["host"]
    health = {name: doc["spec"]["path"] for name, doc in routes.items()
              if doc["spec"].get("host") == api_host and doc["spec"].get("path", "/").startswith("/health")}
    assert health == HEALTH_ROUTES, f"{overlay}: the API host publishes {health}"
    whole_host = [name for name, doc in routes.items()
                  if doc["spec"].get("host") == api_host and doc["spec"].get("path", "/") == "/"]
    assert not whole_host, f"{overlay}: {whole_host} publishes every path of the API host"


@pytest.mark.parametrize("overlay", OPENSHIFT_OVERLAYS)
def test_each_health_route_matches_the_other_api_routes(overlay: str):
    """Positive case: same Service port, TLS settings and annotations as the
    API Route beside it, so the endpoints answer exactly as they did."""
    routes = _route_docs(overlay)
    api = routes["testlookup-api"]
    for name in HEALTH_ROUTES:
        route = routes[name]
        assert route["spec"]["to"]["name"] == DIAGNOSTICS
        assert route["spec"]["port"] == api["spec"]["port"], name
        assert route["spec"]["tls"] == api["spec"]["tls"], name
        assert route["metadata"].get("annotations") == api["metadata"].get("annotations"), name
        assert route["metadata"].get("labels") == api["metadata"].get("labels"), name


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


def test_the_deploy_script_removes_the_retired_prefix_route():
    """`oc apply -k` never deletes: on an install made before the split, the old
    testlookup-api-health Route would keep publishing the whole /health/ prefix."""
    script = (OVERLAYS.parents[1] / "openshiftsetup" / "deploy-openshift-artifactory.sh").read_text(encoding="utf-8")
    apply_at = script.index('"$KCLI" apply -k "$OVERLAY_DIR"' + chr(10))  # the real apply, not the dry run
    delete_at = script.index('delete route testlookup-api-health --ignore-not-found')
    assert apply_at < delete_at, "the old Route is deleted before the new ones exist"
