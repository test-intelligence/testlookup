"""The API must be able to trust its proxy's forwarded client address.

Re-audit finding H2. The production API runs under gunicorn/UvicornWorker, and
``gunicorn_conf.py`` set no ``forwarded_allow_ips``. Uvicorn therefore trusted
only 127.0.0.1, so behind ingress-nginx (or the frontend container's own /api
proxy) ``request.client.host`` was the PROXY's address for every caller.

Two production behaviours depended on that address being the real client:

* the login/MFA rate limiter keys its bucket on it (``app/main.py``), so the
  entire external user base collapsed onto one bucket per worker — one script
  hitting the ceiling 429s everyone else out of logging in;
* every IP written for lockout and audit forensics recorded the proxy instead
  of the caller.

The setting must stay fail-closed by default: a deployment that has not
declared its proxy topology must not start believing a client-settable header.
"""
from __future__ import annotations

import importlib.util
import ipaddress
from pathlib import Path

import pytest
import yaml
from uvicorn.middleware.proxy_headers import _TrustedHosts

ROOT = Path(__file__).resolve().parents[3]
CONF = ROOT / "backend" / "gunicorn_conf.py"
CONFIGMAP = ROOT / "k8s" / "base" / "configmap.yaml"
RELEASE_COMPOSE = ROOT / "docker-compose.release.yml"


def _load_conf(monkeypatch, value: str | None):
    """Import gunicorn_conf.py fresh with the env var set (or unset)."""
    if value is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    spec = importlib.util.spec_from_file_location("_gunicorn_conf_under_test", CONF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gunicorn_declares_forwarded_allow_ips(monkeypatch):
    """The setting must exist at all — its absence was the defect."""
    conf = _load_conf(monkeypatch, None)
    assert hasattr(conf, "forwarded_allow_ips"), (
        "gunicorn_conf.py declares no forwarded_allow_ips, so UvicornWorker "
        "falls back to trusting 127.0.0.1 only and every proxied request "
        "reports the proxy as its client address"
    )


def test_default_is_fail_closed(monkeypatch):
    """Unset means trust loopback only — never a wildcard by accident.

    Both loopback forms: gunicorn's own default is ``"127.0.0.1,::1"``, and
    an override that dropped ``::1`` would silently stop honouring the proxy
    on a v6 loopback socket.
    """
    conf = _load_conf(monkeypatch, None)
    assert conf.forwarded_allow_ips == "127.0.0.1,::1"


@pytest.mark.parametrize("value", ["*", "10.42.0.0/16", "192.168.1.7"])
def test_environment_overrides_the_default(monkeypatch, value):
    """Deployments configure the trust boundary through the environment."""
    conf = _load_conf(monkeypatch, value)
    assert conf.forwarded_allow_ips == value


def test_uvicorn_worker_is_still_the_worker_class(monkeypatch):
    """forwarded_allow_ips only reaches ProxyHeadersMiddleware via UvicornWorker.

    If the worker class ever changes, this setting stops having the effect the
    module comment claims, so the two must be asserted together.
    """
    conf = _load_conf(monkeypatch, None)
    assert "UvicornWorker" in conf.worker_class


def test_kubernetes_configmap_declares_the_trust_boundary():
    """k8s runs behind ingress-nginx, so it must opt in explicitly."""
    config = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8"))["data"]
    assert "FORWARDED_ALLOW_IPS" in config, (
        "k8s/base/configmap.yaml does not set FORWARDED_ALLOW_IPS, so the "
        "in-cluster API keeps seeing ingress-nginx as every caller"
    )
    assert _is_a_real_trust_boundary(config["FORWARDED_ALLOW_IPS"])


def test_release_compose_declares_the_trust_boundary():
    """The release compose frontend proxies /api, so it needs it too."""
    services = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    env = services["backend"]["environment"]
    declared = {str(item).partition("=")[0] for item in env}
    assert "FORWARDED_ALLOW_IPS" in declared, (
        "docker-compose.release.yml backend does not set FORWARDED_ALLOW_IPS"
    )


# ── What the value must actually BE ───────────────────────────────────
#
# The first version of this fix shipped "*" and every test above still passed,
# because they all assert on file CONTENT. A wildcard is worse than the bug it
# fixes, so the value itself needs asserting — and so does uvicorn's behaviour
# under it.


def _is_a_real_trust_boundary(value: str) -> bool:
    """True when every entry is a concrete address or network, not a wildcard."""
    entries = [item.strip() for item in str(value).split(",") if item.strip()]
    if not entries:
        return False
    for entry in entries:
        if entry == "*":
            return False
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
    return True


def test_the_helper_rejects_a_wildcard():
    """Guards the guard — a helper that never says no would pass forever."""
    assert not _is_a_real_trust_boundary("*")
    assert not _is_a_real_trust_boundary("10.0.0.0/8,*")
    assert not _is_a_real_trust_boundary("")
    assert _is_a_real_trust_boundary("10.42.0.0/16")
    assert _is_a_real_trust_boundary("127.0.0.1,::1")


def _homelab_value() -> str:
    text = (ROOT / "k8s" / "overlays" / "homelab" / "kustomization.yaml").read_text(
        encoding="utf-8"
    )
    lines = [ln for ln in text.splitlines() if "FORWARDED_ALLOW_IPS" in ln]
    assert lines, "the homelab overlay no longer narrows the trust boundary"
    return lines[0].split(":", 1)[1].strip().strip('"')


def _release_compose_default() -> str:
    services = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    entries = [
        str(item)
        for item in services["backend"]["environment"]
        if str(item).startswith("FORWARDED_ALLOW_IPS=")
    ]
    assert entries, "the release compose no longer sets FORWARDED_ALLOW_IPS"
    # ${FORWARDED_ALLOW_IPS:-<default>} — assert the DEFAULT, which is what an
    # operator who sets nothing actually runs.
    value = entries[0].partition("=")[2]
    assert ":-" in value, "compose no longer supplies a default"
    return value.partition(":-")[2].rstrip("}")


def test_base_configmap_does_not_trust_every_peer():
    value = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8"))["data"][
        "FORWARDED_ALLOW_IPS"
    ]
    assert _is_a_real_trust_boundary(value), _WILDCARD_EXPLANATION.format(
        source="k8s/base/configmap.yaml", value=value
    )


