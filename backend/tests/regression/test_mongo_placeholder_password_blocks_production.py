"""A placeholder Mongo password must stop production from starting.

Re-audit finding N12, raised by the batch 1 review. The CRITICAL
default-secret check was widened in batch 1 to catch every placeholder the
example env files ship -- for ``JWT_SECRET_KEY``, ``APP_SECRET_KEY`` and
``WEBHOOK_SECRET``. ``.env.example`` also ships::

    MONGO_URI=mongodb://testlookup:change-me-to-a-strong-password@localhost:27017/...

and nothing looked at it. Copying the example file and deploying booted
production on a published database password.

Only the password component is judged. A password-less URI -- the default, or a
deployment authenticating another way -- is never a placeholder, and the host
and database names are not secrets.
"""
from __future__ import annotations

import pathlib

import pytest

from app.core.config import settings

pytestmark = pytest.mark.regression

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture
def production(monkeypatch):
    """Production, with every OTHER secret real, so only Mongo is judged."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "c0ffee" * 10)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "beef" * 16)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "cafe" * 16)
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)
    return monkeypatch


def _mongo_failures() -> list[str]:
    return [item for item in settings.critical_security_failures() if "MONGO_URI" in item]


def _example_mongo_uri() -> str:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("MONGO_URI="):
            return line.partition("=")[2].strip()
    raise AssertionError(".env.example no longer declares MONGO_URI — update this test")


def test_the_example_files_mongo_uri_blocks_production(production):
    """The exact value a copied .env.example deploys with."""
    production.setattr(settings, "MONGO_URI", _example_mongo_uri())
    assert _mongo_failures(), (
        "MONGO_URI from .env.example — a published database password — boots "
        "production without complaint"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://svc:replace-with-strong-mongo-password@mongo:27017/logs",
        "mongodb+srv://svc:change-me@cluster0.example.net/logs",
    ],
)
def test_every_placeholder_convention_is_caught(production, uri):
    production.setattr(settings, "MONGO_URI", uri)
    assert _mongo_failures()


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://localhost:27017",                                 # the default
        "mongodb://mongo:27017/logs",                                # no credential at all
        "mongodb://svc:9f8e7d6c5b4a49388271aa@mongo:27017/logs",     # a real password
        "mongodb+srv://svc:c0ffeec0ffeec0ffee@cluster0.example.net/logs",
        # The Cloud Run example verbatim: a placeholder HOST but no password.
        # Judging the whole URI would flag this; only the credential is a
        # secret, and this one would fail to connect rather than leak anything.
        "mongodb+srv://your-mongodb-host/your-database",
    ],
)
def test_a_real_or_absent_password_is_not_flagged(production, uri):
    production.setattr(settings, "MONGO_URI", uri)
    assert _mongo_failures() == [], f"{uri} was wrongly flagged as a placeholder"


def test_outside_production_nothing_is_flagged(monkeypatch):
    """The check gates startup in production and staging, never development."""
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "MONGO_URI", _example_mongo_uri())
    assert _mongo_failures() == []
