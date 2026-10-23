"""W10 INT-04: health rows are keyed ``(provider, scope_key)``; the gauges stay bounded.

``persist_probe_results`` runs for real on a session double that keeps the
``integration_health_checks`` rows in memory and answers each statement from
them, so a select that forgot ``scope_key`` would hand a project's row to the
deployment-wide provider (or the other way round) and the assertions below see
it.

What these pin:

* a project result and the deployment-wide result of the same provider land on
  two rows; the history row carries the scope;
* the provider-labelled gauge is set for deployment-wide results only; the
  per-project gauge is set for all 12 (provider, status) pairs from the table's
  counts, a missing pair to 0 -- never a project label;
* ``prune=True`` (the beat's sweep) deletes the rows of an integration that is
  no longer enabled, in the same transaction, and never a deployment-wide row;
  without ``prune`` nothing is deleted;
* ``_check_alerts`` reads the row of THAT (provider, scope) and names the
  project in its warning.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.core import metrics
from app.db import postgres
from app.models.postgres import IntegrationHealthCheck, IntegrationProbeResult
from app.services import integration_probe_service as probes


def _params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return self._value


class _HealthTable:
    """``integration_health_checks`` in memory, plus the enabled integrations."""

    def __init__(self, enabled=None):
        self.rows: dict[tuple[str, str], IntegrationHealthCheck] = {}
        self.history: list[IntegrationProbeResult] = []
        self.enabled = enabled or {"github": set(), "gitlab": set()}
        self.events: list[str] = []
        self.deletes: list[str] = []

    def session(self):
        return _Session(self)


class _Session:
    def __init__(self, table: _HealthTable):
        self.table = table
        self.pending: list = []

    async def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        params = _params(stmt)
        if sql.startswith("DELETE FROM integration_health_checks"):
            self.table.deletes.append(sql)
            provider = params["provider_1"]
            for key in [k for k in self.table.rows if k[0] == provider and k[1]]:
                if key[1] not in self.table.enabled[provider]:
                    del self.table.rows[key]
            return _Scalar(None)
        if "count(*)" in sql:
            counts: dict[tuple[str, str], int] = {}
            for (provider, scope), row in self.table.rows.items():
                if scope:
                    counts[(provider, row.status)] = counts.get((provider, row.status), 0) + 1
            return _Scalar([(p, s, n) for (p, s), n in counts.items()])
        assert "integration_health_checks.scope_key" in sql, f"a health read without its scope: {sql}"
        key = (params["provider_1"], params["scope_key_1"])
        return _Scalar(self.table.rows.get(key))

    def add(self, obj):
        self.pending.append(obj)

    async def commit(self):
        for obj in self.pending:
            if isinstance(obj, IntegrationHealthCheck):
                self.table.rows[(obj.provider, obj.scope_key)] = obj
            else:
                self.table.history.append(obj)
        self.pending = []
        self.table.events.append("commit")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _ProviderGauge:
    def __init__(self, events):
        self.events = events
        self.values: dict[str, float] = {}
        self._provider = ""

    def labels(self, *, provider):
        self._provider = provider
        return self

    def set(self, value):
        self.events.append("global-gauge")
        self.values[self._provider] = value


class _PairGauge:
    def __init__(self):
        self.values: dict[tuple[str, str], float] = {}
        self._key = ("", "")

    def labels(self, *, provider, status):
        self._key = (provider, status)
        return self

    def set(self, value):
        self.values[self._key] = value


@pytest.fixture
def table(monkeypatch):
    table = _HealthTable()
    monkeypatch.setattr(postgres, "AsyncSessionLocal", table.session)
    table.global_gauge = _ProviderGauge(table.events)
    table.project_gauge = _PairGauge()
    monkeypatch.setattr(metrics, "integration_health_gauge", table.global_gauge)
    monkeypatch.setattr(metrics, "integration_project_health_gauge", table.project_gauge)
    return table


def _project(provider, pid, status, message="m"):
    return probes.ProbeResult(provider, status, 12, message, scope_key=str(pid), project_id=str(pid))


@pytest.mark.asyncio
async def test_a_project_row_and_the_deployment_row_of_one_provider_are_two_rows(table):
    pid = uuid.uuid4()
    await probes.persist_probe_results([
        probes.ProbeResult("github", "skipped", message="No instance-wide GITHUB_TOKEN"),
        _project("github", pid, "auth_error", "HTTP 401: the token was refused"),
    ])

    assert set(table.rows) == {("github", ""), ("github", str(pid))}
    assert table.rows[("github", "")].status == "skipped"
    project_row = table.rows[("github", str(pid))]
    assert (project_row.status, project_row.consecutive_failures) == ("auth_error", 1)
    assert [(h.provider, h.scope_key, h.status) for h in table.history] == [("github", str(pid), "auth_error")]

    # A second sweep updates THAT row; the deployment-wide one is untouched.
    await probes.persist_probe_results([_project("github", pid, "auth_error")])
    assert table.rows[("github", str(pid))].consecutive_failures == 2
    assert table.rows[("github", "")].status == "skipped"


@pytest.mark.asyncio
async def test_a_project_result_never_touches_the_provider_gauge(table):
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await probes.persist_probe_results([
        probes.ProbeResult("jira", "healthy", 30, "Server: Fake Jira"),
        _project("github", a, "healthy"),
        _project("github", b, "auth_error"),
        _project("gitlab", c, "skipped"),
    ])

    assert table.global_gauge.values == {"jira": 1.0}, "a project row moved the provider-labelled gauge"
    assert table.events.index("commit") < table.events.index("global-gauge")
    assert len(table.project_gauge.values) == 12
    assert set(table.project_gauge.values) == {
        (p, s) for p in probes.PROJECT_PROBE_PROVIDERS for s in probes.INTEGRATION_HEALTH_VALUES
    }
    expected = {key: 0 for key in table.project_gauge.values}
    expected.update({("github", "healthy"): 1, ("github", "auth_error"): 1, ("gitlab", "skipped"): 1})
    assert table.project_gauge.values == expected


@pytest.mark.asyncio
async def test_a_recovered_state_reads_zero_not_its_last_count(table):
    pid = uuid.uuid4()
    await probes.persist_probe_results([_project("github", pid, "auth_error")])
    assert table.project_gauge.values[("github", "auth_error")] == 1
    await probes.persist_probe_results([_project("github", pid, "healthy")])
    assert table.project_gauge.values[("github", "auth_error")] == 0
    assert table.project_gauge.values[("github", "healthy")] == 1


@pytest.mark.asyncio
async def test_prune_deletes_the_rows_of_an_integration_no_longer_enabled(table):
    kept, removed, gitlab_kept = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await probes.persist_probe_results([
        probes.ProbeResult("github", "skipped", message="No instance-wide GITHUB_TOKEN"),
        probes.ProbeResult("jira", "healthy", 30, "ok"),
        _project("github", kept, "healthy"),
        _project("github", removed, "healthy"),
        _project("gitlab", gitlab_kept, "healthy"),
    ])
    assert table.deletes == [], "an on-demand persist pruned"
    assert len(table.rows) == 5

    table.enabled = {"github": {str(kept)}, "gitlab": {str(gitlab_kept)}}  # `removed` was disabled
    await probes.persist_probe_results([_project("github", kept, "healthy")], prune=True)

    assert set(table.rows) == {
        ("github", ""), ("jira", ""), ("github", str(kept)), ("gitlab", str(gitlab_kept)),
    }
    assert len(table.deletes) == 2
    for sql in table.deletes:
        assert "integration_health_checks.scope_key != " in sql
        assert "NOT IN (SELECT CAST(" in sql and ".enabled IS true" in sql


@pytest.mark.asyncio
async def test_check_alerts_warns_per_provider_and_project(table, caplog):
    pid = uuid.uuid4()
    table.rows[("github", str(pid))] = SimpleNamespace(consecutive_failures=3)
    table.rows[("github", "")] = SimpleNamespace(consecutive_failures=0)

    with caplog.at_level(logging.WARNING, logger="services.integration_probe"):
        await probes._check_alerts([
            _project("github", pid, "auth_error", "HTTP 401: the token was refused"),
            probes.ProbeResult("github", "down", 0, "global probe down"),
        ])

    alerts = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ALERT")]
    assert alerts == [
        f"ALERT: Integration github of project {pid} has 3 consecutive failures. "
        "Status: auth_error. Message: HTTP 401: the token was refused"
    ]
