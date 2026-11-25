"""The frontend image runs nginx as a non-root user on container port 8080.

The rule: nginx never runs as root. The image starts as uid 101 and listens on
8080 inside the container, because a non-root process cannot be counted on to
bind port 80. Nothing outside the container moves: Compose still publishes the
same host ports, the Kubernetes Service still answers on port 80, and the
public demo's tunnel still reaches ``frontend:80``.

No image can be built here, so the files are read:

* the Dockerfile's production stage (user, port, the main nginx config);
* the main nginx config (no ``user``, every written path under ``/tmp``);
* the three server configs (the template and the two ConfigMap copies);
* the Kubernetes base (container port, probes, Service, NetworkPolicy);
* every Compose file that publishes or probes the frontend, and the end-to-end
  harness that reaches it by container port.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "frontend" / "Dockerfile"
MAIN_CONF = ROOT / "frontend" / "nginx.main.conf"
TEMPLATE = ROOT / "frontend" / "nginx.conf.template"
CONFIGMAPS = (
    ROOT / "k8s" / "overlays" / "k3s" / "frontend-nginx-configmap.yaml",
    ROOT / "k8s" / "overlays" / "openshift-artifactory" / "frontend-nginx-configmap.yaml",
)
BASE = ROOT / "k8s" / "base"
CONTAINER_PORT = 8080
NGINX_UID = 101


class _TagTolerantLoader(yaml.SafeLoader):
    """Compose's ``!reset`` / ``!override`` tags are not YAML types."""


_TagTolerantLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _compose(name: str) -> dict:
    text = (ROOT / name).read_text(encoding="utf-8")
    return (yaml.load(text, Loader=_TagTolerantLoader) or {}).get("services") or {}


def _production_stage() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = re.split(r"(?m)^FROM\s+", text)
    production = [stage for stage in stages if re.match(r"\S+\s+AS\s+production\b", stage)]
    assert len(production) == 1, "frontend/Dockerfile needs exactly one production stage"
    # Comments are prose: a rule must be met by an instruction.
    return "\n".join(line for line in production[0].splitlines() if not line.lstrip().startswith("#"))


