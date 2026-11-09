"""The public demo's compose override keeps the demo locked down.

``docker-compose.demo.yml`` layers on ``docker-compose.release.yml``. Each
assertion below is a property the public demo's safety rests on; losing any
one of them is silent (the demo still starts):

* DEMO_MODE on, with a non-empty demo account, on the backend;
* nothing can reach outside: offline AI, every LLM key forced empty on every
  backend-image service (a paid key is the one way bots could raise a bill),
  SMTP, registration and dev-login off;
* the only public entry is cloudflared, behind the ``public`` profile; the MCP
  server is not started;
* every published port of the release file can be bound to loopback, because
  Docker-published ports bypass host firewalls;
* nginx gets the real client address and a per-address limit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "docker-compose.demo.yml"
RELEASE = ROOT / "docker-compose.release.yml"
EDGE_CONF = ROOT / "deploy" / "demo" / "nginx-demo-edge.conf"

BACKEND_IMAGE_SERVICES = ("backend", "worker", "worker-children", "seed-init", "db-migrate")
LLM_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def demo() -> dict:
    return _load(DEMO)["services"]


def test_backend_runs_in_demo_mode_with_an_account(demo):
    env = demo["backend"]["environment"]
    assert env["DEMO_MODE"] == "true"
    assert re.fullmatch(r"\$\{DEMO_USERNAME:-[a-z_]+\}", env["DEMO_USERNAME"]), (
        "the demo account must default to a seeded, non-admin user"
    )
    assert "admin" not in env["DEMO_USERNAME"].split(":-", 1)[1]


@pytest.mark.parametrize("service", BACKEND_IMAGE_SERVICES)
def test_nothing_on_the_demo_can_reach_outside(demo, service):
    env = demo[service]["environment"]
    for key in LLM_KEYS:
        assert env.get(key) == "", f"{service}: {key} must be forced empty on the public demo"
    assert env["AI_OFFLINE_MODE"] == "true"
    assert env["SMTP_ENABLED"] == "false"
    assert env["REGISTRATION_ENABLED"] == "false"
    assert env["DEV_AUTO_LOGIN_ENABLED"] == "false"
    assert env["APP_ENV"] == "production"


def test_every_llm_key_the_settings_know_is_blanked():
    from app.core.config import Settings

    llm_keys = {name for name in Settings.model_fields if name in LLM_KEYS or re.fullmatch(
        r"(OPENAI|ANTHROPIC|GOOGLE|GEMINI|OPENROUTER|MISTRAL|COHERE|GROQ|AZURE_OPENAI)_API_KEY", name
    )}
    assert llm_keys <= set(LLM_KEYS), f"a new LLM key must be blanked in docker-compose.demo.yml: {sorted(llm_keys - set(LLM_KEYS))}"


def test_the_only_public_entry_is_the_tunnel_behind_a_profile(demo):
    tunnel = demo["cloudflared"]
    assert tunnel["profiles"] == ["public"]
    assert re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+", tunnel["image"]), "pin cloudflared"
    assert "ports" not in tunnel
    assert demo["mcp"]["profiles"] == ["mcp"]


@pytest.mark.parametrize(("service", "variable"), [
    ("postgres", "POSTGRES_PUBLISH_ADDR"),
    ("minio", "MINIO_PUBLISH_ADDR"),
    ("frontend", "FRONTEND_PUBLISH_ADDR"),
])
def test_release_ports_on_every_interface_can_be_bound_to_loopback(service, variable):
    ports = _load(RELEASE)["services"][service]["ports"]
    assert len(ports) == 1
    assert str(ports[0]).startswith(f"${{{variable}:+${{{variable}}}:}}"), (
        f"{service}: its published port must take an optional bind address ({variable})"
    )


@pytest.mark.parametrize("variable", ["POSTGRES_PUBLISH_ADDR", "MINIO_PUBLISH_ADDR", "FRONTEND_PUBLISH_ADDR", "BACKEND_PUBLISH_ADDR"])
def test_the_demo_script_binds_every_port_to_loopback(variable):
    script = (ROOT / "scripts" / "demo" / "demo.sh").read_text(encoding="utf-8")
    assert re.search(rf"^\s*set_env {variable} 127\.0\.0\.1$", script, re.MULTILINE), (
        f"scripts/demo/demo.sh must bind {variable} to loopback"
    )


def test_nginx_gets_the_real_address_and_a_per_address_limit(demo):
    mounts = demo["frontend"]["volumes"]
    assert "./deploy/demo/nginx-demo-edge.conf:/etc/nginx/conf.d/demo-edge.conf:ro" in mounts
    conf = EDGE_CONF.read_text(encoding="utf-8")
    assert "set_real_ip_from 172.28.0.0/16;" in conf
    assert "real_ip_header X-Forwarded-For;" in conf and "real_ip_recursive on;" in conf
    assert re.search(r"^limit_req zone=demo_per_ip burst=\d+ nodelay;$", conf, re.MULTILINE)
    assert "limit_req_status 429;" in conf


def test_every_long_running_service_has_a_memory_ceiling_and_log_rotation(demo):
    for name in ("postgres", "mongo", "redis", "minio", "backend", "worker", "worker-children", "beat", "frontend", "cloudflared"):
        service = demo[name]
        assert service["deploy"]["resources"]["limits"]["memory"], name
        assert service["logging"]["options"]["max-size"], name
