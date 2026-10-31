"""W24 ADM-05 — how secrets stay out of a project export (contract §2.2 S-1 … S-6).

Pure: ``project_export_scrub`` against ``Base.metadata``; no database. The sentinels are REAL-LOOKING,
non-hex and built at run time (string concatenation) so no secret-shaped literal sits in this file.
"""
from __future__ import annotations

import base64
import copy
import urllib.parse
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.postgres import Base
from app.services import project_export_scrub as scrub
from app.services.redaction_service import REDACTED


def _sentinels() -> dict[str, str]:
    s = "SENTINEL"
    return {
        "ghp": "ghp" + "_" + s + "ghpTokenXyzzyQwertyPlughWxyz",
        "glpat": "glpat" + "-" + s + "glpatXyzzyQw",
        "openrouter": "sk-or" + "-v1-" + s + "openrouterXyzzyQwertyPlughWxyz" + s + "openrouterXyzzyQw",
        "aws_key": "AKIA" + s + "AWSKEYXY",
        "aws_secret": "Plugh" + s + "awsSecretXyzzyQw",
        "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9." + s + "jwtPayload." + s + "jwtSig",
        "slack": "https://hooks.slack.com/services/T" + s + "/B" + s + "/" + s + "slackHookXyzzyQw",
        "basic_pass": s + "basicpass",
        "query_tok": s + "queryTok",
        "xoxb": "xoxb" + "-" + s + "slackBotXyzzyQwerty",
    }


def _spec(table: str) -> Any:
    return SimpleNamespace(table=table)


def _forms(value: str) -> list[str]:
    """The sentinel, its base64 and its URL-encoded forms (the archive scan reads all of them)."""
    raw = value.encode()
    return [
        value,
        base64.b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode(),
        urllib.parse.quote(value, safe=""),
        urllib.parse.quote_plus(value),
    ]


def _assert_clean(text: str, sentinels: dict[str, str], *, skip: tuple[str, ...] = ()) -> None:
    for name, value in sentinels.items():
        if name in skip:
            continue
        for form in _forms(value):
            assert form not in text, f"sentinel {name} leaked"


# ── the declared lists match the metadata ───────────────────────────────────

def test_every_declared_column_exists_on_its_table() -> None:
    declared = [
        ("TEXT_PASS_EXEMPT", scrub.TEXT_PASS_EXEMPT),
        ("URL_COLUMNS", scrub.URL_COLUMNS),
        ("FORCED_FALSE", scrub.FORCED_FALSE),
    ]
    for label, mapping in declared:
        for table, columns in mapping.items():
            assert table in Base.metadata.tables, f"{label}: unknown table {table}"
            known = set(Base.metadata.tables[table].columns.keys())
            missing = set(columns) - known
            assert not missing, f"{label}: {table} has no column(s) {sorted(missing)}"


def test_exempt_columns_are_text_and_never_urls() -> None:
    for table, columns in scrub.TEXT_PASS_EXEMPT.items():
        assert not columns & scrub.URL_COLUMNS.get(table, frozenset()), table
        for column in columns:
            assert scrub._kinds_for(table)[column] == "text", f"{table}.{column} is not a text column"


def test_the_four_pattern_columns_are_exempt_by_name() -> None:
    """R-3: these must round-trip byte-exact (documented; a token typed INTO a pattern travels)."""
    assert "match_pattern" in scrub.TEXT_PASS_EXEMPT["release_attribution_rules"]
    assert "pattern" in scrub.TEXT_PASS_EXEMPT["classification_hints"]
    assert "match_pattern" in scrub.TEXT_PASS_EXEMPT["service_ownership_rules"]
    assert "test_fingerprint" in scrub.TEXT_PASS_EXEMPT["canonical_test_cases"]


