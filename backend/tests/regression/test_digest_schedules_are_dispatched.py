"""Regression: every digest schedule is actually DELIVERED, not just accepted.

The bug
-------
``DigestSchedule`` declares six members. The dispatchers handled ``PER_RUN``
(on run completion) and ``DAILY`` / ``WEEKLY`` / ``WEEKLY_RETRO`` (on the
beat). ``PER_RELEASE`` and ``PER_SUITE`` had **no dispatcher at all** — while
the UI offered both in its schedule dropdown and ``PER_SUITE`` had its own
scope input. So a user could subscribe, receive a success confirmation, and
never get anything. Silently, forever.

Why the existing test could not see it
---------------------------------------
``test_digest_schedule_vocab.py`` already derives its cases from the enum, and
its docstring says outright that ``WEEKLY_RETRO`` was added to the enum while
the Celery beat was not updated — it was written for exactly this bug class.
But it asserts every member is **accepted** by the API schema
(``test_every_orm_schedule_is_accepted``), and acceptance is the half that was
never broken. A subscription the API stores and nothing delivers passes it.

So this file guards the other half, derived from the same enum, so a seventh
member fails here until something sends it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.models.postgres import DigestSchedule  # noqa: E402
from app.worker import tasks as worker_tasks  # noqa: E402

TASKS_SRC = Path(worker_tasks.__file__).read_text(encoding="utf-8")


def _dispatched_schedules() -> set[str]:
    """Schedule values the worker actually SELECTS SUBSCRIPTIONS ON.

    Read from the selector expressions only — the ``EVENT_DRIVEN_SCHEDULES``
    tuple, any ``.in_([...])`` list, and any ``... == "X"`` comparison — and
    NOT from string literals anywhere in the file.

    That distinction is the whole point, and the first version of this function
    got it wrong in precisely the way it exists to catch: scanning every
    literal, it found ``"PER_SUITE"`` inside ``run_matches_digest_scope`` and
    passed even when the dispatcher had been narrowed back to ``PER_RUN``
    alone. Mutation testing caught it. A name occurring in a helper nothing
    reaches is not a dispatcher, which is the same "the string is present
    somewhere" blindness this file was written to close one layer up.
    """
    members = {s.value for s in DigestSchedule}
    tree = ast.parse(TASKS_SRC)
    found: set[str] = set()

    def _consts(node) -> set[str]:
        out = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and child.value in members:
                out.add(child.value)
        return out

    for node in ast.walk(tree):
        # DigestSubscription.schedule.in_(...) — a literal list, or a module
        # constant, which is RESOLVED rather than read where it is defined.
        #
        # Crediting the constant at its assignment was the third hole mutation
        # testing found here: swapping the query to `== "PER_RUN"` leaves
        # EVENT_DRIVEN_SCHEDULES defined, unused and still naming all three.
        # A constant nothing queries with dispatches nothing.
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "in_":
            if not _is_model_schedule(node.func.value) or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                value = getattr(worker_tasks, arg.id, ()) or ()
                found |= {v for v in value if v in members}
            else:
                found |= _consts(arg)
        # DigestSubscription.schedule == "PER_RUN"
        elif isinstance(node, ast.Compare) and _is_model_schedule(node.left):
            found |= _consts(node)
    return found


def _is_model_schedule(node) -> bool:
    """``DigestSubscription.schedule`` — the QUERY column, not ``sub.schedule``.

    Both are ``Attribute`` nodes ending in ``schedule``, and accepting either
    let the guard keep passing when the dispatcher was narrowed back to
    ``PER_RUN``: ``run_matches_digest_scope`` compares ``sub.schedule ==
    "PER_SUITE"`` on a row it would then never be handed. Mutation testing
    caught this on the second attempt at this function, having caught a laxer
    version on the first — the same "close enough to the right name" mistake
    twice, one refinement apart.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "schedule"
        and getattr(node.value, "id", "") == "DigestSubscription"
    )


