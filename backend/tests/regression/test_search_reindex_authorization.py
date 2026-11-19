"""Regression: ``POST /api/v1/search/reindex`` had no authorization at all.

The endpoint took neither a user nor a role::

    @router.post("/reindex")
    async def trigger_reindex(project_id: str | None = None, full: bool = False):
        from app.worker.tasks import reindex_search
        task = reindex_search.apply_async(
            kwargs={"project_id": project_id, "full": full}
        )

Every other endpoint in ``routers/search.py`` depends on
``get_current_active_user``. This one accepted no user, checked no role, and
never validated ``project_id`` — it simply queued the Celery job.

**Confirmed against the live deployment.** Unauthenticated access is stopped by
the global auth middleware, so this is not an anonymous vector::

    POST /api/v1/search/reindex            (no Authorization header)  -> 401

But authentication was the *only* barrier. As a **VIEWER** — the lowest role,
holding zero project memberships::

    POST /api/v1/search/reindex?full=true
    200 {"task_id":"d96026cb-...","status":"queued","mode":"full"}

A full rebuild of the entire instance's search index, queued by a user who
cannot see a single project.

Two distinct problems, fixed together:

  * **No role gate.** Reindexing is a maintenance operation that consumes
    worker capacity; VIEWER is a read-only role.
  * **No tenant scoping.** ``project_id`` went to the task unchecked, and
    omitting it means "rebuild everything" — so a lead in one project could
    rebuild every other tenant's index.

The fix mirrors the tenant model rather than picking one flat role: a named
project requires QA_LEAD **and** membership (``resolve_project_scope``), while
the unscoped instance-wide rebuild requires ADMIN, because it is the only
variant whose blast radius crosses tenants.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.routers import search as search_router  # noqa: E402

pytestmark = pytest.mark.regression


_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()


def _user(role):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = "probe"
    return user


class _Task:
    id = "task-123"


async def _reindex(user, project_id=None, full=False, accessible=None):
    """Drive the real endpoint with Celery and membership stubbed.

    ``accessible=None`` models ADMIN; a set models a non-admin's memberships.
    """
    queued: list = []

    def _apply_async(kwargs=None, **_):
        queued.append(kwargs)
        return _Task()

    fake_tasks = MagicMock()
    fake_tasks.reindex_search.apply_async = _apply_async

    with patch.dict("sys.modules", {"app.worker.tasks": fake_tasks}), patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)
    ):
        result = await search_router.trigger_reindex(
            # A string goes through exactly as a client sent it, the empty
            # string included: ``?project_id=`` arrives as "" and not as None.
            project_id=(
                project_id
                if project_id is None or isinstance(project_id, str)
                else str(project_id)
            ),
            full=full,
            db=AsyncMock(),
            current_user=user,
        )
    return result, queued


class TestReadOnlyRolesCannotQueueWork:
    @pytest.mark.asyncio
    async def test_viewer_cannot_trigger_a_global_full_rebuild(self):
        """The measured case: VIEWER, no memberships, full=true, 200 OK."""
        with pytest.raises(HTTPException) as excinfo:
            await _reindex(_user("VIEWER"), project_id=None, full=True, accessible=set())
        assert excinfo.value.status_code in (401, 403), (
            "a VIEWER queued a full rebuild of the entire instance's search index"
        )

    @pytest.mark.asyncio
    async def test_viewer_cannot_trigger_a_project_reindex(self):
        with pytest.raises(HTTPException) as excinfo:
            await _reindex(_user("VIEWER"), project_id=_MINE, accessible={_MINE})
        assert excinfo.value.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_nothing_is_queued_when_denied(self):
        """Denial must happen before the task is dispatched."""
        queued: list = []
        try:
            _, queued = await _reindex(
                _user("VIEWER"), project_id=None, full=True, accessible=set()
            )
        except HTTPException:
            pass
        assert queued == [], f"a Celery job was dispatched despite the denial: {queued}"


class TestTenantScoping:
    @pytest.mark.asyncio
    async def test_lead_cannot_reindex_a_project_they_are_not_in(self):
        with pytest.raises(HTTPException) as excinfo:
            await _reindex(_user("QA_LEAD"), project_id=_THEIRS, accessible={_MINE})
        assert excinfo.value.status_code == 403

    @pytest.mark.asyncio
    async def test_lead_cannot_trigger_the_instance_wide_rebuild(self):
        """No project named == every tenant's index. That is ADMIN-only —
        otherwise a lead in one project rebuilds everyone else's."""
        with pytest.raises(HTTPException) as excinfo:
            await _reindex(_user("QA_LEAD"), project_id=None, full=True, accessible={_MINE})
        assert excinfo.value.status_code == 403


