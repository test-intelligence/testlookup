"""Re-audit N19: the Splunk and OpenShift RUNTIME calls short-circuit offline.

architecture/SECURITY.md s5 says every outbound integration short-circuits on
AI_OFFLINE_MODE. The probes were gated in the H10 review; the triage agent's
Splunk search (tools/query_splunk.py) and the OpenShift pod lookups
(services/ocp_client.py, used by ingestion and the triage agent) were not.
Each test records the outbound calls, with an online control proving the
recorder sees a call when one is made.
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import ocp_client
from app.tools import query_splunk
from app.tools.investigation_context import (
    InvestigationContext,
    reset_investigation_context,
    set_investigation_context,
)

TIMESTAMP = "2026-09-10T10:00:00Z"


class _Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _Recorder:
    def __init__(self):
        self.calls: list[str] = []

    async def post(self, url, **kwargs):
        self.calls.append(url)
        return _Response({"results": [{"_time": "t", "level": "ERROR", "message": "db pool exhausted"}]})

    async def get(self, url, **kwargs):
        self.calls.append(url)
        return _Response({
            "spec": {"containers": [{"image": "img:1"}]},
            "status": {"phase": "Running"},
            "items": [],
        })


@pytest.fixture
def investigation():
    token = set_investigation_context(InvestigationContext(
        project_id="p", run_id="r", test_case_id="t", test_name="n",
        service_name="payments", timestamp=TIMESTAMP,
        ocp_pod_name="pod-1", ocp_namespace="ns",
    ))
    yield
    reset_investigation_context(token)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(settings, "SPLUNK_ENABLED", True)
    monkeypatch.setattr(settings, "SPLUNK_BASE_URL", "https://splunk.example.com:8089")
    monkeypatch.setattr(settings, "SPLUNK_API_TOKEN", "splunk-token")
    monkeypatch.setattr(settings, "OCP_ENABLED", True)
    monkeypatch.setattr(settings, "OCP_API_URL", "https://api.ocp.example.com:6443")
    monkeypatch.setattr(settings, "OCP_SA_TOKEN", "sa-token")
    monkeypatch.setattr(query_splunk, "get_http_client", lambda: rec)
    monkeypatch.setattr(ocp_client, "get_http_client", lambda: rec)
    return rec


async def _search() -> str:
    return await query_splunk.query_splunk_logs.ainvoke(
        {"service_name": "payments", "timestamp_utc": TIMESTAMP}
    )


@pytest.mark.asyncio
async def test_the_splunk_search_is_not_sent_offline(investigation, recorder, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    result = await _search()
    assert recorder.calls == []
    assert "AI_OFFLINE_MODE=true" in result
    assert "db pool exhausted" not in result


@pytest.mark.asyncio
async def test_control_the_splunk_search_is_sent_online(investigation, recorder, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    result = await _search()
    assert recorder.calls == ["https://splunk.example.com:8089/services/search/jobs"]
    assert "db pool exhausted" in result


@pytest.mark.asyncio
async def test_the_pod_lookup_is_not_sent_offline(recorder, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    assert await ocp_client.get_pod_metadata("pod-1", "ns") is None
    summary = await ocp_client.analyze_pod_events("pod-1", "ns", TIMESTAMP)
    assert recorder.calls == []
    assert "Phase" not in summary


@pytest.mark.asyncio
async def test_control_the_pod_lookup_is_sent_online(recorder, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    meta = await ocp_client.get_pod_metadata("pod-1", "ns")
    assert meta is not None and meta["phase"] == "Running"
    assert recorder.calls == [
        "https://api.ocp.example.com:6443/api/v1/namespaces/ns/pods/pod-1",
        "https://api.ocp.example.com:6443/api/v1/namespaces/ns/events"
        "?fieldSelector=involvedObject.name=pod-1",
    ]