def test_homelab_overlay_does_not_trust_every_peer():
    value = _homelab_value()
    assert _is_a_real_trust_boundary(value), _WILDCARD_EXPLANATION.format(
        source="k8s/overlays/homelab/kustomization.yaml", value=value
    )


def test_release_compose_does_not_trust_every_peer():
    value = _release_compose_default()
    assert _is_a_real_trust_boundary(value), _WILDCARD_EXPLANATION.format(
        source="docker-compose.release.yml", value=value
    )


_WILDCARD_EXPLANATION = (
    "{source} sets FORWARDED_ALLOW_IPS={value!r}. Under a wildcard uvicorn "
    "returns the LEFTMOST X-Forwarded-For entry, and every proxy in this repo "
    "appends, so that entry is whatever the caller sent — the login rate "
    "limiter becomes defeatable by rotating a header and every audit IP "
    "becomes forgeable."
)


# ── Uvicorn's real selection logic, not our belief about it ──────────────

SPOOFED = "1.2.3.4"
REAL_CLIENT = "203.0.113.9"
INGRESS_POD = "10.42.1.7"
#: What the backend receives: the caller's own header first, then each proxy
#: appending the peer it saw ($proxy_add_x_forwarded_for).
CHAIN = SPOOFED + ", " + REAL_CLIENT + ", " + INGRESS_POD


def test_a_wildcard_returns_the_callers_own_value():
    """The defect, pinned. If uvicorn ever stops doing this, revisit the ban."""
    trusted = _TrustedHosts("*")
    assert trusted.get_trusted_client_address(CHAIN)[0] == SPOOFED


def test_the_pod_cidr_returns_the_real_client():
    """The shipped homelab value, against the same chain."""
    trusted = _TrustedHosts(_homelab_value())
    assert trusted.get_trusted_client_address(CHAIN)[0] == REAL_CLIENT, (
        "the trust boundary no longer resolves the true client — the login "
        "rate limiter and every audit IP depend on this"
    )