# ── S-1: typed secret flags ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("table", "column"),
    [("webhook_subscriptions", "has_secret"), ("github_integrations", "has_pat"), ("gitlab_integrations", "has_pat")],
)
def test_secret_flags_are_written_false(table: str, column: str) -> None:
    row = {"id": "row-one", column: True}
    assert scrub.scrub_row(_spec(table), row)[column] is False
    assert row[column] is True, "the input row must not be modified"


# ── S-3: team channel targets ───────────────────────────────────────────────

@pytest.mark.parametrize("channel_type", ["slack", "teams", "discord-ish-unknown"])
def test_a_webhook_channel_target_is_redacted(channel_type: str) -> None:
    sentinels = _sentinels()
    row = {"team_name": "payments", "channel_type": channel_type, "target": sentinels["slack"]}
    out = scrub.scrub_row(_spec("team_notification_channels"), row)
    assert out["target"] == REDACTED
    assert out["team_name"] == "payments"
    assert out["channel_type"] == channel_type


def test_an_email_channel_target_is_kept_verbatim() -> None:
    target = "payments-team" + "@" + "example.test"
    row = {"team_name": "payments", "channel_type": "email", "target": target}
    assert scrub.scrub_row(_spec("team_notification_channels"), row)["target"] == target


# ── S-4: urls ───────────────────────────────────────────────────────────────

def test_userinfo_is_removed_and_a_denylisted_query_value_redacted() -> None:
    sentinels = _sentinels()
    url = "https://ops:" + sentinels["basic_pass"] + "@hooks.example.test/in?token=" + sentinels["query_tok"] + "&v=2"
    out = scrub.strip_url_credentials(url)
    assert out == "https://hooks.example.test/in?token=" + REDACTED + "&v=2"


def test_a_bare_userinfo_is_removed_and_the_fragment_dropped() -> None:
    url = "https://" + _sentinels()["ghp"] + "@git.example.test/api/v3#section"
    assert scrub.strip_url_credentials(url) == "https://git.example.test/api/v3"


@pytest.mark.parametrize(
    "host",
    ["hooks.slack.com", "outlook.office.com", "acme.webhook.office.com", "discord.com", "discordapp.com",
     "chat.googleapis.com"],
)
def test_a_secret_path_host_keeps_no_path_and_no_query_value(host: str) -> None:
    sentinel = "SENTINEL" + "pathSecretXyzzy"
    url = "https://" + host + "/v1/" + sentinel + "/messages?key=" + sentinel + "&threadKey=" + sentinel + "&bare"
    out = scrub.strip_url_credentials(url)
    assert out == "https://" + host + "/" + REDACTED + "?key=" + REDACTED + "&threadKey=" + REDACTED + "&" + REDACTED


def test_an_ordinary_url_is_unchanged() -> None:
    url = "https://github.example.test/api/v3?per_page=50"
    assert scrub.strip_url_credentials(url) == url
    assert scrub.strip_url_credentials(None) is None
    assert scrub.strip_url_credentials("") == ""


@pytest.mark.parametrize(
    ("table", "column"),
    [("webhook_subscriptions", "target_url"), ("github_integrations", "api_base_url"),
     ("gitlab_integrations", "base_url"), ("releases", "external_url")],
)
def test_every_url_column_goes_through_the_url_rule(table: str, column: str) -> None:
    sentinels = _sentinels()
    url = "https://ops:" + sentinels["basic_pass"] + "@svc.example.test/x?access_token=" + sentinels["query_tok"]
    out = scrub.scrub_row(_spec(table), {column: url})
    assert out[column] == "https://svc.example.test/x?access_token=" + REDACTED


# ── S-5: json ───────────────────────────────────────────────────────────────

