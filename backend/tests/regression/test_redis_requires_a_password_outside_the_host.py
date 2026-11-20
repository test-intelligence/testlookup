"""Redis must have a password wherever something other than the host can reach it.

Redis (Valkey) is the Celery broker, the result backend, the token-revocation
store and the authorisation cache. The release and single-VM Compose stacks
started it with no password when ``REDIS_PASSWORD`` was empty, and the
production startup check accepted that, so an operator who copied an example
env file by hand ran an unauthenticated broker that every container on the
Compose network could write to.

The rule:

* the release stack (``docker-compose.release.yml``) and the single-VM stack
  (``docker-compose.yml`` + ``docker-compose.gcp-vm.yml``) refuse to start
  without ``REDIS_PASSWORD``, and every Redis URL they build carries it;
* in production or staging the backend logs a WARNING at startup when a Redis
  URL names a host other than this machine and carries no password. It is not
  a refusal: the shipped Kubernetes manifests still run the in-cluster Redis
  without a password (closed separately, with the cluster deploy path) and
  some managed Redis services have no AUTH, and an upgrade must not stop those
  installs.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from compose_topology_contract import load_compose_file

from app.core.config import settings

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
GENERATED = "c0ffee" * 10  # what `openssl rand -hex 30` looks like
REDIS_FIELDS = ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")


def _redis_url(host: str, password: str = "", database: int = 0) -> str:
    credentials = (":" + password + "@") if password else ""
    return "redis://" + credentials + host + ":6379/" + str(database)


def _environment(service: dict) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env if "=" in item)


# ── Compose: the stack refuses to start without the password ────────────────


@pytest.mark.parametrize("stack", ["docker-compose.release.yml", "docker-compose.gcp-vm.yml"])
def test_the_redis_service_refuses_an_empty_password(stack):
    redis = load_compose_file(ROOT / stack)["services"]["redis"]
    value = _environment(redis).get("REDIS_PASSWORD", "")
    assert value.startswith("${REDIS_PASSWORD:?"), (
        f"{stack}: the redis service takes REDIS_PASSWORD as {value!r}; it must be "
        "${REDIS_PASSWORD:?...} so Compose stops when the password is missing or empty"
    )


@pytest.mark.parametrize("stack", ["docker-compose.release.yml", "docker-compose.gcp-vm.yml"])
def test_no_line_falls_back_to_an_empty_redis_password(stack):
    text = (ROOT / stack).read_text(encoding="utf-8")
    assert "${REDIS_PASSWORD:-}" not in text, f"{stack}: a line still defaults REDIS_PASSWORD to empty"


@pytest.mark.parametrize("stack", ["docker-compose.release.yml", "docker-compose.gcp-vm.yml"])
def test_every_redis_url_carries_the_password(stack):
    services = load_compose_file(ROOT / stack)["services"]
    urls = {
        f"{name}.{key}": value
        for name, service in services.items()
        for key, value in _environment(service or {}).items()
        if key in REDIS_FIELDS
    }
    assert urls, f"{stack}: no service sets a Redis URL, so this check proves nothing"
    anonymous = {key: url for key, url in urls.items() if "${REDIS_PASSWORD" not in url.split("@")[0]}
    assert not anonymous, f"{stack}: these connect to Redis without the password: {anonymous}"


def test_the_release_redis_always_starts_with_requirepass():
    """Not ``${REDIS_PASSWORD:+--requirepass ...}``: that form drops the flag for an empty value."""
    command = str(load_compose_file(ROOT / "docker-compose.release.yml")["services"]["redis"]["command"])
    assert '--requirepass "$$REDIS_PASSWORD"' in command or '--requirepass "$REDIS_PASSWORD"' in command
    assert "REDIS_PASSWORD:+--requirepass" not in command


def test_the_single_vm_template_names_the_redis_password():
    lines = (ROOT / ".env.gcp-vm.example").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("REDIS_PASSWORD=") for line in lines), (
        ".env.gcp-vm.example has no REDIS_PASSWORD line, so a copied file starts Redis without one"
    )


def test_the_paths_that_start_the_release_stack_set_a_password():
    """Positive case: the installer's generator and the release test harness keep working."""
    generator = (ROOT / "scripts" / "gen-dev-env.sh").read_text(encoding="utf-8")
    assert 'set_kv REDIS_PASSWORD "$REDIS_PW"' in generator
    for key in REDIS_FIELDS:
        assert f"set_kv {key} " in generator
    harness = (ROOT / "e2e-runner" / "ephemeral.py").read_text(encoding="utf-8")
    assert "REDIS_PASSWORD" in harness


# ── Backend: production warns about an unauthenticated Redis on another host ─


@pytest.fixture
def production(monkeypatch):
    """Production with every credential real and Redis password-protected."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)
    for name in ("JWT_SECRET_KEY", "APP_SECRET_KEY", "WEBHOOK_SECRET", "POSTGRES_PASSWORD",
                 "MINIO_SECRET_KEY", "REDIS_PASSWORD"):
        monkeypatch.setattr(settings, name, GENERATED)
    monkeypatch.setattr(settings, "MONGO_URI", "mongodb://mongo:27017/logs")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://postgres:5432/db")
    for name in REDIS_FIELDS:
        monkeypatch.setattr(settings, name, _redis_url("redis", GENERATED))
    assert settings.validate_production_secrets() == [], "the baseline must be clean"
    return monkeypatch


@pytest.mark.parametrize("field", REDIS_FIELDS)
@pytest.mark.parametrize("url", [_redis_url("redis"), "redis://:@redis:6379/0", _redis_url("10.0.0.5")],
                         ids=["no-credentials", "empty-password", "private-address"])
def test_production_warns_about_a_redis_on_another_host_without_a_password(production, field, url):
    production.setattr(settings, field, url)
    warnings = settings.validate_production_secrets()
    assert len(warnings) == 1 and field in warnings[0] and warnings[0].startswith("WARNING"), warnings
    assert "REDIS_PASSWORD" in warnings[0], "the message must say what to set"
    assert settings.critical_security_failures() == [], "a warning, never a refusal to start"


def test_staging_warns_too(production):
    production.setattr(settings, "APP_ENV", "staging")
    production.setattr(settings, "REDIS_URL", _redis_url("redis"))
    assert len(settings.validate_production_secrets()) == 1
    assert settings.critical_security_failures() == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_a_redis_on_this_machine_may_run_without_a_password(production, host):
    for field in REDIS_FIELDS:
        production.setattr(settings, field, _redis_url(host))
    assert settings.validate_production_secrets() == []


def test_a_password_protected_redis_on_another_host_starts(production):
    for field in REDIS_FIELDS:
        production.setattr(settings, field, _redis_url("redis.internal", GENERATED))
    assert settings.validate_production_secrets() == []


def test_the_shipped_kubernetes_manifests_still_start(production):
    """The shipped manifests: APP_ENV=production, redis://testlookup-redis:6379/0."""
    for database, field in enumerate(REDIS_FIELDS):
        production.setattr(settings, field, _redis_url("testlookup-redis", database=min(database, 1)))
    assert settings.critical_security_failures() == []
    warnings = settings.validate_production_secrets()
    assert len(warnings) == 3 and all(w.startswith("WARNING") for w in warnings), warnings


def test_development_is_never_blocked(production):
    production.setattr(settings, "APP_ENV", "development")
    production.setattr(settings, "REDIS_URL", _redis_url("redis"))
    assert settings.validate_production_secrets() == []