def test_trusting_the_clients_too_reopens_the_spoof():
    """Why the homelab overlay narrows instead of inheriting the base value.

    That cluster's users are on 192.168.0.0/24. Trusting the range they sit in
    makes the caller itself a trusted hop, so the reverse walk continues past
    its real address and lands back on the value the caller supplied.
    """
    lan_chain = SPOOFED + ", 192.168.0.50, " + INGRESS_POD
    broad = _TrustedHosts("10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    assert broad.get_trusted_client_address(lan_chain)[0] == SPOOFED

    narrow = _TrustedHosts(_homelab_value())
    assert narrow.get_trusted_client_address(lan_chain)[0] == "192.168.0.50"


def test_the_header_is_ignored_when_the_peer_is_not_trusted():
    """Why a narrow value is fail-closed rather than merely narrow.

    ProxyHeadersMiddleware consults X-Forwarded-For only when the immediate TCP
    peer is itself trusted, so a caller that reaches the API directly cannot
    inject an address at all.
    """
    trusted = _TrustedHosts(_homelab_value())
    assert REAL_CLIENT not in trusted
    assert INGRESS_POD in trusted


def test_the_homelab_value_covers_every_node_pod_cidr():
    """A narrowed value is only correct if it still covers every hop.

    Measured on the live cluster: 10.42.0.0/24, 10.42.1.0/24, 10.42.2.0/24.
    """
    network = ipaddress.ip_network(_homelab_value())
    for node_cidr in ("10.42.0.0/24", "10.42.1.0/24", "10.42.2.0/24"):
        assert ipaddress.ip_network(node_cidr).subnet_of(network), (
            "pod CIDR " + node_cidr + " is outside FORWARDED_ALLOW_IPS, so "
            "requests proxied by a pod on that node lose their real client IP"
        )


def test_the_release_api_port_is_not_published_to_every_interface():
    """nginx is the entry point; a direct peer could otherwise set its own XFF.

    The backend trusts the bridge range so the frontend's forwarded header is
    honoured. Publishing :8000 on 0.0.0.0 let a caller reach the API without
    passing through nginx, from inside that same trusted range.
    """
    services = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    published = [str(p) for p in services["backend"]["ports"]]
    assert published, "the backend no longer publishes a port"
    for mapping in published:
        assert not mapping.startswith("8000:"), (
            "docker-compose.release.yml publishes " + mapping + " on every "
            "interface, bypassing the nginx proxy the trust boundary assumes"
        )
        assert "127.0.0.1" in mapping


# ── Every production topology, not just the one that was reported ────────
#
# The finding named the Kubernetes and release-compose deployments, and the
# first fix covered exactly those. Two other compose files also run the
# PRODUCTION frontend — the nginx image that proxies /api — and one of them
# runs uvicorn directly rather than under gunicorn, so no amount of editing
# gunicorn_conf.py would ever have reached it.

#: Compose files that stand up a production topology on their own.
SELF_CONTAINED_PRODUCTION_COMPOSE = ("docker-compose.release.yml",)

#: Overlays layered on one of the above with `-f base -f override`. They must
#: NOT redefine the backend environment, or they silently drop the boundary the
#: base declares (compose merges list-form `environment:` by key, so a partial
#: list is fine — a replaced one is not).
PRODUCTION_COMPOSE_OVERRIDES = {
    "docker-compose.airgap.yml": "docker-compose.release.yml",
    "docker-compose.gcp-vm.yml": "docker-compose.yml",
}


def _compose_backend(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"][
        "backend"
    ]


def _declared_env_keys(service: dict) -> set:
    env = service.get("environment") or []
    if isinstance(env, dict):
        return set(env)
    return {str(item).partition("=")[0] for item in env}


@pytest.mark.parametrize("name", SELF_CONTAINED_PRODUCTION_COMPOSE)
def test_every_self_contained_production_compose_declares_the_boundary(name):
    assert "FORWARDED_ALLOW_IPS" in _declared_env_keys(_compose_backend(name))


def test_the_gcp_vm_override_declares_it_because_it_bypasses_gunicorn():
    """It sets its own `command:`, so gunicorn_conf.py is never loaded.

    Its base is the DEV compose, which has no reason to declare a proxy trust
    boundary, and it swaps in the production frontend — so this override is the
    only place the setting can live.
    """
    service = _compose_backend("docker-compose.gcp-vm.yml")
    command = str(service.get("command", ""))
    assert "uvicorn" in command and "gunicorn" not in command, (
        "docker-compose.gcp-vm.yml now runs gunicorn, so gunicorn_conf.py "
        "supplies the default and this test should be revisited"
    )
    value = [
        str(item)
        for item in service["environment"]
        if str(item).startswith("FORWARDED_ALLOW_IPS=")
    ]
    assert value, (
        "the GCP VM backend runs uvicorn directly behind the production nginx "
        "and declares no FORWARDED_ALLOW_IPS, so it reports the proxy as every "
        "caller — the exact defect H2 reported, in a topology the fix missed"
    )
    default = value[0].partition(":-")[2].rstrip("}")
    assert _is_a_real_trust_boundary(default)


def test_an_override_does_not_drop_the_boundary_its_base_declares():
    """airgap layers on release; redefining the backend env would drop it."""
    service = _compose_backend("docker-compose.airgap.yml")
    keys = _declared_env_keys(service)
    assert not keys or "FORWARDED_ALLOW_IPS" in keys, (
        "docker-compose.airgap.yml now sets backend environment keys without "
        "FORWARDED_ALLOW_IPS; confirm the merge still leaves the release "
        "value in place, then declare it here too"
    )
