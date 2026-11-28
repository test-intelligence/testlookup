"""Unexpected errors retain diagnostics without changing route body contracts."""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from structlog.contextvars import get_contextvars

from app.core import analytics_errors
from app.middleware.telemetry import REQUEST_ID_CTX, TelemetryMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(TelemetryMiddleware)
    app.add_exception_handler(Exception, analytics_errors.unhandled_exception_handler)

    async def fail():
        raise RuntimeError("fake-internal-error")

    app.add_api_route("/core", fail)
    app.add_api_route("/api/v1/scim/Users", fail)

    @app.get("/analytics")
    @analytics_errors.analytics_error_contract
    async def analytics():
        return await fail()

    @app.get("/healthy")
    async def healthy():
        return {"ok": True}

    @app.get("/refused")
    async def refused():
        raise HTTPException(403, "Access denied")

    return app


@pytest.mark.parametrize("path", ["/core", "/api/v1/scim/Users", "/analytics"])
def test_unexpected_error_has_one_correlated_log_and_preserves_body(path, monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(analytics_errors, "logger", logger)
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.get(path, headers={"X-Request-ID": "review-request-42"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "review-request-42"
    assert "fake-internal-error" not in response.text
    if path == "/analytics":
        assert response.json()["request_id"] == "review-request-42"
        event = "analytics_unhandled_error"
    else:
        assert response.text == "Internal Server Error"
        event = "http_unhandled_error"
    logger.error.assert_called_once()
    assert logger.error.call_args.args == (event,)
    assert logger.error.call_args.kwargs["request_id"] == "review-request-42"
    assert logger.error.call_args.kwargs["status_code"] == 500
    assert isinstance(logger.error.call_args.kwargs["exc_info"], RuntimeError)
    assert REQUEST_ID_CTX.get() == ""
    assert not get_contextvars()


def test_healthy_and_expected_refusal_keep_contract_and_do_not_log_exceptions(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(analytics_errors, "logger", logger)
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/core").status_code == 500
    logger.reset_mock()
    healthy = client.get("/healthy", headers={"X-Request-ID": "next-request"})
    refused = client.get("/refused", headers={"X-Request-ID": "refused-request"})
    assert healthy.json() == {"ok": True}
    assert healthy.headers["X-Request-ID"] == "next-request"
    assert refused.json() == {"detail": "Access denied"}
    assert refused.headers["X-Request-ID"] == "refused-request"
    logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_error_before_routing_never_logs_shared_report_path_credential(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(analytics_errors, "logger", logger)
    request = Request({
        "type": "http", "method": "GET", "headers": [],
        "path": "/api/v1/shared/reports/fake-private-report-token",
        "query_string": b"", "scheme": "https",
    })
    response = await analytics_errors.unhandled_exception_handler(request, RuntimeError("fake-error"))
    assert response.status_code == 500
    assert "fake-private-report-token" not in logger.error.call_args.kwargs["path"]
    assert response.headers["X-Request-ID"]
