"""The Prometheus application-metrics vocabulary (W23d AGT-15), in ONE place.

The deep investigation's log stage may read the system-under-test's metrics
from a Prometheus an ADMIN configures under Settings › Integrations. The
model never writes PromQL: it picks admin-defined TEMPLATES by id, and the
server substitutes ``{service}`` with an escaped label value after a
``SERVICE_RE`` check. Every query is refused under ``AI_OFFLINE_MODE=true``
(D19, like Splunk / OpenShift), and a timeout reads ``timed_out``, never
"no data". The block is SAVED-ONLY: the environment ``PROMETHEUS_URL`` is
TestLookup's own self-monitoring Prometheus and is never a fallback.

``frontend/src/types/prometheus.ts`` mirrors these constants;
``tests/test_payload_metrics_vocab_parity.py`` pins the copies equal.

Absence is not health: an unconfigured Prometheus is ``not_configured``,
never "no anomalies".

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

SETTINGS_KEYS = ("prometheus_enabled", "prometheus_base_url", "prometheus_metric_templates")   # in app_settings "integrations_config"
SECRET_KEY = "prometheus_bearer_token"          # secret_refs scope "integrations_config" (Vault references allowed, W20)
SERVICE_PLACEHOLDER = "{service}"               # replaced LITERALLY; every other brace is PromQL's
SERVICE_RE = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"   # a service name outside this is refused (no query), THEN escaped
TEMPLATE_ID_RE = r"^[a-z][a-z0-9_]{1,39}$"
TEMPLATES_MAX, TEMPLATE_LABEL_MAX, TEMPLATE_PROMQL_MAX = 12, 80, 1_000
TEMPLATE_UNITS = ("seconds", "bytes", "ratio", "per_second", "count")
DEFAULT_METRIC_TEMPLATES = (                    # used while none are saved ("templates_source": "default")
    {"id": "http_p95_latency", "label": "p95 latency", "unit": "seconds",
     "promql": 'histogram_quantile(0.95, sum by (le) (rate(http_server_requests_seconds_bucket{service="{service}"}[1m])))'},
    {"id": "http_5xx_rate", "label": "5xx error rate", "unit": "per_second",
     "promql": 'sum(rate(http_server_requests_seconds_count{service="{service}",status=~"5.."}[1m]))'},
    {"id": "memory_working_set", "label": "Memory working set", "unit": "bytes",
     "promql": 'sum(container_memory_working_set_bytes{container="{service}"})'},
    {"id": "cpu_usage", "label": "CPU usage", "unit": "ratio",
     "promql": 'sum(rate(container_cpu_usage_seconds_total{container="{service}"}[1m]))'},
)   # the four series today's tool queries by default (fetch_app_metrics.py:149-154), p99 → p95 for the acceptance sentence
QUERY_TIMEOUT_SECONDS = 10.0
WINDOW_MINUTES_DEFAULT, WINDOW_MINUTES_MAX = 15, 24 * 60      # the existing caps (fetch_app_metrics.py:16)
TEMPLATES_PER_CALL_MAX = 4                     # the stage asks for at most 4 templates per cluster
POINTS_MAX = 120                               # step = ceil(range / POINTS_MAX), at least 15 s; the spark draws these
CLUSTERS_QUERIED_MAX = 3                       # of the stage's ≤ 5 contexts, the first 3 are queried (≤ 12 range queries)
RATIO_ROSE = 1.5                               # failure-window mean / baseline mean ≥ this → "rose"
OUTCOMES = ("ok", "no_data", "timed_out", "http_error", "auth_error", "offline_mode", "disabled", "not_configured",
            "blocked_target", "invalid_service", "tool_not_allowed", "no_context", "config_unavailable")
OUTCOME_SENTENCES = {                           # the stage's degrade sentences (UI copy); {status} = the HTTP status
    "offline_mode": "Prometheus lookup disabled: offline mode",
    "disabled": "metrics unavailable: the Prometheus integration is switched off",
    "not_configured": "metrics unavailable: Prometheus is not configured",
    "blocked_target": "metrics unavailable: the Prometheus host is not allowed",
    "timed_out": "metrics unavailable: Prometheus timed out",
    "http_error": "metrics unavailable: Prometheus answered HTTP {status}",
    "auth_error": "metrics unavailable: Prometheus refused the token (HTTP {status})",
    "invalid_service": "metrics unavailable: the service name is not a valid label value",
    "tool_not_allowed": "metrics unavailable: the frozen agent configuration does not allow fetch_app_metrics",
    "no_context": "metrics unavailable: the failures name no service and failure time",
    "config_unavailable": "metrics unavailable: the integration settings could not be read",   # runtime_integrations_for → None
    "no_data": "No metric data for {service} in the failure window.",
}
FINDING_SENTENCES = {
    "rose": "{label} for {service} rose {ratio}× in the failure window",          # ratio 1 decimal, e.g. 3.1
    "steady": "{label} for {service} stayed within {ratio}× of its baseline in the failure window",
    "no_baseline": "{label} for {service}: no data before the failure window to compare with",
}
TOOL_ERRORS = {                                # the tool's {"error": code, "message": …} answers
    "raw_promql_refused": "Raw PromQL is not accepted: pass template_ids from the configured metric templates.",
    "unknown_template": "Unknown metric template: {template_id}.",
    "too_many_templates": "At most 4 template_ids per call.",
    "invalid_timestamp": "timestamp_utc must be an ISO-8601 time, for example 2026-10-30T09:15:00Z.",
}
TEMPLATE_ERRORS = {                            # PUT /settings/integrations 422 sentences (loc prometheus_metric_templates.N.field)
    "placeholder": "Each template must contain {service} exactly where the service name goes.",
    "duplicate_id": "Template ids must be unique.",
    "too_many": "At most 12 metric templates.",
}
PROBE_SENTENCES = {"healthy": "Prometheus API reachable", "skipped_off": "Prometheus is switched off or has no URL"}
