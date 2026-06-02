"""Guard for the ai_pipeline_debouncer buffer wire format.

Reviewed in review/ai-pipeline-debouncer (2026-06-02): clean. The Redis
SortedSet stores each queued run as a pipe-delimited
``project|run|build|workflow`` member; ``flush_pending`` parses members back
with ``_QueuedRun.parse`` and **silently drops** any that don't split into
exactly 4 fields. A delimiter/field-count change would therefore silently lose
queued pipeline triggers. These pin the round-trip + the drop-on-malformed
contract — and run locally (the existing test_ai_pipeline_debouncer suite needs
``celery`` and skips/fails locally).
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.ai_pipeline_debouncer import _QueuedRun  # noqa: E402


def test_queued_run_round_trip():
    q = _QueuedRun(
        project_id="proj-1", test_run_id="run-9",
        build_number="build-42", workflow_type="deep",
    )
    parsed = _QueuedRun.parse(q.serialize())
    assert parsed is not None
    assert parsed == q


def test_queued_run_workflow_type_defaults_to_offline():
    # Empty workflow segment → "offline" on both serialize and parse.
    q = _QueuedRun("p", "r", "b", "")
    assert q.serialize().endswith("|offline")
    parsed = _QueuedRun.parse("p|r|b|")
    assert parsed is not None
    assert parsed.workflow_type == "offline"


@pytest.mark.parametrize("bad", [
    "only|three|fields",          # 3 fields
    "a|b|c|d|e",                  # 5 fields
    "",                            # empty
    "noseparators",                # 1 field
])
def test_queued_run_parse_rejects_malformed(bad):
    assert _QueuedRun.parse(bad) is None


def test_queued_run_parse_handles_bytes():
    # decode_responses is the project default, but parse must tolerate bytes.
    parsed = _QueuedRun.parse(b"p|r|b|offline")
    assert parsed is not None
    assert parsed.project_id == "p"
    assert parsed.workflow_type == "offline"
