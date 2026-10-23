"""The precedence rule and the runtime integration resolver (W10 INT-01 / INT-03, D-1 / D-2).

``services/integration_config_service`` is the ONE owner of the rule:

| Key | Order (first that applies wins) |
|---|---|
| any outbound call | ``AI_OFFLINE_MODE=true`` refuses it (read live, never cached) |
| Splunk index | the project's Splunk Index (non-empty) -> env ``SPLUNK_INDEX`` |
| OpenShift namespace of a run | the run's own -> the project's -> saved default -> env ``OCP_DEFAULT_NAMESPACE`` |
| every other text / boolean key | saved (a non-empty text, any boolean) -> env |
| every secret | ``secret_refs`` row -> a legacy inline value -> env |

Each row is pinned below, plus ``_environment_fields`` (which keys the page
shows as "From the deployment environment"), the secrecy of the runtime
config, and the 30-second cache that keeps the agent and the log stage from
opening a session per call.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services import integration_config_service as ics
from app.services.integration_config_service import (
    INTEGRATION_BOOL_KEYS,
    INTEGRATION_SECRET_KEYS,
    INTEGRATION_TEXT_KEYS,
    RUNTIME_CACHE_SECONDS,
    IntegrationRuntimeConfig,
    effective_value,
)
from app.tools.investigation_context import (
    InvestigationContext,
    bind_runtime_integrations,
    reset_investigation_context,
    reset_runtime_integrations,
    runtime_integrations,
    set_investigation_context,
)

PROJECT_ID = uuid.uuid4()
SPLUNK_TOKEN = "env-splunk-token-0000"
OCP_TOKEN = "env-ocp-token-0000"
SAVED_SPLUNK_TOKEN = "saved-splunk-token-1111"


@pytest.fixture(autouse=True)
def _fresh_cache():
    ics.clear_runtime_integration_cache()
    yield
    ics.clear_runtime_integration_cache()


#: Every environment value the resolver can read, set explicitly so the
#: expected ``_environment_fields`` is exact.
ENV = {
    "JIRA_ENABLED": False,
    "JIRA_DOMAIN": "env-jira.example.test",
    "JIRA_EMAIL": None,
    "JIRA_DEFAULT_PROJECT_KEY": "QA",
    "JIRA_API_TOKEN": None,
    "SPLUNK_ENABLED": True,
    "SPLUNK_BASE_URL": "https://env-splunk.example.test:8089",
    "SPLUNK_API_TOKEN": SPLUNK_TOKEN,
    "SPLUNK_INDEX": "main",
    "OCP_ENABLED": True,
    "OCP_API_URL": "https://env-ocp.example.test:6443",
    "OCP_SA_TOKEN": OCP_TOKEN,
    "OCP_DEFAULT_NAMESPACE": "qa-testing",
    "SLACK_ENABLED": False,
    "SLACK_WEBHOOK_URL": None,
    "SLACK_DEFAULT_CHANNEL": "#qa-alerts",
    "TEAMS_ENABLED": False,
    "TEAMS_WEBHOOK_URL": None,
    "GITHUB_TOKEN": None,
    "GITHUB_REPO": "",
}


@pytest.fixture
def env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    return monkeypatch


class _Result:
    def __init__(self, *, row=None, first=None):
        self._row = row
        self._first = first

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        return self._first


class _DB:
    """The two reads the resolver makes: the saved settings row, the project's fields."""

    def __init__(self, saved=None, project=None):
        self.saved = saved
        self.project = project
        self.statements: list[str] = []

    async def execute(self, statement):
        text = str(statement)
        self.statements.append(text)
        if "app_settings" in text:
            return _Result(row=SimpleNamespace(value=self.saved) if self.saved is not None else None)
        if "projects" in text:
            return _Result(first=self.project)
        raise AssertionError(f"unexpected statement: {text}")


def _secrets(values=None):
    values = values or {}

    async def read_secret(db, scope, key):
        assert scope == "integrations_config"
        return values.get(key)

    return patch.object(ics, "read_secret", read_secret)


async def _global(saved=None, secrets=None):
    with _secrets(secrets):
        return await ics.resolve_global_integrations(_DB(saved=saved))


