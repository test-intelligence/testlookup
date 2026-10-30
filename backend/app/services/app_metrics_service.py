"""W23d AGT-15: template-only Prometheus range queries for the system under test.

The ONLY outbound call AGT-15 adds, gated in this order (contract §1.4, D19):

1. ``cfg.prometheus_unavailable_reason()`` -- ``AI_OFFLINE_MODE`` read LIVE
   (no offline exception, D19), then the saved ``prometheus_enabled`` switch,
   then a saved base URL. The block is SAVED-ONLY: the process
   ``PROMETHEUS_URL`` is TestLookup's own Performance-page Prometheus and is
   never a fallback here.
2. The service name must match ``SERVICE_RE`` (no space, no quote, <= 128
   characters) -- otherwise no query -- and is THEN escaped as a PromQL label
   value and substituted LITERALLY for ``{service}`` in ADMIN-written
   templates. No PromQL ever comes from a caller: callers pass template ids.
3. ``egress_block_reason(base_url)`` (loopback / link-local / unspecified
   refused; RFC1918 allowed -- the Splunk rule).
4. The shared client (``follow_redirects=False``), ``QUERY_TIMEOUT_SECONDS``.

A timeout is ``timed_out`` -- never ``no_data`` and never a failure of the
caller. No string Prometheus returns is kept (label values are dropped):
only numbers, template ids / labels and the regex-checked service name leave
this module. Log lines carry outcomes, counts and HTTP statuses -- never a
URL, a service name, a PromQL string or the token. No session, no commit
(``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import httpx
import structlog

from app.core.http_client import get_http_client
from app.services import prometheus_vocab as pv
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.services.url_safety import egress_block_reason

_slog = structlog.get_logger(__name__)

_SERVICE_RE = re.compile(pv.SERVICE_RE)
#: A Prometheus answer larger than this is not parsed (≤ 120 points per series is a few KiB).
_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
#: A transport error that is not a timeout (connection refused, TLS failure): no HTTP status to name.
UNREACHABLE_SENTENCE = "metrics unavailable: Prometheus could not be reached"
#: A metric that was 0 before the failure window and above 0 inside it has no finite ratio.
ROSE_FROM_ZERO_SENTENCE = "{label} for {service} rose from 0 in the failure window"


@dataclass(frozen=True)
class MetricSeries:
    template_id: str
    label: str
    unit: str
    points: tuple[tuple[float, Optional[float]], ...]   # (epoch seconds, value | None), oldest first, <= POINTS_MAX
    baseline_mean: Optional[float]
    window_mean: Optional[float]
    window_max: Optional[float]
    ratio: Optional[float]
    sentence: str
    rose: bool = False


@dataclass(frozen=True)
class MetricsOutcome:
    outcome: str                                        # prometheus_vocab.OUTCOMES
    sentence: str
    service: str                                        # "" unless it matched SERVICE_RE
    window: tuple[datetime, datetime]                   # the queried range (start, end)
    series: tuple[MetricSeries, ...]
    http_status: Optional[int] = None


class TemplateRequestError(ValueError):
    """A template request the caller must correct (``TOOL_ERRORS``): an unknown id, too many ids."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def promql_label_value(value: str) -> str:
    """Escape a string for a PromQL label matcher (moved from ``fetch_app_metrics._promql_label_value``)."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def valid_service(service: str) -> bool:
    """Whether ``service`` may be substituted into a template (``SERVICE_RE``)."""
    return isinstance(service, str) and _SERVICE_RE.fullmatch(service) is not None


def outcome_sentence(outcome: str, *, service: str = "", status: Optional[int] = None) -> str:
    """The stage's sentence for a non-``ok`` outcome (``OUTCOME_SENTENCES``)."""
    template = pv.OUTCOME_SENTENCES.get(outcome)
    if template is None:
        return f"metrics unavailable: {outcome}"
    return template.format(service=service, status=status if status is not None else "")


def query_window(at: datetime, window_minutes: int) -> tuple[datetime, datetime]:
    """(start, end): ``at - 2·window`` .. ``at + 2 min`` (the earlier half is the baseline)."""
    at_utc = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
    return at_utc - timedelta(minutes=2 * window_minutes), at_utc + timedelta(minutes=2)


def configured_templates(cfg: IntegrationRuntimeConfig) -> tuple[Mapping[str, str], ...]:
    """The ADMIN-saved templates, else the defaults (the resolver already fills the defaults)."""
    return tuple(cfg.prometheus_templates) or tuple(pv.DEFAULT_METRIC_TEMPLATES)


