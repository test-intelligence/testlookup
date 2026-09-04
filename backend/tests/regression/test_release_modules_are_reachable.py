"""Regression: a release-epic service must be reachable from a request.

The finding this file exists for
--------------------------------
The design gate found SIX modules of the release epic that nothing in ``app/``
imports — ``saved_view_release``, ``release_phase_gate_service``,
``release_defect_service``, ``policy_resolution``, and the ``sync_milestones`` /
``sync_fix_versions`` functions. Each was well built and well tested. Each was
reachable only from its own test file.

The cause is structural, not careless. The epic's slices were sized as service
MODULES, and each was verified by testing the module — so every one passed its
own gate while no request could reach any of them. Nothing asked *what call
reaches this code*. A module with no importer is a module whose tests measure
nothing a user can observe, and it fails silently: coverage is high, the tests
are green, and the feature does not exist.

``ALLOWED_UNWIRED`` is the live worklist. An entry is removed as its slice
lands, and the second test below fails if an entry stays behind after its module
gains an importer — an allowlist that is not forced to shrink becomes a
permanent exemption, which is the failure mode it was meant to expose.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"

#: Release-epic service modules, each of which must be reachable from a request.
RELEASE_MODULES = (
    "saved_view_release",
    "release_phase_gate_service",
    "release_defect_service",
    "policy_resolution",
    "release_rollup_service",
    "release_gate_decision_service",
    "release_attribution",
    "release_lifecycle_service",
    "execution_time",
    "github_release_sync",
    "jira_release_sync",
)

#: Not yet wired, with the slice that will wire each. Shrinks to empty.
ALLOWED_UNWIRED = {
    "release_phase_gate_service": "W4 — phase gate endpoint + gated update_phase",
    "release_defect_service": "W3 — release-aware blocking-defect count",
    "policy_resolution": "W4 — reached via the phase gate once that is wired",
    "jira_release_sync": "W2 — external release sync entry point",
}


#: Where a request or a scheduled job actually enters the application.
ENTRY_DIRS = ("routers", "worker")


def _imports_of(path: Path) -> set[str]:
    """Module stems imported by one file.

    Parsed rather than grepped: these modules document each other by name in
    prose ("the sibling of ``github_release_sync``"), and a substring search
    reads those docstrings as imports — reporting a dead module as wired for
    mentioning a live one. Local imports inside functions count, because the
    codebase uses them routinely to break cycles.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - the lint gate fails first
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
            names.update(a.name for a in node.names)
    return names


def _reachable_from_entry_points() -> set[str]:
    """Every module stem reachable by import from a router or a worker task.

    **Transitive, and that is the whole point.** ``policy_resolution`` has an
    importer — ``release_phase_gate_service``, which is itself imported by
    nothing. Asking "does anything import this" calls that pair wired; asking
    "can a request get here" calls both dead, which is the truth. A chain of
    modules importing each other is exactly how a feature looks finished from
    the inside while no caller exists.
    """
    graph = {p.stem: _imports_of(p) for p in APP.rglob("*.py")}
    frontier = [
        p.stem
        for p in APP.rglob("*.py")
        if any(part in ENTRY_DIRS for part in p.relative_to(APP).parts[:-1])
    ]
    seen: set[str] = set()
    while frontier:
        stem = frontier.pop()
        if stem in seen:
            continue
        seen.add(stem)
        frontier.extend(graph.get(stem, set()) - seen)
    return seen


@pytest.fixture(scope="module")
def reachable() -> set[str]:
    return _reachable_from_entry_points()


def test_the_walk_finds_modules_that_are_obviously_wired(reachable):
    """Positive control.

    Without it, a walk that silently returned very little would mark every
    module unreachable and this file would read as one long true finding.
    """
    for known_live in ("release_service", "release_linker", "analytics_service"):
        assert known_live in reachable, (
            f"{known_live} is plainly reached from a router — the reachability "
            "walk is broken, not the codebase"
        )


@pytest.mark.parametrize("module", RELEASE_MODULES)
def test_the_module_is_reachable_from_a_request(module, reachable):
    if module in ALLOWED_UNWIRED:
        pytest.skip(f"not yet wired: {ALLOWED_UNWIRED[module]}")
    assert module in reachable, (
        f"app/services/{module}.py cannot be reached from any router or worker "
        "task — it is exercised only by its own tests, so its green suite "
        "measures nothing a user can observe"
    )


def test_the_worklist_does_not_outlive_the_work(reachable):
    """A stale exemption is worse than no exemption.

    Left behind, it keeps asserting a module is unreachable long after it was
    wired, and the next reader trusts it.
    """
    stale = {m for m in ALLOWED_UNWIRED if m in reachable}
    assert not stale, (
        f"{sorted(stale)} are now reachable — remove them from "
        "ALLOWED_UNWIRED so the guard covers them"
    )


def test_the_worklist_only_names_modules_the_guard_watches():
    assert not set(ALLOWED_UNWIRED) - set(RELEASE_MODULES), (
        "an exemption for a module the guard does not check exempts nothing"
    )


# ── Dead functions inside live modules ───────────────────────────────────────
#
# Module reachability is not enough. ``github_release_sync`` IS reached — by
# ``release_linker``, for the attribution ladder's rung 2 — while
# ``sync_milestones`` inside it is called by nothing. A module-level guard
# reports that file as wired and the dead entry point survives underneath.

ENTRY_FUNCTIONS = ("sync_milestones", "sync_fix_versions")

ALLOWED_UNCALLED = {
    "sync_milestones": "W2 — external release sync entry point",
    "sync_fix_versions": "W2 — external release sync entry point",
}


def _callers_of(func: str) -> set[str]:
    """Files under ``app/`` containing a CALL to ``func``.

    A call, not a mention: both of these functions are named in the prose of
    modules that never invoke them, and both are re-exported by name in import
    lists. Only ``ast.Call`` counts.
    """
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            if name == func and path.stem != func:
                found.add(str(path.relative_to(APP)))
    return found


def test_the_caller_scan_finds_a_function_that_is_obviously_called():
    """Positive control for the call scan, matching the one above it."""
    assert _callers_of("resolve_release_for_run"), (
        "release_linker calls this for rung 2 — the call scan is broken"
    )


@pytest.mark.parametrize("func", ENTRY_FUNCTIONS)
def test_the_entry_function_is_called_by_something(func):
    if func in ALLOWED_UNCALLED:
        pytest.skip(f"not yet wired: {ALLOWED_UNCALLED[func]}")
    assert _callers_of(func), (
        f"{func}() is called from nowhere in app/ — its module is reachable "
        "but this entry point is not, so nothing in the product can trigger it"
    )


def test_the_uncalled_worklist_does_not_outlive_the_work():
    stale = {f for f in ALLOWED_UNCALLED if _callers_of(f)}
    assert not stale, (
        f"{sorted(stale)} now have callers — remove them from ALLOWED_UNCALLED"
    )