async def _runtime(saved=None, project=None, project_id=PROJECT_ID, secrets=None):
    db = _DB(saved=saved, project=project)
    with _secrets(secrets):
        cfg = await ics.resolve_runtime_integrations(db, project_id)
    return cfg, db


# ── effective_value ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("saved, expected", [
    ({"k": "https://saved"}, ("https://saved", "saved")),
    ({"k": "  https://saved  "}, ("https://saved", "saved")),   # returned stripped
    ({"k": ""}, ("ENV", "environment")),
    ({"k": "   "}, ("ENV", "environment")),
    ({"k": None}, ("ENV", "environment")),
    ({}, ("ENV", "environment")),
    ({"k": False}, (False, "saved")),                         # a saved boolean is a value
    ({"k": True}, (True, "saved")),
    ({"k": 0}, (0, "saved")),                                 # any other saved non-None value
])
def test_effective_value(saved, expected):
    assert effective_value(saved, "k", "ENV") == expected


# ── The rule, row by row ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_saved_text_beats_the_environment(env):
    cfg = await _global(saved={"splunk_base_url": "https://saved-splunk:8089", "jira_domain": " ui.atlassian.net "})
    assert cfg["splunk_base_url"] == "https://saved-splunk:8089"
    assert cfg["jira_domain"] == "ui.atlassian.net"


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", ["", "   ", None])
async def test_a_saved_empty_text_hands_the_key_back_to_the_environment(env, cleared):
    cfg = await _global(saved={"splunk_base_url": cleared, "ocp_default_namespace": cleared,
                               "slack_default_channel": cleared})
    assert cfg["splunk_base_url"] == ENV["SPLUNK_BASE_URL"]
    assert cfg["ocp_default_namespace"] == "qa-testing"
    assert cfg["slack_default_channel"] == "#qa-alerts"


@pytest.mark.asyncio
async def test_a_saved_false_beats_an_environment_true(env):
    cfg = await _global(saved={"splunk_enabled": False, "ocp_enabled": False})
    assert cfg["splunk_enabled"] is False
    assert cfg["ocp_enabled"] is False
    runtime, _ = await _runtime(saved={"splunk_enabled": False})
    assert runtime.splunk_enabled is False
    assert runtime.splunk_unavailable_reason() == "disabled"


@pytest.mark.asyncio
async def test_a_saved_true_beats_an_environment_false(env):
    cfg = await _global(saved={"jira_enabled": True})
    assert cfg["jira_enabled"] is True


@pytest.mark.asyncio
async def test_secrets_resolve_the_row_then_the_inline_value_then_the_environment(env):
    stored = await _global(secrets={"splunk_api_token": SAVED_SPLUNK_TOKEN})
    assert stored["splunk_api_token"] == SAVED_SPLUNK_TOKEN
    inline = await _global(saved={"splunk_api_token": "legacy-inline"})
    assert inline["splunk_api_token"] == "legacy-inline"
    # A saved-then-cleared token is expired: read_secret answers None, so the
    # environment applies again.
    cleared = await _global(secrets={"splunk_api_token": None})
    assert cleared["splunk_api_token"] == SPLUNK_TOKEN


@pytest.mark.asyncio
async def test_the_project_index_beats_the_environment(env):
    cfg, db = await _runtime(project=("payments", None))
    assert (cfg.splunk_index, cfg.splunk_index_source) == ("payments", "project")
    blank, _ = await _runtime(project=("  ", None))
    assert (blank.splunk_index, blank.splunk_index_source) == ("main", "environment")
    no_project, no_project_db = await _runtime(project_id=None)
    assert (no_project.splunk_index, no_project.splunk_index_source) == ("main", "environment")
    assert no_project.project_id is None
    assert not any("projects" in s for s in no_project_db.statements)