@pytest.mark.parametrize("schedule", [s.value for s in DigestSchedule])
def test_every_schedule_has_a_dispatcher(schedule):
    """Accepted is not delivered.

    A member the API stores and no dispatcher reads is a subscription that can
    never fire — and it fails silently, which is the worst way for it to fail:
    the user has a confirmation and an empty inbox, and nothing anywhere
    reports an error.
    """
    assert schedule in _dispatched_schedules(), (
        f"DigestSchedule.{schedule} is offered and stored but no dispatcher in "
        "worker/tasks.py filters on it — subscribers would never receive it, "
        "with no error anywhere"
    )


def test_the_scan_can_actually_fail():
    """Positive control.

    If the scan returned every string in the file, or the enum were empty, the
    test above would pass vacuously for any member at all.
    """
    found = _dispatched_schedules()
    assert found, "the scan found no schedules — it is broken, not the code"
    assert "NOT_A_REAL_SCHEDULE" not in found


# ── The scope filter the two new schedules rely on ───────────────────────────


class _Sub:
    def __init__(self, schedule, scope_value=None):
        self.schedule = schedule
        self.scope_value = scope_value


class _Run:
    def __init__(self, suite=None, release=None):
        self.primary_suite_name = suite
        self.primary_release_id = release


def test_per_run_stays_unscoped():
    """Unchanged behaviour: PER_RUN is every run of the project."""
    assert worker_tasks.run_matches_digest_scope(_Sub("PER_RUN"), _Run())


@pytest.mark.parametrize(
    "wanted,actual,expected",
    [
        ("checkout", "checkout", True),
        # Case must not matter on EITHER side, and padding must not either.
        # The first version varied case only on the subscription's value, which
        # the code lowercases anyway -- so dropping `.lower()` from the RUN side
        # survived it.
        ("checkout", "Checkout", True),
        ("Checkout", "  checkout ", True),
        ("checkout", "smoke", False),
        ("checkout", None, False),
    ],
)
def test_per_suite_matches_on_the_run_suite(wanted, actual, expected):
    sub = _Sub("PER_SUITE", scope_value=wanted)
    assert worker_tasks.run_matches_digest_scope(sub, _Run(suite=actual)) is expected


def test_an_unscoped_per_suite_subscription_delivers_nothing():
    """Refused rather than widened.

    A PER_SUITE row with no scope_value would otherwise deliver every run in
    the project — which is what PER_RUN already is. Silently turning one
    subscription into another is worse than delivering nothing, because the
    user cannot tell it happened.
    """
    sub = _Sub("PER_SUITE", scope_value=None)
    assert worker_tasks.run_matches_digest_scope(sub, _Run(suite="checkout")) is False


def test_per_release_matches_on_the_runs_attributed_release():
    import uuid

    rid = uuid.uuid4()
    sub = _Sub("PER_RELEASE", scope_value=str(rid))
    assert worker_tasks.run_matches_digest_scope(sub, _Run(release=rid)) is True
    assert worker_tasks.run_matches_digest_scope(sub, _Run(release=uuid.uuid4())) is False
    # A run no release claims must not satisfy a release subscription.
    assert worker_tasks.run_matches_digest_scope(sub, _Run(release=None)) is False


def test_an_unscoped_per_release_subscription_delivers_nothing():
    sub = _Sub("PER_RELEASE", scope_value=None)
    import uuid

    assert worker_tasks.run_matches_digest_scope(sub, _Run(release=uuid.uuid4())) is False


def test_the_dispatcher_consults_the_scope_filter():
    """The wiring, by call rather than by name.

    A scope helper that exists and is never called is the same failure one
    layer down — and it is the failure this whole batch of work exists to
    close, so it gets its own assertion rather than being assumed.
    """
    src = inspect.getsource(worker_tasks)
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", None))
        == "run_matches_digest_scope"
    ]
    assert calls, (
        "run_matches_digest_scope is never called, so PER_SUITE and "
        "PER_RELEASE subscriptions would receive every run of their project"
    )
