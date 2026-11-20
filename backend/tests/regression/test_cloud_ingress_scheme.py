"""What the cloud and dev overlays publish, and over which scheme.

The rules:

* GKE: a GCE Ingress listens on port 80 as well as 443, so the overlay must
  carry a ``FrontendConfig`` that redirects HTTP to HTTPS and the Ingress must
  reference it. Without it the app, the API and the MCP host answer bearer
  tokens over plain HTTP.
* EKS keeps its ALB redirect to 443, and the GKE Ingress still serves both the
  application host and the MCP host.
* The cloud ingresses publish the health endpoints by name (Exact paths), not
  the whole ``/health/`` prefix, and still route them at the diagnostics
  Service that keeps answering while the backend is failing readiness.
  ``/health/details`` stays published because the web UI polls it for its
  degraded banner and version badge.
* The ``dev`` overlay runs one ``APP_ENV`` in every backend process, and that
  value is not ``development``: the overlay is a small install on a shared
  cluster, not a developer's laptop. Its Ingresses do not use the public
  ingress class.

Rendered with kustomize; nothing here talks to a cluster.
"""
from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
OVERLAYS = ROOT / "k8s" / "overlays"

APP_HOST = "testlookup.example.com"
MCP_HOST = "testlookup-mcp.example.com"
DIAGNOSTICS = "testlookup-backend-diagnostics"
PUBLISHED_HEALTH = {"/health/live", "/health/ready", "/health/version", "/health/details"}
CLOUD_OVERLAYS = ("aws-eks", "gcp-gke", "azure-aks")
FRONTEND_CONFIG_ANNOTATION = "networking.gke.io/v1beta1.FrontendConfig"


@functools.lru_cache(maxsize=None)
def _rendered(overlay: str) -> tuple[dict, ...]:
    path = str(OVERLAYS / overlay)
    kustomize, kubectl = shutil.which("kustomize"), shutil.which("kubectl")
    if kustomize:
        command = [kustomize, "build", path]
    elif kubectl:
        command = [kubectl, "kustomize", path]
    else:
        pytest.skip("neither kustomize nor kubectl is installed (rendering only; no cluster access)")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0 and 'unknown command "kustomize"' in result.stderr:
        pytest.skip(f"{command[0]} has no kustomize command (a version-picking wrapper)")
    assert result.returncode == 0, result.stderr
    return tuple(d for d in yaml.safe_load_all(result.stdout) if d)


def _kind(overlay: str, kind: str) -> list[dict]:
    return [d for d in _rendered(overlay) if d.get("kind") == kind]


def _routes(overlay: str) -> dict[tuple[str, str], dict]:
    """(host, path) -> the path entry, across every Ingress the overlay renders."""
    found = {}
    for ingress in _kind(overlay, "Ingress"):
        for rule in ingress["spec"].get("rules", []):
            for route in rule["http"]["paths"]:
                found[(rule.get("host"), route["path"])] = route
    return found


# ── GKE: plain HTTP is redirected ────────────────────────────────────────────

def test_the_gke_ingress_redirects_http_to_https():
    configs = {d["metadata"]["name"]: d for d in _kind("gcp-gke", "FrontendConfig")}
    ingresses = _kind("gcp-gke", "Ingress")
    assert ingresses, "the GKE overlay renders no Ingress"
    for ingress in ingresses:
        name = (ingress["metadata"].get("annotations") or {}).get(FRONTEND_CONFIG_ANNOTATION)
        assert name, (
            f"{ingress['metadata']['name']} references no FrontendConfig, so the GCE load "
            "balancer answers on port 80 without a redirect"
        )
        assert name in configs, f"the Ingress references FrontendConfig {name!r}, which the overlay does not render"
        config = configs[name]
        assert config["metadata"]["namespace"] == ingress["metadata"]["namespace"]
        assert config["spec"]["redirectToHttps"]["enabled"] is True


def test_the_gke_ingress_serves_the_application_and_the_mcp_host():
    """The redirect is no use on an Ingress that has lost the application host."""
    routes = _routes("gcp-gke")
    assert routes[(APP_HOST, "/")]["backend"]["service"]["name"] == "testlookup-frontend"
    assert routes[(APP_HOST, "/api/")]["backend"]["service"]["name"] == "testlookup-backend"
    assert routes[(MCP_HOST, "/")]["backend"]["service"]["name"] == "testlookup-mcp"
    assert [i["metadata"]["name"] for i in _kind("gcp-gke", "Ingress")] == ["testlookup-ingress"]