class TestTheLegitimatePathsStillWork:
    @pytest.mark.asyncio
    async def test_lead_can_reindex_their_own_project(self):
        result, queued = await _reindex(
            _user("QA_LEAD"), project_id=_MINE, accessible={_MINE}
        )
        assert result["status"] == "queued"
        assert queued and queued[0]["project_id"] == str(_MINE)

    @pytest.mark.asyncio
    async def test_admin_can_rebuild_the_instance(self):
        result, queued = await _reindex(
            _user("ADMIN"), project_id=None, full=True, accessible=None
        )
        assert result["mode"] == "full"
        assert queued and queued[0]["project_id"] is None

    @pytest.mark.asyncio
    async def test_admin_can_reindex_any_project(self):
        result, queued = await _reindex(
            _user("ADMIN"), project_id=_THEIRS, accessible=None
        )
        assert result["status"] == "queued"
        assert queued and queued[0]["project_id"] == str(_THEIRS)


class TestAnEmptyProjectIdIsNoProjectAtAll:
    """``?project_id=`` is the instance-wide reindex, so it is ADMIN-only.

    The handler chose the ADMIN-only branch with ``project_id is None``. An
    empty query value arrives as ``""``: it took the QA_LEAD branch, the scope
    helper treated it as "nothing requested" and checked nothing, and the task
    was queued with ``project_id=""`` -- which both indexers read as "every
    project". A QA lead with no memberships could rebuild every tenant's index.

    The task also receives only what the caller was checked against: the
    canonical id of the verified project, or ``None`` on the ADMIN path.
    """

    @pytest.mark.asyncio
    async def test_lead_cannot_reach_the_instance_wide_reindex_with_an_empty_id(self):
        queued: list = []
        with pytest.raises(HTTPException) as excinfo:
            _, queued = await _reindex(_user("QA_LEAD"), project_id="", accessible={_MINE})
        assert excinfo.value.status_code == 403
        assert "instance ADMIN" in excinfo.value.detail
        assert queued == []

    @pytest.mark.asyncio
    async def test_nothing_is_queued_for_a_lead_sending_an_empty_id(self):
        """The first test cannot see the queue once the handler raises."""
        dispatched: list = []
        fake_tasks = MagicMock()
        fake_tasks.reindex_search.apply_async = lambda kwargs=None, **_: dispatched.append(kwargs)

        with patch.dict("sys.modules", {"app.worker.tasks": fake_tasks}), patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=set())
        ):
            with pytest.raises(HTTPException):
                await search_router.trigger_reindex(
                    project_id="", full=True, db=AsyncMock(), current_user=_user("QA_LEAD"),
                )

        assert dispatched == [], f"an instance-wide reindex was queued: {dispatched}"

    @pytest.mark.asyncio
    async def test_project_bound_admin_key_cannot_use_an_empty_id(self):
        """A project-bound key is not an instance admin, whatever its owner is."""
        from app.core import deps

        user = _user("ADMIN")
        setattr(user, deps._API_KEY_BOUND_PROJECT_ATTR, _MINE)
        assert deps._api_key_bound_project(user) == _MINE

        with pytest.raises(HTTPException) as excinfo:
            await _reindex(user, project_id="", accessible={_MINE})
        assert excinfo.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_sending_an_empty_id_queues_none_not_the_empty_string(self):
        result, queued = await _reindex(_user("ADMIN"), project_id="", accessible=None)
        assert result["status"] == "queued"
        assert queued == [{"project_id": None, "full": False}]

    @pytest.mark.asyncio
    async def test_the_task_gets_the_canonical_id_of_the_verified_project(self):
        """Upper case and braces parse to the same project; the task must get
        the one spelling the membership check was made against."""
        spelled = "{" + str(_MINE).upper() + "}"
        result, queued = await _reindex(
            _user("QA_LEAD"), project_id=spelled, accessible={_MINE}
        )
        assert result["status"] == "queued"
        assert queued == [{"project_id": str(_MINE), "full": False}]

    @pytest.mark.asyncio
    async def test_a_value_that_is_not_a_project_id_is_refused(self):
        for junk in (" ", "not-a-uuid"):
            with pytest.raises(HTTPException) as excinfo:
                await _reindex(_user("QA_LEAD"), project_id=junk, accessible={_MINE})
            assert excinfo.value.status_code == 400
