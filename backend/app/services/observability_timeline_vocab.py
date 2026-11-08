"""The per-run observability evidence timeline vocabulary (W39 STRAT-07 first slice), in ONE place.

``GET /api/v1/runs/{run_id}/observability-timeline`` (``routers/run_observability.py``,
``services/run_observability_service.py``) shows the Splunk / OpenShift / Prometheus evidence the AI analysis
ALREADY fetched, read from what is stored: nothing is fetched when someone opens it. Five lanes, each naming where
its evidence came from (contract D-2), each with an honest state in words (D-4), never an empty chart.

``frontend/src/types/observabilityTimeline.ts`` mirrors these constants;
``tests/test_observability_timeline_vocab_parity.py`` pins the copies equal.

Never fabricate a time (D-3): ``parse_evidence_time`` reads ISO 8601 (with or without a zone or fractional
seconds), Splunk's ``%m/%d/%Y:%H:%M:%S`` and epoch seconds; anything else -- a redacted ``[REDACTED_PHONE]``, a
``?``, an empty string, a cut line -- is ``None`` and the item goes to its lane's untimed list. A zone-less time is
read as UTC and says so (``zone_assumed``): the fetchers build their Splunk windows from the UTC wall clock
(``tools/query_splunk.py`` formats ``fail_time ± 5 min`` without a zone; ``tools/reconstruct_trace.py`` formats
``ts ± window`` the same way).

Constants and one pure parser only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

# ── Lanes (D-2 order: the response always carries all five, in this order) ─────────────────────────────────────
LANE_SOURCES = ("prometheus", "splunk_trace", "ocp_events", "splunk_per_test", "ocp_per_test")
LANE_STATES = ("evidence", "none_found", "not_recorded", "not_analysed", "unavailable")
FETCHED_BY = ("analysis", "ingest", "per_test_analysis")
UNTIMED_REASONS = ("no_time_stored", "time_unreadable")

LANE_LABELS = {
    "prometheus": "Prometheus metrics",
    "splunk_trace": "Splunk trace (deep analysis)",
    "ocp_events": "OpenShift pod events (at ingest)",
    "splunk_per_test": "Splunk logs (per-test analysis)",
    "ocp_per_test": "OpenShift pod checks (per-test analysis)",
}
LANE_FETCHED_BY = {
    "prometheus": "analysis",
    "splunk_trace": "analysis",
    "ocp_events": "ingest",
    "splunk_per_test": "per_test_analysis",
    "ocp_per_test": "per_test_analysis",
}
FETCHED_BY_LABELS = {                                        # the lane's chip
    "analysis": "Fetched by the deep analysis",
    "ingest": "Fetched at ingest",
    "per_test_analysis": "Fetched by the per-test analysis",
}
UNTIMED_REASON_LABELS = {
    "no_time_stored": "no time was stored",
    "time_unreadable": "the stored time could not be read",
}
#: ``configured_now``'s keys (booleans only: never a token, URL or index) and how the "now" note names them.
CONFIGURED_SOURCES = ("splunk", "ocp", "prometheus")
CONFIGURED_LABELS = {
    "splunk": "Splunk",
    "ocp": "OpenShift",
    "prometheus": "Prometheus",
}

#: The ONLY ``ai_analysis.evidence_references[].source`` values that are evidence (the real ReAct tool names,
#: ``tools/query_splunk.py`` TOOL_NAME / ``services/agent.py``). Any other source -- the dev seed's fabricated
#: ``splunk`` / ``ocp_events`` rows -- is never shown.
PER_TEST_TOOL_SOURCES = {
    "splunk_per_test": "query_splunk_logs",
    "ocp_per_test": "analyze_openshift_pod_events",
}
TOOL_OBSERVATION_KIND = "tool_observation"

# ── How a stored answer reads (prefixes of the fetchers' own texts; the parity test pins them to the source) ────
SPLUNK_FOUND_PREFIX = "=== Splunk Log Correlation"           # == tools/query_splunk.py SPLUNK_FOUND_PREFIX
SPLUNK_NO_HITS_PREFIX = "No backend errors found in Splunk"  # tools/query_splunk.py splunk_correlation, no results
OCP_FOUND_PREFIX = "Pod: "                                   # == services/ocp_client.py OCP_FOUND_PREFIX
OCP_NO_EVENTS_PREFIX = "No events found for pod"             # services/ocp_client.py analyze_pod_events
TRACE_NO_EVENTS_PREFIX = "No log events found"               # tools/reconstruct_trace.py, an empty search

# ── D-7 bounds (a lane says ``truncated: true`` when it cut anything) ───────────────────────────────────────────
CLUSTERS_MAX = 5
SERIES_PER_CLUSTER_MAX = 4
POINTS_PER_SERIES_MAX = 120
TRACE_STEPS_PER_CLUSTER_MAX = 40
OCP_EVENTS_MAX = 200
PER_TEST_ITEMS_MAX = 200
PER_TEST_ANALYSES_MAX = 200                                  # analysed test cases read per lane query
TITLE_MAX = 300
DETAIL_MAX = 500
NOTES_MAX = 3                                                # distinct "no evidence" sentences appended to a lane

# ── Server-written lane sentences (D-4). ``<source>_<state>``; the extra keys say which honest case it is. ───────
LANE_SENTENCES = {
    "prometheus_evidence": "Application metrics the deep analysis read from Prometheus around the failures.",
    "prometheus_none_found": "Prometheus returned no metric data in the failure window.",
    "prometheus_unavailable": "The Prometheus look-up did not complete.",
    "prometheus_not_recorded": "The deep analysis stored no Prometheus metrics for this run.",
    "prometheus_not_analysed": "No deep analysis of this run has been stored, so there are no Prometheus metrics.",
    "splunk_trace_evidence": "Log events the deep analysis read from Splunk while it rebuilt the request trace.",
    "splunk_trace_no_events": (
        "Splunk returned no trace events for this run — the stored answer does not say whether Splunk was reachable."
    ),
    "splunk_trace_failed": "The Splunk trace look-up failed, so no trace events were stored.",
    "splunk_trace_unavailable": "The Splunk trace look-up did not complete.",
    "splunk_trace_not_recorded": "The deep analysis stored no Splunk trace for this run.",
    "splunk_trace_not_analysed": "No deep analysis of this run has been stored, so there is no Splunk trace.",
    "ocp_events_evidence": "Pod events OpenShift reported for this run's pod when the run was ingested.",
    "ocp_events_none_found": "OpenShift reported no events for this run's pod when the run was ingested.",
    "ocp_events_not_recorded": "This run was not enriched with OpenShift pod data when it was ingested.",
    "ocp_events_unavailable": "The OpenShift pod data stored for this run could not be read.",
    "splunk_per_test_evidence": "Splunk log lines the per-test AI analysis fetched, read from the excerpts it stored.",
    "splunk_per_test_none_found": "The per-test AI analysis searched Splunk and found no matching log lines.",
    "splunk_per_test_unavailable": "The per-test Splunk look-ups did not complete.",
    "splunk_per_test_not_recorded": "The per-test AI analysis of this run stored no Splunk look-up.",
    "splunk_per_test_not_analysed": "No test in this run has a per-test AI analysis yet.",
    "ocp_per_test_evidence": (
        "OpenShift pod checks the per-test AI analysis ran. Their stored answers carry no event times, "
        "so they are listed without a time."
    ),
    "ocp_per_test_none_found": "The per-test AI analysis checked OpenShift and found no pod events.",
    "ocp_per_test_unavailable": "The per-test OpenShift checks did not complete.",
    "ocp_per_test_not_recorded": "The per-test AI analysis of this run stored no OpenShift check.",
    "ocp_per_test_not_analysed": "No test in this run has a per-test AI analysis yet.",
    "store_unreadable": "The analysis store could not be read.",
    "partial": "Some look-ups stored no evidence:",
}

# ── Copy the card renders around the lanes (§2; the frontend file declares the same names) ──────────────────────
CARD_TITLE = "Observability evidence"   # the region name: never "Timeline" / "Evidence" alone (D-5)
HEADER_SENTENCE = "Evidence the AI analysis already fetched — nothing is fetched when you open this card."
OFFLINE_NOW_SENTENCE = "AI offline mode is on, so analyses fetch no observability evidence."   # labelled "now"
UNTIMED_HEADING = "Without a time"
TRUNCATED_SENTENCE = "Some items were left out to keep this lane readable."
ZONE_ASSUMED_SENTENCE = "No time zone was stored; read as UTC."
#: D-4's verbatim sentences, under the frontend's names (it renders them from ``lane.sentence``).
SPLUNK_TRACE_EMPTY_SENTENCE = LANE_SENTENCES["splunk_trace_no_events"]
OCP_NOT_ENRICHED_SENTENCE = LANE_SENTENCES["ocp_events_not_recorded"]
STORE_UNREADABLE_SENTENCE = LANE_SENTENCES["store_unreadable"]


# ── The ONE time parser (§1.3) ──────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidenceTime:
    """A stored time read as an aware UTC instant; ``zone_assumed`` when the text carried no zone."""

    at: datetime
    zone_assumed: bool


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?$")
_SPLUNK_RE = re.compile(r"^\d{2}/\d{2}/\d{4}:\d{2}:\d{2}:\d{2}$")
_EPOCH_RE = re.compile(r"^\d{9,10}(?:\.\d{1,9})?$")
EPOCH_MIN = 946_684_800      # 2000-01-01T00:00:00Z: an epoch outside [EPOCH_MIN, EPOCH_MAX) is not a time we read
EPOCH_MAX = 4_102_444_800    # 2100-01-01T00:00:00Z


def _from_epoch(seconds: float) -> EvidenceTime | None:
    if not math.isfinite(seconds) or not EPOCH_MIN <= seconds < EPOCH_MAX:
        return None
    return EvidenceTime(datetime.fromtimestamp(seconds, tz=timezone.utc), zone_assumed=False)


def parse_evidence_time(value: object) -> EvidenceTime | None:
    """A stored evidence time → ``EvidenceTime``, or ``None`` (the item is untimed, ``time_unreadable``).

    Reads ISO 8601 with or without a zone / fractional seconds (``2026-10-20T10:00:05.000+00:00``,
    ``2026-10-20T09:55:00Z``, ``2026-10-20T10:00:05``), Splunk's ``%m/%d/%Y:%H:%M:%S`` and epoch seconds as a
    number or a string (``1760954405.000``). A zone-less ISO or Splunk time is UTC with ``zone_assumed=True``.
    Never guesses: a date without a time, epoch milliseconds, ``[REDACTED_PHONE]``, ``?`` and ``""`` are ``None``.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _EPOCH_RE.fullmatch(text):
        return _from_epoch(float(text))
    if _ISO_RE.fullmatch(text):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    elif _SPLUNK_RE.fullmatch(text):
        try:
            parsed = datetime.strptime(text, "%m/%d/%Y:%H:%M:%S")
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return EvidenceTime(parsed.replace(tzinfo=timezone.utc), zone_assumed=True)
    return EvidenceTime(parsed.astimezone(timezone.utc), zone_assumed=False)
