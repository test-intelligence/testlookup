"""The health dashboards need a signed-in caller; the probes stay public and say little.

``/health/live``, ``/health/ready`` and ``/health/version`` are probes: Kubernetes,
Compose and uptime monitors call them with no credential, so they stay public.
``/health/details`` and ``/health/ingestion`` are dashboards (version,
environment, which dependency is down, queue names and depths, the admission
thresholds) and answer only a signed-in user or an API key.

A failed check on a public route reports a fixed word, never the driver's
exception text (which can name internal hosts, ports and accounts); the
exception goes to the server log.

``/metrics`` takes an optional bearer token (``METRICS_TOKEN``; empty keeps it
open), and ``API_DOCS_ENABLED=false`` turns the API reference routes off.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import bootstrap
from app.core.config import settings
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.routers import health

pytestmark = pytest.mark.regression

BACKEND = Path(__file__).resolve().parents[2]
DRIVER_TEXT = "could not connect to db.internal.example:5432 as user tl_owner"


async def _ok() -> dict:
    return {"status": "ok"}


async def _no_db():
    yield AsyncMock()


def _health_app(*, signed_in: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(health.router)
    app.dependency_overrides[get_db] = _no_db
    if signed_in:
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id="u1", role="VIEWER", is_active=True
        )
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def healthy_probes(monkeypatch):
    for name in ("_check_postgres", "_check_mongo", "_check_redis", "_check_minio", "_check_ollama", "_check_chromadb"):
        monkeypatch.setattr(health, name, _ok)


# ── The dashboards ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/health/details", "/health/ingestion"])
async def test_a_dashboard_refuses_an_anonymous_caller(path, healthy_probes):
    async with _client(_health_app(signed_in=False)) as client:
        response = await client.get(path)
    assert response.status_code == 401
    assert "checks" not in response.text and "queues" not in response.text


async def test_details_answers_a_signed_in_viewer_with_the_same_fields(healthy_probes):
    async with _client(_health_app(signed_in=True)) as client:
        response = await client.get("/health/details")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "build", "env", "uptime_seconds", "timestamp", "checks"}
    assert set(body["checks"]) == {"postgres", "mongo", "redis", "minio", "ollama", "chromadb"}
    assert body["status"] == "healthy"


@pytest.mark.parametrize("path", ["/health/details", "/health/ingestion"])
def test_the_mounted_dashboard_route_carries_the_sign_in_dependency(path):
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == path)
    calls = {d.call for d in route.dependant.dependencies}
    assert get_current_active_user in calls, f"{path} is mounted without a sign-in dependency"


# ── The probes stay public ───────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/health/version"])
async def test_a_probe_answers_an_anonymous_caller(path, healthy_probes):
    async with _client(_health_app(signed_in=False)) as client:
        response = await client.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/health/version"])
def test_the_mounted_probe_route_has_no_sign_in_dependency(path):
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == path)
    assert not route.dependant.dependencies, f"{path} must stay callable by a kubelet with no credential"


class _Recorder:
    def __init__(self):
        self.lines: list[str] = []

    def warning(self, message, *args, **kwargs):
        self.lines.append(message % args if args else message)


@pytest.mark.parametrize("probe", ["_check_postgres", "_check_mongo", "_check_redis"])
async def test_readiness_names_the_failed_store_without_the_driver_text(probe, healthy_probes, monkeypatch):
    """The probe's own ``except`` and the ``_critical`` wrapper are both covered."""

    async def _raises():
        raise ConnectionError(DRIVER_TEXT)

    monkeypatch.setattr(health, probe, _raises)
    recorder = _Recorder()
    with patch.object(health, "logger", recorder):
        async with _client(_health_app(signed_in=False)) as client:
            response = await client.get("/health/ready")
    assert response.status_code == 503
    store = probe.removeprefix("_check_")
    assert response.json()["checks"][store] == {"status": "error", "detail": health.CHECK_FAILED}
    assert "db.internal" not in response.text and "tl_owner" not in response.text
    assert any(DRIVER_TEXT in line for line in recorder.lines), "the exception must reach the server log"


@pytest.mark.parametrize(
    ("probe", "target", "store"),
    [
        ("_check_postgres", "app.db.postgres.AsyncSessionLocal", "postgres"),
        ("_check_mongo", "app.db.mongo.get_mongo_db", "mongo"),
        ("_check_redis", "app.db.redis_client.get_redis", "redis"),
        ("_check_minio", "app.services.storage_config_service.get_effective_storage_config", "minio"),
        ("_check_chromadb", "app.services.storage_config_service.get_effective_storage_config", "chromadb"),
    ],
)
async def test_a_failing_check_reports_a_fixed_word(probe, target, store):
    def _raises(*args, **kwargs):
        raise ConnectionError(DRIVER_TEXT)

    recorder = _Recorder()
    with patch(target, _raises), patch.object(health, "logger", recorder):
        result = await getattr(health, probe)()
    assert result["detail"] == health.CHECK_FAILED
    assert result["status"] in {"error", "degraded"}
    assert DRIVER_TEXT not in json.dumps(result)
    assert any(DRIVER_TEXT in line for line in recorder.lines), f"{store}: the exception must reach the server log"


async def test_the_optional_probe_budget_reports_a_fixed_word():
    async def _raises():
        raise ConnectionError(DRIVER_TEXT)

    recorder = _Recorder()
    with patch.object(health, "logger", recorder):
        result = await health._with_budget(_raises(), "minio")
    assert result == {"status": "degraded", "detail": health.CHECK_FAILED}
    assert any(DRIVER_TEXT in line for line in recorder.lines)


