"""W25e HYG-06 (E-2) T-B3: the live WebSocket frame vocabulary the SPA and the harness use is the server's.

``frontend/src/types/liveFrames.ts`` mirrors ``app/streams/live_frames.py``: the protocol number the hook sends
in its auth frame, the frame type names it branches on, the pacing interval its footer prints, the listed-results
cap and the coalesced event types, plus the wire shape of a ``live_batch`` (its ``counts`` keys and a listed
result's keys). The stress harness (``backend/scripts/stress_live_ingest.py``, which imports no ``app.*``) asks
for the same protocol number. A drifted name is a frame the client silently ignores; a drifted number is a client
that never opts in. Read as TEXT (no Node in the backend suite), like the other frontend-parity pins.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.streams import live_frames as lf

ROOT = Path(__file__).resolve().parents[2]
LIVE_FRAMES_TS = ROOT / "frontend" / "src" / "types" / "liveFrames.ts"
HARNESS = ROOT / "backend" / "scripts" / "stress_live_ingest.py"

_STRING = r"""'([^'\\]*)'|"([^"\\]*)\""""


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _source() -> str:
    return _read(LIVE_FRAMES_TS)


def _number(name: str) -> float:
    match = re.search(rf"export const {name}\b[^=]*=\s*([0-9][0-9_]*(?:\.[0-9]+)?)\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a plain number literal in {LIVE_FRAMES_TS.name}"
    return float(match.group(1).replace("_", ""))


def _string(name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*(?:{_STRING})\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a plain string literal in {LIVE_FRAMES_TS.name}"
    return match.group(1) if match.group(1) is not None else match.group(2)


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {LIVE_FRAMES_TS.name}"
    return tuple(single or double for single, double in re.findall(_STRING, match.group(1)))


def _interface_keys(body: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"([A-Za-z_]\w*)\??\s*:", body)}


def _interface(name: str) -> str:
    match = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", _source(), re.DOTALL)
    assert match, f"interface {name} is not declared in {LIVE_FRAMES_TS.name}"
    return match.group(1)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("LIVE_PROTOCOL_BATCH", lf.LIVE_PROTOCOL_BATCH),
        ("LIVE_FRAME_INTERVAL_MS", lf.FRAME_INTERVAL_MS),
        ("LIVE_RESULTS_PER_FRAME_MAX", lf.RESULTS_PER_FRAME_MAX),
    ],
)
def test_the_frontend_number_equals_the_server(name, server):
    assert _number(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [("LIVE_BATCH_FRAME", lf.LIVE_BATCH_FRAME), ("LIVE_RECONCILE_FRAME", lf.LIVE_RECONCILE_FRAME)],
)
def test_the_frontend_frame_type_equals_the_server(name, server):
    assert _string(name) == server


def test_the_frontend_coalesced_event_types_equal_the_server():
    assert _array("LIVE_COALESCED_EVENT_TYPES") == lf.COALESCED_EVENT_TYPES


def test_the_harness_asks_for_the_servers_batch_protocol():
    match = re.search(r"^WS_PROTOCOL_BATCH\s*=\s*(\d+)\b", _read(HARNESS), re.MULTILINE)
    assert match, f"WS_PROTOCOL_BATCH is not a plain int literal in {HARNESS.name}"
    assert int(match.group(1)) == lf.LIVE_PROTOCOL_BATCH


def _a_real_batch() -> dict:
    now = [0.0]
    pacer = lf.LiveFramePacer(lf.LIVE_PROTOCOL_BATCH, interval_s=0.2, clock=lambda: now[0])
    for n in range(2):
        pacer.offer({
            "type": "live_test_result", "run_id": "run-zz", "last_test": f"zz_case_{n}", "last_status": "PASSED",
            "sequence_id": f"1700000000000-{n}",
        })
    frame = pacer.take_frame()
    assert frame is not None and frame["type"] == lf.LIVE_BATCH_FRAME
    return frame


def test_the_batch_frame_interface_matches_what_the_server_sends():
    frame = _a_real_batch()
    body = _interface("LiveBatchFrame")
    assert re.search(rf"\btype:\s*'{lf.LIVE_BATCH_FRAME}'", body), "LiveBatchFrame.type is not the server's literal"
    assert re.search(rf"\bprotocol:\s*{lf.LIVE_PROTOCOL_BATCH}\b", body), "LiveBatchFrame.protocol drifted"
    counts = re.search(r"counts:\s*\{([^}]*)\}", body)
    assert counts, "LiveBatchFrame.counts is not an inline object type"
    assert _interface_keys(counts.group(1)) == set(frame["counts"])
    top = _interface_keys(re.sub(r"counts:\s*\{[^}]*\}", "counts: x", body))
    assert top == set(frame), "the frame's top-level keys and the interface's differ"


def test_a_listed_result_matches_the_result_item_interface():
    (entry,) = _a_real_batch()["events"]
    assert _interface_keys(_interface("LiveResultItem")) == set(entry["results"][0])
    entry_keys = _interface_keys(_interface("LiveBatchEntry"))
    assert {"type", "run_id", "sequence_id", "coalesced", "results", "results_dropped"} <= entry_keys
    assert {"coalesced", "results", "results_dropped"} <= set(entry)