@pytest.mark.asyncio
async def test_the_namespace_chain_run_then_project_then_saved_then_environment(env):
    project, _ = await _runtime(saved={"ocp_default_namespace": "qa-saved"}, project=(None, "qa-payments"))
    assert (project.ocp_default_namespace, project.ocp_namespace_source) == ("qa-payments", "project")
    assert project.namespace_for("qa-run") == "qa-run"            # the run's own wins
    assert project.namespace_for("  ") == "qa-payments"           # blank: the project's
    assert project.namespace_for(None) == "qa-payments"

    saved, _ = await _runtime(saved={"ocp_default_namespace": "qa-saved"}, project=(None, ""))
    assert (saved.namespace_for(None), saved.ocp_namespace_source) == ("qa-saved", "saved")

    environment, _ = await _runtime(saved={"ocp_default_namespace": ""}, project=(None, None))
    assert (environment.namespace_for(None), environment.ocp_namespace_source) == ("qa-testing", "environment")


@pytest.mark.asyncio
async def test_the_runtime_config_carries_the_saved_values_and_tokens(env):
    cfg, db = await _runtime(
        saved={"splunk_base_url": "https://saved-splunk:8089", "ocp_api_url": "https://saved-ocp:6443"},
        project=("payments", "qa-payments"),
        secrets={"splunk_api_token": SAVED_SPLUNK_TOKEN},
    )
    assert cfg.project_id == str(PROJECT_ID)
    assert (cfg.splunk_enabled, cfg.splunk_base_url, cfg.splunk_api_token) == (
        True, "https://saved-splunk:8089", SAVED_SPLUNK_TOKEN,
    )
    assert (cfg.ocp_enabled, cfg.ocp_api_url, cfg.ocp_sa_token) == (True, "https://saved-ocp:6443", OCP_TOKEN)
    # Statements: the saved row (resolve_global_integrations) then ONE project read.
    assert len(db.statements) == 2
    assert "app_settings" in db.statements[0] and "projects" in db.statements[1]
    assert "splunk_index" in db.statements[1] and "ocp_namespace" in db.statements[1]


@pytest.mark.asyncio
async def test_offline_mode_is_read_live_and_overrides_a_cached_config(env):
    cfg, _ = await _runtime(project=("payments", "qa-payments"))
    assert cfg.splunk_unavailable_reason() is None
    assert cfg.ocp_unavailable_reason() is None
    env.setattr(settings, "AI_OFFLINE_MODE", True)
    assert cfg.splunk_unavailable_reason() == "offline_mode"
    assert cfg.ocp_unavailable_reason() == "offline_mode"


@pytest.mark.asyncio
async def test_unavailable_reasons_in_order(env):
    base = dict(
        project_id=None, splunk_enabled=True, splunk_base_url="https://s", splunk_index="main",
        splunk_index_source="environment", ocp_enabled=True, ocp_api_url="https://o",
        ocp_default_namespace="qa", ocp_namespace_source="environment",
    )
    assert IntegrationRuntimeConfig(**{**base, "splunk_enabled": False}).splunk_unavailable_reason() == "disabled"
    assert IntegrationRuntimeConfig(**{**base, "splunk_base_url": None}).splunk_unavailable_reason() == "not_configured"
    assert IntegrationRuntimeConfig(**{**base, "splunk_index": "main | delete"}).splunk_unavailable_reason() == "invalid_index"
    assert IntegrationRuntimeConfig(**{**base, "splunk_index": ""}).splunk_unavailable_reason() == "invalid_index"
    assert IntegrationRuntimeConfig(**{**base, "ocp_enabled": False}).ocp_unavailable_reason() == "disabled"
    assert IntegrationRuntimeConfig(**{**base, "ocp_api_url": "  "}).ocp_unavailable_reason() == "not_configured"
    assert IntegrationRuntimeConfig(**base).splunk_unavailable_reason() is None
    for reason in ("offline_mode", "disabled", "not_configured", "invalid_index"):
        assert reason in ics.UNAVAILABLE_REASONS