def select_templates(cfg: IntegrationRuntimeConfig, template_ids: Sequence[str]) -> list[Mapping[str, str]]:
    """The templates for ``template_ids`` in order (none given → the first ``TEMPLATES_PER_CALL_MAX``).

    Raises ``TemplateRequestError`` for more than ``TEMPLATES_PER_CALL_MAX`` ids or an unknown id."""
    available = configured_templates(cfg)
    if not template_ids:
        return list(available[: pv.TEMPLATES_PER_CALL_MAX])
    if len(template_ids) > pv.TEMPLATES_PER_CALL_MAX:
        raise TemplateRequestError("too_many_templates", pv.TOOL_ERRORS["too_many_templates"])
    by_id = {str(item.get("id")): item for item in available}
    chosen: list[Mapping[str, str]] = []
    for template_id in template_ids:
        item = by_id.get(str(template_id))
        if item is None:
            shown = str(template_id)[:40]
            raise TemplateRequestError(
                "unknown_template", pv.TOOL_ERRORS["unknown_template"].format(template_id=shown))
        if item not in chosen:
            chosen.append(item)
    return chosen


def _number(raw: Any) -> Optional[float]:
    """A Prometheus sample value ("0.62", "NaN", "+Inf") as a finite float, else None."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _points(values: Any) -> tuple[tuple[float, Optional[float]], ...]:
    """The first series' ``values`` as (epoch, value) pairs, oldest first, downsampled to <= POINTS_MAX."""
    if not isinstance(values, list):
        return ()
    parsed: list[tuple[float, Optional[float]]] = []
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        stamp = _number(pair[0])
        if stamp is None:
            continue
        parsed.append((stamp, _number(pair[1])))
    parsed.sort(key=lambda item: item[0])
    if len(parsed) > pv.POINTS_MAX:
        # Evenly spaced, both ends kept (indices are distinct because len > POINTS_MAX).
        last = len(parsed) - 1
        parsed = [parsed[round(i * last / (pv.POINTS_MAX - 1))] for i in range(pv.POINTS_MAX)]
    return tuple(parsed)


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def summarise_series(template: Mapping[str, str], service: str, points: tuple[tuple[float, Optional[float]], ...],
                     *, window_start: float) -> MetricSeries:
    """Baseline = points before ``window_start`` (``at - window``), window = the rest; ratio and sentence."""
    label = str(template.get("label") or template.get("id"))
    baseline = [v for t, v in points if t < window_start and v is not None]
    window = [v for t, v in points if t >= window_start and v is not None]
    baseline_mean, window_mean = _mean(baseline), _mean(window)
    window_max = round(max(window), 6) if window else None
    ratio: Optional[float] = None
    rose = False
    if baseline_mean is None or window_mean is None:
        sentence = pv.FINDING_SENTENCES["no_baseline"].format(label=label, service=service)
    elif baseline_mean > 0:
        ratio = round(window_mean / baseline_mean, 3)
        rose = ratio >= pv.RATIO_ROSE
        key = "rose" if rose else "steady"
        shown = ratio if rose else max(ratio, 1.0)
        sentence = pv.FINDING_SENTENCES[key].format(label=label, service=service, ratio=f"{shown:.1f}")
    elif window_mean > 0:
        rose = True
        sentence = ROSE_FROM_ZERO_SENTENCE.format(label=label, service=service)
    else:
        ratio = 1.0
        sentence = pv.FINDING_SENTENCES["steady"].format(label=label, service=service, ratio="1.0")
    return MetricSeries(
        template_id=str(template.get("id")), label=label, unit=str(template.get("unit") or "count"),
        points=points, baseline_mean=baseline_mean, window_mean=window_mean, window_max=window_max,
        ratio=ratio, sentence=sentence, rose=rose,
    )


def _step_seconds(start: datetime, end: datetime) -> int:
    return max(15, math.ceil((end - start).total_seconds() / pv.POINTS_MAX))


def _refused(outcome: str, service: str, window: tuple[datetime, datetime],
             series: tuple[MetricSeries, ...] = (), *, status: Optional[int] = None,
             sentence: Optional[str] = None) -> MetricsOutcome:
    return MetricsOutcome(
        outcome=outcome, sentence=sentence or outcome_sentence(outcome, service=service, status=status),
        service=service, window=window, series=series, http_status=status,
    )


def unavailable(outcome: str, *, service: str, at: datetime, window_minutes: int) -> MetricsOutcome:
    """A non-``ok`` outcome decided by the CALLER with no query (``config_unavailable``, ``tool_not_allowed``,
    ``no_context``): the same shape, the vocabulary sentence, the service only when it matched ``SERVICE_RE``."""
    shown = service if valid_service(service) else ""
    minutes = max(1, min(int(window_minutes), pv.WINDOW_MINUTES_MAX))
    return _refused(outcome, shown, query_window(at, minutes))