def test_no_health_response_is_built_from_exception_text():
    source = (BACKEND / "app" / "routers" / "health.py").read_text(encoding="utf-8")
    assert "str(exc)" not in source, "a health response must not carry exception text; log it and return CHECK_FAILED"


# ── /metrics ─────────────────────────────────────────────────────────────────

SCRAPE_TOKEN = "fake-scrape-token-for-tests"


def _metrics_app(monkeypatch) -> FastAPI:
    app = FastAPI()
    monkeypatch.setattr("app.services.metric_collection.collect_bounded", AsyncMock(return_value=True))
    bootstrap.configure_metrics(app)
    return app


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": SCRAPE_TOKEN}, {"Authorization": "Basic " + SCRAPE_TOKEN}],
)
async def test_metrics_refuses_a_scrape_without_the_token(headers, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", SCRAPE_TOKEN)
    async with _client(_metrics_app(monkeypatch)) as client:
        response = await client.get("/metrics", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "# HELP" not in response.text


async def test_metrics_answers_a_scrape_with_the_token(monkeypatch):
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", SCRAPE_TOKEN)
    async with _client(_metrics_app(monkeypatch)) as client:
        response = await client.get("/metrics", headers={"Authorization": "Bearer " + SCRAPE_TOKEN})
    assert response.status_code == 200
    assert "# HELP" in response.text


async def test_metrics_stays_open_when_no_token_is_set(monkeypatch):
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", "")
    async with _client(_metrics_app(monkeypatch)) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "# HELP" in response.text


async def test_a_refused_scrape_does_not_refresh_the_queue_depths(monkeypatch):
    """The per-scrape collector wraps the route; the refusal must come first."""
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", SCRAPE_TOKEN)
    refreshed = AsyncMock()
    monkeypatch.setattr(bootstrap, "_refresh_celery_queue_depths", refreshed)
    monkeypatch.setattr(
        "app.services.agent_operational_metrics.refresh_agent_operational_metrics", AsyncMock()
    )
    app = FastAPI()
    bootstrap.configure_metrics(app)  # installs the real collector wrapper
    async with _client(app) as client:
        refused = await client.get("/metrics")
        assert refused.status_code == 401
        refreshed.assert_not_awaited()
        allowed = await client.get("/metrics", headers={"Authorization": "Bearer " + SCRAPE_TOKEN})
    assert allowed.status_code == 200
    refreshed.assert_awaited_once()


async def test_mounted_metrics_token_rotates_on_api_restart(tmp_path, monkeypatch):
    token_file = tmp_path / "metrics-token"
    token_file.write_text(SCRAPE_TOKEN + "\n", encoding="ascii")
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(settings, "METRICS_TOKEN", "legacy-token")

    async def scrape(client, token):
        return await client.get("/metrics", headers={"Authorization": "Bearer " + token})

    async with _client(_metrics_app(monkeypatch)) as client:
        assert (await client.get("/metrics")).status_code == 401
        assert (await scrape(client, "legacy-token")).status_code == 401
        allowed = await scrape(client, SCRAPE_TOKEN)
        assert allowed.status_code == 200
        assert "# HELP" in allowed.text
        token_file.write_text("rotated-test-token", encoding="ascii")
        assert (await scrape(client, SCRAPE_TOKEN)).status_code == 200
        assert (await scrape(client, "rotated-test-token")).status_code == 401

    async with _client(_metrics_app(monkeypatch)) as client:
        assert (await scrape(client, SCRAPE_TOKEN)).status_code == 401
        assert (await scrape(client, "rotated-test-token")).status_code == 200


@pytest.mark.parametrize("contents", [None, b"\r\n", b"first\nsecond", b"x" * 4097])
def test_invalid_metrics_token_file_cannot_fall_back(tmp_path, monkeypatch, contents):
    token_file = tmp_path / "metrics-token"
    if contents is not None:
        token_file.write_bytes(contents)
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(settings, "METRICS_TOKEN", "legacy-token")
    with pytest.raises(RuntimeError, match="configured metrics credential file") as failure:
        bootstrap.configure_metrics(FastAPI())
    assert str(token_file) not in str(failure.value)
    assert "legacy-token" not in str(failure.value)


# ── The API reference ────────────────────────────────────────────────────────

DOCS_ROUTES = {"docs_url": "/api-docs", "redoc_url": "/api-docs/redoc", "openapi_url": "/api-docs/openapi.json"}


def _docs_keywords() -> dict[str, ast.expr]:
    tree = ast.parse((BACKEND / "app" / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "FastAPI":
            found = {k.arg: k.value for k in node.keywords if k.arg in DOCS_ROUTES}
            if found:
                return found
    raise AssertionError("could not find the FastAPI(...) call in main.py")


def _evaluate(expression: ast.expr, *, enabled: bool):
    code = compile(ast.Expression(expression), "main.py", "eval")
    return eval(code, {"settings": SimpleNamespace(API_DOCS_ENABLED=enabled)})  # noqa: S307 - our own source


@pytest.mark.parametrize("keyword", sorted(DOCS_ROUTES))
def test_the_api_reference_routes_follow_the_setting(keyword):
    expression = _docs_keywords()[keyword]
    assert _evaluate(expression, enabled=False) is None, f"{keyword} is still served with API_DOCS_ENABLED=false"
    assert _evaluate(expression, enabled=True) == DOCS_ROUTES[keyword]


def test_the_api_reference_is_on_by_default():
    from app.main import app

    assert settings.API_DOCS_ENABLED is True
    paths = {getattr(r, "path", "") for r in app.routes}
    assert set(DOCS_ROUTES.values()) <= paths


def test_fastapi_serves_no_reference_route_when_all_three_are_off():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    assert not {getattr(r, "path", "") for r in app.routes} & set(DOCS_ROUTES.values())
