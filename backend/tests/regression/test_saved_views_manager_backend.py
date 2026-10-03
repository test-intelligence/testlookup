"""VIZ-609: the backend defects under the saved-views manager, against a real
(in-memory SQLite) database rather than a fake session, so the default rule
and the merge are observed in the rows, not in the calls.

1. Exactly one default per user, project AND page. PATCH used to skip the
   unset entirely (two defaults could coexist), and create unset the user's
   defaults on EVERY page of the project (a Trends default cleared Coverage's).
2. Neither writer loses the other's keys: PATCH merges ``filters`` instead of
   replacing them, so the layout writer ({page, instances, version}) keeps the
   digests' ``release_id`` and vice versa. ``null`` removes a key.
3. create / update / delete / share each land a ``saved_view.*`` row in the
   activity ledger (declared in the catalog, emitted by nothing until now).
4. ``filters`` is capped at 32 KB, and the keys the manager writes are typed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select, types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import (
    Base,
    Project,
    ProjectActivityEvent,
    Release,
    SavedView,
    User,
    UserRole,
)
from app.models.schemas import (
    MAX_SAVED_VIEW_FILTERS_BYTES,
    SavedViewCreate,
    SavedViewUpdate,
)
from app.routers import saved_views as router

_TABLES = [
    Project.__table__,
    User.__table__,
    ProjectActivityEvent.__table__,
    Release.__table__,
    SavedView.__table__,
]


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect, coltype):  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def world(session_factory, monkeypatch):
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service")
    user = User(
        id=uuid.uuid4(), email="lead@example.com", username="lead", full_name="Lead",
        hashed_password="x", role=UserRole.QA_LEAD.value,
    )
    async with session_factory() as s:
        s.add_all([project, user])
        await s.commit()

    async def _scope(db, current_user, requested):
        return (uuid.UUID(requested) if requested else None, None)

    monkeypatch.setattr("app.core.deps.resolve_project_scope", _scope)
    return project, user


async def _create(session_factory, user, project, **kw):
    payload = SavedViewCreate(project_id=project.id, name=kw.pop("name", "View"), **kw)
    async with session_factory() as db:
        return await router.create_saved_view(payload=payload, db=db, current_user=user)


async def _patch(session_factory, user, view_id, **kw):
    async with session_factory() as db:
        return await router.update_saved_view(view_id=view_id, payload=SavedViewUpdate(**kw), db=db, current_user=user)


async def _views(session_factory) -> dict[str, SavedView]:
    async with session_factory() as db:
        return {v.name: v for v in (await db.execute(select(SavedView))).scalars().all()}


async def _events(session_factory) -> list[str]:
    async with session_factory() as db:
        rows = (await db.execute(select(ProjectActivityEvent).order_by(ProjectActivityEvent.occurred_at))).scalars().all()
        return [row.event_type for row in rows]


class TestOneDefaultPerUserProjectAndPage:
    async def test_create_unsets_only_the_same_pages_default(self, session_factory, world):
        project, user = world
        await _create(session_factory, user, project, name="Trends A", page="trends", is_default=True)
        await _create(session_factory, user, project, name="Coverage A", page="coverage", is_default=True)
        await _create(session_factory, user, project, name="Trends B", page="trends", is_default=True)
        views = await _views(session_factory)
        assert views["Trends B"].is_default and not views["Trends A"].is_default
        assert views["Coverage A"].is_default, "a Trends default must not clear the Coverage default"

    async def test_patch_to_default_unsets_the_previous_default(self, session_factory, world):
        project, user = world
        await _create(session_factory, user, project, name="One", page="trends", is_default=True)
        two = await _create(session_factory, user, project, name="Two", page="trends")
        await _patch(session_factory, user, two.id, is_default=True)
        views = await _views(session_factory)
        assert views["Two"].is_default
        assert not views["One"].is_default, "PATCH left two defaults on one page"


class TestNeitherWriterLosesTheOthersKeys:
    async def test_layout_save_keeps_the_release_and_release_save_keeps_the_layout(self, session_factory, world):
        project, user = world
        release_id = str(uuid.uuid4())
        view = await _create(
            session_factory, user, project, name="Layout", page="trends",
            filters={"page": "trends", "instances": [{"instanceId": "a", "templateId": "t"}], "version": 2},
        )
        # The digests' writer adds a release.
        await _patch(session_factory, user, view.id, filters={"release_id": release_id})
        # The layout writer saves its whole object, which has no release in it.
        await _patch(
            session_factory, user, view.id,
            filters={"page": "trends", "instances": [{"instanceId": "b", "templateId": "t"}], "version": 2},
        )
        stored = (await _views(session_factory))["Layout"].filters
        assert stored["release_id"] == release_id, "the layout save dropped the release"
        assert stored["instances"] == [{"instanceId": "b", "templateId": "t"}]

    async def test_null_removes_a_key(self, session_factory, world):
        project, user = world
        view = await _create(session_factory, user, project, name="Nulls", filters={"severity": "HIGH", "days": 7})
        await _patch(session_factory, user, view.id, filters={"severity": None})
        assert (await _views(session_factory))["Nulls"].filters == {"days": 7}


class TestMutationsAreRecorded:
    async def test_create_update_share_delete_each_land_a_ledger_row(self, session_factory, world):
        project, user = world
        view = await _create(session_factory, user, project, name="Watch", page="trends")
        await _patch(session_factory, user, view.id, name="Payments release watch")
        await _patch(session_factory, user, view.id, is_shared=True)
        async with session_factory() as db:
            await router.delete_saved_view(view_id=view.id, db=db, current_user=user)
        events = await _events(session_factory)
        assert events == [
            "saved_view.created",
            "saved_view.updated",
            "saved_view.updated",
            "saved_view.shared",
            "saved_view.deleted",
        ]

    async def test_a_patch_that_changes_nothing_records_nothing(self, session_factory, world):
        project, user = world
        view = await _create(session_factory, user, project, name="Same", page="trends")
        await _patch(session_factory, user, view.id, name="Same")
        assert await _events(session_factory) == ["saved_view.created"]


class TestFiltersAreBounded:
    def test_a_filters_object_over_32_kb_is_refused(self):
        with pytest.raises(ValidationError, match="at most"):
            SavedViewCreate(name="Big", filters={"note": "x" * (MAX_SAVED_VIEW_FILTERS_BYTES + 1)})

    @pytest.mark.parametrize(
        "filters",
        [
            {"kind": "something_else"},
            {"release_ids": "not-a-list"},
            {"suites": ["ok", ""]},
            {"window": 0},
            {"window": 400},
            {"window": True},
        ],
    )
    def test_the_managers_keys_are_typed(self, filters):
        with pytest.raises(ValidationError):
            SavedViewCreate(name="Typed", filters=filters)

    def test_a_report_view_and_legacy_free_form_filters_are_accepted(self):
        SavedViewCreate(
            name="Report", page="summary_report",
            filters={"kind": "report_view", "page": "summary_report", "release_ids": ["a"], "suites": ["payments"], "window": 30},
        )
        # filters has always been an open object: older views keep their keys.
        SavedViewCreate(name="Legacy", filters={"severity": "HIGH", "category": "PRODUCT_BUG", "days": 7})
