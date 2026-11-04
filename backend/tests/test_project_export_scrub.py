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
     ("workflow_definitions", "definition"), ("release_gate_policies", "rules")],
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


# ── W30 S-7: run-history free text and JSON ─────────────────────────────────

@pytest.mark.parametrize(
    ("table", "column"),
    [("test_cases", "error_message"), ("test_cases", "stack_trace"), ("test_cases", "triage_notes"),
     ("test_steps", "assertion_message"), ("test_steps", "assertion_trace"), ("test_steps", "expected_value"),
     ("test_steps", "actual_value"), ("ai_analysis", "root_cause_summary"),
     ("ai_analysis_versions", "root_cause_summary"), ("agent_pipeline_runs", "error"),
     ("agent_stage_results", "error"), ("suite_run_reviews", "note"), ("test_execution_reviews", "note"),
     ("release_decisions", "reasoning"), ("release_outcomes", "reason"), ("evidence_artifacts", "summary_excerpt"),
     ("failure_clusters", "representative_error"), ("deep_findings", "root_cause"), ("timeline_notes", "text")],
)
def test_every_history_free_text_column_gets_the_text_pass(table: str, column: str) -> None:
    sentinels = _sentinels()
    out = scrub.scrub_row(_spec(table), {column: _typed_text(sentinels)})
    _assert_clean(out[column], sentinels)
    assert REDACTED in out[column] and out[column].startswith("Use ")


@pytest.mark.parametrize(
    ("table", "column"),
    [("agent_stage_results", "result_data"), ("agent_stage_results", "checkpoint_data"),
     ("agent_stage_results", "decision_log"), ("release_decisions", "input_snapshot"),
     ("release_decisions", "policy_evaluation"), ("run_intelligence_snapshots", "payload"),
     ("test_runs", "run_context"), ("test_runs", "ocp_metadata"), ("test_cases", "source_parameters"),
     ("release_gate_decisions", "policy_snapshot"), ("test_steps", "parameters")],
)
def test_every_history_json_column_is_scrubbed(table: str, column: str) -> None:
    sentinels = _sentinels()
    out = scrub.scrub_row(_spec(table), {column: {"api_key": sentinels["openrouter"],
                                                  "log": "auth " + sentinels["jwt"] + " " + sentinels["ghp"]}})
    assert out[column]["api_key"] == REDACTED
    _assert_clean(repr(out), sentinels)


def test_a_name_value_pair_naming_a_secret_loses_its_value() -> None:
    """Allure parameters / HAR headers: ``{"name": "password", "value": …}`` — the key is neutral, the pair is not."""
    sentinels = _sentinels()
    params = [{"name": "password", "value": sentinels["basic_pass"]},
              {"name": "Authorization", "value": "Basic " + sentinels["aws_secret"]},
              {"key": "api_token", "values": [sentinels["query_tok"]]},
              {"name": "browser", "value": "chrome"}]
    out = scrub.scrub_row(_spec("test_cases"), {"source_parameters": params})["source_parameters"]
    assert out[:3] == [{"name": "password", "value": REDACTED}, {"name": "Authorization", "value": REDACTED},
                       {"key": "api_token", "values": REDACTED}]
    assert out[3] == {"name": "browser", "value": "chrome"}
    assert scrub.scrub_json(out) == out


def test_history_identifiers_stay_byte_exact() -> None:
    """TEXT_PASS_EXEMPT's W30 additions: values the text pass WOULD change (a 40+ character run) round-trip."""
    project, run = "3f1c9e2a-6b7d-4e8f-9a0b-1c2d3e4f5a6b", "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d"
    long_name = "test_refund_is_rejected_when_the_card_has_expired_and_retry_is_off"
    import hashlib

    sha = "c0ffee" + "deadbeef" * 7 + "0123456789"[:2]
    commit = "Plugh" + "9" * 35

    def git_sha(seed: str) -> str:
        return hashlib.sha1(("commit " + seed).encode()).hexdigest()          # 40 hex, like `git rev-parse`

    real_sha256 = hashlib.sha256(b"evidence excerpt").hexdigest()
    assert len(git_sha("base")) == 40 and len(real_sha256) == 64
    cases = [
        ("test_runs", {"minio_prefix": f"{project}/builds/build-2001/", "commit_hash": commit,
                       "ingestion_identity": sha, "build_number": "build-" + "7" * 40,
                       "jenkins_job": "payments/" + "integration-suite-" * 3}),
        ("test_cases", {"minio_s3_prefix": f"{project}/builds/build-2001/{run}/", "test_fingerprint": sha,
                        "source_uuid": sha, "source_history_id": sha, "source_test_case_id": sha}),
        ("test_case_history", {"test_fingerprint": sha}),
        ("suite_executions", {"suite_key": long_name}),
        ("suite_run_reviews", {"suite_name": long_name}),          # UNIQUE (test_run_id, suite_name)
        ("test_attachments", {"source_ref": "attachments/" + sha + "-attachment.png"}),
        ("failure_clusters", {"cluster_id": "C-" + "1" * 18}),
        ("agent_stage_results", {"task_key": "analyze:" + sha, "idempotency_key": sha}),   # UNIQUE per pipeline
        ("ai_analysis_versions", {"test_fingerprint": sha, "content_sha256": sha}),
        ("failure_attribution", {"test_fingerprint": sha}),
        ("deep_findings", {"cluster_id": "C-" + "2" * 18}),
        # a real-looking 40-hex git SHA and a sha256: the bisect range and the evidence digest of an imported run
        ("run_commit_ranges", {"base_commit": git_sha("base"), "head_commit": git_sha("head")}),
        ("evidence_artifacts", {"cluster_id": "C-" + "3" * 18, "content_sha256": real_sha256}),
    ]
    for table, row in cases:
        for column, value in row.items():
            assert scrub.scrub_text(value) != value or len(value) < 40 or "cluster_id" == column, (
                f"{table}.{column}: the sample would not exercise the exemption")
        assert scrub.scrub_row(_spec(table), row) == row, table


