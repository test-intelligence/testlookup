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

import importlib
import ipaddress
from pathlib import Path

import pytest
import yaml
from uvicorn.middleware.proxy_headers import _TrustedHosts

ROOT = Path(__file__).resolve().parents[3]
CONF = ROOT / "backend" / "gunicorn_conf.py"
CONFIGMAP = ROOT / "k8s" / "base" / "configmap.yaml"
RELEASE_COMPOSE = ROOT / "docker-compose.release.yml"


#: gunicorn's ForwardedAllowIPS validator, restated. It runs
#: ``ipaddress.ip_address(addr)`` on every entry, which raises on ANY network,
#: and it runs while gunicorn builds its Config -- from $FORWARDED_ALLOW_IPS,
#: before the config file is read. gunicorn cannot be imported on Windows
#: (`import grp`), so the rule is restated here rather than called.
def _gunicorn_would_accept(value: str) -> bool:
    for entry in [e.strip() for e in str(value).split(",") if e.strip()]:
        if entry == "*":
            continue
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            return False
    return True


def test_the_restated_gunicorn_rule_matches_what_broke():
    """Guards the restatement above against the error we actually saw.

        Error: '10.42.0.0/16' does not appear to be an IPv4 or IPv6 address
    """
    assert not _gunicorn_would_accept("10.42.0.0/16")
    assert not _gunicorn_would_accept("127.0.0.1,10.42.0.0/16")
    assert _gunicorn_would_accept("127.0.0.1,::1")
    assert _gunicorn_would_accept("*")


def test_gunicorn_is_not_handed_the_trust_boundary():
    """The boundary is a CIDR, and gunicorn cannot hold one.

    gunicorn_conf.py must not assign forwarded_allow_ips at all: assigning it
    would either re-introduce the crash or silently narrow the boundary to
    single addresses, and the proxy is a pod whose address changes.
    """
    source = CONF.read_text(encoding="utf-8")
    assignments = [
        line
        for line in source.splitlines()
        if line.strip().startswith("forwarded_allow_ips")
    ]
    assert not assignments, (
        "gunicorn_conf.py assigns forwarded_allow_ips: " + str(assignments) + ". "
        "gunicorn validates that name with ipaddress.ip_address(), which "
        "rejects every CIDR — see app/bootstrap.py for where the boundary lives"
    )


def test_no_deployment_sets_the_name_gunicorn_reads():
    """This is the test that would have caught the outage.

    A ConfigMap carrying FORWARDED_ALLOW_IPS="10.42.0.0/16" killed every worker
    at startup. The old tests could not see it: they imported gunicorn_conf.py
    as a plain module and read an attribute, so gunicorn's validator — the only
    thing that actually rejects the value — never ran.
    """
    offenders = []
    for path in sorted(ROOT.glob("docker-compose*.yml")) + sorted(
        (ROOT / "k8s").rglob("*.yaml")
    ):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if "FORWARDED_ALLOW_IPS" not in line or line.strip().startswith("#"):
                continue
            offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}")

    assert not offenders, (
        "these set FORWARDED_ALLOW_IPS, which gunicorn reads and validates "
        "before any config file runs — a CIDR there kills the process at "
        "startup. Use TRUSTED_PROXY_IPS, which the app applies itself:"
        + _NL_INDENT
        + _NL_INDENT.join(offenders)
    )


def test_uvicorn_worker_is_still_the_worker_class():
    """The app-level middleware needs an ASGI server; pin the worker class."""
    assert "UvicornWorker" in CONF.read_text(encoding="utf-8")


def test_the_app_installs_the_boundary_when_one_is_declared(monkeypatch):
    """The setting must actually reach ProxyHeadersMiddleware."""
    from fastapi import FastAPI

    from app.bootstrap import install_proxy_boundary
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.42.0.0/16")
    app = FastAPI()
    install_proxy_boundary(app)

    installed = [m for m in app.user_middleware if "ProxyHeaders" in str(m.cls)]
    assert installed, (
        "no ProxyHeadersMiddleware is installed, so TRUSTED_PROXY_IPS has no "
        "effect and request.client.host stays the ingress address"
    )