# ── _environment_fields ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_environment_fields_list_exactly_the_environment_sourced_keys(env):
    cfg = await _global(
        saved={"splunk_base_url": "https://saved-splunk:8089", "ocp_default_namespace": "",
               "jira_enabled": True},
        secrets={"ocp_sa_token": "stored-ocp"},
    )
    # Text keys from a non-empty environment value that was never saved (or saved
    # empty); secrets with no stored row, no inline value and a non-empty env.
    # Booleans never appear; an empty env value (JIRA_EMAIL, GITHUB_REPO) does not.
    assert cfg["_environment_fields"] == sorted([
        "jira_domain", "jira_default_project_key", "ocp_api_url", "ocp_default_namespace",
        "slack_default_channel", "splunk_api_token",
    ])
    assert set(cfg["_environment_fields"]) <= set(INTEGRATION_TEXT_KEYS) | set(INTEGRATION_SECRET_KEYS)
    assert not set(cfg["_environment_fields"]) & set(INTEGRATION_BOOL_KEYS)


@pytest.mark.asyncio
async def test_environment_fields_are_names_never_values(env):
    cfg = await _global()
    rendered = repr(cfg["_environment_fields"])
    for value in (SPLUNK_TOKEN, OCP_TOKEN, ENV["SPLUNK_BASE_URL"]):
        assert value not in rendered


@pytest.mark.asyncio
async def test_the_resolver_keeps_every_key_its_callers_read(env):
    cfg = await _global()
    expected = {
        "jira_enabled", "jira_domain", "jira_email", "jira_default_project_key", "splunk_enabled",
        "splunk_base_url", "ocp_enabled", "ocp_api_url", "ocp_default_namespace", "slack_enabled",
        "slack_default_channel", "teams_enabled", "github_repo", *INTEGRATION_SECRET_KEYS, "_environment_fields",
    }
    assert set(cfg) == expected


# ── from_settings and secrecy ───────────────────────────────────────────────


def test_from_settings_reads_the_given_settings_object():
    custom = SimpleNamespace(
        SPLUNK_ENABLED=True, SPLUNK_BASE_URL=" https://tool-splunk:8089 ", SPLUNK_API_TOKEN="tok",
        SPLUNK_INDEX="payments", OCP_ENABLED=False, OCP_API_URL="", OCP_SA_TOKEN=None,
        OCP_DEFAULT_NAMESPACE="qa-x",
    )
    cfg = IntegrationRuntimeConfig.from_settings(custom, project_id=PROJECT_ID)
    assert cfg.project_id == str(PROJECT_ID)
    assert (cfg.splunk_base_url, cfg.splunk_index, cfg.splunk_api_token) == ("https://tool-splunk:8089", "payments", "tok")
    assert (cfg.splunk_index_source, cfg.ocp_namespace_source) == ("environment", "environment")
    assert (cfg.ocp_enabled, cfg.ocp_api_url, cfg.ocp_default_namespace) == (False, None, "qa-x")


def test_the_runtime_config_repr_has_no_token():
    cfg = IntegrationRuntimeConfig(
        project_id=None, splunk_enabled=True, splunk_base_url="https://s", splunk_index="main",
        splunk_index_source="environment", ocp_enabled=True, ocp_api_url="https://o",
        ocp_default_namespace="qa", ocp_namespace_source="environment",
        splunk_api_token=SAVED_SPLUNK_TOKEN, ocp_sa_token=OCP_TOKEN,
    )
    for text in (repr(cfg), str(cfg), repr(InvestigationContext("p", "r", "t", "n", integrations=cfg))):
        assert SAVED_SPLUNK_TOKEN not in text and OCP_TOKEN not in text
    with pytest.raises(AttributeError):
        cfg.splunk_index = "other"  # type: ignore[misc]  # frozen


# ── runtime_integrations_for: bound, cached, short-lived ───────────────────


class _Sessions:
    """A counting AsyncSessionLocal: each open yields a fresh fake session."""

    def __init__(self, saved=None, project=("payments", "qa-payments"), fail=False):
        self.saved = saved
        self.project = project
        self.fail = fail
        self.opened = 0
        self.closed = 0

    def __call__(self):
        @asynccontextmanager
        async def _session():
            self.opened += 1
            try:
                if self.fail:
                    raise ConnectionRefusedError("database is down")
                yield _DB(saved=self.saved, project=self.project)
            finally:
                self.closed += 1

        return _session()


@pytest.fixture
def sessions(env):
    factory = _Sessions()
    env.setattr("app.db.postgres.AsyncSessionLocal", factory, raising=False)
    with _secrets():
        yield factory