def test_secret_named_keys_lose_their_value_and_keys_are_kept() -> None:
    sentinels = _sentinels()
    value = {
        "password": sentinels["basic_pass"],
        "apiKey": sentinels["openrouter"],
        "aws_secret_access_key": sentinels["aws_secret"],
        "slack_webhook_url": sentinels["slack"],
        "github_token": sentinels["ghp"],
        "jira_api_token": sentinels["glpat"],
        "owner": "team-payments",
        "count": 3,
        "enabled": True,
        "nothing": None,
    }
    out = scrub.scrub_json(value)
    assert set(out) == set(value)
    for key in ("password", "apiKey", "aws_secret_access_key", "slack_webhook_url", "github_token", "jira_api_token"):
        assert out[key] == REDACTED, key
    assert out["owner"] == "team-payments"
    assert out["count"] == 3 and out["enabled"] is True and out["nothing"] is None


def test_string_leaves_get_the_text_pass_and_lists_are_walked_in_full() -> None:
    sentinels = _sentinels()
    steps = [{"action": f"step {n}", "data": "plain"} for n in range(500)]
    steps.append({"action": "use " + sentinels["ghp"], "data": "Bearer " + sentinels["jwt"]})
    out = scrub.scrub_json({"steps": steps, "notes": ["see " + sentinels["slack"]]})
    _assert_clean(repr(out), sentinels)
    assert out["steps"][0] == {"action": "step 0", "data": "plain"}
    assert REDACTED in out["steps"][-1]["action"]
    assert len(out["steps"]) == 501


def test_a_container_deeper_than_the_cap_is_redacted() -> None:
    value: Any = "leaf"
    for _ in range(scrub.JSON_DEPTH_MAX + 2):
        value = {"k": value}
    out = scrub.scrub_json(value)
    node = out
    depth = 1
    while isinstance(node, dict):
        node = node["k"]
        depth += 1
    assert node == REDACTED
    assert depth == scrub.JSON_DEPTH_MAX + 1


@pytest.mark.parametrize(
    ("table", "column"),
    [("projects", "component_owner_map"), ("projects", "source_roots"), ("managed_test_cases", "steps"),
     ("managed_test_cases", "parameters"), ("test_case_versions", "steps"), ("agent_configs", "config"),
     ("workflow_definitions", "definition"), ("release_gate_policies", "rules"), ("quality_gates", "rules")],
)
def test_every_named_json_column_is_scrubbed_in_a_row(table: str, column: str) -> None:
    sentinels = _sentinels()
    out = scrub.scrub_row(_spec(table), {column: {"token": sentinels["xoxb"], "note": "key " + sentinels["aws_key"]}})
    assert out[column]["token"] == REDACTED
    _assert_clean(repr(out), sentinels)


# ── S-6: free text ──────────────────────────────────────────────────────────

def _typed_text(sentinels: dict[str, str]) -> str:
    return (
        "Use " + sentinels["ghp"] + " then " + sentinels["glpat"] + ". Key " + sentinels["openrouter"]
        + "; aws " + sentinels["aws_key"] + " aws_secret_access_key=" + sentinels["aws_secret"]
        + " jwt " + sentinels["jwt"] + " hook " + sentinels["slack"]
        + " db https://ops:" + sentinels["basic_pass"] + "@db.example.test/x?token=" + sentinels["query_tok"]
        + " bot " + sentinels["xoxb"]
    )


@pytest.mark.parametrize(
    ("table", "column"),
    [("projects", "description"), ("managed_test_cases", "description"), ("managed_test_cases", "preconditions"),
     ("managed_test_cases", "expected_result"), ("managed_test_cases", "test_data"),
     ("managed_test_cases", "ai_generation_prompt"), ("test_case_versions", "objective"),
     ("test_plans", "objective"), ("test_strategies", "test_approach"), ("release_phases", "notes"),
     ("release_gate_policies", "description"), ("classification_hints", "note"),
     ("service_ownership_rules", "team_contact"), ("workflow_definitions", "description")],
)
def test_every_typed_sentinel_leaves_a_free_text_column(table: str, column: str) -> None:
    sentinels = _sentinels()
    out = scrub.scrub_row(_spec(table), {column: _typed_text(sentinels)})
    _assert_clean(out[column], sentinels)
    assert REDACTED in out[column]
    assert out[column].startswith("Use ")


