"""Regression guard: seeding NotificationTestState must survive a race.

The defect (NOTIF-001)
----------------------
``dispatch_transition_notifications`` seeded missing state rows with a plain
read-then-insert::

    state_rows = select(NotificationTestState).where(project, fp.in_(...))
    for fp in fingerprints:
        if fp not in states_by_fp:
            db.add(NotificationTestState(...))

``notification_test_states`` has ``UniqueConstraint("project_id",
"test_fingerprint")``. Two runs finalizing concurrently for the same project
both saw no row for a fingerprint, both inserted, and the second raised
IntegrityError.

The blast radius is bigger than one row: an IntegrityError aborts the whole
transaction, so the entire notification evaluation for that run is lost, not
just the seed. The engine owns its own session and commits it (see the
allowlist in ``test_architectural_transaction_boundaries.py``), so there is no
outer handler to salvage it.

The fix is ``ON CONFLICT DO NOTHING`` plus a re-select. ``DO NOTHING`` cannot
return the conflicting row, so the rows must be read back; under READ
COMMITTED the conflicting insert blocks until the other transaction commits,
after which the SELECT sees its row.

Why a static guard
------------------
Reproducing the race needs two concurrent transactions against a live
Postgres, which the unit suite has no access to -- and a mocked session never
evaluates a constraint at all (see
``test_project_reset_promises_match_code.py`` for what that blindness cost
elsewhere). So this asserts the *shape* of the write instead: the seed must be
a conflict-tolerant insert, and it must read the rows back.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest

pytestmark = pytest.mark.regression

SERVICE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app" / "services" / "notification_transitions.py"
)


#: The function that loads and seeds the per-fingerprint state rows. Named
#: explicitly rather than sniffed: the first version of this guard took "the
#: first function mentioning NotificationTestState and states_by_fp", which
#: matched the PURE state machine ``evaluate_case_transitions`` -- it receives
#: states_by_fp as a parameter and never writes a row. The guard then asserted
#: against a function that could not contain the fix.
_SEED_FUNCTION = "_load_or_seed_states"


def _seed_function_source() -> str:
    """Source of :data:`_SEED_FUNCTION`, parsed from the AST.

    AST rather than string slicing: a previous guard in this repo sliced to
    the first ``}`` and got truncated by a brace inside a comment, leaving it
    asserting against 60 characters of prose.
    """
    src = SERVICE.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == _SEED_FUNCTION):
            return ast.get_source_segment(src, node) or ""
    return ""


def test_the_guard_found_the_seeding_function():
    """Fail-open: a rename or an empty parse must fail loudly, not silently."""
    src = _seed_function_source()
    assert len(src) > 500, (
        f"could not locate {_SEED_FUNCTION}() (got {len(src)} chars). It was "
        "probably renamed; every assertion below would otherwise pass without "
        "checking anything."
    )
    # Pin that this really is the function that WRITES the rows, not one that
    # merely receives them. Parenthesised deliberately: `A and B or C` binds
    # as `(A and B) or C`, so a bare `pg_insert` would have satisfied the
    # whole condition on its own and the check would prove nothing.
    assert "states_by_fp" in src, f"{_SEED_FUNCTION}() does not build states_by_fp"
    assert ("pg_insert" in src) or ("NotificationTestState(" in src), (
        f"{_SEED_FUNCTION}() no longer writes NotificationTestState rows, so "
        "it is not the function this guard is meant to be checking"
    )


def test_seed_insert_tolerates_a_concurrent_insert():
    """The seed must not be a bare ``db.add`` of a uniquely-constrained row."""
    src = _seed_function_source()
    assert "on_conflict_do_nothing" in src, (
        "the NotificationTestState seed is not conflict-tolerant. Two runs "
        "finalizing concurrently for the same project will both insert the "
        "same (project_id, test_fingerprint) and the second aborts the whole "
        "transaction, losing that run's entire notification evaluation."
    )
    assert re.search(r"index_elements\s*=\s*\[[^\]]*project_id", src), (
        "on_conflict_do_nothing must name the conflict target "
        "(project_id, test_fingerprint) so it cannot silently swallow a "
        "DIFFERENT constraint violation."
    )
    assert re.search(r"index_elements\s*=\s*\[[^\]]*test_fingerprint", src), (
        "the conflict target is missing test_fingerprint"
    )


def test_seeded_rows_are_read_back():
    """``DO NOTHING`` returns no row, so the rows must be re-selected.

    Without the read-back the caller would hold no object for a fingerprint
    the other transaction inserted, and ``evaluate_case_transitions`` would
    KeyError or silently skip that test.
    """
    src = _seed_function_source()
    after = src.split("on_conflict_do_nothing", 1)[1]
    assert "select(NotificationTestState)" in after, (
        "nothing re-selects the state rows after the conflict-tolerant "
        "insert, so a row inserted by a concurrent finalization is never "
        "loaded into states_by_fp."
    )
    assert "states_by_fp[" in after, (
        "the re-selected rows are never merged back into states_by_fp"
    )


def test_the_unique_constraint_this_guards_still_exists():
    """Fail-open: with no unique constraint there is no race to guard."""
    from app.models.postgres import NotificationTestState

    names = {
        getattr(c, "name", None)
        for c in NotificationTestState.__table__.constraints
    }
    assert "uq_notif_test_state_project_fp" in names, (
        "uq_notif_test_state_project_fp is gone. Either the race no longer "
        "exists and this guard should be retired, or the constraint was "
        "dropped by accident and duplicate state rows are now possible."
    )


def test_engine_still_owns_its_transaction():
    """Context the fix depends on.

    The engine opens its own session and commits it, so an IntegrityError has
    no outer handler to recover it -- which is why the seed must not raise in
    the first place. If that ownership ever changes, re-read this guard rather
    than trusting it.
    """
    from app.services import notification_transitions as mod

    doc = inspect.getdoc(mod) or ""
    assert "Celery-task-owned" in doc or "commits" in doc, (
        "the module no longer documents owning its own transaction; the "
        "reasoning behind this guard needs revisiting."
    )


# ── Executable behaviour ────────────────────────────────────────────────────
# The static assertions above pin the SHAPE of the write. These run the code.
# Worth stating why they exist: before this fix `evaluate_run_transitions` had
# NO execution coverage at all -- 102 notification tests passed while the
# seeding path was never once run -- which is a large part of why NOTIF-001
# survived. A fingerprint missing from the returned map is silently skipped by
# `evaluate_case_transitions` (`state is None -> continue`), so a half-done
# merge costs notifications and raises nothing.


class _Row:
    def __init__(self, fp, known_flaky=False):
        self.test_fingerprint = fp
        self.is_known_flaky = known_flaky


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Answers the two SELECTs and records the INSERT.

    ``existing`` is what the first SELECT returns; ``after_insert`` is what the
    read-back returns, which is how a row committed by a CONCURRENT
    finalization is simulated.
    """

    def __init__(self, existing, after_insert):
        self._existing = existing
        self._after_insert = after_insert
        self.selects = 0
        self.inserts = 0
        self.flushes = 0

    async def execute(self, stmt):
        text = str(stmt)
        if "INSERT" in text.upper():
            self.inserts += 1
            return _Scalars([])
        self.selects += 1
        return _Scalars(self._existing if self.selects == 1 else self._after_insert)

    async def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_existing_rows_short_circuit_the_insert():
    """Nothing missing means no write at all."""
    from app.services.notification_transitions import _load_or_seed_states
    import uuid as _uuid

    rows = [_Row("fp-a"), _Row("fp-b")]
    db = _FakeSession(existing=rows, after_insert=[])
    out = await _load_or_seed_states(db, _uuid.uuid4(), ["fp-a", "fp-b"], set())

    assert set(out) == {"fp-a", "fp-b"}
    assert db.inserts == 0, "seeded rows that already existed"
    assert db.selects == 1, "re-selected when nothing was missing"