def _docs(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


# ── the image ───────────────────────────────────────────────────────────────


def test_the_production_stage_runs_as_the_numeric_nginx_user() -> None:
    """Numeric on purpose: with ``runAsNonRoot`` the kubelet refuses an image
    whose user is a name, because it cannot tell that the name is not root."""
    users = re.findall(r"(?m)^USER\s+(\S+)", _production_stage())
    assert users, "the production stage has no USER line, so nginx starts as root"
    assert users[-1] == str(NGINX_UID), users


def test_the_production_stage_exposes_8080_and_not_80() -> None:
    stage = _production_stage()
    assert re.findall(r"(?m)^EXPOSE\s+(.+)$", stage) == [str(CONTAINER_PORT)]
    assert re.search(rf"(?m)^ENV\s+.*\bNGINX_LISTEN_PORT={CONTAINER_PORT}\b", stage), (
        "the template's listen port needs a default in the image"
    )


def test_the_image_ships_the_main_config_and_no_stock_server() -> None:
    stage = _production_stage()
    assert re.search(r"(?m)^COPY\s+nginx\.main\.conf\s+/etc/nginx/nginx\.conf\s*$", stage)
    # The stock default.conf listens on 80. Left in place, a failed template
    # render would start nginx on the stock page instead of failing.
    assert "rm -f /etc/nginx/conf.d/default.conf" in stage
    # uid 101 renders the template into conf.d at start; OpenShift runs the
    # image under an arbitrary uid in group 0, hence the group bits.
    assert re.search(rf"chown -R {NGINX_UID}:0 /etc/nginx/conf\.d", stage)
    assert re.search(r"chmod -R g\+rwX /etc/nginx/conf\.d", stage)


def test_the_stage_still_takes_os_updates_before_it_drops_root() -> None:
    """Positive case: ``apk upgrade`` needs root, so USER comes after it."""
    stage = _production_stage()
    assert stage.index("apk upgrade --no-cache") < stage.index(f"USER {NGINX_UID}")
    assert stage.rstrip().endswith('CMD ["nginx", "-g", "daemon off;"]')


def _main_directives() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in MAIN_CONF.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().rstrip(";")
        if line and not line.endswith("{") and line != "}":
            name, _, rest = line.partition(" ")
            found.setdefault(name, []).append(rest.strip())
    return found


def test_the_main_config_has_no_user_and_writes_only_under_tmp() -> None:
    directives = _main_directives()
    assert "user" not in directives, "a `user` directive only makes sense under a root master"
    assert directives["pid"] == ["/tmp/nginx.pid"]
    for name in ("client_body_temp_path", "proxy_temp_path", "fastcgi_temp_path",
                 "uwsgi_temp_path", "scgi_temp_path"):
        assert len(directives.get(name, [])) == 1, f"{name} must be set: nginx creates it at start"
        assert directives[name][0].startswith("/tmp/"), (name, directives[name])


def test_the_main_config_still_loads_the_server_block_and_mime_types() -> None:
    """Positive case: what the stock file did for the server config, it still does."""
    directives = _main_directives()
    assert "/etc/nginx/conf.d/*.conf" in directives["include"]
    assert "/etc/nginx/mime.types" in directives["include"]
    assert directives["worker_processes"] == ["auto"]


# ── the server configs ──────────────────────────────────────────────────────


def _listen(text: str) -> list[str]:
    return re.findall(r"(?m)^\s*listen\s+([^;]+);", text)


def test_the_template_listens_on_the_image_default() -> None:
    assert _listen(TEMPLATE.read_text(encoding="utf-8")) == ["${NGINX_LISTEN_PORT}"]


@pytest.mark.parametrize("path", CONFIGMAPS, ids=lambda p: p.parent.name)
def test_the_rendered_configmap_copies_listen_on_8080(path: Path) -> None:
    (configmap,) = _docs(path)
    assert _listen(configmap["data"]["default.conf"]) == [str(CONTAINER_PORT)]


# ── Kubernetes base ─────────────────────────────────────────────────────────


def _frontend_container() -> dict:
    (deployment,) = _docs(BASE / "frontend-deployment.yaml")
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    return container


def test_the_pod_listens_and_is_probed_on_8080() -> None:
    container = _frontend_container()
    assert container["ports"] == [{"containerPort": CONTAINER_PORT, "name": "http"}]
    for probe in ("livenessProbe", "readinessProbe"):
        assert container[probe]["httpGet"]["port"] in (CONTAINER_PORT, "http"), probe
    assert container["securityContext"]["runAsUser"] == NGINX_UID


def test_the_service_still_answers_on_80() -> None:
    """Positive case: ingresses, Routes and port-forwards name the Service
    port, so it stays 80 and only its target moves."""
    service = next(
        doc for doc in _docs(BASE / "services.yaml")
        if doc["kind"] == "Service" and doc["metadata"]["name"] == "testlookup-frontend"
    )
    (port,) = service["spec"]["ports"]
    assert (port["name"], port["port"]) == ("http", 80)
    assert port["targetPort"] in (CONTAINER_PORT, "http")


def test_the_network_policy_admits_the_container_port() -> None:
    """A NetworkPolicy port is the pod's port, not the Service's."""
    policy = next(
        doc for doc in _docs(BASE / "networkpolicy.yaml")
        if doc["kind"] == "NetworkPolicy" and doc["metadata"]["name"] == "allow-frontend"
    )
    ports = [port["port"] for rule in policy["spec"]["ingress"] for port in rule.get("ports", [])]
    assert ports and set(ports) == {CONTAINER_PORT}, ports


# ── Compose and the end-to-end harness ──────────────────────────────────────


def test_the_release_stack_publishes_the_same_host_port() -> None:
    frontend = _compose("docker-compose.release.yml")["frontend"]
    assert frontend["ports"] == [
        f"${{FRONTEND_PUBLISH_ADDR:-127.0.0.1}}:3000:{CONTAINER_PORT}"
    ]
    assert "user" not in frontend, "the image sets its own user"


def test_the_release_healthcheck_asks_the_port_nginx_listens_on() -> None:
    test = _compose("docker-compose.release.yml")["frontend"]["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL"
    probes = [part.strip() for part in test[1].split("||")]
    assert probes[0] == f"wget -q -O /dev/null http://127.0.0.1:{CONTAINER_PORT}/"
    # Second try on 80: the public demo keeps nginx there (below), and so does
    # an image from before this change that an upgrade test or a rollback runs.
    assert probes[1:] == ["wget -q -O /dev/null http://127.0.0.1:80/"]


def test_the_single_vm_overlay_still_publishes_host_port_80() -> None:
    frontend = _compose("docker-compose.gcp-vm.yml")["frontend"]
    assert frontend["ports"] == [f"80:{CONTAINER_PORT}"]


def test_the_demo_tunnel_still_finds_the_frontend_on_80() -> None:
    """The tunnel's target (http://frontend:80) is set in the Cloudflare
    dashboard, outside this repository, so the demo keeps nginx on 80."""
    frontend = _compose("docker-compose.demo.yml")["frontend"]
    assert frontend["environment"] == {"NGINX_LISTEN_PORT": "80"}
    # A non-root process may bind a port below 1024 only where this is 0.
    assert frontend["sysctls"] == {"net.ipv4.ip_unprivileged_port_start": "0"}


def test_no_other_compose_service_overrides_the_listen_port() -> None:
    for path in sorted(ROOT.glob("docker-compose*.yml")):
        if path.name == "docker-compose.demo.yml":
            continue
        assert "NGINX_LISTEN_PORT" not in path.read_text(encoding="utf-8"), path.name


def test_the_disposable_stack_reaches_the_frontend_on_8080() -> None:
    source = (ROOT / "e2e-runner" / "ephemeral.py").read_text(encoding="utf-8")
    assert f'("frontend", {CONTAINER_PORT})' in source
    assert f'"TL_WEB=http://frontend:{CONTAINER_PORT}"' in source
    assert f'address("frontend", {CONTAINER_PORT})' in source
    assert '("frontend", 80)' not in source and '"TL_WEB=http://frontend"' not in source
