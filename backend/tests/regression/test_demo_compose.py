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

The second half pins ``scripts/demo/demo.sh`` against the two faults the
first real deployment hit (2026-11-11, a 4 GB Hetzner VM):

* it lowered the worker concurrency without lowering the declared PostgreSQL
  connection total, so ``db-budget-check`` exited 1 ("Compose fleet requires
  19 connections but declares 23") and nothing else started;
* reading a key absent from ``.env`` failed under ``pipefail`` and ended the
  nightly reset just before it logged ``reset: done``.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "docker-compose.demo.yml"
RELEASE = ROOT / "docker-compose.release.yml"
EDGE_CONF = ROOT / "deploy" / "demo" / "nginx-demo-edge.conf"
DEMO_SCRIPT = ROOT / "scripts" / "demo" / "demo.sh"

_VALIDATOR_PATH = ROOT / "scripts" / "validate_db_connection_budget.py"
_SPEC = importlib.util.spec_from_file_location("db_budget_validator_demo", _VALIDATOR_PATH)
assert _SPEC and _SPEC.loader
budget_validator = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = budget_validator
_SPEC.loader.exec_module(budget_validator)

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


# ── scripts/demo/demo.sh ─────────────────────────────────────────────────────


def _demo_settings() -> dict[str, str]:
    """The KEY=VALUE pairs ``ensure_env`` writes to the demo's ``.env`` every run."""
    text = DEMO_SCRIPT.read_text(encoding="utf-8")
    body = text.split("ensure_env() {", 1)[1].split("\n}\n", 1)[0]
    return dict(re.findall(r"^\s*set_env (\w+) (\S+)$", body, re.MULTILINE))


def test_the_demo_settings_fit_the_postgres_connection_budget():
    settings = _demo_settings()
    assert {"CELERY_CONCURRENCY", "CELERY_CHILDREN_CONCURRENCY", "PG_COMPOSE_REQUIRED_CONNECTIONS"} <= settings.keys(), (
        "demo.sh lowers the worker concurrency, so it must declare the matching connection total"
    )
    budgets, limits = budget_validator.validate_compose(_load(RELEASE), settings)
    assert sum(b.connections for b in budgets) + limits["migration"] == int(settings["PG_COMPOSE_REQUIRED_CONNECTIONS"])


def test_the_demo_settings_pass_the_startup_gate():
    """The gate itself, fed the environment Compose gives it under the demo's .env."""
    from app.db.compose_budget_gate import calculate

    settings = _demo_settings()
    gate = _load(RELEASE)["services"]["db-budget-check"]["environment"]
    environment = {key: budget_validator._resolve(value, settings) for key, value in gate.items()}
    result = calculate(environment)  # raises "requires N connections but declares M"
    assert result["required"] == int(settings["PG_COMPOSE_REQUIRED_CONNECTIONS"])


def _find_bash() -> str | None:
    """A POSIX bash; on Windows, Git's, never the WSL launcher in System32."""
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    if found and "system32" in found.lower():
        return None
    return found


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="needs a POSIX bash")


def _function(name: str) -> str:
    """One function's source, lifted by text: running demo.sh would run Docker."""
    text = DEMO_SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in scripts/demo/demo.sh"
    return match.group(0)


