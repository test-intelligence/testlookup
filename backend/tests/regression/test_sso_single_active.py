from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


def _config(*, active: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        display_name="Corporate IdP",
        is_active=active,
    )


def _db_for_config(config, *, flush_error=None):
    selected = MagicMock()
    selected.scalar_one_or_none.return_value = config
    deactivated = MagicMock()
    deactivated.rowcount = 1
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[selected, deactivated])
    db.flush = AsyncMock(side_effect=flush_error)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_activating_sso_config_deactivates_previous_policy(monkeypatch):
    from app.models.schemas import SSOConfigUpdate
    from app.routers import sso

    config = _config()
    db = _db_for_config(config)
    event = AsyncMock()
    monkeypatch.setattr(sso, "log_identity_event", event)
    monkeypatch.setattr(sso, "_config_to_response", lambda value: value)

    response = await sso.update_sso_config(
        config.id,
        SSOConfigUpdate(is_active=True),
        request=SimpleNamespace(client=None),
        current_user=SimpleNamespace(id=uuid.uuid4(), username="admin"),
        db=db,
    )

    assert response is config
    assert config.is_active is True
    assert db.execute.await_count == 2
    replacement = db.execute.await_args_list[1].args[0].compile()
    assert "UPDATE sso_configurations SET is_active=" in str(replacement)
    assert False in replacement.params.values()
    assert config.id in replacement.params.values()
    assert event.await_args.kwargs["detail"]["deactivated_other_configs"] == 1
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_sso_activation_returns_retryable_conflict(monkeypatch):
    from app.models.schemas import SSOConfigUpdate
    from app.routers import sso

    config = _config()
    conflict = IntegrityError("unique active SSO policy", {}, Exception("duplicate"))
    db = _db_for_config(config, flush_error=conflict)
    monkeypatch.setattr(sso, "log_identity_event", AsyncMock())

    with pytest.raises(HTTPException) as caught:
        await sso.update_sso_config(
            config.id,
            SSOConfigUpdate(is_active=True),
            request=SimpleNamespace(client=None),
            current_user=SimpleNamespace(id=uuid.uuid4(), username="admin"),
            db=db,
        )

    assert caught.value.status_code == 409
    assert "concurrently" in caught.value.detail
    db.rollback.assert_awaited_once()


def test_sso_model_has_partial_unique_active_index():
    from app.models.postgres import SSOConfiguration

    index = next(
        item
        for item in SSOConfiguration.__table__.indexes
        if item.name == "uq_sso_config_single_active"
    )
    assert index.unique is True
    assert "is_active IS TRUE" in str(index.dialect_options["postgresql"]["where"])


def test_sso_migration_repairs_legacy_duplicates_before_unique_index(monkeypatch):
    fake_op = MagicMock()
    alembic = types.ModuleType("alembic")
    alembic.op = fake_op
    monkeypatch.setitem(sys.modules, "alembic", alembic)
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "0137_sso_single_active.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0137", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.upgrade()

    cleanup_sql = str(fake_op.execute.call_args.args[0])
    assert "row_number()" in cleanup_sql
    assert "position > 1" in cleanup_sql
    assert fake_op.method_calls[0][0] == "execute"
    assert fake_op.method_calls[1][0] == "create_index"
    assert fake_op.create_index.call_args.kwargs["unique"] is True
