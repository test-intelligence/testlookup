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
from fastapi import HTTPException

from app.routers.metrics import _require_valid_project_id


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
    def test_malformed_values_raise_400_not_500(self, bad):
        with pytest.raises(HTTPException) as exc:
            _require_valid_project_id(bad)
        assert exc.value.status_code == 400, (
            "a malformed project_id must be rejected as a client error; "
            "letting it through produced a 500 from the UUID comparison"
        )
        assert "project_id" in str(exc.value.detail)


class TestPreservesLegitimateInput:
    def test_none_is_allowed_it_means_all_projects(self):
        """``None`` is NOT an error — it is the admin's cross-project view."""
        assert _require_valid_project_id(None) is None

    def test_a_real_uuid_passes_through_unchanged(self):
        pid = str(uuid.uuid4())
        assert _require_valid_project_id(pid) == pid

    def test_uppercase_and_braced_uuids_are_accepted(self):
        """uuid.UUID() accepts these spellings, so the guard must not be
        stricter than the thing it protects."""
        pid = uuid.uuid4()
        assert _require_valid_project_id(str(pid).upper()) is not None
        assert _require_valid_project_id("{%s}" % pid) is not None


def test_both_affected_handlers_call_the_guard():
    """Pins the fix at both sites.

    ``tia-readiness`` already validated correctly (it answered 422), so only
    ``summary`` and ``trends`` were exposed — but a future handler that forgets
    the guard would reintroduce the same 500.
    """
    import inspect

    from app.routers import metrics

    for name in ("dashboard_summary", "trend_data"):
        src = inspect.getsource(getattr(metrics, name))
        assert "_require_valid_project_id" in src, (
            f"{name}() does not validate project_id — a non-UUID would reach "
            "the query layer and 500 for an ADMIN"
        )
