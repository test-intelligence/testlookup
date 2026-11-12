"""Every placeholder credential in a shipped env template refuses production.

The startup guard (``Settings.validate_production_secrets``) refused the
example files' JWT, APP and WEBHOOK secrets and the Mongo password, but not the
Postgres password, the S3 secret key, the Redis password or the passwords
inside ``DATABASE_URL`` and the Redis/Celery URIs. All three templates ship
``APP_ENV=production`` and placeholders for those: ``.env.example``
(``<set-a-strong-password>``), ``.env.gcp-vm.example`` (``replace-with-...``)
and ``infra/cloudrun/backend.env.example`` (``CHANGE_ME``). Copying one and
forgetting a line booted production on a published database or object-store
credential.

An EMPTY value stays allowed: the release compose runs Valkey with no password
on its private network (``redis://:@redis:6379/0``), and a DSN-only deployment
leaves ``POSTGRES_PASSWORD`` unset.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = (".env.example", ".env.gcp-vm.example", "infra/cloudrun/backend.env.example")
PLAIN_FIELDS = ("POSTGRES_PASSWORD", "MINIO_SECRET_KEY", "REDIS_PASSWORD")
URI_FIELDS = ("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
GENERATED = "c0ffee" * 10  # what `openssl rand -hex 30` looks like


@pytest.fixture
def production(monkeypatch):
    """Production with every credential real, so only the field under test is judged."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)
    for name in ("JWT_SECRET_KEY", "APP_SECRET_KEY", "WEBHOOK_SECRET", "POSTGRES_PASSWORD",
                 "MINIO_SECRET_KEY", "REDIS_PASSWORD"):
        monkeypatch.setattr(settings, name, GENERATED)
    monkeypatch.setattr(settings, "MONGO_URI", f"mongodb://svc:{GENERATED}@mongo:27017/logs")
    monkeypatch.setattr(settings, "DATABASE_URL", f"postgresql+asyncpg://u:{GENERATED}@postgres:5432/db")
    for name in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        monkeypatch.setattr(settings, name, f"redis://:{GENERATED}@redis:6379/0")
    assert settings.critical_security_failures() == [], "the baseline must be clean"
    return monkeypatch


def _template(relative: str) -> dict[str, str]:
    values = {}
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.split(" #")[0].strip()
    return values


def _shipped_placeholders():
    from app.core.config import _is_placeholder_secret, _uri_password

    found = []
    for relative in TEMPLATES:
        values = _template(relative)
        for field in PLAIN_FIELDS:
            if values.get(field) and _is_placeholder_secret(values[field]):
                found.append((relative, field, values[field]))
        for field in URI_FIELDS:
            password = _uri_password(values.get(field, ""))
            if password is not None and _is_placeholder_secret(password):
                found.append((relative, field, values[field]))
    return found


def test_the_templates_still_ship_the_placeholders_this_file_checks():
    """Guards the guard: the parametrised test below must not be vacuous."""
    fields = {(relative, field) for relative, field, _ in _shipped_placeholders()}
    assert (".env.example", "POSTGRES_PASSWORD") in fields
    assert (".env.example", "DATABASE_URL") in fields
    assert (".env.gcp-vm.example", "MINIO_SECRET_KEY") in fields
    assert ("infra/cloudrun/backend.env.example", "DATABASE_URL") in fields


@pytest.mark.parametrize(("relative", "field", "value"), _shipped_placeholders(),
                         ids=lambda v: str(v)[:40])
def test_a_copied_template_placeholder_refuses_production(production, relative, field, value):
    production.setattr(settings, field, value)
    assert any(field in item for item in settings.critical_security_failures()), (
        f"{field} from {relative} is a published placeholder and still boots production"
    )


@pytest.mark.parametrize("field", PLAIN_FIELDS)
def test_an_empty_plain_credential_is_left_to_the_service(production, field):
    production.setattr(settings, field, "")
    assert settings.critical_security_failures() == []


@pytest.mark.parametrize("uri", [
    "redis://:@redis:6379/0",          # the release compose: Valkey without a password
    "redis://redis:6379/0",            # no credential at all
    "postgresql+asyncpg://u@postgres:5432/db",
])
def test_a_password_less_uri_is_never_a_placeholder(production, uri):
    field = "DATABASE_URL" if uri.startswith("postgresql") else "REDIS_URL"
    production.setattr(settings, field, uri)
    assert settings.critical_security_failures() == []


def test_development_is_never_blocked(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "<set-a-strong-password>")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://u:CHANGE_ME@postgres:5432/db")
    assert settings.critical_security_failures() == []
