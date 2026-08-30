"""Every by-id test-management route must verify project access, not just a session.

Five routes fetched by primary key behind nothing but ``get_current_active_user``:

* ``/plans/{plan_id}/export/word`` and ``/export/pdf``
* ``/strategies/{strategy_id}/export/word`` and ``/export/pdf``
* ``GET`` and ``PUT /strategies/{strategy_id}``

The exports render the whole document -- every linked case's title, steps and
expected result -- into a downloadable file, so this was a bulk cross-tenant
read with a convenient output format. ``PUT /strategies/{id}`` was worse than a
read: it overwrites another tenant's strategy, and ``update_strategy`` passes
``project_id`` to ``audit_event``, so the audit row lands in the victim's
project naming the attacker as actor.

``require_plan_access`` in ``test_management_shared`` was written for exactly
this class -- its docstring says "without this every by-id endpoint was a
cross-tenant IDOR" -- and ``test_management_plans.py`` applies it to all seven
of its routes. The export module applied it to none, and every sibling of the
two strategy routes calls ``resolve_project_scope`` while those two did not.

A sixth, ``/suites/{suite_name}/deleted``, treated ``project_id`` as an
optional *filter* rather than a scope check, so omitting it returned every
tenant's rows. Both of its immediate siblings resolve scope and carry a comment
calling that "the third recurrence of the class".

These are source-level assertions rather than request tests because the checks
sit inside handler bodies, and a mocked request test would assert the mock.
"""
from __future__ import annotations

import inspect

import pytest

from app.routers import test_management_exports as exports_router
from app.routers import test_management_strategies as strategies_router

#: (module, handler, the object whose project_id must be resolved)
BY_ID_HANDLERS = [
    (exports_router, "export_test_plan_word", "plan"),
    (exports_router, "export_test_plan_pdf", "plan"),
    (exports_router, "export_test_strategy_word", "strategy"),
    (exports_router, "export_test_strategy_pdf", "strategy"),
    (strategies_router, "get_strategy", "strategy"),
    (strategies_router, "update_strategy", "existing"),
]


@pytest.mark.parametrize(
    "module,handler,subject",
    BY_ID_HANDLERS,
    ids=[h for _, h, _ in BY_ID_HANDLERS],
)
def test_by_id_handler_resolves_project_scope(module, handler: str, subject: str):
    src = inspect.getsource(getattr(module, handler))
    assert "resolve_project_scope" in src, (
        f"{handler} fetches by primary key with no project check -- any "
        "authenticated user can read another tenant's row"
    )
    assert f"str({subject}.project_id)" in src, (
        f"{handler} calls resolve_project_scope but not with {subject}.project_id, "
        "so it is not checking the row it just loaded"
    )


@pytest.mark.parametrize(
    "module,handler,subject",
    BY_ID_HANDLERS,
    ids=[h for _, h, _ in BY_ID_HANDLERS],
)
def test_the_check_runs_before_the_row_is_used(module, handler: str, subject: str):
    """A check after the work is done still leaks the work."""
    src = inspect.getsource(getattr(module, handler))
    guard_at = src.index("resolve_project_scope")
    # The first thing every one of these does with the row afterwards.
    for marker in ("Document(", "SimpleDocTemplate(", "row(", "update_strategy_model("):
        at = src.find(marker)
        if at != -1:
            assert at > guard_at, (
                f"{handler} uses the row at {marker!r} before verifying access"
            )
            break
    else:
        pytest.fail(f"{handler}: could not find where the row is consumed")


def test_update_strategy_checks_before_it_writes():
    """The audit row for an unauthorised PUT landed in the victim's project."""
    src = inspect.getsource(strategies_router.update_strategy)
    assert src.index("resolve_project_scope") < src.index("update_strategy_model("), (
        "the strategy is overwritten before access is verified"
    )


def test_deleted_suite_listing_is_scoped_not_merely_filtered():
    """``project_id`` was an optional filter; omitting it spanned every tenant."""
    src = inspect.getsource(exports_router.get_suite_deleted)
    assert "resolve_project_scope" in src, (
        "/suites/{suite_name}/deleted still treats project_id as an optional "
        "filter, so omitting it returns every tenant's membership rows"
    )
    guard_at = src.index("resolve_project_scope")
    assert src.index("select(SuiteMembership)") > guard_at, (
        "the membership query is built before scope is resolved"
    )
    assert "allowed is not None" in src, (
        "a non-admin naming no project must get an empty list rather than "
        "every project's rows -- the pattern both siblings already use"
    )


def test_the_sibling_that_was_already_correct_stays_correct():
    """Guards the reference implementation this fix was modelled on."""
    src = inspect.getsource(exports_router.get_suite_membership)
    assert "resolve_project_scope" in src and "allowed is not None" in src
