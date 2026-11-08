"""W39 STRAT-07: the observability timeline vocabulary and its ONE time parser (contract §1.3, D-3).

The parser is tested on REAL samples: the shared fake's Splunk ``_time`` values and K8s RFC 3339 event times
(``tests/fakes/fake_integrations.py``), the Splunk window format the fetchers build, an epoch-seconds string, the
trace's zone-less window, and what the write-time redactor really turns an epoch string into. A time it cannot read
is ``None`` (the item goes to the untimed list) -- never a guess, never "now".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import observability_timeline_vocab as v
from app.services.evidence_sanitizer import sanitize_persistence_payload
from app.services.redaction_service import redact_text
from tests.fakes.fake_integrations import _demo_ocp_pods, _demo_splunk_events

UTC = timezone.utc


def _splunk_times() -> list[str]:
    return [event["_time"] for events in _demo_splunk_events().values() for event in events]


def _k8s_times() -> list[str]:
    pod = _demo_ocp_pods()["qa-payments"]["payment-runner-7d9f"]
    return [e[key] for e in pod["events"] for key in ("firstTimestamp", "lastTimestamp")]


def test_the_fakes_splunk_times_read_exactly_with_their_zone():
    times = _splunk_times()
    assert len(times) == 5
    parsed = [v.parse_evidence_time(t) for t in times]
    assert all(p is not None and p.zone_assumed is False for p in parsed)
    assert [p.at for p in parsed if p] == [
        datetime(2026, 10, 20, 9, 58, 0, tzinfo=UTC), datetime(2026, 10, 20, 9, 59, 30, tzinfo=UTC),
        datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC), datetime(2026, 10, 20, 10, 0, 7, tzinfo=UTC),
        datetime(2026, 10, 20, 10, 0, 9, tzinfo=UTC),
    ]


def test_the_fakes_k8s_event_times_read_exactly():
    parsed = [v.parse_evidence_time(t) for t in _k8s_times()]
    assert [(p.at, p.zone_assumed) for p in parsed if p] == [
        (datetime(2026, 10, 20, 9, 55, tzinfo=UTC), False), (datetime(2026, 10, 20, 10, 0, 2, tzinfo=UTC), False),
        (datetime(2026, 10, 20, 10, 0, 3, tzinfo=UTC), False), (datetime(2026, 10, 20, 10, 0, 3, tzinfo=UTC), False),
    ]


def test_the_write_time_sanitizer_keeps_the_fakes_times_readable():
    # Path B trace steps pass sanitize_persistence_payload; Path A excerpts pass redact_text. Neither may break them.
    for raw in _splunk_times() + _k8s_times():
        assert redact_text(raw) == raw
        assert sanitize_persistence_payload({"timestamp": raw})[0] == {"timestamp": raw}


def test_the_splunk_window_format_the_fetcher_builds_reads_as_utc_with_the_zone_assumed():
    fail_time = datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC)
    text = (fail_time - timedelta(minutes=5)).strftime("%m/%d/%Y:%H:%M:%S")   # tools/query_splunk.py window_start
    assert text == "10/20/2026:09:55:05"
    parsed = v.parse_evidence_time(text)
    assert parsed == v.EvidenceTime(datetime(2026, 10, 20, 9, 55, 5, tzinfo=UTC), zone_assumed=True)


@pytest.mark.parametrize("text, expected", [
    ("2026-10-20T10:00:05", datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC)),           # trace time_window shape
    ("2026-10-20T10:00:05.250", datetime(2026, 10, 20, 10, 0, 5, 250000, tzinfo=UTC)),
    ("2026-10-20 10:00:05", datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC)),
])
def test_a_zone_less_iso_time_is_utc_and_says_so(text, expected):
    assert v.parse_evidence_time(text) == v.EvidenceTime(expected, zone_assumed=True)


@pytest.mark.parametrize("text, expected", [
    ("2026-10-20T12:00:05+02:00", datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC)),
    ("2026-10-20T05:00:05.5-05:00", datetime(2026, 10, 20, 10, 0, 5, 500000, tzinfo=UTC)),
    ("2026-10-20T10:00:05.123456789Z", datetime(2026, 10, 20, 10, 0, 5, 123456, tzinfo=UTC)),
    ("2026-10-20T10:00:05+0000", datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC)),
])
def test_a_zoned_iso_time_is_converted_to_utc(text, expected):
    assert v.parse_evidence_time(text) == v.EvidenceTime(expected, zone_assumed=False)


def test_epoch_seconds_as_a_string_or_a_number_are_exact():
    expected = datetime.fromtimestamp(1760954405, tz=UTC)
    assert v.parse_evidence_time("1760954405.000") == v.EvidenceTime(expected, zone_assumed=False)
    assert v.parse_evidence_time("1760954405") == v.EvidenceTime(expected, zone_assumed=False)
    assert v.parse_evidence_time(1760954405.0) == v.EvidenceTime(expected, zone_assumed=False)
    assert v.parse_evidence_time(1760954405) == v.EvidenceTime(expected, zone_assumed=False)


def test_the_redactor_really_turns_an_epoch_string_into_a_phone_marker_which_is_untimed():
    redacted = redact_text("1760954405.000")
    assert redacted.startswith("[REDACTED_PHONE]")
    assert v.parse_evidence_time(redacted) is None
    assert v.parse_evidence_time("[REDACTED_PHONE]") is None


@pytest.mark.parametrize("value", [
    "?", "", "   ", None, True, False, "[REDACTED_IP]", "not a time", "2026-10-20", "2026-10-20T10",
    "1760954405000",            # epoch MILLISECONDS: never guessed
    "2026-10-20t10:00:05z",     # lower-case separators are not ISO 8601 as the fetchers send it
    "2026-13-45T10:00:05Z",     # shaped like ISO, not a date
    "13/45/2026:10:00:05",      # shaped like Splunk, not a date
    "[2026-10-20T10:00:0",      # a line the 500-char excerpt cut
    float("nan"), float("inf"), 12, -5.0, 9_999_999_999_999.0, ["2026-10-20T10:00:05Z"], {"at": 1},
])
def test_anything_else_is_unreadable_never_a_guess(value):
    assert v.parse_evidence_time(value) is None


# ── The vocabulary's own shape ────────────────────────────────────────────────────────────────────────────────────


def test_the_lane_tables_cover_every_source_in_the_contract_order():
    assert v.LANE_SOURCES == ("prometheus", "splunk_trace", "ocp_events", "splunk_per_test", "ocp_per_test")
    assert v.LANE_STATES == ("evidence", "none_found", "not_recorded", "not_analysed", "unavailable")
    assert tuple(v.LANE_LABELS) == v.LANE_SOURCES and tuple(v.LANE_FETCHED_BY) == v.LANE_SOURCES
    assert set(v.LANE_FETCHED_BY.values()) == set(v.FETCHED_BY) == set(v.FETCHED_BY_LABELS)
    assert tuple(v.UNTIMED_REASON_LABELS) == v.UNTIMED_REASONS == ("no_time_stored", "time_unreadable")
    assert set(v.PER_TEST_TOOL_SOURCES) == {"splunk_per_test", "ocp_per_test"}
    assert tuple(v.CONFIGURED_LABELS) == v.CONFIGURED_SOURCES == ("splunk", "ocp", "prometheus")


def test_the_contract_labels_and_sentences_are_verbatim():
    assert v.LANE_LABELS == {
        "prometheus": "Prometheus metrics", "splunk_trace": "Splunk trace (deep analysis)",
        "ocp_events": "OpenShift pod events (at ingest)", "splunk_per_test": "Splunk logs (per-test analysis)",
        "ocp_per_test": "OpenShift pod checks (per-test analysis)",
    }
    assert v.LANE_SENTENCES["store_unreadable"] == "The analysis store could not be read."
    assert v.LANE_SENTENCES["ocp_events_not_recorded"] == (
        "This run was not enriched with OpenShift pod data when it was ingested.")
    assert v.LANE_SENTENCES["splunk_trace_no_events"] == (
        "Splunk returned no trace events for this run — the stored answer does not say whether Splunk was reachable.")
    assert v.OFFLINE_NOW_SENTENCE == "AI offline mode is on, so analyses fetch no observability evidence."
    assert v.HEADER_SENTENCE == (
        "Evidence the AI analysis already fetched — nothing is fetched when you open this card.")
    assert v.CARD_TITLE == "Observability evidence"
    assert (v.SPLUNK_TRACE_EMPTY_SENTENCE, v.OCP_NOT_ENRICHED_SENTENCE, v.STORE_UNREADABLE_SENTENCE) == (
        v.LANE_SENTENCES["splunk_trace_no_events"], v.LANE_SENTENCES["ocp_events_not_recorded"],
        v.LANE_SENTENCES["store_unreadable"])


def test_every_lane_has_a_sentence_for_each_state_it_can_reach():
    reachable = {
        "prometheus": v.LANE_STATES,
        "splunk_trace": ("evidence", "not_recorded", "not_analysed", "unavailable"),
        "ocp_events": ("evidence", "none_found", "not_recorded", "unavailable"),
        "splunk_per_test": v.LANE_STATES,
        "ocp_per_test": v.LANE_STATES,
    }
    for source, states in reachable.items():
        for state in states:
            sentence = v.LANE_SENTENCES[f"{source}_{state}"]
            assert sentence and sentence.strip() == sentence and "{" not in sentence


def test_the_caps_are_the_contracts_bounds():
    assert (v.CLUSTERS_MAX, v.SERIES_PER_CLUSTER_MAX, v.POINTS_PER_SERIES_MAX, v.TRACE_STEPS_PER_CLUSTER_MAX,
            v.OCP_EVENTS_MAX, v.PER_TEST_ITEMS_MAX) == (5, 4, 120, 40, 200, 200)
