"""W13 TCL-01: the Views chips the page offers are the presets the server knows.

``frontend/src/pages/test-management/caseFilterState.ts`` mirrors
``core/test_library_vocab``'s ``CASE_PRESETS`` / ``CASE_PRESET_LABELS`` (the
four chips and the filters each applies: "My P0/P1" is owner = me, critical +
high), and the filter vocabularies a saved view and the URL accept
(``CASE_FILTER_MAX_VALUES``, ``CASE_AUTOMATION_FILTERS``, the priorities, the
``me`` owner). A chip that drifted would apply filters the server would refuse
in a saved view, or show a label the guide does not name. Read as TEXT (no
Node in the backend suite), like the other frontend-parity pins.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.test_library_vocab import (
    CASE_AUTOMATION_FILTERS,
    CASE_FILTER_MAX_VALUES,
    CASE_PRESET_LABELS,
    CASE_PRESETS,
    CASE_PRIORITIES,
    OWNER_ME,
)

MODEL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "test-management" / "caseFilterState.ts"


def _source() -> str:
    if not MODEL.exists():
        pytest.fail(f"{MODEL} is missing: the parity this test pins has nothing to read")
    return MODEL.read_text(encoding="utf-8")


def _object_literal(name: str) -> dict:
    """``export const <name>: … = { … }`` (closed by a line holding only ``}``) as JSON."""
    source = _source()
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{\n(.*?)\n\}}", source, re.S)
    assert match, f"{name} is not declared as an object literal in {MODEL.name}"
    body = match.group(1)
    body = re.sub(r"//[^\n]*", "", body)                       # comments
    body = body.replace("'", '"')
    body = re.sub(r"([{,\s])([a-z_][a-z0-9_]*)\s*:", r'\1"\2":', body)   # bare keys
    body = re.sub(r",(\s*[}\]])", r"\1", body)                  # trailing commas
    body = re.sub(r",\s*$", "", body.strip())
    return json.loads("{" + body + "}")


def _array_literal(name: str) -> list:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]", _source())
    assert match, f"{name} is not declared as an array literal in {MODEL.name}"
    return re.findall(r"'([^']*)'", match.group(1))


def test_the_presets_apply_exactly_the_servers_filters():
    assert _object_literal("CASE_PRESETS") == CASE_PRESETS


def test_the_preset_labels_are_the_servers():
    assert _object_literal("CASE_PRESET_LABELS") == CASE_PRESET_LABELS


def test_the_views_row_lists_every_preset_once_in_the_servers_order():
    assert _array_literal("CASE_PRESET_IDS") == list(CASE_PRESETS)


def test_the_filter_vocabularies_match():
    source = _source()
    (max_values,) = re.findall(r"export const CASE_FILTER_MAX_VALUES = (\d+)", source)
    assert int(max_values) == CASE_FILTER_MAX_VALUES
    assert tuple(_array_literal("CASE_AUTOMATION_FILTERS")) == CASE_AUTOMATION_FILTERS
    assert tuple(_array_literal("CASE_FILTER_PRIORITIES")) == CASE_PRIORITIES
    assert re.search(rf"export const OWNER_ME = '{OWNER_ME}'", source)
