"""Regression: a saved view's release survives the round trip (W1).

``saved_view_release`` shipped complete and mutation-tested, and nothing in
``app/`` imported it — a behaviour contract with no path to reach it. Its own
tests called the service directly, so they proved the rules were right and
nothing at all about whether a request ever applies them.

These tests call the ROUTER handlers. Each one fails if the handler stops
consulting the service, which is the failure that shipped.

The rule the wiring adds beyond the service
--------------------------------------------
``resolve_project_scope`` returns ``allowed=None`` for an ADMIN *and* for a
non-admin pinned to one verified project; the two are told apart only by
``scoped``. Passing ``allowed`` straight into ``resolve_for_reader`` would
therefore treat a pinned non-admin as unrestricted — and a view whose own
project matches the active one could carry a release belonging to a THIRD
project past the access check. ``_reader_scope`` narrows to the verified
project, and the last test here is that case.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.routers import saved_views as router_mod  # noqa: E402
from app.services import saved_view_release  # noqa: E402

PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
RELEASE_IN_A = uuid.uuid4()
RELEASE_IN_B = uuid.uuid4()


class _View:
    """A SavedView as far as SavedViewResponse.model_validate cares."""

    def __init__(self, *, filters, project_id=PROJECT_A, is_shared=False):
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.project_id = project_id
        self.name = "My view"
        self.description = None
        self.page = "coverage"
        self.filters = filters
        self.is_shared = is_shared
        self.is_default = False
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = None


class _Release:
    def __init__(self, rid, project_id):
        self.id = rid
        self.project_id = project_id


class _User:
    id = uuid.uuid4()


class _Session:
    """Answers by statement: saved_views -> the views, releases -> the release.

    Each test holds at most one release, so the lookup returns it rather than
    re-implementing the WHERE clause — matching on the bound id here would be
    asserting SQLAlchemy works, not that the handler asked.
    """

    def __init__(self, views=(), releases=()):
        self._views = list(views)
        self._releases = list(releases)
        self.release_lookups = 0

    async def execute(self, stmt, params=None):
        if "releases" in str(stmt):
            self.release_lookups += 1
            return _Result(single=self._releases[0] if self._releases else None)
        return _Result(many=self._views)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    def add(self, obj):
        return None


class _Result:
    def __init__(self, many=None, single=None):
        self._many = many or []
        self._single = single

    def scalars(self):
        return self

    def all(self):
        return self._many

    def scalar_one_or_none(self):
        return self._single


def _patch_scope(mp, *, scoped, allowed):
    async def _scope(_db, _user, _pid):
        return scoped, allowed

    mp.setattr(router_mod, "resolve_project_scope", _scope, raising=False)
    from app.core import deps

    mp.setattr(deps, "resolve_project_scope", _scope)


# ── The list path consults the service ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_listed_view_carries_this_readers_verdict_on_its_release():
    view = _View(filters={"release_id": str(RELEASE_IN_A)})
    db = _Session(views=[view], releases=[_Release(RELEASE_IN_A, PROJECT_A)])

    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp, scoped=PROJECT_A, allowed=None)
        rows = await router_mod.list_saved_views(
            project_id=PROJECT_A, page=None, db=db, current_user=_User()
        )

    assert rows[0].release is not None, (
        "the response carries no release verdict — the handler is not calling "
        "saved_view_release at all"
    )
    assert rows[0].release.applied is True
    assert rows[0].release.release_id == str(RELEASE_IN_A)
    assert db.release_lookups == 1


@pytest.mark.asyncio
async def test_a_view_saved_for_another_project_loses_its_release_not_its_name():
    """The failure the service exists to prevent, now reachable.

    Applying it would filter every page by an id nothing in the active project
    matches, and the page would render empty with the view's name in the
    picker — "the product looks broken", arriving by a different route.
    """
    view = _View(filters={"release_id": str(RELEASE_IN_B)}, project_id=PROJECT_B)
    db = _Session(views=[view], releases=[_Release(RELEASE_IN_B, PROJECT_B)])

    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp, scoped=PROJECT_A, allowed=None)
        rows = await router_mod.list_saved_views(
            project_id=PROJECT_A, page=None, db=db, current_user=_User()
        )

    assert rows[0].name == "My view", "the view itself must still open"
    assert rows[0].release.applied is False
    assert rows[0].release.reason == "saved for a different project"


@pytest.mark.asyncio
async def test_a_view_with_no_release_costs_no_extra_query():
    """The read path is hot, and most views carry no release.

    ``resolve_for_reader`` returns before touching the database when there is
    nothing stored, so listing views issues exactly the queries it did before
    this wiring existed.
    """
    db = _Session(views=[_View(filters={"page": "coverage"})])

    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp, scoped=PROJECT_A, allowed=None)
        rows = await router_mod.list_saved_views(
            project_id=PROJECT_A, page=None, db=db, current_user=_User()
        )

    assert db.release_lookups == 0
    assert rows[0].release.applied is False
    assert rows[0].release.reason is None


# ── The write path canonicalises ─────────────────────────────────────────────


def test_a_null_release_is_removed_rather_than_stored():
    """``{"release_id": null}`` and an absent key read identically to most
    consumers but not to all, and storing the null invites a reader to treat
    it as a filter value."""
    out = saved_view_release.store_release({"release_id": None, "days": 30}, None)
    assert "release_id" not in out
    assert out["days"] == 30


@pytest.mark.asyncio
async def test_creating_a_view_stores_the_release_through_the_service():
    from app.models.schemas import SavedViewCreate

    captured = {}

    class _CaptureSession(_Session):
        def add(self, obj):
            captured["filters"] = obj.filters

        async def refresh(self, obj):
            # What a real refresh does: the server-side defaults land. The
            # handler does not validate the row itself — FastAPI does that
            # against `response_model` — so this is here to keep the fake
            # honest about the state a caller downstream would actually see,
            # not to dodge an error.
            obj.id = obj.id or uuid.uuid4()
            obj.created_at = obj.created_at or datetime.now(timezone.utc)

    db = _CaptureSession()
    payload = SavedViewCreate(
        project_id=PROJECT_A,
        name="With release",
        filters={"release_id": None, "days": 30},
    )

    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp, scoped=PROJECT_A, allowed=None)
        await router_mod.create_saved_view(
            payload=payload, db=db, current_user=_User()
        )

    assert "release_id" not in captured["filters"], (
        "the create path stored a null release verbatim — it is not going "
        "through store_release"
    )


# ── The scoping rule the wiring adds ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pinned_non_admin_cannot_resolve_a_release_from_a_third_project():
    """The hole that passing ``allowed`` straight through would leave.

    The view's project matches the active one, so the project check passes.
    Only the reader-access check stands between a stored id from an unrelated
    project and this response — and for a pinned non-admin ``allowed`` is None,
    which means "unrestricted" to the service.
    """
    view = _View(filters={"release_id": str(RELEASE_IN_B)}, project_id=PROJECT_A)
    db = _Session(views=[view], releases=[_Release(RELEASE_IN_B, PROJECT_B)])

    with pytest.MonkeyPatch.context() as mp:
        _patch_scope(mp, scoped=PROJECT_A, allowed=None)
        rows = await router_mod.list_saved_views(
            project_id=PROJECT_A, page=None, db=db, current_user=_User()
        )

    assert rows[0].release.applied is False, (
        "a release belonging to a project the reader is not scoped to was "
        "applied — _reader_scope is not narrowing to the verified project"
    )
    assert rows[0].release.reason == "you do not have access to that release"
