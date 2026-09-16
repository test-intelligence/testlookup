"""Real PostgreSQL proof that password reset wins a concurrent old-password login."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.core import token_revocation
from app.core.security import get_password_hash, verify_password
from app.models.postgres import Project, User, UserRole
from app.routers import auth
from app.services import default_qa_lead_service

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_reset_commit_precedes_concurrent_old_password_login(monkeypatch):
    """A waiting login must re-read the password after the reset commits."""
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    username = f"serial-{user_id.hex}"
    old_password = "Old-Password-123!"
    new_password = "New-Password-456!"

    async with sessions() as setup:
        setup.add(
            User(
                id=user_id,
                email=f"{username}@example.com",
                username=username,
                hashed_password=get_password_hash(old_password),
                role=UserRole.QA_ENGINEER,
                is_active=True,
            )
        )
        await setup.commit()

    request = Request({"type": "http", "method": "POST", "path": "/api/v1/auth/login", "headers": [], "client": ("127.0.0.1", 1)})
    form = OAuth2PasswordRequestForm(username=username, password=old_password)

    try:
        async with sessions() as reset_db, sessions() as login_db:
            locked = (
                await reset_db.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
            ).scalar_one()
            locked.hashed_password = get_password_hash(new_password)
            await reset_db.flush()

            login_task = asyncio.create_task(auth.login(request, form, login_db))
            await asyncio.sleep(0.05)
            assert not login_task.done(), "login read the pre-reset password without waiting for the user lock"

            await reset_db.commit()
            with pytest.raises(HTTPException) as exc:
                await login_task
            assert exc.value.status_code == 401
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
        await engine.dispose()


async def test_default_qa_lead_cutoff_shares_password_reset_transaction(monkeypatch):
    """The cutoff must neither self-block nor outlive a rolled-back password reset."""
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    marker = uuid.uuid4().hex
    old_password = "Default-Lead-Old-123!"
    new_password = "Default-Lead-New-456!"
    cutoff_jti = f"cutoff:{user_id}"

    class Redis:
        def __init__(self):
            self.store = {}

        async def set(self, key, value, **_kwargs):
            self.store[key] = value
            return True

        async def get(self, key):
            return self.store.get(key)

    cache = Redis()

    async def redis():
        return cache

    async def durable(statement, params):
        async with sessions() as durable_db:
            result = await durable_db.execute(text(statement), params)
            rows = result.fetchall() if result.returns_rows else []
            await durable_db.commit()
            return rows

    monkeypatch.setattr(token_revocation, "_redis", redis)
    monkeypatch.setattr(token_revocation, "_durable_execute", durable)

    async with sessions() as setup:
        setup.add(
            User(
                id=user_id,
                email=f"lead-{marker}@example.com",
                username=f"lead-{marker}",
                hashed_password=get_password_hash(old_password),
                role=UserRole.QA_LEAD,
                is_active=True,
                is_synthetic=True,
            )
        )
        setup.add(
            Project(
                id=project_id,
                name=f"Serialization {marker}",
                slug=f"serialization-{marker}",
                default_qa_lead_user_id=user_id,
            )
        )
        await setup.commit()

    try:
        async with sessions() as reset_db:
            project = await reset_db.get(Project, project_id)
            assert project is not None
            await asyncio.wait_for(
                default_qa_lead_service.reset_default_qa_lead_password(
                    reset_db, project, new_password=new_password
                ),
                timeout=2,
            )
            cutoff = (
                await reset_db.execute(
                    text("SELECT user_id FROM auth_token_revocations WHERE jti=:jti"),
                    {"jti": cutoff_jti},
                )
            ).scalar_one()
            assert cutoff == user_id
            changed = await reset_db.get(User, user_id)
            assert changed is not None
            assert verify_password(new_password, changed.hashed_password)
            await reset_db.rollback()

        async with sessions() as verify_db:
            cutoff_after_rollback = (
                await verify_db.execute(
                    text("SELECT 1 FROM auth_token_revocations WHERE jti=:jti"),
                    {"jti": cutoff_jti},
                )
            ).scalar_one_or_none()
            unchanged = await verify_db.get(User, user_id)
            assert cutoff_after_rollback is None
            assert unchanged is not None
            assert verify_password(old_password, unchanged.hashed_password)
            assert await token_revocation.is_token_before_cutoff(user_id, 1.0) is False
            cutoff_after_read = (
                await verify_db.execute(
                    text("SELECT 1 FROM auth_token_revocations WHERE jti=:jti"),
                    {"jti": cutoff_jti},
                )
            ).scalar_one_or_none()
            assert cutoff_after_read is None
            assert cache.store == {}
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
        await engine.dispose()
