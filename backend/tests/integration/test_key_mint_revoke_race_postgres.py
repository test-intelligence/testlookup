"""Review R-B45-D-4: a key minted while its parent is being revoked cannot escape.

The N35 cascade revoked every active descendant it could SEE. A mint that
INSERTed its child before the revoke's recursive query, and committed after,
was invisible to it: the child committed active under a revoked parent. The
FK's KEY SHARE lock on the parent does not conflict with the revoke's UPDATE.

Now ``create_api_key`` takes the parent FOR SHARE (``_lock_active_minting_parent``)
and refuses a revoked one, and the revoke repeats its cascade until a pass
finds nothing (``_revoke_subtree``). These drive both helpers on two real
PostgreSQL connections through each interleaving:

* the mint holds the parent first: the revoke waits, then cascades to the child;
* the revoke holds the parent first: the mint waits, then is refused (401);
* the mint is under a DESCENDANT of the key being revoked: the revoke's first
  pass waits for the mint's lock and cannot see its child; the second pass does.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
import textwrap
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.routers import api_keys as api_keys_router

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


_KEY_INSERT = text(
    "INSERT INTO api_keys (id, user_id, name, key_hash, key_hint, scopes, project_id, "
    "minted_by_key_id, is_active) VALUES (:id, :user, :name, :hash, 'qai_x...', "
    "CAST('[]' AS json), :project, :parent, true)"
)


@pytest.fixture
async def world():
    import app.db.postgres as app_postgres

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    user, project = uuid.uuid4(), uuid.uuid4()
    root, parent = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO projects (id, name, slug, is_active) VALUES (:id, :n, :n, true)"
        ), {"id": project, "n": f"d4-{project.hex[:10]}"})
        await conn.execute(text(
            "INSERT INTO users (id, email, username, full_name, hashed_password, role) "
            "VALUES (:id, :e, :u, 'D4', '!unusable', 'ADMIN')"
        ), {"id": user, "e": f"d4-{user.hex[:10]}@example.com", "u": f"d4_{user.hex[:10]}"})
        for key, minted_by in ((root, None), (parent, root)):
            await conn.execute(_KEY_INSERT, {
                "id": key, "user": user, "name": f"k-{key.hex[:6]}", "hash": uuid.uuid4().hex * 2,
                "project": project, "parent": minted_by,
            })
    try:
        yield engine, user, project, root, parent
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM api_keys WHERE user_id = :u"), {"u": user})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
            await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _pid(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def _waits_on_a_lock(engine, pid: int, task: asyncio.Task, timeout: float = 10.0) -> bool:
    """True once ``pid`` is blocked on a lock; False if ``task`` finished first."""
    deadline = asyncio.get_running_loop().time() + timeout
    async with engine.connect() as observer:
        while asyncio.get_running_loop().time() < deadline:
            if task.done():
                return False
            waiting = (await observer.execute(text(
                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
            ), {"pid": pid})).scalar()
            await observer.commit()
            if waiting:
                return True
            await asyncio.sleep(0.02)
    raise AssertionError("the second transaction neither blocked nor finished")


async def _mint_child(session: AsyncSession, user, project, parent) -> uuid.UUID:
    """What create_api_key does for a key-authenticated caller, up to its commit."""
    await api_keys_router._lock_active_minting_parent(session, parent)
    child = uuid.uuid4()
    await session.execute(_KEY_INSERT, {
        "id": child, "user": user, "name": f"c-{child.hex[:6]}", "hash": uuid.uuid4().hex * 2,
        "project": project, "parent": parent,
    })
    return child


async def _revoke(session: AsyncSession, key) -> list:
    """What revoke_api_key does, up to its commit."""
    await session.execute(text("UPDATE api_keys SET is_active = false WHERE id = :id"), {"id": key})
    return await api_keys_router._revoke_subtree(session, key)


async def _active(engine, key) -> bool:
    async with engine.connect() as conn:
        return bool((await conn.execute(
            text("SELECT is_active FROM api_keys WHERE id = :id"), {"id": key}
        )).scalar_one())


async def test_a_mint_holding_the_parent_makes_the_revoke_wait_then_cascade(world):
    engine, user, project, _root, parent = world
    async with AsyncSession(engine) as mint, AsyncSession(engine) as revoke:
        child = await _mint_child(mint, user, project, parent)
        revoke_pid = await _pid(revoke)

        async def _revoke_and_commit():
            revoked = await _revoke(revoke, parent)
            await revoke.commit()
            return revoked

        task = asyncio.create_task(_revoke_and_commit())
        blocked = await _waits_on_a_lock(engine, revoke_pid, task)
        await mint.commit()
        revoked = await asyncio.wait_for(task, 30)
    assert blocked, "the revoke did not wait for the mint holding its parent"
    assert [row.id for row in revoked] == [child]
    assert not await _active(engine, child), "the child escaped the cascade"


async def test_a_revoke_holding_the_parent_makes_the_mint_wait_then_refuses_it(world):
    engine, user, project, _root, parent = world
    async with AsyncSession(engine) as revoke, AsyncSession(engine) as mint:
        await _revoke(revoke, parent)
        mint_pid = await _pid(mint)
        task = asyncio.create_task(_mint_child(mint, user, project, parent))
        blocked = await _waits_on_a_lock(engine, mint_pid, task)
        await revoke.commit()
        with pytest.raises(HTTPException) as refused:
            await asyncio.wait_for(task, 30)
        await mint.rollback()
    assert blocked, "the mint did not wait for the revoke holding its parent"
    assert refused.value.status_code == 401


async def test_a_mint_under_a_descendant_is_caught_by_a_second_pass(world):
    """Revoking ROOT while PARENT (its child) mints: the first pass waits on
    PARENT's row with a snapshot that predates the mint's commit."""
    engine, user, project, root, parent = world
    async with AsyncSession(engine) as mint, AsyncSession(engine) as revoke:
        child = await _mint_child(mint, user, project, parent)
        revoke_pid = await _pid(revoke)

        async def _revoke_and_commit():
            revoked = await _revoke(revoke, root)
            await revoke.commit()
            return revoked

        task = asyncio.create_task(_revoke_and_commit())
        blocked = await _waits_on_a_lock(engine, revoke_pid, task)
        await mint.commit()
        revoked = await asyncio.wait_for(task, 30)
    assert blocked, "the revoke's pass did not wait for the mint"
    assert sorted(str(row.id) for row in revoked) == sorted([str(parent), str(child)])
    assert not await _active(engine, child), "the grandchild escaped the cascade"


def _call_lines(func) -> dict[str, list[int]]:
    """Name -> source lines of each call of that bare name (ast.walk is not source order)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    lines: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            lines.setdefault(node.func.id, []).append(node.lineno)
    return lines


def test_the_routes_use_the_helpers():
    """The helpers are proven above; this pins that the routes call them, and
    that the mint locks its parent before it builds the child."""
    create = _call_lines(api_keys_router.create_api_key)
    assert "_lock_active_minting_parent" in create
    assert min(create["_lock_active_minting_parent"]) < min(create["ApiKey"])
    assert "_revoke_subtree" in _call_lines(api_keys_router.revoke_api_key)