def test_the_eks_ingresses_still_redirect_to_443():
    ingresses = _kind("aws-eks", "Ingress")
    assert {i["metadata"]["name"] for i in ingresses} == {"testlookup-ingress", "testlookup-mcp-ingress"}
    for ingress in ingresses:
        assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/ssl-redirect"] == "443"


# ── Cloud ingresses: health endpoints by name ────────────────────────────────

@pytest.mark.parametrize("overlay", CLOUD_OVERLAYS)
def test_cloud_ingresses_publish_health_endpoints_by_name(overlay):
    health = {path: route for (host, path), route in _routes(overlay).items()
              if host == APP_HOST and path.startswith("/health")}
    assert set(health) == PUBLISHED_HEALTH, (
        f"{overlay} publishes {sorted(health)}; a /health/ prefix also publishes every "
        "health endpoint added later"
    )
    for path, route in health.items():
        assert route["pathType"] == "Exact", f"{overlay}: {path} is {route['pathType']}, not Exact"
        assert route["backend"]["service"]["name"] == DIAGNOSTICS, (
            f"{overlay}: {path} no longer reaches the Service that answers while the backend is NotReady"
        )


@pytest.mark.parametrize("overlay", CLOUD_OVERLAYS)
def test_cloud_ingresses_still_route_the_application(overlay):
    routes = _routes(overlay)
    for path, service in (("/api/", "testlookup-backend"), ("/ws/", "testlookup-backend"),
                          ("/webhooks/", "testlookup-backend"), ("/", "testlookup-frontend")):
        route = routes[(APP_HOST, path)]
        assert route["pathType"] == "Prefix"
        assert route["backend"]["service"]["name"] == service
    assert routes[(MCP_HOST, "/")]["backend"]["service"]["name"] == "testlookup-mcp"


# ── dev overlay ──────────────────────────────────────────────────────────────

def _app_env_by_workload(overlay: str) -> dict[str, str]:
    config = next(d for d in _kind(overlay, "ConfigMap") if d["metadata"]["name"] == "testlookup-config")
    from_config = config["data"]["APP_ENV"]
    found = {}
    for deployment in _kind(overlay, "Deployment"):
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            reads_config = any((ref.get("configMapRef") or {}).get("name") == "testlookup-config"
                               for ref in container.get("envFrom") or [])
            pinned = [e.get("value") for e in container.get("env") or [] if e["name"] == "APP_ENV"]
            if pinned:  # an explicit env entry wins over envFrom
                found[deployment["metadata"]["name"]] = pinned[0]
            elif reads_config:
                found[deployment["metadata"]["name"]] = from_config
    return found


def test_dev_backend_and_workers_agree_on_app_env():
    values = _app_env_by_workload("dev")
    assert {"testlookup-backend", "testlookup-worker-critical", "testlookup-beat"} <= set(values)
    assert len(set(values.values())) == 1, f"the dev overlay runs mixed APP_ENV values: {values}"
    assert set(values.values()) == {"production"}, (
        "the dev overlay is a cluster install; development mode skips the production "
        "secret check and mounts the debug routes"
    )


def test_dev_ingresses_are_not_on_the_public_ingress_class():
    ingresses = _kind("dev", "Ingress")
    public = {i["spec"]["ingressClassName"] for i in _kind("prod", "Ingress")}
    assert public == {"nginx"}
    for ingress in ingresses:
        assert ingress["spec"].get("ingressClassName") not in public | {None}, (
            f"{ingress['metadata']['name']} in the dev overlay is served by the public ingress controller"
        )


def test_prod_keeps_the_public_ingress_and_production_mode():
    """The positive case: the dev changes did not leak into the overlay cloud installs build on."""
    assert set(_app_env_by_workload("prod").values()) == {"production"}
    assert (None, "/") not in _routes("prod")
    assert _routes("prod")[(APP_HOST, "/")]["backend"]["service"]["name"] == "testlookup-frontend"
