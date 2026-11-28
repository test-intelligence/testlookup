"""The release checker's harmless marker uses the real authenticated SDK app."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip("mcp.server.mcpserver")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_missing_path_echoes_marker_and_exports_candidate_bound_log_without_http(monkeypatch):
    import httpx
    import requests
    import operational_logs
    from config import settings
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

    attempts = []
    def forbidden(*_args, **_kwargs):
        attempts.append(True)
        raise AssertionError("outbound HTTP is outside this ASGI fixture")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    exporter = InMemoryLogRecordExporter()
    monkeypatch.setattr(operational_logs, "OTLPLogExporter", lambda **_kwargs: exporter)
    real_provider, providers = operational_logs.LoggerProvider, []
    def provider(**kwargs):
        item = real_provider(**kwargs)
        providers.append(item)
        return item
    monkeypatch.setattr(operational_logs, "LoggerProvider", provider)
    monkeypatch.setattr(settings, "otlp_logs_endpoint", "http://127.0.0.1:1/v1/logs")
    monkeypatch.setattr(settings, "telemetry_environment", "development")
    monkeypatch.setenv("BUILD_REVISION", "a" * 40)
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    monkeypatch.setenv("TESTLOOKUP_TELEMETRY_INSTALLATION", "synthetic-site")
    import server
    import client
    from mcp.server.transport_security import TransportSecuritySettings
    monkeypatch.setattr(server, "TRANSPORT_SECURITY", TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=["mcp:8002"], allowed_origins=[],
    ))
    monkeypatch.setattr(client, "_get_client", forbidden)
    monkeypatch.setattr(client, "_get_auth_client", forbidden)
    marker = "release-signal-sdk-marker"
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.sse_app()), base_url="http://mcp:8002") as http:
            return await http.get("/release-signal-probe", headers={"X-Request-ID": marker})
    try:
        response = asyncio.run(invoke())
        assert response.status_code == 404 and response.headers["X-Request-ID"] == marker
        assert len(providers) == 1 and providers[0].force_flush(timeout_millis=5000)
        entry, = exporter.get_finished_logs()
        body = json.loads(entry.log_record.body)
        assert body["event"] == "mcp_request" and body["status_code"] == 404
        assert body["request_id"] == hashlib.sha256(marker.encode()).hexdigest()
        assert entry.resource.attributes["testlookup.build.revision"] == "a" * 40
        assert entry.resource.attributes["testlookup.installation"] == "synthetic-site"
        assert entry.resource.attributes["service.version"] == "1.2.3"
        assert attempts == []
    finally:
        for item in providers:
            item.shutdown()
