"""W23a: the insight / cadence vocabulary the UI renders is the server's, and the server's copies agree.

``frontend/src/types/insights.ts`` mirrors ``services/insight_vocab.py`` and
``frontend/src/types/runCadence.ts`` mirrors ``services/run_cadence_vocab.py``,
constant for constant (kinds, severities, resolve reasons, ranges, the link
paths, the not-measured sentences and the refusal sentences; modes, weekdays,
outcomes, the grace / pause / status-day bounds and the cadence sentences).
Read as TEXT (no Node in the backend suite), with the W22b regex readers of
``test_retention_export_vocab_frontend_parity.py``. A drifted copy would show
one sentence on the client and refuse with another on the server.

The server-side copies agree too: ``RUN_MISSED_EVENT`` is
``NotificationEventType.RUN_MISSED``; every kind / severity / resolve reason /
mode / window outcome is in the matching CHECK of migration 0243 / 0244
(source read) and of the ORM; the flake detector's floors are the literals of
``analytics_service.flaky_tests``' SQL; and the flake detector imports
``flaky_signals.MIN_FLIPS_FOR_INTERMITTENCY`` rather than declaring its own
flip count.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from app.services import insight_vocab as iv
from app.services import run_cadence_vocab as cv

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND_TYPES = BACKEND.parent / "frontend" / "src" / "types"
INSIGHTS_TS = FRONTEND_TYPES / "insights.ts"
CADENCE_TS = FRONTEND_TYPES / "runCadence.ts"
VERSIONS = BACKEND / "migrations" / "versions"
SERVICES = BACKEND / "app" / "services"

# A single- or double-quoted TS string literal (a value carrying an apostrophe is double-quoted).
_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _strings(text: str) -> tuple[str, ...]:
    return tuple(a or b for a, b in re.findall(_STR, text))


def _array(path: Path, name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _read(path))
    assert match, f"{name} is not declared as an `as const` array literal in {path.name}"
    return _strings(match.group(1))


def _number_array(path: Path, name: str) -> tuple[int, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _read(path))
    assert match, f"{name} is not declared as an `as const` array literal in {path.name}"
    return tuple(int(n) for n in re.findall(r"\d+", match.group(1)))


def _number(path: Path, name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _read(path), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {path.name}"
    return int(match.group(1))


def _string(path: Path, name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*{_STR}\s*$", _read(path), re.MULTILINE)
    assert match, f"{name} is not declared as a one-piece string literal in {path.name}"
    return match.group(1) or match.group(2) or ""


def _record(path: Path, name: str) -> dict[str, str]:
    # The literal ends at the first column-0 `} as const`.
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _read(path), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {path.name}"
    body = match.group(1)
    entries = {
        key: a or b
        for key, a, b in re.findall(rf"^\s*'?([A-Za-z_.]+)'?:\s*{_STR},?\s*$", body, re.MULTILINE)
    }
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


# ── types/insights.ts ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("INSIGHT_KINDS", iv.INSIGHT_KINDS),
        ("INSIGHT_SEVERITIES", iv.INSIGHT_SEVERITIES),
        ("INSIGHT_RESOLVE_REASONS", iv.INSIGHT_RESOLVE_REASONS),
        ("INSIGHT_RANGES", tuple(iv.INSIGHT_RANGES)),
        ("INSIGHT_LINK_PATHS", tuple(path for path, _label in iv.INSIGHT_LINKS.values())),
    ],
)
def test_the_insight_arrays_equal_the_server(name, server):
    assert _array(INSIGHTS_TS, name) == tuple(server)


def test_the_insight_scalars_and_sentences_equal_the_server():
    assert _string(INSIGHTS_TS, "INSIGHT_FLAG") == iv.INSIGHT_FLAG
    assert _string(INSIGHTS_TS, "INSIGHT_RANGE_DEFAULT") == iv.INSIGHT_RANGE_DEFAULT
    assert _number(INSIGHTS_TS, "INSIGHT_LIST_MAX") == iv.INSIGHT_LIST_MAX
    assert _number(INSIGHTS_TS, "INSIGHT_LIST_DEFAULT") == iv.INSIGHT_LIST_DEFAULT
    assert _record(INSIGHTS_TS, "NOT_MEASURED") == iv.NOT_MEASURED
    assert _record(INSIGHTS_TS, "INSIGHT_ERRORS") == iv.INSIGHT_ERRORS
    assert list(iv.INSIGHT_LINKS) == list(iv.INSIGHT_KINDS)       # one link per kind, in kind order


# ── types/runCadence.ts ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("CADENCE_MODES", cv.CADENCE_MODES),
        ("WEEKDAYS", cv.WEEKDAYS),
        ("WINDOW_OUTCOMES", cv.WINDOW_OUTCOMES),
        ("STATUS_OUTCOMES", cv.STATUS_OUTCOMES),
    ],
)
def test_the_cadence_arrays_equal_the_server(name, server):
    assert _array(CADENCE_TS, name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("GRACE_DEFAULT", cv.GRACE_DEFAULT),
        ("GRACE_MIN", cv.GRACE_MIN),
        ("GRACE_MAX", cv.GRACE_MAX),
        ("PAUSE_MAX_DAYS", cv.PAUSE_MAX_DAYS),
        ("PAUSE_REASON_MAX", cv.PAUSE_REASON_MAX),
    ],
)
def test_the_cadence_numbers_equal_the_server(name, server):
    assert _number(CADENCE_TS, name) == server


def test_the_cadence_status_days_and_sentences_equal_the_server():
    assert _number_array(CADENCE_TS, "STATUS_DAYS") == cv.STATUS_DAYS
    assert _string(CADENCE_TS, "NO_CADENCE_SENTENCE") == cv.NO_CADENCE_SENTENCE
    assert _string(CADENCE_TS, "CONFIGURE_LABEL") == cv.CONFIGURE_LABEL
    assert _record(CADENCE_TS, "CADENCE_ERRORS") == cv.CADENCE_ERRORS
    assert set(cv.WINDOW_OUTCOMES) < set(cv.STATUS_OUTCOMES)      # /status only ADDS "pending"


# ── the server-side copies agree ─────────────────────────────────────────────

def test_the_run_missed_event_is_the_notification_enum_value():
    from app.models.postgres import NotificationEventType

    assert cv.RUN_MISSED_EVENT == NotificationEventType.RUN_MISSED.value


def _tuple_constant(source: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"^{name} = \(([^)]*)\)", source, re.MULTILINE)
    assert match, f"{name} is not a tuple literal"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.parametrize(
    ("filename", "constant", "check", "vocab"),
    [
        ("0243_cross_run_insights.py", "_KINDS", "ck_project_insights_kind", iv.INSIGHT_KINDS),
        ("0243_cross_run_insights.py", "_KINDS", "ck_insight_detector_states_kind", iv.INSIGHT_KINDS),
        ("0243_cross_run_insights.py", "_SEVERITIES", "ck_project_insights_severity", iv.INSIGHT_SEVERITIES),
        ("0243_cross_run_insights.py", "_REASONS", "ck_project_insights_resolved_reason", iv.INSIGHT_RESOLVE_REASONS),
        ("0244_run_cadences.py", "_MODES", "ck_run_cadences_mode", cv.CADENCE_MODES),
        ("0244_run_cadences.py", "_OUTCOMES", "ck_run_cadence_windows_outcome", cv.WINDOW_OUTCOMES),
    ],
)
def test_every_vocabulary_literal_is_in_the_migration_check_and_the_orm(filename, constant, check, vocab):
    source = (VERSIONS / filename).read_text(encoding="utf-8")
    assert _tuple_constant(source, constant) == tuple(vocab)
    assert f"_in({constant})" in re.search(rf'CheckConstraint\(\s*(f?"[^"]*")[^)]*name="{check}"', source).group(1)

    from app.models import postgres as models

    orm_checks = {
        c.name: str(c.sqltext)
        for table in models.Base.metadata.tables.values()
        for c in table.constraints
        if c.__class__.__name__ == "CheckConstraint" and c.name == check
    }
    assert set(orm_checks) == {check}
    assert tuple(re.findall(r"'([^']+)'", orm_checks[check])) == tuple(vocab)


def test_the_flake_floors_are_the_flaky_tests_sql_literals():
    from app.services import analytics_service

    sql = inspect.getsource(analytics_service.flaky_tests)
    having = re.search(r"HAVING COUNT\(\*\) >= (\d+)", sql)
    band = re.search(r"BETWEEN ([\d.]+) AND ([\d.]+)", sql)
    assert having and band, "flaky_tests' HAVING clause changed shape; re-read FAIL-11's flake rule"
    assert iv.FLAKE_MIN_OBSERVATIONS == int(having.group(1))
    assert iv.FLAKE_FAIL_SHARE == (float(band.group(1)), float(band.group(2)))


def _module_tree(name: str) -> ast.Module:
    path = SERVICES / name
    if not path.exists():
        pytest.fail(f"{path.name} is missing (G-B's FAIL-11 engine): waiting for G-B")
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_flake_detector_imports_the_flip_count_and_declares_none_of_its_own():
    imported: list[str] = []
    for name in ("insight_detectors.py", "insights_service.py"):
        tree = _module_tree(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.flaky_signals":
                imported += [alias.name for alias in node.names]
        own = [
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name) and "FLIP" in target.id.upper()
        ]
        assert not own, f"{name} declares its own flip count {own}; import MIN_FLIPS_FOR_INTERMITTENCY instead"
    assert "MIN_FLIPS_FOR_INTERMITTENCY" in imported
