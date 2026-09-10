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
from pathlib import Path

import pytest
import yaml

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
    """Unset means trust nothing — never a wildcard by accident."""
    conf = _load_conf(monkeypatch, None)
    assert conf.forwarded_allow_ips == "127.0.0.1"


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
    assert config["FORWARDED_ALLOW_IPS"].strip()


def test_release_compose_declares_the_trust_boundary():
    """The release compose frontend proxies /api, so it needs it too."""
    services = yaml.safe_load(RELEASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    env = services["backend"]["environment"]
    declared = {str(item).partition("=")[0] for item in env}
    assert "FORWARDED_ALLOW_IPS" in declared, (
        "docker-compose.release.yml backend does not set FORWARDED_ALLOW_IPS"
    )
