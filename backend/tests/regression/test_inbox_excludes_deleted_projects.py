"""A soft-deleted project's failures must not stay in the assignment inbox.

Found by exploratory testing against the live homelab (2026-08-07) -- and found
by accident, which is the interesting part. An earlier iteration created a
throwaway project, probed it, and deleted it. Two iterations later,
``GET /api/v1/me/assigned-failures?scope=team`` still returned that project's
failures:

    total = 13
      project 8858608f-... (DELETED)  -> 2 items   <-- test_infra_0, test_infra_1
      project 2aefa4fa-... (live)     -> 11 items

``DELETE /projects/{id}`` is a **soft** delete -- ``projects.py:165`` sets
``project.is_active = False``. Only the project LIST honours that flag
(``projects.py:32``). Everything else kept reading the row, so the inbox listed
actionable work for a project that is absent from every project picker and
cannot be opened, filtered by, or navigated to.

Both the list and the ``/count`` badge are fixed together on purpose: if only
one had been, the sidebar badge would advertise work the page cannot show --
the same list/badge disagreement class this module's own comments already warn
about.

Related, deliberately NOT changed here: ``GET /projects/{id}`` still returns
**200** for a soft-deleted project (see the ledger). Whether a soft-deleted
project should 404 on direct fetch is a product call with real blast radius --
audit trails and historical run pages legitimately resolve deleted projects by
id -- so it is filed rather than decided.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import my_failures  # noqa: E402


def _compiled(expr) -> str:
    return str(expr.compile(compile_kwargs={"literal_binds": False})).lower()


class TestTheFilterItself:
    def test_helper_restricts_to_active_projects(self):
        sql = _compiled(my_failures._live_projects_only())
        assert "is_active" in sql, "the filter must key on the soft-delete flag"
        assert "project" in sql
        assert "in (" in sql or "in(" in sql, "expected an IN-subquery over project ids"

    def test_it_filters_on_the_run_s_project(self):
        """TestCase has no project_id of its own — the link is via TestRun."""
        sql = _compiled(my_failures._live_projects_only())
        assert "project_id" in sql


class TestBothEndpointsAgree:
    """The list and the badge must apply the SAME restriction.

    A badge counting rows the list cannot render is the exact
    'counts disagreeing between surfaces' bug class this repo tracks.
    """

    def test_list_endpoint_applies_it(self):
        import inspect

        src = inspect.getsource(my_failures.list_my_assigned_failures)
        assert "_live_projects_only()" in src, (
            "the inbox list still returns failures from soft-deleted projects"
        )

    def test_count_endpoint_applies_it(self):
        import inspect

        src = inspect.getsource(my_failures.my_assigned_failures_count)
        assert "_live_projects_only()" in src, (
            "the sidebar badge would count work the inbox page cannot display"
        )

    def test_neither_endpoint_hand_rolls_the_predicate(self):
        """Both must go through the helper so they cannot drift apart."""
        import inspect

        for fn in (
            my_failures.list_my_assigned_failures,
            my_failures.my_assigned_failures_count,
        ):
            src = inspect.getsource(fn)
            assert "Project.is_active" not in src, (
                f"{fn.__name__} hand-rolls the active-project predicate; use "
                "_live_projects_only() so the two endpoints stay in step"
            )


def test_soft_delete_is_still_what_delete_project_does():
    """Pins the assumption the fix rests on.

    If ``delete_project`` ever becomes a hard delete, this filter is harmless
    but redundant -- and this test failing is the signal to revisit it.
    """
    import inspect

    from app.routers import projects

    src = inspect.getsource(projects.delete_project)
    assert "is_active" in src, (
        "delete_project no longer soft-deletes; re-check whether the inbox "
        "filter is still the right mechanism"
    )
