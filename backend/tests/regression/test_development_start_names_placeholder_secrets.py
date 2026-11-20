"""A development start does not accept the published placeholder secrets silently.

The rules (``APP_ENV=development`` is the code default, so a process started
with no environment at all runs in this mode):

* At startup, one WARNING per placeholder secret, naming the variable
  (``JWT_SECRET_KEY``, ``APP_SECRET_KEY``, ``WEBHOOK_SECRET``).
* The API refuses to start while ``JWT_SECRET_KEY`` is a placeholder, unless
  ``ALLOW_INSECURE_DEV_SECRETS=true``. With the published key anyone can sign
  an access token for any user.
* The refusal is in the startup path (``main.lifespan``), not in building
  ``Settings``: importing the app, the test suite and scripts keep working
  with no environment variables.

Why: a bare ``uvicorn app.main:app`` or a hand-written manifest that left out
``APP_ENV`` and the secrets ran with three published secrets and logged nothing.
"""
# ruff: noqa: F811 - pytest fixtures imported from another test module are requested by name
from __future__ import annotations

import pytest

from app import main
from app.core.config import Settings
from tests.regression.test_startup_heap_freeze import (  # noqa: F401 - fixtures used by name
    clean_gc_state,
    quiet_lifespan,
)

pytestmark = pytest.mark.regression

SECRET_VARS = ("JWT_SECRET_KEY", "APP_SECRET_KEY", "WEBHOOK_SECRET")
GENERATED = {name: f"{i}f" * 32 for i, name in enumerate(SECRET_VARS, start=1)}  # hex, like openssl rand


@pytest.fixture
def bare_environment(monkeypatch):
    """No APP_ENV, no secrets, no opt-in: what a bare ``uvicorn app.main:app`` sees."""
    for name in ("APP_ENV", "ALLOW_INSECURE_DEV_SECRETS", *SECRET_VARS):
        monkeypatch.delenv(name, raising=False)


def _bare_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ── the settings helpers ────────────────────────────────────────────────────


def test_a_bare_start_is_development_and_building_settings_never_refuses(bare_environment):
    settings = _bare_settings()  # must not raise: the refusal is not here
    assert settings.APP_ENV == "development"
    assert settings.ALLOW_INSECURE_DEV_SECRETS is False


def test_one_warning_per_placeholder_secret_naming_the_variable(bare_environment):
    warnings = _bare_settings().development_secret_warnings()
    assert len(warnings) == 3
    for name in SECRET_VARS:
        assert sum(name in message for message in warnings) == 1, (name, warnings)


def test_an_example_file_placeholder_is_named_too(bare_environment):
    settings = _bare_settings(
        **{**GENERATED, "WEBHOOK_SECRET": "change-me-generate-with-openssl-rand-hex-32"}
    )
    [only] = settings.development_secret_warnings()
    assert "WEBHOOK_SECRET" in only


def test_generated_secrets_warn_about_nothing_and_start(bare_environment):
    settings = _bare_settings(**GENERATED)
    assert settings.development_secret_warnings() == []
    assert settings.development_startup_refusal() is None


def test_a_placeholder_jwt_secret_refuses_the_start(bare_environment):
    refusal = _bare_settings().development_startup_refusal()
    assert refusal is not None
    assert "JWT_SECRET_KEY" in refusal
    assert "ALLOW_INSECURE_DEV_SECRETS" in refusal  # says what to do instead


def test_only_the_jwt_secret_refuses(bare_environment):
    """The other two placeholders are warned about; they do not stop a development start."""
    settings = _bare_settings(JWT_SECRET_KEY=GENERATED["JWT_SECRET_KEY"])
    assert settings.development_startup_refusal() is None
    assert len(settings.development_secret_warnings()) == 2


def test_the_opt_in_allows_the_start_and_keeps_the_warnings(bare_environment):
    settings = _bare_settings(ALLOW_INSECURE_DEV_SECRETS=True)
    assert settings.development_startup_refusal() is None
    assert len(settings.development_secret_warnings()) == 3


def test_the_opt_in_is_read_from_the_environment(bare_environment, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_DEV_SECRETS", "true")
    assert _bare_settings().development_startup_refusal() is None


@pytest.mark.parametrize("env", ["production", "staging"])
def test_production_and_staging_keep_their_own_guard(bare_environment, env):
    """The opt-in is for development only: it never lets a placeholder through elsewhere."""
    settings = _bare_settings(APP_ENV=env, ALLOW_INSECURE_DEV_SECRETS=True)
    assert settings.development_secret_warnings() == []
    assert settings.development_startup_refusal() is None
    assert any("JWT_SECRET_KEY" in m for m in settings.critical_security_failures())


# ── the startup path ────────────────────────────────────────────────────────


@pytest.fixture
def placeholder_app_settings(monkeypatch):
    """The running app's settings as a bare development start would have them."""
    defaults = Settings.model_fields
    monkeypatch.setattr(main.settings, "APP_ENV", "development")
    for name in SECRET_VARS:
        monkeypatch.setattr(main.settings, name, defaults[name].default)
    monkeypatch.setattr(main.settings, "ALLOW_INSECURE_DEV_SECRETS", False)
    return main.settings


class _Recorder:
    """Stands in for the module's structlog logger; keeps (level, event, fields)."""

    def __init__(self):
        self.records: list[tuple[str, str, dict]] = []

    def __getattr__(self, level):
        def _log(event, *args, **fields):
            self.records.append((level, event, fields))

        return _log


@pytest.fixture
def startup_log(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(main, "logger", recorder)
    return recorder


async def test_the_lifespan_refuses_a_placeholder_jwt_secret(
    quiet_lifespan, placeholder_app_settings, startup_log
):
    entered = False
    with pytest.raises(RuntimeError) as refused:
        async with main.lifespan(main.app):
            entered = True
    assert entered is False, "the application must not start serving"
    assert "JWT_SECRET_KEY" in str(refused.value)
    assert "ALLOW_INSECURE_DEV_SECRETS" in str(refused.value)
    # The warnings are logged before the refusal, one per variable.
    warned = [fields.get("message", "") for level, _event, fields in startup_log.records if level == "warning"]
    for name in SECRET_VARS:
        assert sum(name in message for message in warned) == 1, (name, warned)


async def test_the_lifespan_starts_with_the_opt_in_and_still_warns(
    quiet_lifespan, placeholder_app_settings, startup_log, monkeypatch
):
    monkeypatch.setattr(main.settings, "ALLOW_INSECURE_DEV_SECRETS", True)
    entered = False
    async with main.lifespan(main.app):
        entered = True
    assert entered is True
    warned = [fields.get("message", "") for level, _event, fields in startup_log.records if level == "warning"]
    for name in SECRET_VARS:
        assert sum(name in message for message in warned) == 1, (name, warned)


async def test_the_lifespan_starts_quietly_with_generated_secrets(
    quiet_lifespan, placeholder_app_settings, startup_log, monkeypatch
):
    for name, value in GENERATED.items():
        monkeypatch.setattr(main.settings, name, value)
    async with main.lifespan(main.app):
        pass
    assert not [
        fields for level, _event, fields in startup_log.records
        if level == "warning" and any(name in str(fields.get("message", "")) for name in SECRET_VARS)
    ]


def test_the_test_suite_itself_runs_with_the_opt_in():
    """``tests/conftest.py`` sets it, so local pytest needs no environment variables."""
    import os

    assert os.environ.get("ALLOW_INSECURE_DEV_SECRETS", "").lower() == "true"