@pytest.mark.asyncio
async def test_a_row_lost_to_conflict_is_still_returned():
    """The race, from this transaction's point of view.

    ``fp-new`` is absent from the first SELECT, our INSERT hits DO NOTHING
    because a concurrent finalization already committed it, and the read-back
    is the ONLY way we get an object for it. Without the merge this fingerprint
    would be missing and its transition silently dropped.
    """
    from app.services.notification_transitions import _load_or_seed_states
    import uuid as _uuid

    db = _FakeSession(existing=[_Row("fp-old")], after_insert=[_Row("fp-new")])
    out = await _load_or_seed_states(
        db, _uuid.uuid4(), ["fp-old", "fp-new"], set()
    )

    assert db.inserts == 1 and db.flushes == 1
    assert "fp-new" in out, (
        "the row that lost the insert race is missing from the returned map, "
        "so evaluate_case_transitions would silently skip that test and its "
        "notification would never fire."
    )
    assert set(out) == {"fp-old", "fp-new"}


@pytest.mark.asyncio
async def test_only_missing_fingerprints_are_seeded():
    """The insert must cover the missing ones, not re-insert what was read."""
    from app.services.notification_transitions import _load_or_seed_states
    import uuid as _uuid

    captured = {}

    class _Capture(_FakeSession):
        async def execute(self, stmt):
            text = str(stmt)
            if "INSERT" in text.upper():
                captured["sql"] = text
            return await super().execute(stmt)

    db = _Capture(existing=[_Row("fp-old")], after_insert=[_Row("fp-new")])
    await _load_or_seed_states(db, _uuid.uuid4(), ["fp-old", "fp-new"], set())

    assert "sql" in captured, "no INSERT was issued for the missing fingerprint"
    assert "ON CONFLICT" in captured["sql"].upper(), (
        "the executed INSERT is not conflict-tolerant:\n" + captured["sql"]
    )
