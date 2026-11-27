"""Known-workload API and retained-intent accounting, independent of Celery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from prometheus_client import CollectorRegistry, Counter, Gauge
from prometheus_client.parser import text_string_to_metric_families
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, settings
from app.middleware import journey_sli as middleware
from app.services import journey_sli_metrics as service
from app.services.journey_sli_contracts import API_ROUTES, COHORT_OUTCOMES, JOURNEYS, WINDOWS

NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
SCOPE = {"installation": "synthetic-site", "environment": "development"}


def _value(template, **labels):
    samples = [sample for family in text_string_to_metric_families(service.render_journey_cohort_metrics().decode())
               for sample in family.samples if sample.name == template.name and sample.labels == labels]
    assert len(samples) == 1
    return samples[0].value


@pytest.fixture
def api_values(monkeypatch):
    monkeypatch.setattr(settings, "OBSERVABILITY_INSTALLATION", SCOPE["installation"])
    monkeypatch.setattr(settings, "APP_ENV", SCOPE["environment"])
    registry = CollectorRegistry()
    counter = Counter("fixture_journey_events", "fixture", ["installation", "environment", "objective", "outcome"], registry=registry)
    configured = Gauge("fixture_sli_configured", "fixture", ["installation", "environment", "objective"], registry=registry)
    monkeypatch.setattr(middleware, "api_journey_events", counter)
    monkeypatch.setattr(middleware, "api_sli_configured", configured)
    return counter, configured


async def _invoke(app, *, route="/api/v1/projects", path=None, method="GET", resolved=True):
    scope = {"type": "http", "method": method, "path": path or route, "query_string": b"token=synthetic-sensitive-query"}
    if resolved:
        scope["route"] = SimpleNamespace(path=route)
    messages = []
    async def send(message):
        messages.append(message)
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    await middleware.JourneySLIMiddleware(app)(scope, receive, send)
    return messages


def _response(status=200, *, more=False):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"synthetic", "more_body": more})
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "availability", "latency"), [(200, "good", "good"), (302, "good", "good"), (429, "bad", "bad"), (500, "bad", "bad"), (503, "bad", "bad")])
async def test_actual_completed_attempt_has_one_outcome_per_objective(api_values, monkeypatch, status, availability, latency):
    monkeypatch.setattr(middleware, "perf_counter", lambda: 1.0)
    messages = await _invoke(_response(status))
    assert messages[-1]["body"] == b"synthetic"
    counter, configured = api_values
    for objective, good in (("api_availability", availability), ("api_latency", latency)):
        assert configured.labels(**SCOPE, objective=objective)._value.get() == 1
        for outcome in ("good", "bad"):
            assert counter.labels(**SCOPE, objective=objective, outcome=outcome)._value.get() == int(outcome == good)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
async def test_client_refusals_are_not_a_success_only_denominator(api_values, status):
    await _invoke(_response(status))
    for objective in ("api_availability", "api_latency"):
        assert sum(api_values[0].labels(**SCOPE, objective=objective, outcome=outcome)._value.get() for outcome in ("good", "bad")) == 0


@pytest.mark.asyncio
async def test_latency_uses_final_body_before_background_work(api_values, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(middleware, "perf_counter", lambda: clock[0])
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        clock[0] = .201  # existing project-list p95 budget is .2s
        await send({"type": "http.response.body", "body": b""})
        clock[0] = 500
        raise RuntimeError("synthetic post-response background failure")
    with pytest.raises(RuntimeError):
        await _invoke(app)
    values = api_values[0]
    assert values.labels(**SCOPE, objective="api_availability", outcome="good")._value.get() == 1
    assert values.labels(**SCOPE, objective="api_latency", outcome="bad")._value.get() == 1
    assert values.labels(**SCOPE, objective="api_availability", outcome="bad")._value.get() == 0


@pytest.mark.asyncio
async def test_unhandled_and_incomplete_response_count_bad_then_propagate(api_values):
    async def broken(scope, receive, send):
        raise RuntimeError("synthetic handler failure")
    with pytest.raises(RuntimeError):
        await _invoke(broken)
    await _invoke(_response(more=True))
    for objective in ("api_availability", "api_latency"):
        assert api_values[0].labels(**SCOPE, objective=objective, outcome="bad")._value.get() == 2


@pytest.mark.asyncio
async def test_early_guard_matches_only_fixed_core_templates_without_labels(api_values):
    await _invoke(_response(429), path="/api/v1/runs/synthetic-private-id", resolved=False)
    await _invoke(_response(500), route="/health/details", resolved=False)
    await _invoke(_response(500), path="/api/v1/shared/reports/synthetic-bearer", resolved=False)
    await _invoke(_response(500), method="POST")
    for objective in ("api_availability", "api_latency"):
        assert api_values[0].labels(**SCOPE, objective=objective, outcome="bad")._value.get() == 1
    labels = [sample.labels for family in api_values[0].collect() for sample in family.samples]
    assert all(set(value) == {"installation", "environment", "objective", "outcome"} for value in labels)
    assert "synthetic-private-id" not in str(labels) and "synthetic-sensitive-query" not in str(labels)


@pytest.mark.parametrize(("path", "root", "matches"), [
    ("/proxy/api/v1/runs/synthetic-id", "/proxy", True),
    ("/api/v1/runs/synthetic-id", "/proxy", True),  # stripped by upstream already
    ("/proxy2/api/v1/runs/synthetic-id", "/proxy", False),
    ("/proxy", "/proxy", False),
])
def test_early_guard_match_respects_asgi_root_path_boundary(path, root, matches):
    assert (middleware._threshold({"method": "GET", "path": path, "root_path": root}) is not None) is matches


@pytest.mark.asyncio
async def test_unset_installation_disables_inputs_and_does_not_infer_identity(api_values, monkeypatch):
    monkeypatch.setattr(settings, "OBSERVABILITY_INSTALLATION", "")
    await _invoke(_response(500))
    assert list(api_values[0].collect())[0].samples == []
    assert await service.refresh_journey_cohort_metrics(now=NOW) is True


@pytest.mark.parametrize("value", ["a", "EU_site.3-prod", "a" * 63, ""])
def test_valid_installation_scope(value):
    assert Settings(_env_file=None, OBSERVABILITY_INSTALLATION=value).OBSERVABILITY_INSTALLATION == value


@pytest.mark.parametrize("value", ["a" * 64, " site", "site ", "../site", "https://site", "équipe", "site\n", "-site"])
def test_invalid_installation_scope_fails_configuration(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OBSERVABILITY_INSTALLATION=value)


@pytest_asyncio.fixture
async def sql_world(monkeypatch):
    monkeypatch.setattr(service, "_CACHE", None)
    monkeypatch.setattr(service, "_PUBLISHED", None)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE run_downstream_outbox (id INTEGER PRIMARY KEY, operation TEXT, status TEXT, created_at DATETIME, completed_at DATETIME, attempts INTEGER DEFAULT 0)"))
        await connection.execute(text("CREATE TABLE agent_pipeline_runs (id INTEGER PRIMARY KEY, status TEXT, created_at DATETIME, completed_at DATETIME, execution_metadata JSON)"))
    sessions = async_sessionmaker(engine)
    from app.db import postgres
    monkeypatch.setattr(postgres, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(settings, "OBSERVABILITY_INSTALLATION", SCOPE["installation"])
    monkeypatch.setattr(settings, "APP_ENV", SCOPE["environment"])
    monkeypatch.setattr(settings, "AI_PIPELINE_DEADLINE_SECONDS", 1500)
    yield engine, sessions
    await engine.dispose()


async def _row(engine, *, table="run_downstream_outbox", status="completed", age=7200, elapsed=10, operation="run_notifications", metadata=None):
    accepted = NOW - timedelta(seconds=age)
    completed = None if elapsed is None else accepted + timedelta(seconds=elapsed)
    async with engine.begin() as connection:
        if table == "run_downstream_outbox":
            await connection.execute(text("INSERT INTO run_downstream_outbox(operation,status,created_at,completed_at) VALUES (:operation,:status,:accepted,:completed)"),
                                     {"operation": operation, "status": status, "accepted": accepted, "completed": completed})
        else:
            await connection.execute(text("INSERT INTO agent_pipeline_runs(status,created_at,completed_at,execution_metadata) VALUES (:status,:accepted,:completed,:metadata)"),
                                     {"status": status, "accepted": accepted, "completed": completed, "metadata": json.dumps(metadata or {})})


@pytest.mark.asyncio
async def test_matured_intent_denominator_includes_every_nonterminal_state_and_retries(sql_world):
    engine, sessions = sql_world
    await _row(engine)
    await _row(engine, elapsed=4201)
    await _row(engine, status="failed", elapsed=None)
    for status in ("waiting", "pending", "sending", "published", "processing"):
        await _row(engine, status=status, elapsed=None)
    await _row(engine, elapsed=None)  # malformed completed without timestamp
    await _row(engine, operation="synthetic-unknown-operation")
    await _row(engine, status="pending", age=1, elapsed=None)  # not yet mature
    async with sessions() as db:
        snapshot = await service.read_journey_cohorts(db, now=NOW)
    measured = {outcome: snapshot["downstream"][("1h", outcome)] for outcome in COHORT_OUTCOMES}
    assert measured == {"good": 1, "late": 1, "failed": 1, "overdue": 5, "degraded": 0, "unknown": 2}
    assert sum(measured.values()) == 10  # not merely the two completed rows
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE run_downstream_outbox SET attempts=attempts+5"))
    async with sessions() as db:
        assert await service.read_journey_cohorts(db, now=NOW) == snapshot


@pytest.mark.asyncio
async def test_quality_is_explicit_and_degraded_not_full_quality(sql_world):
    engine, sessions = sql_world
    for metadata in ({"fallback_used": False}, {"fallback_used": True}, {"fallback_used": False, "stage_quality": "degraded"}, {}):
        await _row(engine, table="agent_pipeline_runs", age=2000, metadata=metadata)
    await _row(engine, table="agent_pipeline_runs", age=2000, status="retry_wait", elapsed=None)
    await _row(engine, table="agent_pipeline_runs", age=2000, status="failed", elapsed=30)
    await _row(engine, table="agent_pipeline_runs", age=2000, elapsed=1501, metadata={"fallback_used": False})
    async with sessions() as db:
        snapshot = await service.read_journey_cohorts(db, now=NOW)
    assert snapshot["ai_completion"][("1h", "good")] == 4
    full = snapshot["ai_reported_full_quality"]
    assert {key: full[("1h", key)] for key in COHORT_OUTCOMES} == {"good": 1, "degraded": 3, "overdue": 1, "failed": 1, "late": 1, "unknown": 0}


@pytest.mark.asyncio
async def test_full_quality_requires_json_boolean_false_not_coercible_values(sql_world):
    engine, sessions = sql_world
    for value in (0, "false", None, [], {}):
        await _row(engine, table="agent_pipeline_runs", age=2000, metadata={"fallback_used": value})
    async with sessions() as db:
        result = await service.read_journey_cohorts(db, now=NOW)
    assert result["ai_reported_full_quality"][("1h", "good")] == 0
    assert result["ai_reported_full_quality"][("1h", "degraded")] == 5


@pytest.mark.asyncio
async def test_window_boundaries_late_recovery_and_live_subset(sql_world):
    engine, sessions = sql_world
    await _row(engine, status="pending", age=4200, elapsed=None, operation="persist_live_session")
    await _row(engine, age=4200 + 3600, operation="persist_live_session")  # exclusive left boundary
    await _row(engine, age=4200 + 3600 - 1)
    async with sessions() as db:
        before = await service.read_journey_cohorts(db, now=NOW)
    assert before["live_materialization"][("1h", "overdue")] == 1
    assert before["live_materialization"][("1h", "good")] == 0
    assert before["live_materialization"][("6h", "good")] == 1
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE run_downstream_outbox SET status='completed', completed_at=:now WHERE status='pending'"), {"now": NOW})
    async with sessions() as db:
        after = await service.read_journey_cohorts(db, now=NOW + timedelta(seconds=1))
    assert after["live_materialization"][("1h", "good")] == 1


@pytest.mark.asyncio
async def test_never_completed_then_late_completion_stays_in_bad_denominator(sql_world):
    engine, sessions = sql_world
    await _row(engine, status="processing", age=7200, elapsed=None)
    async with sessions() as db:
        before = await service.read_journey_cohorts(db, now=NOW)
    assert before["downstream"][("1h", "overdue")] == 1
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE run_downstream_outbox SET status='completed', completed_at=:now"), {"now": NOW})
    async with sessions() as db:
        after = await service.read_journey_cohorts(db, now=NOW)
    assert after["downstream"][("1h", "overdue")] == 0
    assert after["downstream"][("1h", "late")] == 1
    assert after["downstream"][("1h", "good")] == 0


@pytest.mark.asyncio
async def test_impossible_completion_timestamps_are_unknown_not_good(sql_world):
    engine, sessions = sql_world
    await _row(engine, age=7200, elapsed=-1)
    await _row(engine, age=7200, elapsed=7201)
    async with sessions() as db:
        result = await service.read_journey_cohorts(db, now=NOW)
    assert result["downstream"][("1h", "unknown")] == 2


@pytest.mark.asyncio
async def test_disabled_ai_deadline_is_unsupported_without_erasing_outbox(sql_world, monkeypatch):
    engine, _sessions = sql_world
    await _row(engine)
    monkeypatch.setattr(settings, "AI_PIPELINE_DEADLINE_SECONDS", 0)
    assert await service.refresh_journey_cohort_metrics(now=NOW)
    assert _value(service.journey_cohort_events, **SCOPE, journey="downstream", window="1h", outcome="good") == 1
    assert _value(service.journey_source_supported, **SCOPE, journey="ai_completion") == 0


@pytest.mark.asyncio
async def test_replicas_restart_and_empty_source_rederive_absolute_snapshot(sql_world):
    engine, sessions = sql_world
    await _row(engine, status="pending", elapsed=None)
    await _row(engine, table="agent_pipeline_runs", age=2000, metadata={"fallback_used": False})
    assert await service.refresh_journey_cohort_metrics(now=NOW)
    expected = _value(service.journey_cohort_events, **SCOPE, journey="downstream", window="1h", outcome="overdue")
    assert expected == 1
    # Another API replica or restarted collector sees durable state, not a
    # local increment/checkpoint. A repeated observation must not double it.
    assert await service.refresh_journey_cohort_metrics(now=NOW)
    assert _value(service.journey_cohort_events, **SCOPE, journey="downstream", window="1h", outcome="overdue") == expected
    for contract in JOURNEYS:
        assert _value(service.journey_source_supported, **SCOPE, journey=contract.name) == int(contract.supported)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM run_downstream_outbox"))
        await connection.execute(text("DELETE FROM agent_pipeline_runs"))
    assert await service.refresh_journey_cohort_metrics(now=NOW)
    for journey in (item.name for item in JOURNEYS if item.supported):
        assert all(_value(service.journey_cohort_events, **SCOPE, journey=journey, window=window, outcome=outcome) == 0
                   for window in WINDOWS for outcome in COHORT_OUTCOMES)


@pytest.mark.asyncio
async def test_paired_journeys_scan_each_source_once(sql_world):
    engine, sessions = sql_world
    queries = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with sessions() as db:
            await service.read_journey_cohorts(db, now=NOW)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert len(queries) == 2


@pytest.mark.asyncio
async def test_cache_preserves_actual_read_time_failed_query_does_not_refresh(sql_world, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])
    reader = AsyncMock(wraps=service.read_journey_cohorts)
    monkeypatch.setattr(service, "read_journey_cohorts", reader)
    await service.refresh_journey_cohort_metrics()
    cached = service._CACHE
    observed = _value(service.journey_cohort_observed_at, **SCOPE)
    assert reader.await_count == 1 and cached is not None
    clock[0] = 20
    await service.refresh_journey_cohort_metrics()
    assert reader.await_count == 1
    assert _value(service.journey_cohort_observed_at, **SCOPE) == observed
    clock[0] = 31
    reader.side_effect = RuntimeError("synthetic unavailable source")
    with pytest.raises(RuntimeError):
        await service.refresh_journey_cohort_metrics()
    assert service._CACHE is cached
    assert _value(service.journey_cohort_observed_at, **SCOPE) == observed


@pytest.mark.asyncio
async def test_cache_never_mixes_site_environment_deadline_or_explicit_now(sql_world, monkeypatch):
    monkeypatch.setattr(service, "monotonic", lambda: 0.0)
    reader = AsyncMock(wraps=service.read_journey_cohorts)
    monkeypatch.setattr(service, "read_journey_cohorts", reader)
    await service.refresh_journey_cohort_metrics()
    assert reader.await_count == 1
    await service.refresh_journey_cohort_metrics(now=NOW)
    assert reader.await_count == 2  # deterministic reads never borrow cache
    for field, value in (("OBSERVABILITY_INSTALLATION", "second-site"), ("APP_ENV", "staging"), ("AI_PIPELINE_DEADLINE_SECONDS", 1499)):
        monkeypatch.setattr(settings, field, value)
        await service.refresh_journey_cohort_metrics()
    assert reader.await_count == 5


@pytest.mark.asyncio
async def test_export_keeps_changing_cohort_as_one_immutable_bundle(sql_world):
    engine, _sessions = sql_world
    await _row(engine, status="processing", age=7200, elapsed=None)
    await service.refresh_journey_cohort_metrics(now=NOW)
    first = service.render_journey_cohort_metrics()
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE run_downstream_outbox SET status='completed', completed_at=:now"), {"now": NOW})
    await service.refresh_journey_cohort_metrics(now=NOW)
    second = service.render_journey_cohort_metrics()
    assert first != second
    for payload, expected in ((first, "overdue"), (second, "late")):
        samples = [sample for family in text_string_to_metric_families(payload.decode()) for sample in family.samples
                   if sample.name == service.journey_cohort_events.name
                   and sample.labels.get("journey") == "downstream" and sample.labels.get("window") == "1h"]
        assert sum(sample.value for sample in samples) == 1
        assert next(sample.value for sample in samples if sample.labels["outcome"] == expected) == 1
    assert service.journey_cohort_events.samples == []  # global unregistered template stays untouched


def test_finite_route_contract_reuses_existing_latency_budgets():
    assert len(API_ROUTES) == 6
    assert all(method == "GET" and path.startswith("/api/v1/") for method, path in API_ROUTES)
