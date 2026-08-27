"""Diagnosis must survive the outage it exists to describe.

``/health/ready`` treats Postgres, Mongo and Redis as equally critical, and that
is correct: token revocation fails closed by default, so with Redis down every
authenticated request 503s anyway. Pulling the pods stops routing traffic at a
backend that cannot serve it.

The side effect was measured by fault injection against the live deployment.
Scaling Redis to zero took both replicas to 0/1, the Service's endpoint list
emptied, and Traefik answered ``503 no available server`` for *every* path --
including ``/health/details``, whose entire job is naming which dependency
died. From outside the cluster a Redis outage and a Postgres outage were
indistinguishable, and the CLI could only report that the server had erred.

The fix routes the health prefix at a second Service that publishes NotReady
addresses. These tests pin both halves of that arrangement: health reaches pods
that are failing readiness, and serving traffic still does not.

Read from the manifests rather than asserting names, so reverting either half
fails here instead of at the next outage.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
SERVICES = REPO / "k8s" / "base" / "services.yaml"
INGRESSES = (
    REPO / "k8s" / "base" / "ingress.yaml",
    REPO / "k8s" / "overlays" / "homelab" / "ingress-traefik.yaml",
)

# The endpoints an operator needs while the backend is refusing traffic. All
# live under one prefix, so one routing rule covers them.
HEALTH_PREFIX = "/health/"
# Paths that carry real traffic. These must stay behind the strict gate: if the
# backend cannot serve, routing at it produces errors instead of a clean 503.
SERVING_PREFIXES = ("/api/", "/ws/", "/webhooks/")


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _services() -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d
        for d in _docs(SERVICES)
        if d.get("kind") == "Service"
    }


def _routes(path: Path) -> dict[str, str]:
    """path prefix -> backend Service name, for every Ingress in the file."""
    found: dict[str, str] = {}
    for doc in _docs(path):
        if doc.get("kind") != "Ingress":
            continue
        for rule in doc.get("spec", {}).get("rules", []):
            for route in rule.get("http", {}).get("paths", []):
                svc = route.get("backend", {}).get("service", {}).get("name")
                if svc:
                    found[route["path"]] = svc
    return found


@pytest.mark.parametrize("ingress", INGRESSES, ids=lambda p: p.parent.name)
def test_health_is_routed_at_a_service_that_publishes_notready_pods(ingress):
    """The load-bearing assertion. Everything else here supports it."""
    routes = _routes(ingress)
    assert HEALTH_PREFIX in routes, (
        str(ingress) + " no longer routes " + HEALTH_PREFIX + " at all"
    )

    target = routes[HEALTH_PREFIX]
    services = _services()
    assert target in services, (
        HEALTH_PREFIX + " points at Service '" + target + "', which is not "
        "defined in k8s/base/services.yaml"
    )

    assert services[target]["spec"].get("publishNotReadyAddresses") is True, (
        "health is routed at Service '" + target + "', which does not set "
        "publishNotReadyAddresses. When a critical dependency goes down every "
        "replica fails readiness, that Service empties, and /health/details -- "
        "the endpoint that names the failed dependency -- becomes unreachable "
        "at the one moment it matters."
    )


def test_traefik_is_told_to_target_the_cluster_ip():
    """publishNotReadyAddresses is necessary but not sufficient.

    Measured on Traefik 3.6.10 during the same fault injection: the flag makes
    the EndpointSlice report ready=true and kube-proxy routes the ClusterIP
    correctly, but Traefik builds its server list per-endpoint and those
    endpoints carry serving=false. It matched the router, found zero servers,
    and answered "no available server" -- indistinguishable from the original
    bug, with the flag correctly in place.

    nativeLB makes Traefik target the ClusterIP as one server and leaves the
    filtering to kube-proxy, which honours the flag. Dropping this annotation
    silently restores the outage-blindness on every Traefik-fronted cluster,
    and no other assertion here would notice.
    """
    services = _services()
    target = _routes(INGRESSES[0])[HEALTH_PREFIX]
    annotations = services[target]["metadata"].get("annotations") or {}

    assert (
        annotations.get("traefik.ingress.kubernetes.io/service.nativelb") == "true"
    ), (
        "Service '" + target + "' does not set nativelb. Under Traefik the "
        "readiness gate still hides /health/details, because Traefik drops "
        "endpoints whose EndpointSlice condition serving=false regardless of "
        "publishNotReadyAddresses."
    )


@pytest.mark.parametrize("ingress", INGRESSES, ids=lambda p: p.parent.name)
def test_serving_traffic_stays_behind_the_readiness_gate(ingress):
    """The counterpart, and the reason this is a second Service rather than a
    flag on the first one. Routing /api/ at NotReady pods would replace a clean
    503 with a scatter of downstream errors from a backend that already knows
    it cannot serve."""
    routes = _routes(ingress)
    services = _services()

    for prefix in SERVING_PREFIXES:
        target = routes.get(prefix)
        if target is None:  # not every ingress routes every prefix
            continue
        assert services.get(target, {}).get("spec", {}).get(
            "publishNotReadyAddresses"
        ) is not True, (
            prefix + " is routed at '" + target + "', which publishes NotReady "
            "addresses. Serving traffic must stop when readiness fails."
        )


def test_the_diagnostics_service_selects_the_same_pods():
    """A diagnostics Service whose selector has drifted matches nothing, so it
    would answer 'no available server' exactly like the bug it replaced -- and
    every assertion above would still pass."""
    services = _services()
    health_target = _routes(INGRESSES[0])[HEALTH_PREFIX]
    serving_target = _routes(INGRESSES[0])["/api/"]

    diagnostics = services[health_target]["spec"]
    serving = services[serving_target]["spec"]

    assert diagnostics.get("selector") == serving.get("selector"), (
        "the diagnostics Service selects " + repr(diagnostics.get("selector"))
        + " but traffic goes to " + repr(serving.get("selector"))
        + " -- diagnostics would match no pods"
    )
    assert [p.get("targetPort") for p in diagnostics.get("ports", [])] == [
        p.get("targetPort") for p in serving.get("ports", [])
    ], "the diagnostics Service does not reach the same container port"
