"""Security regression (F1): the shipped ``.env.example`` must default to a
locked-down deployment, not a development one.

Bug: a naive `docker compose up` after `cp .env.example .env` (i.e. without
running `scripts/gen-dev-env.sh`, which docker-compose does NOT require) ran
with ``APP_ENV=development`` — which disables the startup fail-fast guard
(``Settings.critical_security_failures`` only fires for production/staging,
see ``test_bl02_secret_hardening.py``) — AND ``DEV_AUTO_LOGIN_ENABLED=true``,
exposing ``POST /api/v1/auth/dev-login`` (mints an ADMIN JWT with no
credentials) alongside the default, guessable ``JWT_SECRET_KEY``.

``.env.example`` now ships ``APP_ENV=production`` / ``DEV_AUTO_LOGIN_ENABLED=
false`` so that path fails closed: the app refuses to start with default
secrets, and dev-login is a 404. ``scripts/gen-dev-env.sh`` (the actual local/
demo entry point — ``make dev`` / ``make quickstart``) restores the dev
affordances for local use; see ``test_quickstart_env_gen.py::
test_generated_env_restores_dev_convenience``.
"""
from __future__ import annotations

from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _parse_env(text: str) -> dict[str, str]:
    """Minimal KEY=value parser for the hand-authored .env.example, which
    (unlike a generated .env) carries inline ``# comment`` annotations after
    some values — strip those before comparing."""
    env: dict[str, str] = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            v = v.split("#", 1)[0].strip()
            env[k] = v
    return env


def _env() -> dict[str, str]:
    return _parse_env(_EXAMPLE.read_text(encoding="utf-8"))


def test_app_env_defaults_to_production():
    assert _env()["APP_ENV"] == "production"


def test_dev_auto_login_defaults_to_false():
    assert _env()["DEV_AUTO_LOGIN_ENABLED"] == "false"


def test_app_debug_defaults_to_false():
    assert _env()["APP_DEBUG"] == "false"


def test_redis_password_field_present():
    """F2 — the template must surface REDIS_PASSWORD so an operator hand-editing
    .env.example (instead of using gen-dev-env.sh) is prompted to set one."""
    env = _env()
    assert "REDIS_PASSWORD" in env