def test_the_boundary_is_outermost_on_the_REAL_app(monkeypatch):
    """Assert on app.main.app, not on a synthetic FastAPI.

    The first version of this test built a bare FastAPI, called
    configure_middlewares, and checked index 0. It passed while the real app
    was wrong: main.py registers ``rate_limit_auth`` with
    @app.middleware("http") AFTER configure_middlewares returns, and Starlette
    inserts at index 0 — so the login rate limiter, the loudest consequence in
    H2, sat OUTSIDE the correction and kept bucketing every external caller
    onto the ingress address. Nothing in a synthetic app can show that.
    """
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.42.0.0/16")
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.42.0.0/16")

    import app.main as main_module

    importlib.reload(main_module)

    stack = [str(m.cls) for m in main_module.app.user_middleware]
    assert stack, "the app registers no middleware at all"
    assert "ProxyHeaders" in stack[0], (
        "ProxyHeadersMiddleware is not outermost, so everything registered "
        "after it reads the proxy's address instead of the caller's. Order "
        "was:" + _NL_INDENT + _NL_INDENT.join(stack)
    )


def test_the_rate_limiter_runs_inside_the_boundary(monkeypatch):
    """Name the specific consumer, because it is the one H2 was about.

    slowapi keys its login/MFA buckets on ``get_remote_address(request)``,
    i.e. ``request.client.host``. If its middleware runs outside the
    correction, one script exhausting the ceiling still 429s everybody else.
    """
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.42.0.0/16")
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.42.0.0/16")

    import app.main as main_module

    importlib.reload(main_module)

    stack = [str(m.cls) for m in main_module.app.user_middleware]
    proxy_at = next(i for i, name in enumerate(stack) if "ProxyHeaders" in name)
    limiter_at = next(
        (i for i, name in enumerate(stack) if "BaseHTTPMiddleware" in name), None
    )
    assert limiter_at is not None, (
        "the @app.middleware('http') rate limiter is gone; if it moved, point "
        "this test at wherever the auth ceiling now lives"
    )
    assert proxy_at < limiter_at, (
        "the auth rate limiter runs OUTSIDE the proxy boundary, so it buckets "
        "on the ingress address: " + str(stack)
    )


def test_nothing_is_installed_when_no_boundary_is_declared(monkeypatch):
    """Undeclared topology trusts nothing — the fail-closed default."""
    from fastapi import FastAPI

    from app.bootstrap import install_proxy_boundary
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "")
    app = FastAPI()
    install_proxy_boundary(app)

    assert not [m for m in app.user_middleware if "ProxyHeaders" in str(m.cls)]


def test_the_default_is_to_trust_nothing():
    from app.core.config import Settings

    assert Settings.model_fields["TRUSTED_PROXY_IPS"].default == ""


def test_kubernetes_configmap_declares_the_trust_boundary():
    """k8s runs behind ingress-nginx, so it must opt in explicitly."""
    config = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8"))["data"]
    assert "TRUSTED_PROXY_IPS" in config, (
        "k8s/base/configmap.yaml does not set FORWARDED_ALLOW_IPS, so the "
        "in-cluster API keeps seeing ingress-nginx as every caller"
    )
    assert _is_a_real_trust_boundary(config["TRUSTED_PROXY_IPS"])


def test_release_compose_declares_the_trust_boundary():
    """The release compose frontend proxies /api, so it needs it too."""
    services = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    env = services["backend"]["environment"]
    declared = {str(item).partition("=")[0] for item in env}
    assert "TRUSTED_PROXY_IPS" in declared, (
        "docker-compose.release.yml backend does not set FORWARDED_ALLOW_IPS"
    )


# ── What the value must actually BE ───────────────────────────────────
#
# The first version of this fix shipped "*" and every test above still passed,
# because they all assert on file CONTENT. A wildcard is worse than the bug it
# fixes, so the value itself needs asserting — and so does uvicorn's behaviour
# under it.