def _run(tmp_path: Path, body: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run ``body`` under demo.sh's own shell options, with ENV_FILE in tmp_path.

    The script goes through a file, not ``bash -c``: Git Bash rewrites
    arguments that look like POSIX paths.
    """
    script = tmp_path / "run.sh"
    prelude = (
        "set -euo pipefail\n"
        'log() { printf "LOG %s\n" "$*"; }\n'
        'die() { printf "DIE %s\n" "$*" >&2; exit 1; }\n'
        'ENV_FILE="$PWD/.env"\n'
    )
    with script.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(prelude + body)
    return subprocess.run(
        [BASH, str(script)], cwd=tmp_path, input=stdin, capture_output=True, text=True, timeout=60, check=False
    )


def _write_env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    with env.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return env


@needs_bash
def test_a_key_missing_from_env_reads_as_empty(tmp_path):
    _write_env(tmp_path, "PRESENT=yes\n")
    body = _function("env_value") + (
        'missing="$(env_value DEMO_HEARTBEAT_URL)"\n'
        'printf "missing=[%s] present=[%s]\n" "$missing" "$(env_value PRESENT)"\n'
    )
    result = _run(tmp_path, body)
    assert result.returncode == 0, (
        "reading a key absent from .env ended the script under pipefail:\n" + result.stderr
    )
    assert "missing=[] present=[yes]" in result.stdout


FAKE_TOKEN = "eyJhIjoiZGVtby10ZXN0In0" + "Q" * 40


@needs_bash
def test_the_tunnel_token_goes_into_env_and_nowhere_else(tmp_path):
    env = _write_env(tmp_path, "A=1\nCLOUDFLARE_TUNNEL_TOKEN=\nB=2\n")
    stubs = 'docker() { echo healthy; }\nstart_tunnel() { echo TUNNEL-STARTED; }\n'
    result = _run(
        tmp_path,
        stubs + _function("cmd_token") + "cmd_token\n",
        stdin=f"cloudflared.exe service install {FAKE_TOKEN}\n",
    )
    assert result.returncode == 0, result.stderr
    lines = env.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("CLOUDFLARE_TUNNEL_TOKEN=")] == [
        f"CLOUDFLARE_TUNNEL_TOKEN={FAKE_TOKEN}"
    ]
    assert "A=1" in lines and "B=2" in lines
    assert FAKE_TOKEN not in result.stdout + result.stderr, "the token was echoed"
    assert "TUNNEL-STARTED" in result.stdout
    if os.name != "nt":
        assert env.stat().st_mode & 0o777 == 0o600


@needs_bash
def test_a_paste_without_a_token_changes_nothing(tmp_path):
    env = _write_env(tmp_path, "CLOUDFLARE_TUNNEL_TOKEN=\n")
    stubs = 'docker() { echo healthy; }\nstart_tunnel() { echo TUNNEL-STARTED; }\n'
    result = _run(tmp_path, stubs + _function("cmd_token") + "cmd_token\n", stdin="not a token\n")
    assert result.returncode != 0
    assert env.read_text(encoding="utf-8") == "CLOUDFLARE_TUNNEL_TOKEN=\n"
    assert "TUNNEL-STARTED" not in result.stdout


@needs_bash
def test_pull_tags_the_published_images_with_the_names_the_stack_runs(tmp_path):
    _write_env(tmp_path, "DEMO_IMAGES=pull\n")
    calls = tmp_path / "docker-calls"
    stub = f'docker() {{ echo "$*" >> "{calls.as_posix()}"; }}\n'
    result = _run(tmp_path, stub + _function("env_value") + _function("cmd_pull") + "cmd_pull\n")
    assert result.returncode == 0, result.stderr
    made = calls.read_text(encoding="utf-8").splitlines()
    text = DEMO_SCRIPT.read_text(encoding="utf-8")
    for part in ("backend", "frontend"):
        local = f"testlookup-demo/{part}:local"
        assert f'{part.upper()}_IMAGE="{local}"' in text, "pull and build must produce the same image names"
        assert f"pull ghcr.io/test-intelligence/testlookup/{part}:latest" in made
        assert f"tag ghcr.io/test-intelligence/testlookup/{part}:latest {local}" in made


@needs_bash
@pytest.mark.parametrize(("mode", "expected"), [("pull", "PULLED"), ("build", "BUILT"), ("", "BUILT")])
def test_demo_images_picks_how_setup_gets_the_images(tmp_path, mode, expected):
    _write_env(tmp_path, f"DEMO_IMAGES={mode}\n")
    stubs = "cmd_pull() { echo PULLED; }\ncmd_build() { echo BUILT; }\n"
    result = _run(tmp_path, stubs + _function("env_value") + _function("cmd_images") + "cmd_images\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@needs_bash
def test_an_unknown_demo_images_value_is_refused(tmp_path):
    _write_env(tmp_path, "DEMO_IMAGES=pul\n")
    stubs = "cmd_pull() { echo PULLED; }\ncmd_build() { echo BUILT; }\n"
    result = _run(tmp_path, stubs + _function("env_value") + _function("cmd_images") + "cmd_images\n")
    assert result.returncode != 0
    assert "PULLED" not in result.stdout and "BUILT" not in result.stdout
