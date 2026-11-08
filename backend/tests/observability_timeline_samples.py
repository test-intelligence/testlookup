"""W39 STRAT-07: REAL stored-evidence samples for the observability timeline tests (not a test module).

Every sample is produced by the product's own code against the shared fake (``tests/fakes/fake_integrations.py``,
served in-process over ``httpx.ASGITransport``): the Splunk answer ``query_splunk.splunk_correlation`` renders, the
OpenShift answer ``ocp_client.analyze_pod_events`` renders, the ``ocp_metadata`` dict ``ocp_client.get_pod_metadata``
returns (what ``ocp_enrichment.stage_ocp_metadata`` stores), the trace ``reconstruct_distributed_trace`` returns
(sanitized like the log stage stores it), the ``app_metrics`` block ``_metrics_block_from_answer`` builds, and the
``ai_analysis.evidence_references`` items ``agent._tool_observation_references`` builds from a tool step. So the
timeline is tested on what the analysis really stores, not on a hand-written guess of it.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from types import SimpleNamespace
from typing import Any

import httpx

from app.agents.log_intelligence_agent import _metrics_block_from_answer
from app.core.config import settings
from app.services import ocp_client, url_safety
from app.services.agent import _tool_observation_references
from app.services.evidence_sanitizer import sanitize_persistence_payload
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.tools import query_splunk, reconstruct_trace
from app.tools.investigation_context import bind_runtime_integrations, reset_runtime_integrations
from tests.fakes.fake_integrations import FakeState, create_app

TS = "2026-10-20T10:00:00Z"
POD, NAMESPACE = "payment-runner-7d9f", "qa-payments"
QUIET_POD, QUIET_NAMESPACE = "quiet-runner-1a2b", "qa-testing"
SPLUNK_TOOL, OCP_TOOL = "query_splunk_logs", "analyze_openshift_pod_events"
#: What the fake's pod would leak if the timeline ever read ``node`` out (its hostIP and node name).
POD_HOST_IP, POD_NODE = "10.0.4.12", "worker-2"


#: The real resolver, captured before any test patches ``socket.getaddrinfo`` (the patch is process-wide:
#: ``url_safety.socket`` IS the ``socket`` module).
_REAL_GETADDRINFO = socket.getaddrinfo
FAKE_HOST_SUFFIX = ".fake.test"


def _resolver():
    """``socket.getaddrinfo`` for the egress check: a fake host name (``*.fake.test``) never resolves (so it is not
    refused as private); an IP literal resolves to itself WITH its port; EVERY OTHER name goes to the real resolver —
    the patch is process-wide, and the integration test's real stores are ``localhost`` on CI (``127.0.0.1``
    locally), so blocking every name broke their connections on CI only."""
    def getaddrinfo(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name.endswith(FAKE_HOST_SUFFIX):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        try:
            ipaddress.ip_address(name)
        except ValueError:
            return _REAL_GETADDRINFO(host, port, *args, **kwargs)
        family = socket.AF_INET6 if ":" in name else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (name, int(port or 0)))]

    return getaddrinfo


def wire_fake(monkeypatch) -> FakeState:
    """The fake behind the fetchers' HTTP client; offline mode OFF only while the samples are produced."""
    state = FakeState()
    state.ocp_pods[QUIET_NAMESPACE][QUIET_POD] = {
        "phase": "Running", "image": "registry.fake.test/qa/quiet:1.0", "limits": {}, "node": "worker-1",
        "host_ip": "10.0.4.13", "events": [],
    }
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    for module in (query_splunk, reconstruct_trace, ocp_client):
        monkeypatch.setattr(module, "get_http_client", lambda: client)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _resolver())
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    return state


def config(state: FakeState, *, index: str = "payments") -> IntegrationRuntimeConfig:
    return IntegrationRuntimeConfig(
        project_id=None, splunk_enabled=True, splunk_base_url="http://splunk.fake.test:8089", splunk_index=index,
        splunk_index_source="project", ocp_enabled=True, ocp_api_url="http://ocp.fake.test:6443",
        ocp_default_namespace=NAMESPACE, ocp_namespace_source="project", splunk_api_token=state.splunk_token,
        ocp_sa_token=state.ocp_token,
    )


def references(tool: str, observation: str) -> list[dict[str, Any]]:
    """The ``evidence_references`` items the analysis stores for one executed tool step."""
    return _tool_observation_references([(SimpleNamespace(tool=tool), observation)])


async def splunk_text(cfg: IntegrationRuntimeConfig, service: str = "checkout") -> str:
    return await query_splunk.splunk_correlation(cfg, service, TS)


async def ocp_text(cfg: IntegrationRuntimeConfig, pod: str = POD, namespace: str = NAMESPACE) -> str:
    return await ocp_client.analyze_pod_events(pod, namespace, TS, config=cfg)


async def ocp_metadata(cfg: IntegrationRuntimeConfig, pod: str = POD, namespace: str = NAMESPACE) -> dict:
    meta = await ocp_client.get_pod_metadata(pod, namespace, config=cfg)
    assert meta is not None, "the fake did not answer the pod lookup"
    return meta


async def distributed_trace(cfg: IntegrationRuntimeConfig, services: tuple[str, ...] = ("checkout",)) -> dict:
    token = bind_runtime_integrations(cfg)
    try:
        raw = await reconstruct_trace.reconstruct_distributed_trace.ainvoke({"params_json": json.dumps({
            "timestamp_utc": TS, "services": list(services), "window_seconds": 45,
        })})
    finally:
        reset_runtime_integrations(token)
    return sanitize_persistence_payload(json.loads(raw))[0]


def metric_points(start: float = 1_792_404_000.0, count: int = 8, step: float = 60.0, low: float = 0.2,
                  high: float = 0.62) -> list[list[float]]:
    """``[[epoch_seconds, value]]`` rising from ``low`` to ``high`` (the fake's checkout p95 shape)."""
    return [[start + i * step, low if i < count // 2 else high] for i in range(count)]


def app_metrics(outcome: str = "ok", sentence: str = "", series: list[dict] | None = None,
                service: str = "checkout") -> dict:
    """The stored ``app_metrics`` block, built by the log stage's own builder from a tool-shaped answer."""
    return _metrics_block_from_answer({"outcome": outcome, "sentence": sentence, "service": service,
                                       "series": series or []})


def p95_series(points: list[list[float]] | None = None, *, template_id: str = "http_p95_latency",
               label: str = "p95 latency") -> dict:
    return {"template_id": template_id, "label": label, "unit": "seconds", "points": points or metric_points(),
            "baseline_mean": 0.2, "window_mean": 0.62, "window_max": 0.62, "ratio": 3.1, "rose": True,
            "sentence": f"{label} for checkout rose 3.1× in the failure window"}