@pytest.mark.asyncio
async def test_a_cache_miss_opens_one_short_session_that_is_closed(sessions):
    cfg = await ics.runtime_integrations_for(PROJECT_ID)
    assert cfg is not None and cfg.splunk_index == "payments"
    assert (sessions.opened, sessions.closed) == (1, 1)


@pytest.mark.asyncio
async def test_a_second_call_within_the_window_opens_no_session(sessions):
    first = await ics.runtime_integrations_for(PROJECT_ID)
    second = await ics.runtime_integrations_for(str(PROJECT_ID))
    assert second is first
    assert sessions.opened == 1


def _age_entry(project_id, seconds: float) -> None:
    """Make the cached entry ``seconds`` old (never patch time.monotonic: the
    event loop reads it too)."""
    key = str(project_id)
    stored_at, cfg = ics._RUNTIME_CACHE[key]
    ics._RUNTIME_CACHE[key] = (stored_at - seconds, cfg)


@pytest.mark.asyncio
async def test_an_old_entry_is_read_again(sessions):
    await ics.runtime_integrations_for(PROJECT_ID)
    _age_entry(PROJECT_ID, RUNTIME_CACHE_SECONDS - 1)
    await ics.runtime_integrations_for(PROJECT_ID)
    assert sessions.opened == 1
    _age_entry(PROJECT_ID, 2)
    await ics.runtime_integrations_for(PROJECT_ID)
    assert sessions.opened == 2


@pytest.mark.asyncio
async def test_invalidation_makes_the_next_call_read(sessions):
    other = uuid.uuid4()
    await ics.runtime_integrations_for(PROJECT_ID)
    await ics.runtime_integrations_for(other)
    assert sessions.opened == 2
    ics.invalidate_runtime_integrations(other)          # one project's save
    await ics.runtime_integrations_for(PROJECT_ID)
    assert sessions.opened == 2
    await ics.runtime_integrations_for(other)
    assert sessions.opened == 3
    ics.invalidate_runtime_integrations()               # a global save clears every entry
    await ics.runtime_integrations_for(PROJECT_ID)
    await ics.runtime_integrations_for(other)
    assert sessions.opened == 5


@pytest.mark.asyncio
async def test_a_bound_config_wins_and_opens_nothing(sessions):
    bound = IntegrationRuntimeConfig.from_settings(settings, project_id=PROJECT_ID)
    context_token = set_investigation_context(InvestigationContext(
        project_id=str(PROJECT_ID), run_id="r", test_case_id="t", test_name="n", integrations=bound,
    ))
    try:
        assert runtime_integrations() is bound
        assert await ics.runtime_integrations_for(PROJECT_ID) is bound
    finally:
        reset_investigation_context(context_token)
    standalone = bind_runtime_integrations(bound)
    try:
        assert await ics.runtime_integrations_for(str(PROJECT_ID)) is bound
        # Bound for another project: not used.
        other = await ics.runtime_integrations_for(uuid.uuid4())
        assert other is not bound
    finally:
        reset_runtime_integrations(standalone)
    assert runtime_integrations() is None
    assert sessions.opened == 1


@pytest.mark.asyncio
async def test_an_exception_answers_none_and_caches_nothing(env):
    failing = _Sessions(fail=True)
    env.setattr("app.db.postgres.AsyncSessionLocal", failing, raising=False)
    assert await ics.runtime_integrations_for(PROJECT_ID) is None
    assert await ics.runtime_integrations_for(PROJECT_ID) is None
    assert failing.opened == 2 and failing.closed == 2


@pytest.mark.asyncio
async def test_a_resolve_that_started_before_a_save_is_not_cached(sessions, env):
    real = ics.resolve_runtime_integrations

    async def resolve_then_save(db, project_id):
        cfg = await real(db, project_id)
        ics.invalidate_runtime_integrations(project_id)   # a PUT lands mid-resolve
        return cfg

    env.setattr(ics, "resolve_runtime_integrations", resolve_then_save)
    await ics.runtime_integrations_for(PROJECT_ID)
    env.setattr(ics, "resolve_runtime_integrations", real)
    await ics.runtime_integrations_for(PROJECT_ID)
    assert sessions.opened == 2
