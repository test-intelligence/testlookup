"""W39 STRAT-07: the observability timeline vocabulary the UI renders is the server's.

``frontend/src/types/observabilityTimeline.ts`` mirrors ``services/observability_timeline_vocab.py`` constant for
constant: the lane sources (in the D-2 order), the states, ``fetched_by``, the labels, the untimed reasons, the
``configured_now`` keys and the card's sentences. Read as TEXT (no Node in the backend suite), like the W24 / W37
frontend-parity pins. The vocabulary's cross-module facts are pinned here too: the real tool names, the answer
prefixes the service classifies stored text by, the stored caps it re-applies, and the response model's
``configured_now`` keys.
"""
from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from app.models import schemas
from app.services import observability_timeline_vocab as v
from app.services import ocp_client
from app.services import prometheus_vocab as pv
from app.tools import analyze_ocp, query_splunk, reconstruct_trace

REPO = Path(__file__).resolve().parents[2]
TYPES_TS = REPO / "frontend" / "src" / "types" / "observabilityTimeline.ts"


def _source() -> str:
    if not TYPES_TS.exists():
        pytest.fail(f"{TYPES_TS} is missing: the parity this test pins has nothing to read")
    return TYPES_TS.read_text(encoding="utf-8")


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {TYPES_TS.name}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _record(name: str) -> dict[str, str]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _source(), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {TYPES_TS.name}"
    body = match.group(1)
    found = re.findall(r"""^\s*'?([A-Za-z_]+)'?:\s*(?:'([^']*)'|"([^"]*)"),?\s*$""", body, re.MULTILINE)
    entries = {key: single or double for key, single, double in found}
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


def _string(name: str) -> str:
    match = re.search(rf"""^export const {name}\b[^=]*=\s*(?:'([^']*)'|"([^"]*)")\s*$""", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a one-line string literal in {TYPES_TS.name}"
    return match.group(1) if match.group(1) is not None else match.group(2)


@pytest.mark.parametrize(
    ("name", "server"),
    [("LANE_SOURCES", v.LANE_SOURCES), ("LANE_STATES", v.LANE_STATES), ("FETCHED_BY", v.FETCHED_BY),
     ("UNTIMED_REASONS", v.UNTIMED_REASONS), ("CONFIGURED_SOURCES", v.CONFIGURED_SOURCES)],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [("LANE_LABELS", v.LANE_LABELS), ("LANE_FETCHED_BY", v.LANE_FETCHED_BY),
     ("FETCHED_BY_LABELS", v.FETCHED_BY_LABELS), ("UNTIMED_REASON_LABELS", v.UNTIMED_REASON_LABELS),
     ("CONFIGURED_LABELS", v.CONFIGURED_LABELS)],
)
def test_the_frontend_records_equal_the_server(name, server):
    record = _record(name)
    assert record == server
    assert tuple(record) == tuple(server), f"{name} keeps the server's order"


@pytest.mark.parametrize(
    "name",
    ["CARD_TITLE", "HEADER_SENTENCE", "OFFLINE_NOW_SENTENCE", "UNTIMED_HEADING", "TRUNCATED_SENTENCE",
     "ZONE_ASSUMED_SENTENCE", "SPLUNK_TRACE_EMPTY_SENTENCE", "OCP_NOT_ENRICHED_SENTENCE", "STORE_UNREADABLE_SENTENCE"],
)
def test_the_frontend_sentences_equal_the_server(name):
    assert _string(name) == getattr(v, name)


def test_the_card_title_avoids_the_names_other_regions_and_tabs_already_use():
    # D-5: Playwright matches role names by substring; "Timeline" is the Timing tab's region, "Evidence" a tab.
    assert v.CARD_TITLE == "Observability evidence"
    assert "Timeline" not in v.CARD_TITLE and "Evidence" not in v.CARD_TITLE


# ── Cross-module facts the service relies on ──────────────────────────────────────────────────────────────────────


def test_the_per_test_sources_are_the_real_tool_names():
    assert v.PER_TEST_TOOL_SOURCES["splunk_per_test"] == query_splunk.TOOL_NAME == "query_splunk_logs"
    assert v.PER_TEST_TOOL_SOURCES["ocp_per_test"] == analyze_ocp.TOOL_NAME == "analyze_openshift_pod_events"
    assert not {"splunk", "ocp_events"} & set(v.PER_TEST_TOOL_SOURCES.values())   # the dev seed's sources


def test_the_answer_prefixes_are_the_fetchers_own_text():
    assert v.SPLUNK_FOUND_PREFIX == query_splunk.SPLUNK_FOUND_PREFIX
    assert v.OCP_FOUND_PREFIX == ocp_client.OCP_FOUND_PREFIX
    assert f'f"{v.SPLUNK_NO_HITS_PREFIX} for service' in Path(query_splunk.__file__).read_text(encoding="utf-8")
    assert f'f"{v.OCP_NO_EVENTS_PREFIX} \'' in Path(ocp_client.__file__).read_text(encoding="utf-8")
    assert f'"{v.TRACE_NO_EVENTS_PREFIX} in the specified window' in Path(
        reconstruct_trace.__file__).read_text(encoding="utf-8")


def test_the_caps_are_the_stored_caps():
    assert v.SERIES_PER_CLUSTER_MAX == pv.TEMPLATES_PER_CALL_MAX
    assert v.POINTS_PER_SERIES_MAX == pv.POINTS_MAX
    assert "for event in raw_events[:40]:" in Path(reconstruct_trace.__file__).read_text(encoding="utf-8")
    assert v.TRACE_STEPS_PER_CLUSTER_MAX == 40


def test_configured_now_carries_exactly_the_configured_sources():
    assert tuple(schemas.ObservabilityConfiguredNow.model_fields) == v.CONFIGURED_SOURCES
    assert tuple(schemas.ObservabilityLane.model_fields) == (
        "source", "label", "fetched_by", "state", "sentence", "truncated", "series", "events", "untimed")


@pytest.mark.parametrize(
    ("model", "field", "server"),
    [("ObservabilityLane", "source", v.LANE_SOURCES), ("ObservabilityLane", "fetched_by", v.FETCHED_BY),
     ("ObservabilityLane", "state", v.LANE_STATES), ("ObservabilityUntimed", "reason", v.UNTIMED_REASONS)],
)
def test_the_response_models_literals_are_the_vocabulary(model, field, server):
    assert typing.get_args(getattr(schemas, model).model_fields[field].annotation) == server
