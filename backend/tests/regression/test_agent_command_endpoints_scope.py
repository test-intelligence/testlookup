"""``/agents/defect-command`` and ``/agents/regression-watch`` were role-gated, not scoped.

Both took ``run_id`` and ``project_id`` as **query** params behind a bare
``require_role(UserRole.QA_ENGINEER)``. That is a check on *what the caller is*,
never on *what the caller may touch* -- so any QA_ENGINEER on the install could
pass another tenant's ``run_id``:

* ``/regression-watch`` loads that run's failure clusters and returns their
  classifications synchronously. A pure read, no write needed.
* ``/defect-command`` loads the cluster and its AI analyses, generates a
  Jira-ready description from that failure text, and persists a ``Defect`` row
  into whatever ``project_id`` the caller typed -- copying another tenant's
  failure data across the boundary, where correctly-scoped endpoints then serve
  it back quite legitimately.

Two guards were structurally unable to catch it:

* ``require_run_access()`` reads ``run_id`` from ``request.path_params`` and
  returns the caller **unchanged** when it is absent. Attaching it to a route
  that takes ``run_id`` as a query param is a silent no-op, not a check -- so
  the obvious "fix" would have looked right and done nothing. The test at the
  bottom pins that trap so nobody applies it.
* ``tests/test_architectural_authorization.py`` matches scoped **path** params;
  a route with none is declared protected by default, so both of these sat in
  its blind spot while the gate stayed green.

The project is now derived from the run rather than trusted from the caller.
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routers import agents as agents_router

_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()
_RUN = uuid.uuid4()


def _user(role="QA_ENGINEER"):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = "zzprobeagent"
    return user


def _db_returning_project(project_id):
    """An AsyncSession whose next scalar_one_or_none() is the run's project."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=project_id)
    db.execute = AsyncMock(return_value=result)
    return db


class TestRunOwnershipIsVerified:
    @pytest.mark.asyncio
    async def test_another_tenants_run_is_refused(self):
        """The vulnerability: a QA_ENGINEER naming a run they cannot access."""
        db = _db_returning_project(_THEIRS)
        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await agents_router._authorize_run_and_project(
                    db, _user(), _RUN, _THEIRS
                )
        assert excinfo.value.status_code == 403

    @pytest.mark.asyncio
    async def test_a_missing_run_is_a_404_not_a_pass(self):
        db = _db_returning_project(None)
        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await agents_router._authorize_run_and_project(
                    db, _user(), _RUN, _MINE
                )
        assert excinfo.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_project_id_that_does_not_own_the_run_is_refused(self):
        """``/defect-command`` wrote the Defect into the caller's project_id.

        Even with run access verified, honouring a caller-supplied project_id
        would let an operator file another run's failure text into a project of
        their choosing.
        """
        db = _db_returning_project(_MINE)
        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE, _THEIRS}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await agents_router._authorize_run_and_project(
                    db, _user(), _RUN, _THEIRS
                )
        assert excinfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_a_member_is_allowed_and_gets_the_runs_own_project(self):
        db = _db_returning_project(_MINE)
        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            resolved = await agents_router._authorize_run_and_project(
                db, _user(), _RUN, _MINE
            )
        assert resolved == _MINE

    @pytest.mark.asyncio
    async def test_an_admin_is_allowed(self):
        db = _db_returning_project(_THEIRS)
        with patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)
        ):
            resolved = await agents_router._authorize_run_and_project(
                db, _user(role="ADMIN"), _RUN, _THEIRS
            )
        assert resolved == _THEIRS


class TestBothEndpointsActuallyCallTheGuard:
    """A helper nobody calls is not a fix."""

    @pytest.mark.parametrize("handler", ["defect_command", "regression_watch"])
    def test_handler_authorizes_before_running_the_agent(self, handler: str):
        src = inspect.getsource(getattr(agents_router, handler))
        assert "_authorize_run_and_project" in src, (
            f"{handler} does not verify run ownership -- it is role-gated only, "
            "which is what made it a cross-tenant primitive"
        )
        guard_at = src.index("_authorize_run_and_project")
        runner = "run_defect_commander" if handler == "defect_command" else "run_regression_watchman"
        assert src.index(runner) > guard_at, (
            f"{handler} invokes the agent before authorizing the run"
        )

    @pytest.mark.parametrize("handler", ["defect_command", "regression_watch"])
    def test_the_agent_receives_the_resolved_project(self, handler: str):
        src = inspect.getsource(getattr(agents_router, handler))
        assert "project_id=str(scoped_project_id)" in src, (
            f"{handler} still forwards the caller-supplied project_id"
        )


def test_require_run_access_is_a_no_op_for_a_query_param():
    """Pin the trap that makes the obvious fix useless.

    ``require_run_access()`` reads ``run_id`` from **path** params only. If a
    future change moves these routes onto it instead of the explicit helper,
    the guard silently passes every caller and the IDOR returns with a
    dependency sitting right there that looks like protection.
    """
    src = inspect.getsource(agents_router)
    for handler in ("async def defect_command", "async def regression_watch"):
        start = src.index(handler)
        signature = src[start : src.index("):", start)]
        assert "require_run_access" not in signature, (
            "require_run_access() cannot see a query-param run_id -- it returns "
            "the caller unchanged. Use _authorize_run_and_project instead."
        )
