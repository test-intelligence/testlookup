"""M17 regressions for saved analytics layouts and tenant boundaries."""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.schemas import SavedViewCreate, SavedViewUpdate
from app.routers import saved_views
from app.services import ingestion, ingestion_pipeline, metrics_service
from app.services.cache_service import invalidate_analytics_cache


def _instance(index: int) -> dict[str, str]:
    return {"instanceId": f"instance-{index}", "templateId": "pass_fail_trend"}


@pytest.mark.parametrize("schema", [SavedViewCreate, SavedViewUpdate])
def test_saved_view_contract_rejects_layouts_over_the_widget_limit(schema):
    payload = {
        "filters": {"instances": [_instance(index) for index in range(13)]},
    }
    if schema is SavedViewCreate:
        payload["name"] = "Bounded dashboard"

    with pytest.raises(ValidationError, match="cannot contain more than 12"):
        schema(**payload)


@pytest.mark.parametrize(
    "filters",
    [
        {"instances": {}},
        {"instances": "not-a-list"},
        {"instances": [{"instanceId": "only-an-id"}]},
        {"widgets": ["pass_fail_trend", ""]},
    ],
)
def test_saved_view_contract_rejects_malformed_analytics_layouts(filters):
    with pytest.raises(ValidationError):
        SavedViewCreate(name="Malformed dashboard", filters=filters)


def test_saved_view_contract_keeps_legacy_filter_only_rows_compatible():
    view = SavedViewCreate(
        name="Critical failures",
        page="failures",
        filters={"severity": "critical", "date_range": 30},
    )

    assert view.filters == {"severity": "critical", "date_range": 30}


def test_saved_view_contract_rejects_unknown_pages():
    with pytest.raises(ValidationError):
        SavedViewCreate(name="Unknown page", page="billing", filters={})


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_shared_view_uuid_does_not_cross_project_membership(monkeypatch):
    owner_id = uuid.uuid4()
    reader_id = uuid.uuid4()
    foreign_project_id = uuid.uuid4()
    view = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner_id,
        project_id=foreign_project_id,
        name="Foreign shared view",
        filters={"severity": "critical"},
        is_shared=True,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(view)))

    async def refuse_foreign_scope(_db, _user, requested_project_id):
        assert requested_project_id == str(foreign_project_id)
        raise HTTPException(status_code=403, detail="foreign project")

    monkeypatch.setattr("app.core.deps.resolve_project_scope", refuse_foreign_scope)

    with pytest.raises(HTTPException) as exc:
        await saved_views.get_saved_view(
            view.id,
            db=db,
            current_user=SimpleNamespace(id=reader_id),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_projectless_shared_view_is_private_to_its_owner():
    owner_id = uuid.uuid4()
    reader_id = uuid.uuid4()
    view = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner_id,
        project_id=None,
        is_shared=True,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(view)))

    with pytest.raises(HTTPException) as exc:
        await saved_views.get_saved_view(
            view.id,
            db=db,
            current_user=SimpleNamespace(id=reader_id),
        )

    assert exc.value.status_code == 403


class _EmptyScalars:
    def scalars(self):
        return self

    def all(self):
        return []


@pytest.mark.asyncio
async def test_all_projects_scope_queries_accessible_and_owned_global_views(monkeypatch):
    accessible_project_id = uuid.uuid4()

    async def all_projects_scope(_db, _user, requested_project_id):
        assert requested_project_id is None
        return None, {accessible_project_id}

    monkeypatch.setattr("app.core.deps.resolve_project_scope", all_projects_scope)
    db = SimpleNamespace(execute=AsyncMock(return_value=_EmptyScalars()))

    result = await saved_views.list_saved_views(
        project_id=None,
        page="dashboard",
        db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert result == []
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    assert accessible_project_id in {
        item
        for value in statement.compile().params.values()
        for item in (value if isinstance(value, (list, set, tuple)) else [value])
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_removed_member_cannot_mutate_an_owned_project_view(monkeypatch, operation):
    owner_id = uuid.uuid4()
    view = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner_id,
        project_id=uuid.uuid4(),
        filters={},
        is_shared=False,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(view)))

    async def refuse_removed_member(_db, _user, _view):
        raise HTTPException(status_code=403, detail="membership revoked")

    monkeypatch.setattr(saved_views, "_require_view_project_access", refuse_removed_member)

    with pytest.raises(HTTPException) as exc:
        if operation == "update":
            await saved_views.update_saved_view(
                view.id,
                SavedViewUpdate(name="Still mine"),
                db=db,
                current_user=SimpleNamespace(id=owner_id),
            )
        else:
            await saved_views.delete_saved_view(
                view.id,
                db=db,
                current_user=SimpleNamespace(id=owner_id),
            )

    assert exc.value.status_code == 403


class _EmptyRows:
    def fetchall(self):
        return []


@pytest.mark.asyncio
async def test_trend_window_starts_at_utc_midnight_and_covers_exactly_n_days(monkeypatch):
    from datetime import datetime, timezone

    fixed_now = datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(metrics_service, "datetime", _FrozenDateTime)
    db = SimpleNamespace(execute=AsyncMock(return_value=_EmptyRows()))

    assert await metrics_service.get_trend_data(db, "project-a", days=7) == []

    params = db.execute.await_args.args[1]
    assert params["period_start"] == datetime(2026, 10, 26, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_project_cache_invalidation_also_clears_all_projects_analytics():
    redis = AsyncMock()
    redis.scan.side_effect = [
        (0, ["analytics:dashboard:project-a:days=7"]),
        (0, ["analytics:dashboard:all:days=7"]),
    ]

    with patch("app.db.redis_client.get_redis", return_value=redis):
        await invalidate_analytics_cache("project-a")

    assert redis.scan.await_args_list[0].kwargs["match"] == "analytics:*:project-a:*"
    assert redis.scan.await_args_list[1].kwargs["match"] == "analytics:*:all:*"
    assert redis.delete.await_count == 2


def test_file_and_unified_ingestion_invalidate_analytics_only_after_commit():
    sentinel_source = inspect.getsource(ingestion.process_sentinel)
    unified_source = inspect.getsource(ingestion_pipeline.finalize_run)

    for source in (sentinel_source, unified_source):
        commit = source.index("await db.commit()")
        invalidate = source.index("await invalidate_analytics_cache")
        assert commit < invalidate
