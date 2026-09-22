"""Regression guard: the nightly quarantine loop must not have a dead end.

The defect
----------
``run_recheck_cycle`` ends a still-flaky test's window by writing
``RE_QUARANTINED`` with a fresh ``quarantine_expires_at`` and a fresh
``recheck_at``. ``schedule_pending_rechecks`` — the sweep that arms the next
evaluation — selected ``status == QUARANTINED`` only. Nothing else selects
``RE_QUARANTINED``, so once a row landed there it was **never looked at
again**:

* it stayed in ``_ACTIVE_QUARANTINE_STATES`` past its own expiry, for ever;
* the CI quarantine manifest kept publishing it, so ``testlookup ci-verdict``
  kept exiting 0 for that test's failures;
* ingestion kept tagging it ``quarantined``, so the release gate kept
  excluding it.

A test quarantined twice could therefore never fail a build again — not when
it regressed, and not when it was fixed. Measured against a real Postgres
running the real beat chain, clock advanced 14 days per night, for a test
that is flaky on night 1 and fixed from then on:

    night 2: 12 consecutive passes, rechecks_scheduled=0 -> RE_QUARANTINED
    night 3: 24 consecutive passes, rechecks_scheduled=0 -> RE_QUARANTINED

With the fix, night 2 sweeps the row and the same pass releases it.

The model's own state machine always said this should loop —
``postgres.py``: ``flip-rate still high -> [RE_QUARANTINED] -> QUARANTINED
(new window)``. Only the query disagreed, and a query is not visible in a
docstring.

What is guarded
---------------
Not "the tuple contains RE_QUARANTINED" — the *class*: **every non-terminal
status the nightly maintenance chain writes must be one a sweep in that same
chain can move a row out of.** A live state nothing can leave is a trap. Add
a new one tomorrow and forget the selector, and this fails.

Two scope notes, both of which decide whether the guard can see anything:

* ``update_quarantine_stability`` reads ``_ACTIVE_QUARANTINE_STATES`` (which
  does contain ``RE_QUARANTINED``) and is deliberately not counted. It runs
  at run finalization, only advances a pass counter, and releases nothing
  unless the per-project ``auto_promote`` policy is on — off by default.
* Selecting a status is not the same as escaping it. ``mark_stale_quarantines``
  selects every active status and only stamps a notification timestamp. The
  first version of this guard counted that read and therefore **survived both
  mutations of the very defect it was written for**. A status counts only when
  the function selecting it also writes some status.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.regression

pytest.importorskip("sqlalchemy")

from app.models.postgres import (  # noqa: E402
    FlakyQuarantineStatus,
    _LIVE_QUARANTINE_STATES,
)
from app.services import flaky_quarantine_service as svc  # noqa: E402

SERVICE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app" / "services" / "flaky_quarantine_service.py"
)

#: The nightly beat chain, in the order ``run_flaky_quarantine_maintenance``
#: calls them. Pinned BY NAME: a guard that scans a whole module would count
#: reads from functions that cannot advance the state machine.
MAINTENANCE_FUNCTIONS = (
    "expire_stale_proposals",
    "schedule_pending_rechecks",
    "run_recheck_cycle",
    "mark_stale_quarantines",
)


def _module() -> ast.Module:
    return ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))


def _status_name(node: ast.AST) -> str | None:
    """``FlakyQuarantineStatus.X`` / ``FlakyQuarantineStatus.X.value`` -> ``X``."""
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "FlakyQuarantineStatus"
    ):
        return node.attr
    return None


def _module_status_tuples(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level constants holding tuples of statuses, e.g.
    ``_RECHECKABLE_STATES = (QUARANTINED, RE_QUARANTINED)``."""
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        names = {
            name
            for element in node.value.elts
            if (name := _status_name(element)) is not None
        }
        if not names:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = names
    return out


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(
        f"{name} is not in {SERVICE_PATH.name} — it was renamed or removed. "
        "This guard compares what the nightly chain writes against what it "
        "can select; it cannot do that against a function it cannot find."
    )


def _statuses_written(fn: ast.AST) -> set[str]:
    """Statuses assigned to a ``.status`` attribute inside ``fn``."""
    written: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        touches_status = any(
            isinstance(t, ast.Attribute) and t.attr == "status" for t in node.targets
        )
        if not touches_status:
            continue
        if (name := _status_name(node.value)) is not None:
            written.add(name)
    return written