#: Newline + indent used in the multi-line assertion messages below.
_NL_INDENT = chr(10) + "  "

#: Addresses no deployment's PROXY will ever be. If the configured boundary
#: trusts one of these, it trusts arbitrary internet peers, and an arbitrary
#: internet peer can then choose its own recorded address.
#:
#: Probing is what makes this a real check. A syntactic one accepted
#: ``0.0.0.0/0`` — and ``0.0.0.0/1,128.0.0.0/1``, which covers the same space in
#: two legal-looking halves. Both select exactly what ``"*"`` selects. Probing
#: still admits a NARROW public range, which the GKE overlay legitimately needs
#: (Google Front Ends reach the pod directly from 35.191.0.0/16).
_ARBITRARY_INTERNET_PEERS = ("1.2.3.4", "8.8.8.8", "203.0.113.9", "2001:db8::1")


def _is_a_real_trust_boundary(value: str) -> bool:
    """True when the value names actual proxies rather than the whole internet."""
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

    # Ask uvicorn's own membership test, not our reading of the string.
    trusted = _TrustedHosts(",".join(entries))
    return not any(peer in trusted for peer in _ARBITRARY_INTERNET_PEERS)


def test_the_helper_rejects_a_wildcard():
    """Guards the guard — a helper that never says no would pass forever."""
    assert not _is_a_real_trust_boundary("*")
    assert not _is_a_real_trust_boundary("10.0.0.0/8,*")
    assert not _is_a_real_trust_boundary("")
    assert _is_a_real_trust_boundary("10.42.0.0/16")
    assert _is_a_real_trust_boundary("127.0.0.1,::1")


@pytest.mark.parametrize(
    "spelling",
    [
        "0.0.0.0/0",
        "0.0.0.0/0,::/0",
        "0.0.0.0/1,128.0.0.0/1",
        "10.42.0.0/16,0.0.0.0/0",
        "::/0",
    ],
)
def test_the_helper_rejects_every_other_spelling_of_a_wildcard(spelling):
    """A syntactic check passed all of these; each trusts the open internet.

    This helper exists because "*" slipped through eight tests. It would be a
    poor joke for it to have a second spelling of the same hole.
    """
    assert not _is_a_real_trust_boundary(spelling)


@pytest.mark.parametrize(
    "spelling",
    ["0.0.0.0/0", "0.0.0.0/0,::/0", "0.0.0.0/1,128.0.0.0/1", "10.42.0.0/16,0.0.0.0/0"],
)
def test_an_all_of_ipv4_spelling_selects_exactly_what_a_wildcard_selects(spelling):
    """Prove the consequence rather than asserting it.

    Only the spellings that cover all of IPv4 are checked here: "::/0" trusts
    every IPv6 peer but no IPv4 one, so against an IPv4 chain it still stops at
    the first untrusted hop. It is rejected above for the IPv6 half.
    """
    assert _TrustedHosts(spelling).get_trusted_client_address(CHAIN)[0] == SPOOFED
    assert _TrustedHosts("*").get_trusted_client_address(CHAIN)[0] == SPOOFED


def test_the_helper_still_allows_a_narrow_public_range():
    """GKE's Google Front Ends are public, and are genuinely the proxy.

    A rule of "must be RFC1918" would have been wrong; the rule is "must not
    trust arbitrary internet peers".
    """
    assert _is_a_real_trust_boundary("35.191.0.0/16,130.211.0.0/22")


def _homelab_value() -> str:
    text = (ROOT / "k8s" / "overlays" / "homelab" / "kustomization.yaml").read_text(
        encoding="utf-8"
    )
    lines = [ln for ln in text.splitlines() if "TRUSTED_PROXY_IPS" in ln]
    assert lines, "the homelab overlay no longer narrows the trust boundary"
    return lines[0].split(":", 1)[1].strip().strip('"')


