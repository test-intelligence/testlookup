"""W18 DEV-05: ``--release`` and ``--project`` take what the UI shows (a name), never a guess.

* a UUID / ``unattributed`` passes through with no request;
* a unique name (or, failing that, a version) resolves, case-insensitively;
* two releases with that name exit 4 listing ``name (version, status, project) id`` for each;
* an unknown name exits 4 naming the closest names;
* ``analytics trends --project <name> --release <name>`` sends ids to chart-data.
"""
from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from testlookup_cli import client, release_resolver
from testlookup_cli.app import app
from testlookup_cli.errors import CLIError

P1 = "11111111-1111-4111-8111-111111111111"
R1 = "22222222-2222-4222-8222-222222222221"
R2 = "22222222-2222-4222-8222-222222222222"
R3 = "22222222-2222-4222-8222-222222222223"
RELEASES = [
    {"id": R1, "name": "2026.10", "version": "2026.10.0", "status": "active", "project_id": P1, "project_name": "Checkout Service"},
    {"id": R2, "name": "Sprint 12", "version": "12a", "status": "planned", "project_id": P1, "project_name": "Checkout Service"},
    {"id": R3, "name": "Sprint 12", "version": "12b", "status": "released", "project_id": P1, "project_name": "Checkout Service"},
]


@pytest.fixture
def calls(monkeypatch):
    seen: list[tuple[str, str, dict | None]] = []

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        seen.append((method, path, params))
        if path == "/api/v1/releases":
            return {"items": RELEASES, "total": len(RELEASES)}
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Checkout Service"}, {"id": "p2", "name": "Payments"}]
        if path == "/api/v1/analytics/chart-data":
            return {"series": [], "meta": {}}
        raise AssertionError(path)

    monkeypatch.setattr(client, "request", _request)
    return seen


def test_ids_pass_through_without_a_request(calls):
    assert asyncio.run(release_resolver.resolve_release(R1)) == R1
    assert asyncio.run(release_resolver.resolve_release("Unattributed")) == "unattributed"
    assert asyncio.run(release_resolver.resolve_project(P1)) == P1
    assert calls == []


def test_a_unique_name_then_a_version_resolves_case_insensitively(calls):
    assert asyncio.run(release_resolver.resolve_release("2026.10", project_id=P1)) == R1
    assert calls[-1] == ("GET", "/api/v1/releases", {"project_id": P1})
    assert asyncio.run(release_resolver.resolve_release("12B")) == R3
    assert asyncio.run(release_resolver.resolve_project("checkout service")) == P1


def test_an_ambiguous_name_exits_4_with_every_choice(calls):
    with pytest.raises(CLIError) as exc:
        asyncio.run(release_resolver.resolve_release("sprint 12"))
    assert exc.value.exit_code == 4
    msg = str(exc.value)
    assert "2 releases are called 'sprint 12'" in msg
    assert f"Sprint 12 (12a, planned, Checkout Service) {R2}" in msg and R3 in msg


def test_an_unknown_name_exits_4_with_the_closest(calls):
    with pytest.raises(CLIError) as exc:
        asyncio.run(release_resolver.resolve_release("Sprint 13"))
    assert exc.value.exit_code == 4 and "Closest: Sprint 12" in str(exc.value)
    with pytest.raises(CLIError) as exc:
        asyncio.run(release_resolver.resolve_project("Checkout"))
    assert exc.value.exit_code == 4 and "Closest: Checkout Service" in str(exc.value)


def test_analytics_sends_ids_for_names(calls):
    result = CliRunner().invoke(app, [
        "analytics", "trends", "--project", "Checkout Service", "--release", "2026.10", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    chart = [c for c in calls if c[1] == "/api/v1/analytics/chart-data"][0]
    assert chart[2]["project_id"] == P1 and chart[2]["release_id"] == [R1]


def test_analytics_refuses_an_ambiguous_release_before_any_chart_request(calls):
    result = CliRunner().invoke(app, ["analytics", "trends", "--release", "Sprint 12"])
    assert result.exit_code == 4
    assert "2 releases are called 'Sprint 12'" in result.output
    assert not any(c[1] == "/api/v1/analytics/chart-data" for c in calls)
