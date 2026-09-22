"""A malformed ``project_id`` must never reach a UUID column comparison.

Found by exploratory testing against the live homelab (2026-08-07):
``GET /api/v1/metrics/summary?project_id=all`` returned **500** for an ADMIN,
reproducibly, while the sibling ``/api/v1/runs?project_id=all`` correctly
answered 400. ``/metrics/trends`` had the same fault.

Why it hid: ``_project_in_scope()`` rejects a non-UUID, so NON-admins were
covered by accident (they got an empty payload). But
``get_accessible_project_ids()`` returns ``None`` for an ADMIN, which skips the
scope check entirely — the raw string went to the query layer and blew up. The
bug was role-dependent, so any test using a non-admin fixture would pass.

``ALL_PROJECTS_ID`` ("all") is a frontend-only sentinel; a stale link, a
bookmarked URL, or one missed guard in the SPA is enough to send it.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.services.analytics_scope import parse_project_id

# VIZ-201/210: the guard moved into the shared scope parser, which every
# analytics route runs BEFORE any database access, and a malformed id is now
# the contract's 422 ``project_id_format`` (it was a 400 here, a 400 on the
# analytics routes and a 422 on the summary report -- three answers to one
# question). Still a client error, still never a 500.


class TestRejectsMalformedProjectId:
    @pytest.mark.parametrize(
        "bad",
        [
            "all",            # the ALL_PROJECTS_ID sentinel — the real-world case
            "",               # empty string from a stripped query param
            "undefined",      # a JS value stringified into the URL
            "null",
            "123",
            "not-a-uuid",
            "' OR 1=1--",     # nothing clever should reach the driver either
        ],
    )
    def test_malformed_values_are_a_client_error_not_a_500(self, bad):
        with pytest.raises(AnalyticsQueryError) as exc:
            parse_project_id(bad)
        assert exc.value.status_code == 422, (
            "a malformed project_id must be rejected as a client error; "
            "letting it through produced a 500 from the UUID comparison"
        )
        assert exc.value.code == "project_id_format"
        assert exc.value.param == "project_id"


class TestPreservesLegitimateInput:
    def test_none_is_allowed_it_means_all_projects(self):
        """``None`` is NOT an error — it is the admin's cross-project view."""
        assert parse_project_id(None) is None

    def test_a_real_uuid_passes_through_unchanged(self):
        pid = uuid.uuid4()
        assert parse_project_id(str(pid)) == pid

    def test_uppercase_and_braced_uuids_are_accepted(self):
        """uuid.UUID() accepts these spellings, so the guard must not be
        stricter than the thing it protects."""
        pid = uuid.uuid4()
        assert parse_project_id(str(pid).upper()) == pid
        assert parse_project_id("{%s}" % pid) == pid


def test_both_affected_handlers_take_the_validating_scope():
    """Pins the fix at both sites: ``summary`` and ``trends`` read project_id
    only through the scope dependency, whose parser is the guard above."""
    import inspect

    from app.routers import metrics

    for name in ("dashboard_summary", "trend_data"):
        params = inspect.signature(getattr(metrics, name)).parameters
        assert "project_id" not in params, (
            f"{name}() reads project_id itself instead of through the scope "
            "dependency -- a non-UUID would reach the query layer and 500 for an ADMIN"
        )
        assert "scope" in params