def _release_compose_default() -> str:
    services = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    entries = [
        str(item)
        for item in services["backend"]["environment"]
        if str(item).startswith("TRUSTED_PROXY_IPS=")
    ]
    assert entries, "the release compose no longer sets TRUSTED_PROXY_IPS"
    # ${TRUSTED_PROXY_IPS:-<default>} — assert the DEFAULT, which is what an
    # operator who sets nothing actually runs.
    value = entries[0].partition("=")[2]
    assert ":-" in value, "compose no longer supplies a default"
    return value.partition(":-")[2].rstrip("}")


def test_base_configmap_does_not_trust_every_peer():
    value = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8"))["data"][
        "TRUSTED_PROXY_IPS"
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
    "{source} sets TRUSTED_PROXY_IPS={value!r}. Under a wildcard uvicorn "
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
            "pod CIDR " + node_cidr + " is outside TRUSTED_PROXY_IPS, so "
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

#: Compose files with no production frontend, so no proxy to trust.
NON_PRODUCTION_COMPOSE = (
    "docker-compose.yml",            # dev: vite serves and proxies
    "docker-compose.dev-lite.yml",   # dev: same, minus the heavy services
    "docker-compose.monitoring.yml", # prometheus/grafana only, no app services
)


def _compose_files_on_disk():
    return sorted(p.name for p in ROOT.glob("docker-compose*.yml"))


def test_every_compose_file_is_classified():
    """Enumerate from disk; a hardcoded list is how gcp-vm was missed.

    The first version of this file listed three compose files by hand and
    asserted over those. There are six, and the one running uvicorn behind the
    production nginx was not among them — so the fix shipped with that topology
    still carrying the defect. Classifying every file on disk means a new
    compose file is a decision someone has to make, not a silent omission.
    """
    classified = (
        set(SELF_CONTAINED_PRODUCTION_COMPOSE)
        | set(PRODUCTION_COMPOSE_OVERRIDES)
        | set(NON_PRODUCTION_COMPOSE)
    )
    on_disk = set(_compose_files_on_disk())
    assert on_disk, "no compose files found — this scan would pass vacuously"

    unclassified = sorted(on_disk - classified)
    assert not unclassified, (
        "new compose files are not classified here. If one runs the production "
        "frontend (the nginx image that proxies /api) its backend must declare "
        "FORWARDED_ALLOW_IPS, or the API reports the proxy as every caller:"
        + _NL_INDENT
        + _NL_INDENT.join(unclassified)
    )

    stale = sorted(classified - on_disk)
    assert not stale, (
        "these classified compose files no longer exist — delete the entries:"
        + _NL_INDENT
        + _NL_INDENT.join(stale)
    )


def test_a_compose_file_claiming_to_be_non_production_has_no_production_frontend():
    """Make the "no proxy here" claim checkable rather than a comment."""
    mislabelled = []
    for name in NON_PRODUCTION_COMPOSE:
        path = ROOT / name
        if not path.exists():
            continue
        services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
            "services"
        ) or {}
        frontend = services.get("frontend") or {}
        target = str((frontend.get("build") or {}).get("target", ""))
        if target == "production":
            mislabelled.append(name)
    assert not mislabelled, (
        "these are listed as non-production but build the production frontend, "
        "which proxies /api — their backend needs a trust boundary:"
        + _NL_INDENT
        + _NL_INDENT.join(mislabelled)
    )


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
    assert "TRUSTED_PROXY_IPS" in _declared_env_keys(_compose_backend(name))


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
        if str(item).startswith("TRUSTED_PROXY_IPS=")
    ]
    assert value, (
        "the GCP VM backend runs uvicorn directly behind the production nginx "
        "and declares no TRUSTED_PROXY_IPS, so it reports the proxy as every "
        "caller — the exact defect H2 reported, in a topology the fix missed"
    )
    default = value[0].partition(":-")[2].rstrip("}")
    assert _is_a_real_trust_boundary(default)


