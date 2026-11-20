"""The release stack's backend-image services run with no capability and no way to gain one.

``docker-compose.release.yml`` set none of the container restrictions the
public demo override (``docker-compose.demo.yml``) applies. Every service that
runs the backend image starts as a non-root user, so it needs no Linux
capability and must not be able to gain a privilege through a setuid binary:
``cap_drop: [ALL]`` and ``no-new-privileges``.

Only the services the public demo already ran with these two restrictions are
covered, so they are known to start with them. The data stores and nginx start
as root and drop to their own user; they are not covered by this rule.

The demo override must not repeat ``security_opt`` for those services: Compose
appends an override's list to the base file's and then refuses the stack for
the repeated item ("security_opt items at 0 and 1 are equal"). The demo now
inherits both restrictions from the release file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from compose_topology_contract import load_compose_file

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
LOCKED_DOWN = (
    "db-budget-check", "minio-setup", "db-migrate", "backend",
    "worker", "worker-children", "beat", "seed-init",
)


def _services(relative: str) -> dict:
    return load_compose_file(ROOT / relative)["services"]


@pytest.mark.parametrize("service", LOCKED_DOWN)
def test_a_backend_image_service_drops_every_capability(service):
    definition = _services("docker-compose.release.yml")[service]
    assert definition.get("cap_drop") == ["ALL"], f"{service}: runs as a non-root user and needs no capability"
    assert "no-new-privileges:true" in (definition.get("security_opt") or []), service


def test_the_list_is_every_service_that_runs_the_backend_image():
    """Guards the guard: a new backend-image service must be added to the rule."""
    services = _services("docker-compose.release.yml")
    backend_image = {name for name, s in services.items() if "/backend:" in str(s.get("image", ""))}
    assert backend_image == set(LOCKED_DOWN)


@pytest.mark.parametrize("override", ["docker-compose.demo.yml", "docker-compose.airgap.yml"])
def test_no_override_of_the_release_file_repeats_a_security_option(override):
    """Compose appends security_opt across files and refuses a repeated item."""
    release = _services("docker-compose.release.yml")
    repeated = {
        name: sorted(set(service.get("security_opt") or []) & set(release.get(name, {}).get("security_opt") or []))
        for name, service in _services(override).items()
        if service
    }
    repeated = {name: items for name, items in repeated.items() if items}
    assert not repeated, (
        f"{override} repeats a security_opt docker-compose.release.yml already sets; "
        f"`docker compose -f docker-compose.release.yml -f {override} config` fails: {repeated}"
    )


def test_the_demo_still_locks_down_the_services_only_it_defines_or_restricts():
    """Positive case: the demo keeps its own restrictions where the release file has none."""
    demo = _services("docker-compose.demo.yml")
    assert demo["cloudflared"].get("cap_drop") == ["ALL"]
    for name in ("cloudflared", "postgres", "mongo", "redis", "minio", "frontend"):
        assert demo[name].get("security_opt") == ["no-new-privileges:true"], name


def test_the_services_that_start_as_root_keep_their_capabilities():
    """Positive case: nothing the demo does not prove is restricted here.

    postgres, mongo, valkey, versitygw and nginx start as root and change user,
    which needs SETUID/SETGID/CHOWN; dropping every capability stops them.
    """
    services = _services("docker-compose.release.yml")
    for name in ("postgres", "mongo", "redis", "minio", "frontend"):
        assert "cap_drop" not in services[name], name


def test_the_frontend_image_and_port_are_unchanged():
    frontend = _services("docker-compose.release.yml")["frontend"]
    assert frontend["ports"] == ["${FRONTEND_PUBLISH_ADDR:+${FRONTEND_PUBLISH_ADDR}:}3000:80"]
    assert "user" not in frontend
