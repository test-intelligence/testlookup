"""Real PostgreSQL proof that password reset wins a concurrent old-password login."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.core.security import get_password_hash
from app.models.postgres import User, UserRole
from app.routers import auth

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
