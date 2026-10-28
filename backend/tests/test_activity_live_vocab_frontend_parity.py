"""W21d ACT-02: the live tail's and Inbox › Activity's vocabulary the UI renders is the server's.

``frontend/src/types/activityLive.ts`` mirrors ``services/activity/live_vocab.py``:
the SSE event names the client branches on, the ``refresh`` / ``bye`` reasons,
the heartbeat and first-retry timings, the stream route's refusal sentences
(shown verbatim in the status line), the ``/activity/mine`` views and
involvements with their chip labels, and the default window. A drifted name
is a frame the client silently ignores or a chip that reads ``undefined``.
Read as TEXT (no Node in the backend suite), like the other frontend-parity
pins.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.activity import live_vocab as v

ACTIVITY_LIVE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "activityLive.ts"

# One quoted TS string literal, single- or double-quoted (a sentence with an
# apostrophe is double-quoted).
_STRING = r"""'([^'\\]*)'|"([^"\\]*)\""""


def _source() -> str:
    if not ACTIVITY_LIVE.exists():
        pytest.fail(f"{ACTIVITY_LIVE} is missing: the parity this test pins has nothing to read")
    return ACTIVITY_LIVE.read_text(encoding="utf-8")


def _strings(text: str) -> list[str]:
    return [single or double for single, double in re.findall(_STRING, text)]


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {ACTIVITY_LIVE.name}"
    return tuple(_strings(match.group(1)))


_ENTRY = re.compile(r"""(?:'([^']+)'|([A-Za-z_]\w*))\s*:\s*(?:'([^'\\]*)'|"([^"\\]*)"),?""")


def _record(name: str) -> dict[str, str]:
    """An object literal, one ``'key': 'value',`` entry per line, closed by a ``}`` line.

    A value may hold braces (``{project_id}``), so the body ends at the first
    line that starts with ``}``, not at the first brace.
    """
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{\n(.*?)\n\}}", _source(), re.DOTALL)
    assert match, f"{name} is not declared as a multi-line object literal in {ACTIVITY_LIVE.name}"
    pairs: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        entry = _ENTRY.fullmatch(line)
        assert entry, f"{name}: {line!r} is not a 'key': 'value' literal"
        quoted, bare, single, double = entry.groups()
        pairs[quoted or bare] = single if single is not None else double
    return pairs


def _number(name: str) -> float:
    match = re.search(rf"export const {name}\b[^=]*=\s*([0-9][0-9_]*(?:\.[0-9]+)?)\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a plain number literal in {ACTIVITY_LIVE.name}"
    return float(match.group(1).replace("_", ""))


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("SSE_EVENTS", v.SSE_EVENTS),
        ("REFRESH_REASONS", v.REFRESH_REASONS),
        ("BYE_REASONS", v.BYE_REASONS),
        ("MINE_VIEWS", v.MINE_VIEWS),
        ("INVOLVEMENTS", v.INVOLVEMENTS),
    ],
)
def test_the_frontend_array_equals_the_server(name, server):
    assert _array(name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("STREAM_ERROR_SENTENCES", v.STREAM_ERROR_SENTENCES),
        ("INVOLVEMENT_LABELS", v.INVOLVEMENT_LABELS),
    ],
)
def test_the_frontend_record_equals_the_server(name, server):
    assert _record(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("HEARTBEAT_SECONDS", v.HEARTBEAT_SECONDS),
        ("RETRY_MILLISECONDS", v.RETRY_MILLISECONDS),
        ("MINE_DEFAULT_DAYS", v.MINE_DEFAULT_DAYS),
    ],
)
def test_the_frontend_number_equals_the_server(name, server):
    assert _number(name) == server


def test_the_involvement_labels_cover_every_involvement_in_order():
    assert tuple(_record("INVOLVEMENT_LABELS")) == v.INVOLVEMENTS


def test_the_too_many_streams_sentence_names_the_cap():
    """The sentence is shown verbatim, so it must say the server's real cap."""
    assert f"{v.MAX_STREAMS_PER_USER} live activity views" in _record("STREAM_ERROR_SENTENCES")["too_many_streams"]


def test_the_status_text_covers_every_live_status():
    match = re.search(r"export type LiveStatus\s*=\s*([^\n]+)", _source())
    assert match, "LiveStatus is not declared as a union of string literals"
    statuses = _strings(match.group(1))
    assert statuses == ["off", "connecting", "live", "reconnecting", "unavailable", "refused"]
    assert set(_record("LIVE_STATUS_TEXT")) == set(statuses)
