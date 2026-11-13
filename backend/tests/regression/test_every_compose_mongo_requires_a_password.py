"""Every Compose stack's MongoDB requires a password.

dev-lite (`make dev-lite`, and Codespaces) ran MongoDB with no root user and
pointed the backend at ``mongodb://mongo:27017`` (OSS review 2026-11-12, H-3);
its port is on loopback, but any process on the machine could read or drop the
test logs. It now takes the same ``MONGO_USER``/``MONGO_PASSWORD`` from ``.env``
as ``docker-compose.yml``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from compose_topology_contract import load_compose_file

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
STACKS = ("docker-compose.yml", "docker-compose.release.yml", "docker-compose.dev-lite.yml")


def _environment(service: dict) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env if "=" in item)


@pytest.mark.parametrize("stack", STACKS)
def test_the_mongo_service_creates_a_root_user(stack):
    mongo = load_compose_file(ROOT / stack)["services"]["mongo"]
    env = _environment(mongo)
    assert env.get("MONGO_INITDB_ROOT_USERNAME"), stack
    assert "MONGO_PASSWORD" in env.get("MONGO_INITDB_ROOT_PASSWORD", ""), stack


@pytest.mark.parametrize("stack", STACKS)
def test_every_mongo_uri_carries_the_credentials(stack):
    services = load_compose_file(ROOT / stack)["services"]
    uris = {name: _environment(s)["MONGO_URI"] for name, s in services.items() if "MONGO_URI" in _environment(s)}
    assert uris, f"{stack}: no service sets MONGO_URI, so this check proves nothing"
    anonymous = {name: uri for name, uri in uris.items() if "${MONGO_PASSWORD}@" not in uri}
    assert not anonymous, f"{stack}: these services connect to MongoDB without a password: {anonymous}"


def test_dev_env_generation_writes_the_password_the_stacks_read():
    script = (ROOT / "scripts" / "gen-dev-env.sh").read_text(encoding="utf-8")
    assert "set_kv MONGO_PASSWORD" in script
