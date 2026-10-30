"""W23d AGT-15: ``app_metrics_service.query_templates`` -- the one Prometheus egress, template-only.

Each refusal is proven with a transport that FAILS the test on any request
(offline, disabled, not configured, a service outside SERVICE_RE, a loopback
host), so "no call" is observed, not assumed. Timestamps are built inside the
tests.
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import settings
from app.services import app_metrics_service as ams
from app.services import prometheus_vocab as pv
from app.services import url_safety
from app.services.integration_config_service import IntegrationRuntimeConfig

BASE = "http://prometheus.metrics.test:9090"
TOKEN = "prom_w23dSampleBearerValueQx7Lm"


def _cfg(**overrides) -> IntegrationRuntimeConfig:
    values = dict(
        project_id=None, splunk_enabled=False, splunk_base_url=None, splunk_index="", splunk_index_source="environment",
        ocp_enabled=False, ocp_api_url=None, ocp_default_namespace=None, ocp_namespace_source="environment",
        prometheus_enabled=True, prometheus_base_url=BASE,
        prometheus_templates=tuple(dict(t) for t in pv.DEFAULT_METRIC_TEMPLATES), prometheus_bearer_token=TOKEN,
    )
    values.update(overrides)
    return IntegrationRuntimeConfig(**values)


class _Recorder:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def info(self, event, **kwargs):
        self.calls.append((event, kwargs))

    warning = debug = info


@pytest.fixture
def world(monkeypatch):
    """A MockTransport behind the shared client; DNS answers nothing (a test host is never resolved)."""
    seen: list[httpx.Request] = []
    script: dict = {"handler": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if script["handler"] is None:
            pytest.fail(f"unexpected Prometheus request: {request.method} {request.url.path}")
        return script["handler"](request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ams, "get_http_client", lambda: client)

    def getaddrinfo(host, *_a, **_k):
        if host == "127.0.0.1":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    recorder = _Recorder()
    monkeypatch.setattr(ams, "_slog", recorder)
    return type("World", (), {"seen": seen, "script": script, "log": recorder})


def _matrix(values: list[list]) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"service": "checkout", "pod": "w23dLabelSentinelPod"}, "values": values},
    ]}}


def _rising_series(request: httpx.Request) -> httpx.Response:
    """0.20 before at-15m, 0.62 after: ratio 3.1."""
    query = parse_qs(urlsplit(str(request.url)).query)
    start, end = float(query["start"][0]), float(query["end"][0])
    at = end - 120
    values = []
    for k in range(int((end - start) // 8) + 1):     # every 8 s: ~240 samples, more than POINTS_MAX
        t = start + 8 * k
        values.append([t, "0.62" if t >= at - 15 * 60 else "0.20"])
    return httpx.Response(200, json=_matrix(values))


async def _query(cfg=None, *, service="checkout", template_ids=("http_p95_latency",), at=None):
    return await ams.query_templates(
        cfg or _cfg(), service=service, at=at or datetime.now(timezone.utc) - timedelta(minutes=5),
        window_minutes=15, template_ids=list(template_ids),
    )


# ── Refusals: no request at all ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_mode_refuses_before_any_request(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    result = await _query()
    assert result.outcome == "offline_mode"
    assert result.sentence == "Prometheus lookup disabled: offline mode"
    assert world.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides, outcome", [
    ({"prometheus_enabled": False}, "disabled"),
    ({"prometheus_base_url": None}, "not_configured"),
    ({"prometheus_base_url": "   "}, "not_configured"),
])
async def test_disabled_or_unconfigured_makes_no_request(world, overrides, outcome):
    result = await _query(_cfg(**overrides))
    assert (result.outcome, result.sentence) == (outcome, pv.OUTCOME_SENTENCES[outcome])
    assert world.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ['checkout"} or vector(1) #', "check\\out", "check\nout", "two words", "", "x" * 200])
async def test_a_service_outside_service_re_makes_no_request(world, service):
    result = await _query(service=service)
    assert result.outcome == "invalid_service"
    assert result.service == ""
    assert world.seen == []


def test_the_label_value_escape_handles_quote_backslash_and_newline():
    assert ams.promql_label_value('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


@pytest.mark.asyncio
async def test_a_loopback_base_url_is_blocked_before_the_token_is_sent(world):
    result = await _query(_cfg(prometheus_base_url="http://127.0.0.1:9090"))
    assert result.outcome == "blocked_target"
    assert result.sentence == "metrics unavailable: the Prometheus host is not allowed"
    assert world.seen == []


@pytest.mark.asyncio
async def test_unknown_and_too_many_templates_are_refused_before_any_request(world):
    with pytest.raises(ams.TemplateRequestError) as unknown:
        await _query(template_ids=("http_p95_latency", "not_a_template"))
    assert unknown.value.code == "unknown_template"
    assert unknown.value.message == "Unknown metric template: not_a_template."
    with pytest.raises(ams.TemplateRequestError) as many:
        await _query(template_ids=("a1", "a2", "a3", "a4", "a5"))
    assert many.value.code == "too_many_templates"
    assert world.seen == []


# ── The query and its answer ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rising_series_reads_rose_3_1x_and_keeps_no_prometheus_string(world):
    world.script["handler"] = _rising_series
    result = await _query()
    assert result.outcome == "ok"
    (series,) = result.series
    assert series.sentence == "p95 latency for checkout rose 3.1× in the failure window"
    assert series.ratio == pytest.approx(3.1, abs=0.01)
    assert series.rose is True
    assert len(series.points) == pv.POINTS_MAX                    # ~240 samples downsampled, both ends kept
    # The label values Prometheus returned are dropped: no string from the answer survives.
    assert "w23dLabelSentinelPod" not in repr(ams.outcome_as_dict(result))
    (request,) = world.seen
    assert f"{request.url.scheme}://{request.url.host}:{request.url.port}" == BASE
    assert request.url.path == "/api/v1/query_range"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    query = parse_qs(urlsplit(str(request.url)).query)
    assert query["query"] == [pv.DEFAULT_METRIC_TEMPLATES[0]["promql"].replace("{service}", "checkout")]
    start, end = float(query["start"][0]), float(query["end"][0])
    assert end - start == pytest.approx(32 * 60, abs=1)          # at - 2·15 min .. at + 2 min
    assert query["step"] == ["16s"]                               # ceil(1920 / 120)


@pytest.mark.asyncio
async def test_the_service_is_substituted_literally_and_escaped(world):
    world.script["handler"] = _rising_series
    template = {"id": "custom_rate", "label": "Custom", "unit": "count", "promql": 'up{job="{service}"} + on() {x}'}
    await _query(_cfg(prometheus_templates=(template,)), service="pay-ments.v2:eu", template_ids=("custom_rate",))
    query = parse_qs(urlsplit(str(world.seen[0].url)).query)["query"][0]
    assert query == 'up{job="pay-ments.v2:eu"} + on() {x}'     # every other brace is PromQL's


@pytest.mark.asyncio
async def test_no_token_means_no_authorization_header(world):
    world.script["handler"] = _rising_series
    await _query(_cfg(prometheus_bearer_token=None))
    assert "Authorization" not in world.seen[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_token_is_auth_error(world, status):
    world.script["handler"] = lambda _r: httpx.Response(status, json={"status": "error"})
    result = await _query()
    assert (result.outcome, result.http_status) == ("auth_error", status)
    assert result.sentence == f"metrics unavailable: Prometheus refused the token (HTTP {status})"


@pytest.mark.asyncio
async def test_a_timeout_is_timed_out_never_no_data_and_stops(world):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    world.script["handler"] = slow
    result = await _query(template_ids=("http_p95_latency", "http_5xx_rate"))
    assert result.outcome == "timed_out"
    assert result.sentence == "metrics unavailable: Prometheus timed out"
    assert len(world.seen) == 1, "no further template after a timeout"


@pytest.mark.asyncio
async def test_http_errors_and_unreachable_hosts_are_outcomes(world):
    world.script["handler"] = lambda _r: httpx.Response(500, text="boom")
    failed = await _query()
    assert (failed.outcome, failed.sentence) == ("http_error", "metrics unavailable: Prometheus answered HTTP 500")
    world.script["handler"] = lambda _r: httpx.Response(200, json={"status": "error", "error": "bad"})
    assert (await _query()).outcome == "http_error"

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    world.script["handler"] = refused
    unreachable = await _query()
    assert (unreachable.outcome, unreachable.sentence) == ("http_error", ams.UNREACHABLE_SENTENCE)


@pytest.mark.asyncio
async def test_an_empty_answer_is_no_data_for_the_service(world):
    world.script["handler"] = lambda _r: httpx.Response(200, json={"status": "success", "data": {"result": []}})
    result = await _query()
    assert (result.outcome, result.sentence) == ("no_data", "No metric data for checkout in the failure window.")


def test_steady_no_baseline_and_rose_from_zero_sentences():
    template = {"id": "t_x", "label": "5xx error rate", "unit": "per_second"}
    steady = ams.summarise_series(template, "checkout", ((0.0, 1.0), (10.0, 1.1)), window_start=5.0)
    assert steady.sentence == "5xx error rate for checkout stayed within 1.1× of its baseline in the failure window"
    none_before = ams.summarise_series(template, "checkout", ((10.0, 1.0),), window_start=5.0)
    assert none_before.sentence == "5xx error rate for checkout: no data before the failure window to compare with"
    from_zero = ams.summarise_series(template, "checkout", ((0.0, 0.0), (10.0, 0.4)), window_start=5.0)
    assert (from_zero.rose, from_zero.ratio) == (True, None)
    assert from_zero.sentence == "5xx error rate for checkout rose from 0 in the failure window"


@pytest.mark.asyncio
async def test_log_lines_carry_no_token_url_service_or_promql(world):
    world.script["handler"] = _rising_series
    await _query()
    assert world.log.calls, "the query logs its outcome"
    rendered = repr(world.log.calls)
    for secret in (TOKEN, BASE, "prometheus.metrics.test", "checkout", "histogram_quantile"):
        assert secret not in rendered
    assert TOKEN not in repr(_cfg())
