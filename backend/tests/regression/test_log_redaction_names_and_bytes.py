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


@pytest.mark.parametrize(
    "name",
    ["access_tokens", "refresh_tokens", "webhook_url", "smtp_pass", "signing_key", "hmac_key",
     "app_secret_key_previous", "APP_SECRET_KEY_PREVIOUS",
     # QA round 4.
     "passphrase", "ssh_key_passphrase", "session_cookie"],
)
def test_names_the_suffixes_cannot_reach_are_listed(name):
    """Code review round 4."""
    assert redact_log_field(name, "s3cr3t-value-0123456789") == "[REDACTED]"


@pytest.mark.parametrize("name", ["next_page_token", "page_token", "pagination_token"])
def test_a_pagination_cursor_is_not_a_secret(name):
    assert redact_log_field(name, "eyJvZmZzZXQiOjIwfQ") == "eyJvZmZzZXQiOjIwfQ"


@pytest.mark.parametrize(
    "url, secret",
    [
        ("https://hooks.slack.com/workflows/T0/A0/123/XyZsecret99", "XyZsecret99"),
        ("https://hooks.slack.com/triggers/T0/123/XyZsecret99", "XyZsecret99"),
        ("https://outlook.office.com/webhook/abc@def/IncomingWebhook/XyZsecret99/ghi", "XyZsecret99"),
        ("https://discord.com/api/webhooks/123/XyZsecret99", "XyZsecret99"),
        ("https://prod-11.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke"
         "?api-version=2016-06-01&sp=%2Ftriggers&sv=1.0&sig=XyZsecret99", "XyZsecret99"),
        ("https://default0a1b.2c.environment.api.powerplatform.com:443/powerautomate/automations/direct"
         "/workflows/abc/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers&sig=XyZsecret99", "XyZsecret99"),
    ],
    ids=["slack workflows", "slack triggers", "outlook legacy", "discord", "teams workflows",
         "teams workflows powerplatform"],
)
def test_other_webhook_url_shapes_lose_their_credential(url, secret):
    assert secret not in redact_log_field("url", url)
