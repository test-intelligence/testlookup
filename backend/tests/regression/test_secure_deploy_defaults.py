"""Deploy files keep unauthenticated services off the network by default.

Found by the 2026-11-12 open-source repository review:

* ``make quickstart`` / ``make dev`` / ``make dev-lite`` run with
  ``APP_ENV=development`` and dev-login on, and published the API (8000) and
  UI (3000) on every interface: anyone on the contributor's network could
  ``POST /api/v1/auth/dev-login?role=admin``. dev-lite's MongoDB, which has no
  authentication, sat on ``0.0.0.0:27017``;
* the release compose (what ``install.sh`` runs) published the Postgres
  superuser (5433) and the S3 root API (9000) on every interface, and the
  documented ``local-llm`` profile published Ollama (no authentication) on
  ``0.0.0.0:11434``. Docker-published ports bypass ufw and firewalld;
* Ollama was pinned to 0.5.4: GHSA-x8qc-fggm-mpqg / CVE-2026-7482 (heap
  out-of-bounds read in the GGUF loader) is fixed only from 0.17.1;
* the monitoring add-on published Jaeger, OTLP, Prometheus and Grafana on every
  interface, with Prometheus' unauthenticated ``/-/quit`` and ``/-/reload``;
* the public Kubernetes ingress routed ``/metrics``, which has no auth;
* ``gen-dev-env.sh`` left ``.env`` (every generated secret) world-readable.

Every published port must resolve to 127.0.0.1 with an empty environment. The
two deliberate public entry points are the release frontend (the product's
front door) and the single-VM frontend on port 80.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.dev-lite.yml",
    "docker-compose.release.yml",
    "docker-compose.monitoring.yml",
    "docker-compose.gcp-vm.yml",
    "docker-compose.airgap.yml",
    "docker-compose.demo.yml",
    ".devcontainer/docker-compose.codespaces.yml",
)
#: (file, service): the deliberate front doors, published on every interface.
PUBLIC_ENTRY_POINTS = {
    ("docker-compose.release.yml", "frontend"),
    ("docker-compose.gcp-vm.yml", "frontend"),
}
#: First Ollama release with CVE-2026-7482 (GHSA-x8qc-fggm-mpqg) fixed.
OLLAMA_FLOOR = (0, 17, 1)

_INNERMOST = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?])([^${}]*))?\}")


def _resolve_unset(text: str) -> str:
    """Compose interpolation with every variable unset, innermost first."""
    def one(match: re.Match) -> str:
        operator, word = match.group(2), match.group(3) or ""
        if operator in (":-", "-"):
            return word
        return ""
    while True:
        resolved = _INNERMOST.sub(one, text)
        if resolved == text:
            return resolved
        text = resolved


def _published(entry) -> tuple[str, str]:
    """(host address, mapping) of one ``ports`` entry; '' means every interface."""
    if isinstance(entry, dict):
        return str(entry.get("host_ip") or ""), str(entry)
    mapping = _resolve_unset(str(entry))
    parts = mapping.split(":")
    return (parts[0] if len(parts) == 3 else ""), mapping


def _services(relative: str) -> dict:
    return (yaml.safe_load((ROOT / relative).read_text(encoding="utf-8")) or {}).get("services") or {}


def _port_cases():
    for relative in COMPOSE_FILES:
        for name, service in _services(relative).items():
            for entry in (service or {}).get("ports") or []:
                yield relative, name, entry


@pytest.mark.parametrize(("relative", "service", "entry"), list(_port_cases()),
                         ids=lambda v: str(v)[:48])
def test_every_published_port_defaults_to_loopback(relative, service, entry):
    address, mapping = _published(entry)
    if (relative, service) in PUBLIC_ENTRY_POINTS:
        return
    assert address == "127.0.0.1", (
        f"{relative}: {service} publishes {mapping!r} on every interface. Docker-published "
        "ports bypass host firewalls; default it to 127.0.0.1 with an opt-in "
        "<SERVICE>_PUBLISH_ADDR override"
    )


def test_the_port_scan_covers_the_services_that_were_exposed():
    """Guards the guard: these mappings existed and must still be scanned."""
    seen = {(relative, service) for relative, service, _ in _port_cases()}
    for case in [
        ("docker-compose.yml", "backend"), ("docker-compose.yml", "frontend"),
        ("docker-compose.yml", "ollama"), ("docker-compose.dev-lite.yml", "mongo"),
        ("docker-compose.dev-lite.yml", "backend"), ("docker-compose.release.yml", "postgres"),
        ("docker-compose.release.yml", "minio"), ("docker-compose.release.yml", "ollama"),
        ("docker-compose.monitoring.yml", "prometheus"),
    ]:
        assert case in seen, f"{case} no longer publishes a port, or the scan lost it"


def test_the_resolver_reads_compose_interpolation():
    assert _published("${POSTGRES_PUBLISH_ADDR:-127.0.0.1}:5433:5432")[0] == "127.0.0.1"
    assert _published("${FRONTEND_PUBLISH_ADDR:+${FRONTEND_PUBLISH_ADDR}:}3000:80")[0] == ""
    assert _published("8000:8000")[0] == ""
    assert _published("127.0.0.1:27017:27017")[0] == "127.0.0.1"


def _version(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", tag)[:3])


def _ollama_tags() -> list[tuple[str, str]]:
    found = []
    pattern = re.compile(r"ollama/ollama:([0-9][0-9.]*)")
    for relative in ("docker-compose.yml", "docker-compose.release.yml", "docker-compose.airgap.yml",
                     "k8s/base/ollama-deployment.yaml", "deploy/images.manifest.txt"):
        for tag in pattern.findall((ROOT / relative).read_text(encoding="utf-8")):
            found.append((relative, tag))
    overlay = (ROOT / "k8s/overlays/openshift-artifactory/kustomization.yaml").read_text(encoding="utf-8")
    match = re.search(r"- name: ollama/ollama\n\s+newName: [^\n]+\n\s+newTag: \"?([0-9.]+)", overlay)
    assert match, "the Artifactory overlay no longer retags ollama/ollama"
    found.append(("k8s/overlays/openshift-artifactory/kustomization.yaml", match.group(1)))
    return found


def test_ollama_is_pinned_past_the_known_advisories_everywhere():
    tags = _ollama_tags()
    assert len(tags) >= 6, tags
    old = [f"{where}: {tag}" for where, tag in tags if _version(tag) < OLLAMA_FLOOR]
    assert not old, "Ollama older than 0.17.1 (CVE-2026-7482): " + ", ".join(old)
    assert len({tag for _, tag in tags}) == 1, f"Ollama versions drifted apart: {tags}"


def test_the_kubernetes_ollama_container_drops_privileges():
    docs = list(yaml.safe_load_all((ROOT / "k8s/base/ollama-deployment.yaml").read_text(encoding="utf-8")))
    deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
    context = deployment["spec"]["template"]["spec"]["containers"][0].get("securityContext") or {}
    assert context.get("allowPrivilegeEscalation") is False
    assert context.get("capabilities", {}).get("drop") == ["ALL"]
    assert context.get("seccompProfile", {}).get("type") == "RuntimeDefault"


@pytest.mark.parametrize("relative", ["docker-compose.yml", "docker-compose.release.yml"])
def test_the_compose_ollama_runs_local_only(relative):
    """Ollama 0.40 calls ollama.com at startup and every ~4 h unless told not to.

    The calls (model recommendations and the cloud model list) are signed with a
    per-install key; DATA_AND_PRIVACY.md promises the bundled stack phones no one.
    """
    env = _services(relative)["ollama"].get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    assert str(env.get("OLLAMA_NO_CLOUD", "")).lower() in {"1", "true"}, (
        f"{relative}: set OLLAMA_NO_CLOUD=1 on the ollama service"
    )


def test_the_kubernetes_ollama_runs_local_only():
    docs = list(yaml.safe_load_all((ROOT / "k8s/base/ollama-deployment.yaml").read_text(encoding="utf-8")))
    deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
    env = {e["name"]: e.get("value") for e in deployment["spec"]["template"]["spec"]["containers"][0].get("env") or []}
    assert str(env.get("OLLAMA_NO_CLOUD", "")).lower() in {"1", "true"}


@pytest.mark.parametrize("relative", ["k8s/base/ingress.yaml", "k8s/overlays/k3s/ingress-traefik.yaml"])
def test_the_public_ingress_does_not_route_metrics(relative):
    paths = []
    for doc in yaml.safe_load_all((ROOT / relative).read_text(encoding="utf-8")):
        for rule in ((doc or {}).get("spec") or {}).get("rules") or []:
            paths += [p["path"] for p in (rule.get("http") or {}).get("paths") or []]
    assert paths, f"{relative}: no ingress paths found, so this check proves nothing"
    assert not [p for p in paths if p.rstrip("/") == "/metrics"], (
        f"{relative} routes /metrics, which has no authentication, to the internet; "
        "Prometheus scrapes pods directly (k8s/monitoring/podmonitors.yaml)"
    )


@pytest.mark.parametrize("overlay", ["openshift", "openshift-artifactory"])
def test_no_openshift_route_publishes_the_whole_backend(overlay):
    """A Route without a path sends every path of its host, /metrics included.

    Both OpenShift overlays had one Route for the whole API host (OSS review
    2026-11-12, M-12); the API host now carries one Route per path the Ingress
    sends to the backend.
    """
    routes = [d for d in yaml.safe_load_all((ROOT / f"k8s/overlays/{overlay}/route.yaml").read_text(encoding="utf-8"))
              if d and d.get("kind") == "Route"]
    backend = [r for r in routes if r["spec"]["to"]["name"].startswith("testlookup-backend")]
    assert backend, f"{overlay}: no Route to the backend found, so this check proves nothing"
    bad = [r["metadata"]["name"] for r in backend
           if (r["spec"].get("path") or "/").rstrip("/") in ("", "/metrics")]
    assert not bad, f"{overlay}: these Routes publish /metrics (no path, or /metrics itself): {bad}"


def test_prometheus_has_no_unauthenticated_lifecycle_api():
    command = _services("docker-compose.monitoring.yml")["prometheus"]["command"]
    assert "--web.enable-lifecycle" not in command


def test_the_single_vm_frontend_calls_the_api_same_origin():
    """The VM guide keeps 8000 on loopback; the browser must not need it."""
    build = _services("docker-compose.gcp-vm.yml")["frontend"]["build"]["args"]
    assert _resolve_unset(str(build["VITE_API_BASE_URL"])) == ""
    example = (ROOT / ".env.gcp-vm.example").read_text(encoding="utf-8")
    url = re.search(r"^VITE_API_BASE_URL=(.*)$", example, re.MULTILINE).group(1).strip()
    assert ":8000" not in url, ".env.gcp-vm.example points browsers at the backend port"


def test_gen_dev_env_makes_the_env_file_owner_only_before_writing_secrets():
    script = (ROOT / "scripts/gen-dev-env.sh").read_text(encoding="utf-8")
    copy = script.index('cp "$EXAMPLE" "$ENV_FILE"')
    chmod = script.index('chmod 600 "$ENV_FILE"')
    first_secret = script.index("set_kv POSTGRES_PASSWORD")
    assert copy < chmod < first_secret
    assert re.search(r"^\s*chmod 600 \.env", (ROOT / "install.sh").read_text(encoding="utf-8"), re.MULTILINE)


def _bash() -> list[str] | None:
    for candidate in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if candidate and Path(candidate).exists():
            try:
                subprocess.run([candidate, "-c", "exit 0"], check=True, capture_output=True, timeout=30)
                return [candidate]
            except (OSError, subprocess.SubprocessError):
                continue
    return None


@pytest.mark.skipif(os.name == "nt", reason="NTFS has no POSIX permission bits to observe")
def test_the_generated_env_file_is_not_readable_by_other_users(tmp_path):
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/gen-dev-env.sh", tmp_path / "scripts/gen-dev-env.sh")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    env = {**os.environ, "TL_PYTHON_BIN": sys.executable}
    subprocess.run([*bash, "scripts/gen-dev-env.sh", "--release"], cwd=tmp_path, env=env,
                   check=True, capture_output=True, timeout=120)
    mode = stat.S_IMODE((tmp_path / ".env").stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0, f".env mode is {oct(mode)}"
