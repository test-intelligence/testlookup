"""Regression guards: the project reset must delete what it says, and succeed.

Two defects, found together. The second is the severe one and is documented on
``test_delete_order_respects_restrict_foreign_keys``: the ``full`` reset
deleted ``test_suites`` before ``canonical_test_cases``, which holds a NOT NULL
``ON DELETE RESTRICT`` FK to it, so on any project that had ever ingested a run
Postgres aborted the transaction and **nothing at all was deleted**. The first
is below.

The defect
----------
``ProjectDataPage`` puts an explicit table list in front of an irreversible,
ADMIN-only wipe, under the heading **"This will permanently delete:"**. For
``full`` it named::

    test_suites, canonical_test_cases, suite_memberships

``_FULL_RESET_TABLES`` deleted the first two and **never** ``suite_memberships``.
Nothing cascaded to it either: a membership names its suite by ``suite_name``
(a ``String``, not a FK to ``test_suites.id``), its three ``test_runs``
pointers are ``SET NULL``, and its only ``CASCADE`` is on ``projects.id`` —
which a reset never deletes, since the Project row is a documented keep.

The surviving rows were not inert. ``suite_sync_service`` still writes this
table on every ingest (migration 0075 deferred that move to a Phase 2 that
has not landed), so on the first post-reset ingest every pre-reset
fingerprint took the "already seen" branch: counted unchanged/restored rather
than added, ``first_seen_run_id`` never assigned and NULL forever, and any
survivor missing from that run marked ``needs_review`` and moved to the
``-deleted`` bucket. A project advertised as green-field would report
restoring and deleting tests it had never ingested.

Why the guard is shaped this way
--------------------------------
Pinning the string ``suite_memberships`` would guard the instance. The class
is *"the modal promises a table the code does not delete"*, so this compares
the whole rendered list against the whole delete set, and computes cascade
reachability from the models rather than trusting the ``(CASCADE)`` annotations
the page writes next to a name.

Reachability has to be **transitive**: the page claims ``ai_analysis
(CASCADE)``, which is true only via ``test_cases`` — ``ai_analysis`` has no FK
to ``test_runs`` at all.

Both directions matter, and the second one more:

* a table promised but not deleted  → the user keeps data they were told was gone
* a table deleted but not promised  → the blast radius silently grew

For a destructive action the second is the dangerous one, so it is not merely
symmetry.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.regression

REPO = pathlib.Path(__file__).resolve().parents[3]
PAGE = REPO / "frontend" / "src" / "pages" / "settings" / "ProjectDataPage.tsx"
MODELS = REPO / "backend" / "app" / "models" / "postgres.py"

#: Names the modal uses as prose rather than as a table.
_PROSE = re.compile(r"^(everything|the project|managed test cases \(rag\))", re.I)


def _documented_deletes(mode: str) -> set[str]:
    """Table names from one ``MODE_SPECS`` entry's ``deletes`` array.

    The page writes entries like ``'test_cases (CASCADE)'`` and groups several
    names on one line, so split on commas and strip the annotation.
    """
    src = PAGE.read_text(encoding="utf-8")
    start = src.index(f"mode: '{mode}'")
    block = src[start : src.index("keeps:", start)]
    body = block[block.index("deletes:") :]

    names: set[str] = set()
    for raw in re.findall(r"'([^']+)'", body) + re.findall(r"[‘’]([^‘’]+)[‘’]", body):
        for part in raw.split(","):
            part = part.strip()
            part = re.sub(r"\s*\(.*?\)\s*", "", part).strip()
            if not part or _PROSE.match(part):
                continue
            if re.fullmatch(r"[a-z_]+", part):
                names.add(part)
    return names


def _fk_graph() -> dict[str, list[tuple[str, str]]]:
    """``table -> [(parent_table, ondelete), ...]`` parsed from the models."""
    src = MODELS.read_text(encoding="utf-8")
    graph: dict[str, list[tuple[str, str]]] = {}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ClassDef):
            continue
        seg = ast.get_source_segment(src, node) or ""
        m = re.search(r'__tablename__\s*=\s*"([^"]+)"', seg)
        if not m:
            continue
        edges: list[tuple[str, str]] = []
        for fk in re.finditer(r'ForeignKey\(\s*"([a-z_]+)\.id"([^)]*)\)', seg):
            parent, rest = fk.group(1), fk.group(2)
            # "BLOCKING" covers an explicit RESTRICT and an omitted ondelete
            # alike: Postgres defaults to NO ACTION, which refuses the delete
            # just the same.
            od = (
                "CASCADE" if "CASCADE" in rest
                else "SET NULL" if "SET NULL" in rest
                else "BLOCKING"
            )
            edges.append((parent, od))
        graph[m.group(1)] = edges
    return graph


def _cascade_reachable(roots: set[str]) -> set[str]:
    """Tables a DB-level ``ON DELETE CASCADE`` removes when *roots* are deleted."""
    graph = _fk_graph()
    reached = set(roots)
    changed = True
    while changed:
        changed = False
        for table, edges in graph.items():
            if table in reached:
                continue
            if any(parent in reached and od == "CASCADE" for parent, od in edges):
                reached.add(table)
                changed = True
    return reached


def _explicit_deletes() -> set[str]:
    from app.services.project_reset_service import _FULL_RESET_TABLES

    return {name for name, _model in _FULL_RESET_TABLES}


def _actually_deleted(mode: str) -> set[str]:
    """Everything a *mode* removes: its explicit deletes plus their cascades.

    The roots differ by mode, and rooting the walk at ``test_runs`` alone
    would under-count ``full`` — that mode also deletes ``test_suites`` and
    friends outright, so anything CASCADE-ing from *those* goes as well.
    Getting this wrong makes the guard claim a promise is broken when the row
    really does disappear.
    """
    roots = {"test_runs"}
    if mode == "full":
        roots |= _explicit_deletes()
    return _cascade_reachable(roots)


# ── fail-open checks ─────────────────────────────────────────────────────────
# Each parser reads prose or source that is free to be reformatted. An empty
# parse must fail loudly here rather than make every assertion below vacuous.


@pytest.mark.skipif(not PAGE.exists(), reason="frontend tree not present")
def test_the_guard_can_read_the_modal():
    for mode in ("runs", "full"):
        found = _documented_deletes(mode)
        assert len(found) >= 3, (
            f"parsed too few table names from the {mode!r} deletes list: {found}. "
            "MODE_SPECS probably changed shape and the comparisons below would "
            "pass without comparing anything."
        )


def test_the_guard_can_read_the_fk_graph():
    graph = _fk_graph()
    assert len(graph) > 50, f"parsed only {len(graph)} tables from the models"
    # A known transitive chain: ai_analysis -> test_cases -> test_runs.
    assert ("test_cases", "CASCADE") in graph["ai_analysis"]
    assert ("test_runs", "CASCADE") in graph["test_cases"]
    reachable = _cascade_reachable({"test_runs"})
    assert "ai_analysis" in reachable, "transitive cascade walk is not walking"


# ── the promise ──────────────────────────────────────────────────────────────


@pytest.mark.skipif(not PAGE.exists(), reason="frontend tree not present")
def test_every_promised_table_is_actually_deleted():
    """Nothing may be listed under "This will permanently delete" unless it is."""
    for mode in ("runs", "full"):
        promised = _documented_deletes(mode)
        broken = sorted(promised - _actually_deleted(mode))
        assert not broken, (
            f"the {mode!r} reset modal promises to permanently delete {broken}, "
            "but the reset neither deletes those tables explicitly "
            "(_FULL_RESET_TABLES) nor reaches them by ON DELETE CASCADE from "
            "test_runs. The user is told data is gone and it is still there."
        )


@pytest.mark.skipif(not PAGE.exists(), reason="frontend tree not present")
def test_nothing_is_deleted_that_the_modal_does_not_name():
    """The blast radius may not grow silently.

    Adding a table to ``_FULL_RESET_TABLES`` without adding it to the modal
    would wipe data the user was never warned about — the more dangerous
    direction of the two.
    """
    promised = _documented_deletes("full") | _documented_deletes("runs")
    unannounced = sorted(_explicit_deletes() - promised)
    assert not unannounced, (
        f"the full reset deletes {unannounced}, which the confirmation modal "
        "never names. Add them to MODE_SPECS['full'].deletes so the warning "
        "matches the blast radius."
    )


def test_delete_order_respects_restrict_foreign_keys():
    """A parent may not be deleted before a child that RESTRICTs it.

    ``canonical_test_cases.test_suite_id`` is NOT NULL ``ON DELETE RESTRICT``,
    and ``test_suites`` was listed first. Postgres aborted the transaction on
    every project that had ever ingested a run, so the whole ``full`` reset
    deleted nothing — including the ``test_runs`` rows it had already removed
    earlier in the same transaction. Proven on PostgreSQL 16:

        ERROR: update or delete on table "test_suites" violates foreign key
        constraint "canonical_test_cases_test_suite_id_fkey"

    The unit tests could not catch it: they mock ``db.execute``, so no
    constraint is ever evaluated. This checks the ordering statically instead,
    for every pair rather than the one that broke.
    """
    from app.services.project_reset_service import _FULL_RESET_TABLES

    order = [name for name, _model in _FULL_RESET_TABLES]
    position = {name: i for i, name in enumerate(order)}
    graph = _fk_graph()

    problems = []
    for child, edges in graph.items():
        for parent, ondelete in edges:
            if ondelete in ("CASCADE", "SET NULL"):
                continue  # the DB resolves these for us
            if parent in position and child in position:
                if position[child] > position[parent]:
                    problems.append(
                        f"{child} (#{position[child]}) holds a blocking "
                        f"{ondelete} FK to {parent} (#{position[parent]}) but "
                        f"is deleted after it"
                    )
    assert not problems, (
        "_FULL_RESET_TABLES deletes a parent before a child that RESTRICTs "
        "it; Postgres will abort the whole reset transaction:\n  "
        + "\n  ".join(problems)
    )


def test_the_restrict_fk_this_ordering_exists_for_is_still_there():
    """Fail-open: if the RESTRICT is gone, the ordering test proves nothing."""
    graph = _fk_graph()
    assert ("test_suites", "BLOCKING") in graph["canonical_test_cases"], (
        "canonical_test_cases no longer RESTRICTs test_suites — the ordering "
        "guard above would now pass vacuously. Re-read it."
    )


def test_suite_memberships_cannot_be_reached_by_cascade():
    """Pin the premise, so the fix cannot be quietly undone.

    If someone later adds a CASCADE FK from ``suite_memberships`` to
    ``test_suites``, the explicit delete becomes redundant — but this guard's
    reasoning would also be stale, and it should be re-read rather than keep
    passing for the wrong reason.
    """
    others = _explicit_deletes() - {"suite_memberships", "suite_membership_events"}
    assert "suite_memberships" not in _cascade_reachable({"test_runs"} | others), (
        "suite_memberships is now cascade-reachable from something the reset "
        "already deletes; revisit whether the explicit delete in "
        "_FULL_RESET_TABLES is still needed, rather than leaving this guard "
        "passing for a reason that no longer holds."
    )
    assert "suite_memberships" in _explicit_deletes(), (
        "suite_memberships is not cascade-reachable, so it must stay in "
        "_FULL_RESET_TABLES or the modal's promise breaks again."
    )