def _statuses_selected(fn: ast.AST, tuples: dict[str, set[str]]) -> set[str]:
    """Statuses this function can MATCH ON — ``status == X`` or ``status.in_(...)``.

    Only comparison / ``in_`` positions count. A status merely mentioned in a
    log line or a comment does not make a row reachable.
    """
    selected: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            for operand in [node.left, *node.comparators]:
                if (name := _status_name(operand)) is not None:
                    selected.add(name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "in_":
            for arg in node.args:
                elements = arg.elts if isinstance(arg, (ast.Tuple, ast.List, ast.Set)) else [arg]
                for element in elements:
                    if (name := _status_name(element)) is not None:
                        selected.add(name)
                    elif isinstance(element, ast.Name) and element.id in tuples:
                        selected |= tuples[element.id]
                if isinstance(arg, ast.Name) and arg.id in tuples:
                    selected |= tuples[arg.id]
    return selected


def _chain_vocabulary() -> tuple[set[str], set[str]]:
    """``(statuses written, statuses a row can ESCAPE from)``.

    Escapable is deliberately narrower than selected. ``mark_stale_quarantines``
    selects every active status — including ``RE_QUARANTINED`` — but only
    stamps a notification timestamp; a row it reads is exactly as stuck
    afterwards. Counting that read let the first version of this guard survive
    the very mutation it exists to kill. So a status only counts as escapable
    when the function that selects it also **writes some status**, i.e. can
    move the row on.
    """
    tree = _module()
    tuples = _module_status_tuples(tree)
    written: set[str] = set()
    escapable: set[str] = set()
    for name in MAINTENANCE_FUNCTIONS:
        fn = _function(tree, name)
        writes_here = _statuses_written(fn)
        written |= writes_here
        if writes_here:
            escapable |= _statuses_selected(fn, tuples)
    return written, escapable


def test_the_guard_can_actually_read_the_chain():
    """Fail-open: an empty vocabulary would make the real assertion vacuous."""
    written, escapable = _chain_vocabulary()
    assert FlakyQuarantineStatus.RECHECK_SCHEDULED.value in written, (
        f"parsed no RECHECK_SCHEDULED write from {MAINTENANCE_FUNCTIONS}; "
        f"got writes={sorted(written)}"
    )
    assert FlakyQuarantineStatus.RECHECK_SCHEDULED.value in escapable, (
        f"parsed no RECHECK_SCHEDULED selector; got escapable={sorted(escapable)}"
    )


def test_every_live_status_the_nightly_chain_writes_can_be_swept_again():
    """The class: a live state written by the chain must be selectable by it."""
    written, escapable = _chain_vocabulary()
    live = set(_LIVE_QUARANTINE_STATES)
    stranded = sorted((written & live) - escapable)
    assert not stranded, (
        f"the nightly quarantine chain writes {stranded} but no sweep in it "
        f"selects those statuses — a row that lands there is never examined "
        f"again: it stays active past its own expiry, keeps suppressing that "
        f"test's failures in the CI manifest, and keeps it out of release-gate "
        f"scoring for ever. Escapable: {sorted(escapable)}."
    )


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


class _FakeCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_the_recheck_sweep_moves_a_re_quarantined_row():
    """End of the loop, driven: a due RE_QUARANTINED row is armed for recheck.

    Asserts on the compiled SELECT's bound parameters and on the row the sweep
    actually mutated — both executable constructs. A docstring promising the
    transition cannot satisfy either.
    """
    row = SimpleNamespace(
        # A real FlakyQuarantineRequest always carries its project; the sweep
        # reads it to bump that project's analytics epoch after the commit.
        project_id=uuid.uuid4(),
        status=FlakyQuarantineStatus.RE_QUARANTINED.value,
        recheck_at=svc.datetime.now(svc.timezone.utc) - svc.timedelta(days=1),
        updated_at=None,
    )
    captured: dict = {}

    class _FakeDB:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _ScalarsResult([row])

        async def commit(self):
            captured["committed"] = True

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "AsyncSessionLocal", lambda: _FakeCM(_FakeDB())):
        moved = await svc.schedule_pending_rechecks()

    # An ``in_()`` binds its whole tuple as ONE expanding parameter, so the
    # value is a list — flatten or the status never appears and this asserts
    # against something it cannot see.
    bound: set[str] = set()
    for value in captured["stmt"].compile().params.values():
        for item in (value if isinstance(value, (list, tuple, set)) else [value]):
            bound.add(str(item))
    assert FlakyQuarantineStatus.RE_QUARANTINED.value in bound, (
        "schedule_pending_rechecks does not select RE_QUARANTINED rows, so a "
        "re-quarantined test's next recheck is never armed. Bound status "
        f"values: {sorted(b for b in bound if b.isupper())}"
    )
    assert FlakyQuarantineStatus.QUARANTINED.value in bound, (
        "the first-window state must still be swept"
    )
    assert moved == 1
    assert row.status == FlakyQuarantineStatus.RECHECK_SCHEDULED.value, (
        "the sweep selected the row but did not arm it for recheck"
    )
