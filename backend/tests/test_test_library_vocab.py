"""``core/test_library_vocab`` (W13): the one module of the test-library words.

Pinned here:
* it is pure (imports nothing from ``app``: ``schemas.py`` imports it);
* every vocabulary it MIRRORS equals its source -- one "flaky" bar, one set of
  lifecycle states, priorities and retired / runnable states, the export's
  formula prefixes, the long-text cap;
* its own tables agree with each other (labels / titles per kind, the gap-kind
  pattern, the presets' values, the timeline presets, the sentences that spell a
  number spell the constant's), and the DB check of migration 0229 holds the
  link types;
* ``normalize_requirement_key`` and ``timeline_title`` render what the
  contract says.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core import test_library_vocab as V

BACKEND = Path(__file__).resolve().parents[1]
VOCAB = BACKEND / "app" / "core" / "test_library_vocab.py"
EXPORTS = BACKEND / "app" / "routers" / "test_management_exports.py"
M0229 = BACKEND / "migrations" / "versions" / "0229_test_case_requirement_links.py"


def test_the_module_imports_nothing_from_app():
    tree = ast.parse(VOCAB.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name == "app" or name.startswith("app.") for name in imported), imported


# ── mirrors ──────────────────────────────────────────────────────────────────


def test_the_lifecycle_states_are_the_enums_in_its_order():
    from app.models.postgres import TestCaseLifecycleState

    assert V.LIFECYCLE_STATES == tuple(state.value for state in TestCaseLifecycleState)


def test_the_priorities_retired_and_runnable_states_are_the_services():
    from app.services.test_case_editing_service import PRIORITIES
    from app.services.test_case_lifecycle_service import RETIRED_STATES, RUNNABLE_STATES

    assert V.CASE_PRIORITIES == tuple(PRIORITIES)
    assert V.RETIRED_STATES == tuple(RETIRED_STATES)
    assert V.RUNNABLE_STATES == tuple(RUNNABLE_STATES)
    assert set(V.REVIEW_STATES) <= set(V.LIFECYCLE_STATES)


def test_one_flaky_bar_in_the_product():
    from app.services.failure_attribution_service import FLAKY_SCORE_FLOOR, WEAK_CONFIDENCES

    assert V.FLAKY_SCORE_FLOOR == FLAKY_SCORE_FLOOR
    assert V.FLAKY_WEAK_CONFIDENCES == frozenset(WEAK_CONFIDENCES)


def test_the_formula_prefixes_are_the_excel_exports():
    tree = ast.parse(EXPORTS.read_text(encoding="utf-8"))
    (func,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_excel_text"]
    calls = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "startswith"
    ]
    (call,) = calls
    assert tuple(ast.literal_eval(call.args[0])) == V.FORMULA_PREFIXES


def test_the_cell_cap_is_the_long_text_cap():
    from app.models.schemas import MAX_LONG_TEXT

    assert V.IMPORT_MAX_CELL_CHARS == MAX_LONG_TEXT


def test_the_db_check_holds_the_link_types():
    source = M0229.read_text(encoding="utf-8")
    (listed,) = re.findall(r"link_type IN \(([^)]*)\)", source)
    assert tuple(re.findall(r"'([a-z_]+)'", listed)) == V.REQUIREMENT_LINK_TYPES


# ── the module's own tables agree ────────────────────────────────────────────


def test_labels_titles_and_weights_cover_exactly_their_kinds():
    assert set(V.HEALTH_COMPONENT_LABELS) == set(V.HEALTH_COMPONENTS) == set(V.HEALTH_WEIGHTS)
    assert sum(V.HEALTH_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(V.STRATEGY_GAP_TITLES) == set(V.STRATEGY_GAP_KINDS)
    assert set(V.REQUIREMENT_GAP_TEXT) == set(V.REQUIREMENT_GAP_REASONS)
    assert set(V.CASE_PRESET_LABELS) == set(V.CASE_PRESETS)
    assert len(V.HEALTH_TAGS) == len(V.HEALTH_TAG_THRESHOLDS) + 1
    assert list(V.HEALTH_TAG_THRESHOLDS) == sorted(V.HEALTH_TAG_THRESHOLDS, reverse=True) == [85, 70]


def test_the_gap_kind_pattern_accepts_exactly_the_kinds():
    pattern = re.compile(V.EVIDENCE_GAP_KIND_PATTERN)
    assert all(pattern.match(kind) for kind in V.EVIDENCE_GAP_KINDS)
    for other in ("never", "no_owner_x", "flaky", "", "NO_OWNER"):
        assert not pattern.match(other), other
    assert {"flaky_active", "no_owner"} <= set(V.STRATEGY_GAP_KINDS)


def test_the_presets_hold_valid_case_view_values_only():
    for preset_id, filters in V.CASE_PRESETS.items():
        assert set(filters) <= V.CASE_VIEW_KEYS - {"kind"}, preset_id
        assert set(filters.get("statuses", [])) <= set(V.LIFECYCLE_STATES), preset_id
        assert set(filters.get("priorities", [])) <= set(V.CASE_PRIORITIES), preset_id
        assert filters.get("owner") in (None, V.OWNER_ME), preset_id
        assert filters.get("automation") in (None, *V.CASE_AUTOMATION_FILTERS), preset_id
    assert V.CASE_PRESET_LABELS["my_p0_p1"] == "My P0/P1", "O-1: owner = me, not a team"


def test_the_timeline_presets_name_known_kinds():
    for name, kinds in V.TIMELINE_PRESETS.items():
        assert set(kinds) <= set(V.TIMELINE_KINDS), name
    assert V.TIMELINE_LIMIT_DEFAULT <= V.TIMELINE_LIMIT_MAX
    assert ", ".join(V.TIMELINE_KINDS[:-1]) in V.TIMELINE_KIND_INVALID
    assert V.TIMELINE_KIND_INVALID.endswith(f"or {V.TIMELINE_KINDS[-1]}.")


def test_each_sentence_that_spells_a_number_spells_the_constant():
    assert f"at most {V.CASE_FILTER_MAX_VALUES} values" in V.TOO_MANY_VALUES_SENTENCE
    assert f"{V.IMPORT_MAX_BYTES // (1024 * 1024)} MB" in V.IMPORT_TOO_LARGE
    assert f"{V.IMPORT_MAX_ROWS:,} rows" in V.IMPORT_TOO_MANY_ROWS
    assert f"{V.IMPORT_MAX_XLSX_UNCOMPRESSED // (1024 * 1024)} MB" in V.IMPORT_XLSX_TOO_BIG
    assert f"{V.IMPORT_MAX_CELL_CHARS:,} characters" in V.IMPORT_ROW_CELL_TOO_LONG
    assert f"{V.STALE_DRAFT_DAYS}d" in V.STRATEGY_GAP_TITLES["stale_drafts"]
    assert f"1 to {V.REQUIREMENT_KEY_MAX} characters" in V.REQUIREMENT_KEY_INVALID
    for key in sorted(V.CASE_VIEW_KEYS - {"kind"}):
        assert key in V.CASE_VIEW_KEYS_SENTENCE, key
    for link_type in V.REQUIREMENT_LINK_TYPES:
        assert link_type in V.REQUIREMENT_LINK_TYPE_INVALID
    for status in V.AUTOMATION_STATUSES:
        assert status in V.IMPORT_ROW_AUTOMATION_STATUS
    for severity in V.CASE_SEVERITIES:
        assert severity in V.IMPORT_WARN_SEVERITY


def test_the_import_headers_template_and_ignored_lists_agree():
    template = {header.lower() for header in V.IMPORT_TEMPLATE_HEADERS}
    assert template == set(V.IMPORT_HEADERS), "the template names exactly the columns the import reads"
    assert not set(V.IMPORT_IGNORED_HEADERS) & set(V.IMPORT_HEADERS)
    assert {header.lower() for header in V.EXPORT_APPENDED_HEADERS} <= set(V.IMPORT_HEADERS) | set(V.IMPORT_IGNORED_HEADERS)
    assert set(V.PRIORITY_ALIASES.values()) <= set(V.CASE_PRIORITIES)


# ── normalize_requirement_key ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("[REQ-12]", "REQ-12"),
        ("(REQ-12)", "REQ-12"),
        (" req-12 ", "REQ-12"),
        ("REQ  12", "REQ 12"),
        ("[ req\t12 ]", "REQ 12"),
        ("[[REQ-12]]", "[REQ-12]"),          # ONE pair only
        ("[REQ-12)", "[REQ-12)"),            # not a pair
        ("REQ-PAY-07", "REQ-PAY-07"),
        ("", ""),
        ("   ", ""),
        ("[]", ""),
        ("\t", ""),
    ],
)
def test_normalize_requirement_key(raw, normalised):
    assert V.normalize_requirement_key(raw) == normalised


def test_a_control_character_is_kept_for_the_service_to_refuse():
    assert V.normalize_requirement_key("REQ\x0012") == "REQ\x0012"
    assert V.REQUIREMENT_KEY_CONTROL_RE.search("REQ\x0012")
    assert V.REQUIREMENT_KEY_CONTROL_RE.search("REQ\x7f")
    assert not V.REQUIREMENT_KEY_CONTROL_RE.search("REQ-12 ü")


# ── timeline_title ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "facts", "title"),
    [
        ("version", {"change_type": "updated", "version": 4, "source": "person"}, "Saved as v4 (person)"),
        ("version", {"change_type": "updated", "version": 4}, "Saved as v4"),
        ("version", {"change_type": "created", "version": 1}, "Created as v1"),
        ("version", {"change_type": "promoted", "version": 1}, "Created as v1"),
        ("version", {"change_type": "bulk_updated", "version": 5}, "Bulk edit"),
        ("version", {"change_type": "restored", "version": 6, "restored_from": 3}, "Restored from v3"),
        ("version", {"change_type": "restored", "version": 6}, "Restored as v6"),
        ("version", {"change_type": "approve", "version": 2, "status": "approved"}, "Moved to approved"),
        ("version", {"change_type": "request_review", "status": "review_requested"}, "Moved to review requested"),
        ("review_requested", {}, "Review requested"),
        ("review_decided", {"status": "approved"}, "Approved"),
        ("review_decided", {"status": "changes_requested"}, "Changes requested"),
        ("review_decided", {"status": "rejected"}, "Rejected"),
        ("comment", {"comment_type": "general"}, "Comment"),
        ("comment", {"comment_type": "review"}, "Review comment"),
        ("comment", {"comment_type": "suggestion"}, "Suggestion"),
        ("comment", {"comment_type": "question"}, "Question"),
        ("plan_execution", {"plan_name": "Sprint 42", "status": "passed", "version": 3},
         "Executed in plan Sprint 42 · PASSED (v3)"),
        ("plan_execution", {"plan_name": "Sprint 42"}, "Executed in plan Sprint 42"),
        ("automated_execution", {"status": "failed"}, "Automated run · FAILED"),
        ("defect", {"jira_key": "PAY-123"}, "Defect PAY-123 opened"),
        ("defect", {}, "Defect opened"),
        ("requirement", {"requirement_key": "REQ-PAY-07", "link_type": "verifies"}, "Linked REQ-PAY-07 (verifies)"),
        ("requirement", {"requirement_key": "REQ-PAY-07", "link_type": "verifies", "linked": False},
         "Unlinked REQ-PAY-07"),
    ],
)
def test_timeline_title(kind, facts, title):
    assert V.timeline_title(kind, **facts) == title


def test_every_timeline_kind_has_a_title_and_an_unknown_one_is_an_error():
    for kind in V.TIMELINE_KINDS:
        assert V.timeline_title(kind, plan_name="P", requirement_key="K")
    with pytest.raises(ValueError):
        V.timeline_title("bogus")