def test_an_override_does_not_drop_the_boundary_its_base_declares():
    """airgap layers on release; redefining the backend env would drop it."""
    service = _compose_backend("docker-compose.airgap.yml")
    keys = _declared_env_keys(service)
    assert not keys or "TRUSTED_PROXY_IPS" in keys, (
        "docker-compose.airgap.yml now sets backend environment keys without "
        "FORWARDED_ALLOW_IPS; confirm the merge still leaves the release "
        "value in place, then declare it here too"
    )


#: Overlays that may inherit the base value: their callers arrive from public
#: addresses, so trusting the private ranges pod networks come from is right.
#: Everything else must name its own, because on a cluster whose users share
#: RFC1918 with its pods the base value trusts the callers too.
OVERLAYS_MAY_INHERIT_BASE = frozenset({"aws-eks", "azure-aks", "dev", "prod", "staging"})


def _overlay_boundary(path):
    import re

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#"):
            continue
        match = re.match(r'\s*TRUSTED_PROXY_IPS:\s*"?([^"#]+)"?', line)
        if match:
            return match.group(1).strip()
    return None


def _overlays_on_disk():
    return sorted((ROOT / "k8s" / "overlays").glob("*/kustomization.yaml"))


def test_every_overlay_that_sets_the_boundary_sets_a_real_one():
    """Whichever overlays narrow it, none may re-open it."""
    overlays = _overlays_on_disk()
    assert overlays, "no overlays found — this scan would pass vacuously"

    seen = 0
    for path in overlays:
        value = _overlay_boundary(path)
        if value is None:
            continue
        seen += 1
        assert _is_a_real_trust_boundary(value), _WILDCARD_EXPLANATION.format(
            source=path.relative_to(ROOT).as_posix(), value=value
        )
    assert seen >= 2, (
        "expected several overlays to narrow the trust boundary, found "
        f"{seen} — this scan is probably no longer finding them"
    )


def test_every_on_premise_overlay_narrows_the_boundary():
    """The base file states the rule; this checks the overlays obey it.

    Enumerated from disk, because asserting a hardcoded path is how three
    on-premise overlays kept inheriting a value shaped for the cloud. An
    overlay is exempt only by being listed as public-facing above, which is a
    decision someone has to write down.
    """
    inheriting = []
    for path in _overlays_on_disk():
        name = path.parent.name
        if name in OVERLAYS_MAY_INHERIT_BASE:
            continue
        if _overlay_boundary(path) is None:
            inheriting.append(name)

    assert not inheriting, (
        "these overlays inherit the base trust boundary, which trusts the "
        "private ranges their own users sit in — an on-LAN caller is then a "
        "trusted hop and uvicorn returns the X-Forwarded-For value that caller "
        "supplied. Narrow each to its cluster's pod CIDR:"
        + _NL_INDENT
        + _NL_INDENT.join(sorted(inheriting))
    )


def test_the_inheritance_exemptions_are_real_overlays():
    """A stale exemption silently covers the next overlay of the same name."""
    names = {p.parent.name for p in _overlays_on_disk()}
    stale = sorted(OVERLAYS_MAY_INHERIT_BASE - names)
    assert not stale, (
        "these overlays no longer exist — delete them from "
        "OVERLAYS_MAY_INHERIT_BASE:" + _NL_INDENT + _NL_INDENT.join(stale)
    )


def test_the_gke_overlay_trusts_the_front_ends_that_actually_reach_the_pod():
    """Container-native load balancing changes who the peer is.

    The standalone NEG annotations in that overlay mean a Google Front End
    connects DIRECTLY to the backend pod, so the peer is a GFE address and not
    a pod address. The base value covers pod networks, so without this patch
    uvicorn never reads X-Forwarded-For at all and H2 is back.
    """
    text = (ROOT / "k8s" / "overlays" / "gcp-gke" / "kustomization.yaml").read_text(
        encoding="utf-8"
    )
    assert "cloud.google.com/neg" in text, (
        "the GKE overlay no longer uses standalone NEGs; if traffic now arrives "
        "via an in-cluster proxy, this patch should be revisited"
    )
    lines = [ln for ln in text.splitlines() if "TRUSTED_PROXY_IPS" in ln]
    assert lines, (
        "gcp-gke does not narrow TRUSTED_PROXY_IPS, so the backend pod's peer "
        "(a Google Front End) is untrusted, X-Forwarded-For is ignored, and "
        "request.client.host is the load balancer for every caller"
    )
    trusted = _TrustedHosts(lines[0].split(":", 1)[1].strip().strip('"'))
    assert "35.191.4.20" in trusted
    assert "130.211.1.5" in trusted
    assert "1.2.3.4" not in trusted