def test_exempt_columns_stay_byte_exact() -> None:
    long_name = "test_user_can_login_with_valid_credentials_and_remember_me_checked"
    pattern = "^payments/(api|worker)/" + "[A-Za-z0-9_]{40,}" + "$"
    cases = [
        ("managed_test_cases", {"title": long_name, "test_fingerprint": "fp" + "x" * 60}),
        ("canonical_test_cases", {"test_name": long_name, "class_name": "tests.payments.TestCheckoutFlowEndToEnd"}),
        ("release_attribution_rules", {"match_pattern": pattern, "name": long_name}),
        ("classification_hints", {"pattern": pattern, "category": "product_bug"}),
        ("service_ownership_rules", {"match_pattern": pattern, "service_name": long_name}),
        ("releases", {"name": "2026.10 " + long_name, "version": "2026.10.30-rc1"}),
    ]
    for table, row in cases:
        assert scrub.scrub_row(_spec(table), row) == row, table


def test_a_column_the_metadata_does_not_know_is_scrubbed_as_text() -> None:
    sentinels = _sentinels()
    out = scrub.scrub_row(_spec("test_suites"), {"future_free_text": "use " + sentinels["ghp"], "future_count": 3})
    assert out == {"future_free_text": "use " + REDACTED, "future_count": 3}


def test_a_secret_named_column_is_redacted_whatever_its_table() -> None:
    sentinels = _sentinels()
    out = scrub.scrub_row(
        _spec("projects"), {"jira_api_token": sentinels["glpat"], "deploy_key": "short", "client_secret_set": True},
    )
    assert out["jira_api_token"] == REDACTED
    assert out["deploy_key"] == REDACTED


# ── idempotence ─────────────────────────────────────────────────────────────

def _rows_of_every_scrubbed_kind(sentinels: dict[str, str]) -> list[tuple[str, dict[str, Any]]]:
    text = _typed_text(sentinels)
    return [
        ("projects", {"description": text, "component_owner_map": {"api": ["@team", text]}, "jira_project_key": "PAY"}),
        ("managed_test_cases", {"title": "Login", "steps": [{"action": text}], "test_data": text, "tags": [text]}),
        ("team_notification_channels", {"team_name": "t", "channel_type": "slack", "target": sentinels["slack"]}),
        ("team_notification_channels", {"team_name": "t", "channel_type": "email", "target": "t" + "@" + "x.test"}),
        ("webhook_subscriptions", {"name": "hook", "has_secret": True, "events": ["run.completed"],
                                   "target_url": "https://ops:" + sentinels["basic_pass"] + "@h.example.test/?token=x1"}),
        ("github_integrations", {"has_pat": True, "api_base_url": "https://" + sentinels["ghp"] + "@api.github.test"}),
        ("releases", {"external_url": sentinels["slack"] + "?sig=" + sentinels["query_tok"], "description": text}),
        ("agent_configs", {"config": {"nested": {"apiKey": sentinels["openrouter"], "list": [text] * 3}}}),
    ]


def test_scrubbing_a_scrubbed_row_changes_nothing() -> None:
    sentinels = _sentinels()
    for table, row in _rows_of_every_scrubbed_kind(sentinels):
        original = copy.deepcopy(row)
        once = scrub.scrub_row(_spec(table), row)
        assert row == original, f"{table}: the input row was modified"
        assert scrub.scrub_row(_spec(table), once) == once, table
        _assert_clean(repr(once), sentinels)


@pytest.mark.parametrize("name", list(_sentinels()))
def test_each_function_is_idempotent_on_each_sentinel(name: str) -> None:
    value = "x " + _sentinels()[name] + " y"
    for fn in (scrub.scrub_text, scrub.strip_url_credentials, scrub.scrub_json):
        once = fn(value)
        assert fn(once) == once, (fn.__name__, name)
    if name not in ("aws_secret", "basic_pass", "query_tok"):   # those three are secret only by their context
        assert _sentinels()[name] not in scrub.scrub_text(value)