def _finished(outcome: MetricsOutcome, *, templates: int) -> MetricsOutcome:
    # Outcome, counts and the HTTP status only: never the URL, the service, a PromQL string or the token.
    _slog.info("prometheus_query", outcome=outcome.outcome, templates=templates, series=len(outcome.series),
               http_status=outcome.http_status)
    return outcome


async def query_templates(cfg: IntegrationRuntimeConfig, *, service: str, at: datetime, window_minutes: int,
                          template_ids: Sequence[str]) -> MetricsOutcome:
    """Query each chosen template for ``service`` around ``at`` (contract §1.4, in that order).

    Raises ``TemplateRequestError`` (unknown id / too many ids) -- the caller's
    input to correct; every other condition is a ``MetricsOutcome``.
    """
    minutes = max(1, min(int(window_minutes), pv.WINDOW_MINUTES_MAX))
    window = query_window(at, minutes)
    reason = cfg.prometheus_unavailable_reason()
    if reason is not None:
        return _finished(_refused(reason, service if valid_service(service) else "", window), templates=0)
    if not valid_service(service):
        return _finished(_refused("invalid_service", "", window), templates=0)
    templates = select_templates(cfg, template_ids)
    base = (cfg.prometheus_base_url or "").strip().rstrip("/")
    if await egress_block_reason(base):
        return _finished(_refused("blocked_target", service, window), templates=0)

    start, end = window
    step = _step_seconds(start, end)
    window_start = (end - timedelta(minutes=2) - timedelta(minutes=minutes)).timestamp()
    headers = {"Authorization": f"Bearer {cfg.prometheus_bearer_token}"} if cfg.prometheus_bearer_token else {}
    label_value = promql_label_value(service)
    client = get_http_client()
    series: list[MetricSeries] = []
    for template in templates:
        query = str(template.get("promql") or "").replace(pv.SERVICE_PLACEHOLDER, label_value)
        try:
            resp = await client.get(
                f"{base}/api/v1/query_range",
                params={"query": query, "start": f"{start.timestamp():.3f}", "end": f"{end.timestamp():.3f}",
                        "step": f"{step}s"},
                headers=headers,
                timeout=pv.QUERY_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            # A timeout is not a failure and not "no data": say so, and ask no further template.
            return _finished(_refused("timed_out", service, window, tuple(series)), templates=len(templates))
        except Exception:  # noqa: BLE001 - a transport error is an outcome, never an exception out of the stage
            return _finished(_refused("http_error", service, window, tuple(series), sentence=UNREACHABLE_SENTENCE),
                             templates=len(templates))
        if resp.status_code in (401, 403):
            return _finished(_refused("auth_error", service, window, tuple(series), status=resp.status_code),
                             templates=len(templates))
        body: Any = None
        if resp.status_code == 200 and len(resp.content) <= _RESPONSE_MAX_BYTES:
            try:
                body = resp.json()
            except ValueError:
                body = None
        if not isinstance(body, dict) or body.get("status") != "success":
            return _finished(_refused("http_error", service, window, tuple(series), status=resp.status_code),
                             templates=len(templates))
        data = body.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        first = result[0] if isinstance(result, list) and result and isinstance(result[0], dict) else None
        # The first series only (templates aggregate); its labels are DROPPED -- no Prometheus string is kept.
        points = _points(first.get("values")) if first is not None else ()
        if not any(value is not None for _, value in points):
            continue
        series.append(summarise_series(template, service, points, window_start=window_start))

    if not series:
        return _finished(_refused("no_data", service, window), templates=len(templates))
    sentence = "; ".join(dict.fromkeys(item.sentence for item in series))
    return _finished(MetricsOutcome(outcome="ok", sentence=sentence, service=service, window=window,
                                    series=tuple(series)), templates=len(templates))


def outcome_as_dict(outcome: MetricsOutcome) -> dict[str, Any]:
    """The JSON shape the tool answers and the log stage stores (numbers, ids, labels, sentences only)."""
    return {
        "outcome": outcome.outcome,
        "sentence": outcome.sentence,
        "service": outcome.service,
        "time_window": {"start": outcome.window[0].isoformat(), "end": outcome.window[1].isoformat()},
        "http_status": outcome.http_status,
        "series": [
            {
                "template_id": item.template_id, "label": item.label, "unit": item.unit,
                "points": [[stamp, value] for stamp, value in item.points],
                "baseline_mean": item.baseline_mean, "window_mean": item.window_mean,
                "window_max": item.window_max, "ratio": item.ratio, "rose": item.rose, "sentence": item.sentence,
            }
            for item in outcome.series
        ],
    }