def test_history_names_get_the_text_pass_like_any_free_text() -> None:
    """Orchestrator decision (W30): only what the round trip NEEDS stays byte-exact. A test / suite / branch name
    can carry a token — the scrub is the safety net (a redacted name is cosmetic, a leaked token is not)."""
    sentinels = _sentinels()
    carrier = "test_login_with_" + sentinels["ghp"]
    cases = [
        ("test_runs", ("branch", "primary_suite_name")),
        ("test_cases", ("test_name", "full_name", "suite_name", "class_name", "package_name")),
        ("suite_executions", ("suite_name", "branch")),
        ("test_health_recommendations", ("test_name",)),
        ("timeline_notes", ("suite_name",)),
        ("run_baselines", ("baseline_build_number",)),          # a label, like a name
    ]
    for table, columns in cases:
        assert not set(columns) & scrub.TEXT_PASS_EXEMPT.get(table, frozenset()), table
        out = scrub.scrub_row(_spec(table), {column: carrier for column in columns})
        for column in columns:
            assert sentinels["ghp"] not in out[column] and REDACTED in out[column], (table, column)
    short = {"test_name": "test_login", "class_name": "tests.payments.TestCheckout"}
    assert scrub.scrub_row(_spec("test_cases"), short) == short          # an ordinary name is unchanged


# ── W30 S-8: history URLs ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("table", "column"),
    [("test_runs", "ci_run_url"), ("test_execution_reviews", "defect_link"), ("evidence_artifacts", "uri_or_ref")],
)
def test_every_history_url_column_goes_through_the_url_rule(table: str, column: str) -> None:
    sentinels = _sentinels()
    url = "https://ci:" + sentinels["basic_pass"] + "@ci.example.test/job/7?access_token=" + sentinels["query_tok"]
    out = scrub.scrub_row(_spec(table), {column: url})
    assert out[column] == "https://ci.example.test/job/7?access_token=" + REDACTED


@pytest.mark.parametrize("ref", ["pipeline-artifacts/pipeline/2026/10/01/run/stage/a.json",
                                 "s3://pipeline-artifacts/pipeline/2026/10/01/run/stage/a.json",
                                 "pipeline-artifacts"])
def test_an_evidence_ref_into_pipeline_scratch_is_written_null(ref: str) -> None:
    assert scrub.is_pipeline_scratch_ref(ref) is True
    assert scrub.scrub_row(_spec("evidence_artifacts"), {"uri_or_ref": ref}) == {"uri_or_ref": None}


@pytest.mark.parametrize("ref", ["https://jira.example.test/browse/PAY-1", "pipeline-artifacts-backup/x.json",
                                 "other-bucket/pipeline-artifacts/x.json", None, ""])
def test_any_other_evidence_ref_is_kept(ref: Any) -> None:
    assert scrub.is_pipeline_scratch_ref(ref) is False
    assert scrub.scrub_row(_spec("evidence_artifacts"), {"uri_or_ref": ref}) == {"uri_or_ref": ref}
    # the rule is evidence_artifacts' own: a pipeline-artifacts URL elsewhere is just a URL
    assert scrub.scrub_row(_spec("test_runs"), {"ci_run_url": "pipeline-artifacts/x"})["ci_run_url"] is not None


# ── W30 S-9: stored documents ───────────────────────────────────────────────

def test_a_document_is_redacted_by_key_and_by_pattern_in_full() -> None:
    from datetime import datetime, timezone

    from bson import ObjectId

    sentinels = _sentinels()
    stamp = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
    ref = ObjectId()
    doc = {
        "test_run_id": "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d",
        "request": {"headers": {"Authorization": "Bearer " + sentinels["glpat"], "X-Trace": "t-1"},
                    "url": "https://api.example.test/v1?token=" + sentinels["openrouter"]},
        "parameters": [{"name": "password", "value": sentinels["basic_pass"]}, {"password": sentinels["aws_secret"]}],
        "log": ["step " + str(n) for n in range(150)] + ["boom " + sentinels["ghp"]],
        "at": stamp, "ref": ref,
    }
    original = copy.deepcopy(doc)
    out = scrub.scrub_document(doc)
    assert doc == original                                  # a NEW dict
    _assert_clean(repr(out), sentinels)
    assert out["request"]["headers"]["Authorization"] == REDACTED
    assert out["parameters"][0] == {"name": "password", "value": REDACTED}     # the Allure parameter shape
    assert out["parameters"][1]["password"] == REDACTED
    assert len(out["log"]) == 151 and out["log"][-1] == "boom " + REDACTED     # no truncation (ACT-03)
    assert out["at"] == stamp and out["ref"] == ref         # stored types survive for Extended JSON
    assert out["test_run_id"] == doc["test_run_id"] and out["request"]["headers"]["X-Trace"] == "t-1"
    assert scrub.scrub_document(out) == out                 # idempotent


def test_a_document_nested_past_the_cap_is_redacted_not_truncated_silently() -> None:
    node: dict[str, Any] = {"leaf": "deep"}
    for _ in range(15):
        node = {"k": node}
    out = scrub.scrub_document({"test_run_id": "r", "body": node})
    flat = repr(out)
    assert "deep" not in flat and REDACTED in flat
