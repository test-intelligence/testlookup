"""Log redaction: bytes values, the deployment's own secret names, webhook URLs (QA of the H3 fix).

* A bytes value was skipped as a non-string and decoded by the renderer after
  redaction had run, so ``raw=b"password=..."`` reached the log intact.
* The secret-name list matched exactly and held none of the deployment's own
  settings: ``github_token=``, ``openai_api_key=`` or ``postgres_password=``
  printed their value, which carries no marker a pattern could see.
* A Slack or Teams incoming-webhook URL is the credential itself.
"""
from __future__ import annotations

import pytest

from app.services.redaction_service import redact_log_field, redact_log_message

SECRET = "hunter2-very-secret-value"


def test_a_bytes_value_is_redacted_like_text():
    out = redact_log_field("raw", f"password={SECRET}".encode())
    assert SECRET not in str(out)
    out = redact_log_field("raw", bytearray(f"password={SECRET}".encode()))
    assert SECRET not in str(out)


@pytest.mark.parametrize(
    "name",
    [
        "github_token", "jira_api_token", "confluence_api_token", "splunk_api_token",
        "ocp_sa_token", "slack_bot_token", "slack_webhook_url", "teams_webhook_url",
        "openai_api_key", "anthropic_api_key", "jwt_secret_key", "app_secret_key",
        "postgres_password", "redis_password", "minio_access_key", "GITHUB-TOKEN",
    ],
)
def test_the_deployments_own_secret_names_are_redacted_by_name(name):
    assert redact_log_field(name, "ghp_0123456789abcdefghij") == "[REDACTED]"


@pytest.mark.parametrize(
    "name, value",
    [
        ("token_count", 42), ("prompt_tokens", 9), ("max_tokens", 4096),
        ("password_changed", True), ("api_key_id", "0b7e"), ("tokens", 3),
        ("secret_rotation_days", 90), ("token_expires_at", "2026-09-11"),
    ],
)
def test_counters_and_flags_that_merely_mention_a_secret_are_kept(name, value):
    assert redact_log_field(name, value) == value


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/services/T0000/B0000/XYZsecretXYZ123",
        "https://acme.webhook.office.com/webhookb2/abc@def/IncomingWebhook/123/xyz",
    ],
)
def test_an_incoming_webhook_url_loses_its_credential(url):
    for out in (redact_log_field("url", url), redact_log_message(f"posting to {url} failed")):
        assert "XYZsecretXYZ123" not in out and "IncomingWebhook/123/xyz" not in out, out
        assert "[REDACTED]" in out


def test_a_set_of_strings_is_still_walked():
    out = redact_log_field("tags", {f"password={SECRET}", "ok"})
    assert all(SECRET not in item for item in out)
