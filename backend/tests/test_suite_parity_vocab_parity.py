"""W25a HYG-04: the suite-membership parity vocabulary the UI renders is the server's.

``frontend/src/types/suiteParity.ts`` mirrors ``services/suite_parity_vocab.py``
constant for constant: the run statuses / triggers, the 6 blocking and 7
expected reasons, the info counts, the clean-day requirement and the history
window. Read as TEXT (no Node in the backend suite), like the other
frontend-parity pins (the W22b / W24 regex readers). The vocabulary's own
facts are pinned here too: the two reason tuples partition the reasons, every
reason has exactly one UI sentence, and the module imports nothing from the
application and declares no ``*_CHANNEL`` constant.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.services import suite_parity_vocab as v

REPO = Path(__file__).resolve().parents[2]
TYPES_TS = REPO / "frontend" / "src" / "types" / "suiteParity.ts"
VOCAB_PY = REPO / "backend" / "app" / "services" / "suite_parity_vocab.py"


def _source() -> str:
    if not TYPES_TS.exists():
        pytest.fail(f"{TYPES_TS} is missing: the parity this test pins has nothing to read")
    return TYPES_TS.read_text(encoding="utf-8")


def _array(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {TYPES_TS.name}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _number(name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _source(), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {TYPES_TS.name}"
    return int(match.group(1))


def _record(name: str) -> dict[str, str]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}", _source(), re.DOTALL)
    assert match, f"{name} is not declared as an object literal in {TYPES_TS.name}"
    body = match.group(1)
    found = re.findall(r"^\s*'?([A-Za-z_]+)'?:\s*'([^']*)',?\s*$", body, re.MULTILINE)
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(found) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return dict(found)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("RUN_STATUSES", v.RUN_STATUSES),
        ("RUN_TRIGGERS", v.RUN_TRIGGERS),
        ("BLOCKING_REASONS", v.BLOCKING_REASONS),
        ("EXPECTED_REASONS", v.EXPECTED_REASONS),
        ("INFO_COUNTS", v.INFO_COUNTS),
    ],
)
def test_the_frontend_arrays_equal_the_server(name, server):
    assert _array(name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("REQUIRED_CLEAN_DAYS", v.REQUIRED_CLEAN_DAYS),
        ("HISTORY_DAYS", v.HISTORY_DAYS),
        ("SAMPLE_PER_REASON", v.SAMPLE_PER_REASON),
    ],
)
def test_the_frontend_numbers_equal_the_server(name, server):
    assert _number(name) == server


def test_every_reason_has_exactly_one_ui_sentence():
    labels = _record("REASON_LABEL")
    assert set(labels) == set(v.BLOCKING_REASONS) | set(v.EXPECTED_REASONS)
    assert all(text.strip() == text and text for text in labels.values())
    assert len(set(labels.values())) == len(labels), "two reasons read the same sentence"


def test_the_reasons_partition_without_overlap():
    assert len(v.BLOCKING_REASONS) == 6 and len(v.EXPECTED_REASONS) == 7
    assert not set(v.BLOCKING_REASONS) & set(v.EXPECTED_REASONS)
    assert not set(v.INFO_COUNTS) & (set(v.BLOCKING_REASONS) | set(v.EXPECTED_REASONS))
    assert len(set(v.BLOCKING_REASONS)) == 6 and len(set(v.EXPECTED_REASONS)) == 7


def test_the_caps_and_clock_are_the_contract_values():
    assert v.SAMPLE_PER_REASON == 20
    assert v.PER_PROJECT_MAX == 200
    assert v.REQUIRED_CLEAN_DAYS == 7
    assert v.HISTORY_DAYS >= 2 * v.REQUIRED_CLEAN_DAYS
    assert v.RUN_STALE_MINUTES == 60
    assert v.RUN_STATUSES[0] == "running"


def test_every_status_column_value_fits_its_string_width():
    # the table's trigger / status columns are String(10); error_code is String(64)
    assert max(len(s) for s in v.RUN_STATUSES + v.RUN_TRIGGERS) <= 10
    assert len(v.INTERRUPTED_ERROR_CODE) <= 64


def test_the_vocabulary_imports_nothing_from_the_application():
    tree = ast.parse(VOCAB_PY.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert modules == ["__future__"], f"suite_parity_vocab.py is constants only; it imports {modules}"


def test_no_constant_is_named_like_a_distribution_channel():
    tree = ast.parse(VOCAB_PY.read_text(encoding="utf-8"))
    names = [t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)]
    assert names and not [n for n in names if n.endswith("_CHANNEL")]