def test_the_self_hosted_overlay_narrows_like_the_base_file_demands():
    """The base file states the rule; this is the overlay it is addressed to."""
    text = (ROOT / "k8s" / "overlays" / "self-hosted" / "kustomization.yaml").read_text(
        encoding="utf-8"
    )
    lines = [
        ln
        for ln in text.splitlines()
        if "TRUSTED_PROXY_IPS:" in ln and not ln.strip().startswith("#")
    ]
    assert lines, (
        "k8s/base/configmap.yaml tells an on-premise cluster it MUST narrow "
        "this in its overlay, and self-hosted is that overlay (kubeadm, K3s, "
        "RKE, Rancher, on-prem VMware). Inheriting the base value makes an "
        "on-LAN caller a trusted hop, which re-opens the spoof."
    )
    trusted = _TrustedHosts(lines[0].split(":", 1)[1].strip().strip('"'))
    # A user on the corporate LAN must NOT be trusted...
    assert "192.168.0.50" not in trusted
    assert "10.8.0.55" not in trusted
    # ...while the pod networks those distributions default to must be.
    assert "10.42.1.7" in trusted
    assert "10.244.1.7" in trusted


def test_the_release_network_subnet_matches_its_own_trust_boundary():
    """Docker's default pool can put the bridge outside the declared range.

    Without an explicit subnet, a host that has already used 172.17-172.31
    allocates this network from 192.168.x; the nginx peer is then untrusted and
    H2 returns for that host with no signal.
    """
    compose = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))
    config = compose["networks"]["testlookup_net"].get("ipam", {}).get("config")
    assert config, (
        "testlookup_net has no explicit subnet, so the bridge address is "
        "whatever Docker's address pool hands out and may fall outside "
        "TRUSTED_PROXY_IPS"
    )
    subnet = ipaddress.ip_network(config[0]["subnet"])
    boundary = _TrustedHosts(_release_compose_default())
    assert str(subnet.network_address + 1) in boundary, (
        f"the compose network {subnet} is not inside the declared trust "
        "boundary, so the backend will not honour the frontend's forwarded "
        "header"
    )


def test_the_middleware_corrects_the_client_for_a_trusted_peer():
    """Drive the real middleware, not a belief about it."""
    import asyncio

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    seen = {}

    async def _app(scope, receive, send):
        seen["client"] = scope.get("client")

    wrapped = ProxyHeadersMiddleware(_app, trusted_hosts="10.42.0.0/16")
    scope = {
        "type": "http",
        "client": (INGRESS_POD, 5000),
        "headers": [(b"x-forwarded-for", CHAIN.rsplit(",", 1)[0].encode())],
    }
    asyncio.run(wrapped(scope, None, None))
    assert seen["client"][0] == REAL_CLIENT, (
        "a trusted proxy's forwarded header did not resolve the real caller"
    )


def test_the_middleware_ignores_a_header_from_an_untrusted_peer():
    import asyncio

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    seen = {}

    async def _app(scope, receive, send):
        seen["client"] = scope.get("client")

    wrapped = ProxyHeadersMiddleware(_app, trusted_hosts="10.42.0.0/16")
    scope = {
        "type": "http",
        "client": ("203.0.113.50", 5000),
        "headers": [(b"x-forwarded-for", b"1.2.3.4")],
    }
    asyncio.run(wrapped(scope, None, None))
    assert seen["client"][0] == "203.0.113.50", (
        "a direct caller injected its own address"
    )
