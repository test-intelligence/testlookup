"""Shipped placeholders are not configuration (D-76, D-33).

``.env.example`` shipped live-looking assignments for every optional
integration; the installer copied it to ``.env``, so a new install's settings
pages said "Password saved." and "A webhook is set" about settings nobody
made. Two guards:

* the template itself: every uncommented assignment in ``.env.example`` is
  empty, a safe non-secret default, or an infrastructure secret that the
  generator replaces and production refuses to start with;
* the readers: an install whose ``.env`` still carries the old placeholders
  reads every one of them as not configured.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.config import _is_placeholder_secret, settings
from app.core.sample_values import configured_value, is_sample_value
from app.routers import app_settings
from app.services import integration_config_service as ics
from app.services.notification import email_service

_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

#: The integration block exactly as ``.env.example`` shipped it before D-76:
#: an older install's ``.env`` still holds these.
OLD_EXAMPLE_VALUES = {
    "JIRA_DOMAIN": "yourcompany.atlassian.net",
    "JIRA_EMAIL": "admin@yourcompany.com",
    "JIRA_API_TOKEN": "your-jira-api-token",
    "SPLUNK_BASE_URL": "https://your-splunk-instance:8089",
    "SPLUNK_API_TOKEN": "your-splunk-token",
    "OCP_API_URL": "https://api.your-cluster.com:6443",
    "OCP_SA_TOKEN": "your-service-account-token",
    "SLACK_WEBHOOK_URL": "replace-with-slack-webhook-url",
    "TEAMS_WEBHOOK_URL": "https://yourorg.webhook.office.com/webhookb2/...",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_USER": "you@example.com",
    "SMTP_PASSWORD": "your-app-password",
}

#: Infrastructure secrets the template must name (Compose refuses to start
#: without them). scripts/gen-dev-env.sh replaces each with a random value and
#: production refuses to start while one is still a placeholder
#: (test_example_placeholders_block_production.py). E2E_ADMIN_PASSWORD is read
#: by the Playwright suite only, never by the app.
GENERATED_SECRETS = {
    "APP_SECRET_KEY", "POSTGRES_PASSWORD", "DATABASE_URL", "MONGO_PASSWORD", "MONGO_URI",
    "FLOWER_PASSWORD", "MINIO_SECRET_KEY", "JWT_SECRET_KEY", "WEBHOOK_SECRET",
    "E2E_ADMIN_PASSWORD", "GF_SECURITY_ADMIN_PASSWORD",
}

_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _example_assignments() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            out[match.group(1)] = match.group(2).split(" #", 1)[0].strip()
    return out


# -- The template ------------------------------------------------------------


def test_no_uncommented_assignment_in_env_example_is_a_sample_value():
    assignments = _example_assignments()
    assert len(assignments) > 50, "the parser found too few assignments"
    samples = sorted(
        key for key, value in assignments.items()
        if key not in GENERATED_SECRETS and (is_sample_value(value) or (value and _is_placeholder_secret(value)))
    )
    assert not samples, (
        ".env.example ships a placeholder as a live value; comment it out (# KEY=example): " + ", ".join(samples)
    )


def test_every_optional_integration_key_in_env_example_is_unset():
    assignments = _example_assignments()
    keys = set(ics._ENV_SETTING.values()) | set(ics._SECRET_ENV_SETTING.values()) | {
        "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SLACK_BOT_TOKEN",
        "CONFLUENCE_DOMAIN", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
    }
    # A boolean switch (false) or a safe default (QA, qa-testing, #qa-alerts)
    # is fine; a host, address or credential must not be set.
    defaults = {"JIRA_DEFAULT_PROJECT_KEY", "OCP_DEFAULT_NAMESPACE", "SLACK_DEFAULT_CHANNEL"}
    set_values = sorted(
        key for key in keys - defaults
        if assignments.get(key) and not key.endswith("_ENABLED")
    )
    assert not set_values, f".env.example sets optional integration values: {set_values}"


def test_generated_secrets_are_still_refused_in_production():
    # The allow-list above is only safe while production refuses each value.
    assignments = _example_assignments()
    for key in ("APP_SECRET_KEY", "JWT_SECRET_KEY", "WEBHOOK_SECRET"):
        assert _is_placeholder_secret(assignments[key]), key


# -- The helper --------------------------------------------------------------


@pytest.mark.parametrize("value", sorted(OLD_EXAMPLE_VALUES.values()))
def test_every_old_example_value_is_a_sample(value):
    assert is_sample_value(value)
    assert configured_value(value) is None


@pytest.mark.parametrize("value", [
    "acme.atlassian.net", "qa@acme.io", "https://splunk.acme.internal:8089", "smtp.gmail.com",
    "env-jira.example.test", "QA", "#qa-alerts", "hooks.slack.com", "hunter2-but-long",
    # "your-" inside a value someone chose is not the template's prefix.
    "#ask-your-team", "Keep-your-head-42",
])
def test_real_values_are_kept(value):
    assert not is_sample_value(value)
    assert configured_value(value) == value


# -- The readers -------------------------------------------------------------


@pytest.fixture
def old_install(monkeypatch):
    """Settings as an install whose .env predates D-76 sees them."""
    for name, value in OLD_EXAMPLE_VALUES.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    monkeypatch.setattr(settings, "GITHUB_REPO", None)
    return monkeypatch


class _NoRows:
    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)


async def _no_secret(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_resolver_reads_old_placeholders_as_not_configured(old_install):
    with patch.object(ics, "read_secret", _no_secret):
        cfg = await ics.resolve_global_integrations(_NoRows())
    for key in ("jira_domain", "jira_email", "splunk_base_url", "ocp_api_url", *ics.INTEGRATION_SECRET_KEYS):
        assert not cfg[key], f"{key} = {cfg[key]!r}"
    # Only the safe defaults remain environment-sourced.
    assert set(cfg["_environment_fields"]) <= {
        "jira_default_project_key", "ocp_default_namespace", "slack_default_channel",
    }
    read = app_settings._integrations_read({**cfg, "_vault_fields": []})
    assert read.slack_webhook_set is False
    assert read.teams_webhook_set is False
    assert read.jira_token_set is False


@pytest.mark.asyncio
async def test_resolver_keeps_a_real_environment_value(old_install):
    old_install.setattr(settings, "JIRA_DOMAIN", "acme.atlassian.net")
    old_install.setattr(settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T0/B0/real")
    with patch.object(ics, "read_secret", _no_secret):
        cfg = await ics.resolve_global_integrations(_NoRows())
    assert cfg["jira_domain"] == "acme.atlassian.net"
    assert cfg["slack_webhook_url"] == "https://hooks.slack.com/services/T0/B0/real"
    assert {"jira_domain", "slack_webhook_url"} <= set(cfg["_environment_fields"])


@pytest.mark.asyncio
async def test_smtp_read_on_a_new_install_says_no_password(old_install):
    """D-33: "Disabled" with "Password saved." on a new install."""
    old_install.setattr(settings, "SMTP_ENABLED", False)

    async def no_secret(*_args, **_kwargs):
        return False

    with patch("app.services.secret_service.has_secret", no_secret), \
            patch("app.services.secret_service.read_secret", _no_secret):
        read = await app_settings.get_smtp_config(None, _NoRows())
    assert read.password_set is False
    assert read.host == "localhost"
    assert read.user is None


def test_email_service_env_fallback_drops_placeholders(old_install):
    cfg = email_service._env_smtp_cfg()
    assert cfg["password"] is None
    assert cfg["user"] is None
    assert cfg["host"] == "localhost"
