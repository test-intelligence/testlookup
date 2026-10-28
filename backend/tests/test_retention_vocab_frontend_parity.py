"""W22a: the retention-hold / keep-last-floor vocabulary the UI renders is the server's.

``frontend/src/types/retentionHolds.ts`` mirrors ``services/retention_vocab.py``
constant for constant: the hold entity types and states, the reason / expiry /
list bounds, the ``Held: …`` refusal sentence, the project-hold label, the hold
routes' error sentences, the floor bounds and note, the reference-warning
labels and the purged-baseline sentence. Read as TEXT (no Node in the backend
suite), like the other frontend-parity pins. A drifted copy would show one
sentence on the client and refuse with another on the server.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import schemas
from app.services import retention_vocab as v

HOLDS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "retentionHolds.ts"


def _source() -> str:
    if not HOLDS_TS.exists():
        pytest.fail(f"{HOLDS_TS} is missing: the parity this test pins has nothing to read")
    return HOLDS_TS.read_text(encoding="utf-8")


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {HOLDS_TS.name}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _number(name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {HOLDS_TS.name}"
    return int(match.group(1))


def _string(name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*'([^']*)'\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a single-quoted string literal in {HOLDS_TS.name}"
    return match.group(1)


def _record(name: str) -> dict[str, str]:
    # The values carry `{count}` placeholders, so the literal ends at the first
    # column-0 `} as const`, not at the first `}`.
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _source(), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {HOLDS_TS.name}"
    body = match.group(1)
    entries = dict(re.findall(r"^\s*'?([A-Za-z_.]+)'?:\s*'([^']*)',?\s*$", body, re.MULTILINE))
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


@pytest.mark.parametrize(
    ("name", "server"),
    [("HOLD_ENTITY_TYPES", v.HOLD_ENTITY_TYPES), ("HOLD_STATES", v.HOLD_STATES)],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("HOLD_REASON_MIN", v.HOLD_REASON_MIN),
        ("HOLD_REASON_MAX", v.HOLD_REASON_MAX),
        ("HOLD_MAX_EXPIRY_DAYS", v.HOLD_MAX_EXPIRY_DAYS),
        ("HOLD_LIST_DEFAULT_LIMIT", v.HOLD_LIST_DEFAULT_LIMIT),
        ("HOLD_LIST_MAX_LIMIT", v.HOLD_LIST_MAX_LIMIT),
        ("KEEP_LAST_RUNS_DEFAULT", v.KEEP_LAST_RUNS_DEFAULT),
        ("KEEP_LAST_RUNS_MIN", v.KEEP_LAST_RUNS_MIN),
        ("KEEP_LAST_RUNS_MAX", v.KEEP_LAST_RUNS_MAX),
    ],
)
def test_the_frontend_bounds_equal_the_server(name, server):
    assert _number(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("HELD_SENTENCE", v.HELD_SENTENCE),
        ("PROJECT_HOLD_LABEL", v.PROJECT_HOLD_LABEL),
        ("FLOOR_NOTE_EXPLICIT", v.FLOOR_NOTE_EXPLICIT),
        ("REFERENCE_FALLBACK_LABEL", v.REFERENCE_FALLBACK_LABEL),
        ("REFERENCE_FALLBACK_LABEL_ONE", v.REFERENCE_FALLBACK_LABEL_ONE),
        ("BASELINE_PURGED_SENTENCE", v.BASELINE_PURGED_SENTENCE),
    ],
)
def test_the_frontend_sentences_equal_the_server(name, server):
    assert _string(name) == server


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("HOLD_ERROR_SENTENCES", v.HOLD_ERROR_SENTENCES),
        ("REFERENCE_LABELS", v.REFERENCE_LABELS),
        ("REFERENCE_LABELS_ONE", v.REFERENCE_LABELS_ONE),
    ],
)
def test_the_frontend_records_equal_the_server(name, server):
    assert _record(name) == server


def test_the_server_copies_agree_with_each_other():
    """The vocabulary's own internal facts, and the schema defaults that read it."""
    assert v.HOLD_ERROR_SENTENCES["unknown_entity_type"].endswith(", ".join(v.HOLD_ENTITY_TYPES) + ".")
    assert v.HOLD_ERROR_SENTENCES["unknown_state"].endswith(", ".join((*v.HOLD_STATES, "all")) + ".")
    assert f"{v.HOLD_REASON_MIN} to {v.HOLD_REASON_MAX}" in v.HOLD_ERROR_SENTENCES["reason_length"]
    assert str(v.HOLD_MAX_EXPIRY_DAYS) in v.HOLD_ERROR_SENTENCES["expiry_too_far"]
    assert v.HELD_SENTENCE.format(reason="Audit Q4 sentinel") == "Held: Audit Q4 sentinel"
    assert schemas.DeletionPreviewResponse.model_fields["floor_note"].default == v.FLOOR_NOTE_EXPLICIT
    assert schemas.DeleteRunAcceptedResponse.model_fields["floor_note"].default == v.FLOOR_NOTE_EXPLICIT
    assert schemas.RetentionPolicyRead.model_fields["keep_last_runs"].default == v.KEEP_LAST_RUNS_DEFAULT
