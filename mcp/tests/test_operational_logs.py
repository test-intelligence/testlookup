"""Verify SSE correlation without sending request payloads to an exporter."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
from starlette.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operational_logs import add_operational_logs


def test_mcp_operational_log_contains_only_correlation_and_response_metadata(monkeypatch) -> None:
    import operational_logs

    records = []
    monkeypatch.setenv("BUILD_REVISION", "b" * 40)
    monkeypatch.setenv("TESTLOOKUP_TELEMETRY_INSTALLATION", "synthetic-site")

    class Provider:
        def __init__(self, *, resource, shutdown_on_exit):
            assert shutdown_on_exit is False
            assert resource.attributes["testlookup.service.role"] == "mcp"
            assert resource.attributes["testlookup.build.revision"] == "b" * 40
            assert resource.attributes["testlookup.installation"] == "synthetic-site"

        def add_log_record_processor(self, processor):
            pass

        def get_logger(self, _scope):
            return self

        def emit(self, **record):
            records.append(record)

        def force_flush(self, **_kwargs):
            return True

        def shutdown(self):
            pass

    monkeypatch.setattr(operational_logs, "LoggerProvider", Provider)
    monkeypatch.setattr(operational_logs, "BatchLogRecordProcessor", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(operational_logs, "OTLPLogExporter", lambda **_kwargs: object())

    async def asgi(scope, receive, send):
        await JSONResponse({"ok": True})(scope, receive, send)

    async def exercise():
        app = add_operational_logs(asgi, "http://otel-collector:4318/v1/logs", "production")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
            request_id = "synthetic-opaque-id"
            response = await client.post(
                "/messages?argument=not-central",
                headers={"X-Request-ID": request_id, "Authorization": "Bearer fixture-only"},
                json={"prompt": "synthetic-payload"},
            )
            assert response.json() == {"ok": True}
            assert response.headers["X-Request-ID"] == request_id
            await client.get("/health/live")
            assert len(records) == 1
            record = records[0]
            assert record["event_name"] == "mcp_request"
            body = json.loads(record["body"])
            assert set(body) == {"event", "request_id", "status_code", "duration_ms"}
            assert body["request_id"] == hashlib.sha256(request_id.encode()).hexdigest()

    asyncio.run(exercise())


def test_remote_plaintext_mcp_endpoint_preserves_application() -> None:
    app = object()
    assert add_operational_logs(app, "http://fixture.example/v1/logs", "production") is app
