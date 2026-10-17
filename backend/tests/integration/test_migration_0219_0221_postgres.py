"""Migrations 0219, 0220 and 0221 (W09 FLK-01 / NTF-01 / NTF-04) on real PostgreSQL.

0219: the untouched 0065 seed of ``flaky_auto_quarantine`` flips ON and carries
the marker; a row with an actor stays OFF; a row whose ``updated_at`` moved
stays as it is; a row an admin turned ON stays ON without the marker; a second
upgrade is a no-op; the downgrade reverts only the flipped row (back to
``updated_at = created_at``, no marker) and a re-upgrade flips it again; an
admin PATCH after the upgrade (actor stamped) survives the downgrade.

0220: ``["flaky_test_detected"]`` -> ``["test.newly_flaky"]``; ``["run_failed",
"flaky_test_detected", "test.newly_flaky"]`` -> ``["run_failed",
"test.newly_flaky"]`` (order kept, duplicate dropped); ``[]``, NULL,
``{"a": 1}`` and alias-free rows untouched (the column is NOT NULL: "NULL" is
the JSON ``null``); a second upgrade is a no-op; the
downgrade restores both memberships and drops the bookkeeping table.

0221: the column, the backfill from a bound view, the downgrade.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema (``LIKE ... INCLUDING DEFAULTS`` copies of the four tables,
``saved_view_name`` dropped from the digest copy); the scratch schema is
dropped afterwards and the shared tables are never written.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
M0219 = "0219_flaky_quarantine_on.py"
M0220 = "0220_flaky_preference_rewrite.py"
M0221 = "0221_digest_saved_view_name.py"
KEY = "flaky_auto_quarantine"
SEED_DESCRIPTION = "Flaky-test auto-quarantine workflow (QA Lead approval, recheck-after-N-days, audit trail). Tier 1 item 3."
SEEDED_AT = datetime(2026, 4, 14, 9, 0, tzinfo=timezone.utc)
ADMIN = uuid.UUID(int=0xAD)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(f"m_{filename[:4]}_pg", VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MARKER = _migration(M0219).MARKER


@pytest.fixture
async def conn():
    """A connection whose work commits; the scratch schema is dropped afterwards."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            connection.info["scratch_schemas"] = []
            try:
                yield connection
            finally:
                await connection.rollback()
                await connection.execute(text("RESET search_path"))
                for schema in connection.info["scratch_schemas"]:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.commit()
    finally:
        await engine.dispose()


async def _scratch(conn) -> str:
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w09a_{uuid.uuid4().hex[:10]}"
    conn.info["scratch_schemas"].append(scratch)
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    for table in ("feature_flags", "notification_preferences", "saved_views", "digest_subscriptions"):
        await conn.execute(text(
            f'CREATE TABLE "{scratch}".{table} (LIKE "{base}".{table} INCLUDING DEFAULTS)'
        ))
        await conn.execute(text(f'ALTER TABLE "{scratch}".{table} ADD PRIMARY KEY (id)'))
    # The digest table as 0220 left it: no saved_view_name yet.
    await conn.execute(text(f'ALTER TABLE "{scratch}".digest_subscriptions DROP COLUMN IF EXISTS saved_view_name'))
    await conn.execute(text(f'SET search_path TO "{scratch}", "{base}"'))
    await conn.commit()
    return scratch


async def _run(conn, filename: str, step: str) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _migration(filename)

    def _go(sync_conn):
        context = MigrationContext.configure(sync_conn)
        module.op = Operations(context)
        with context.begin_transaction():
            getattr(module, step)()

    await conn.commit()
    await conn.run_sync(_go)
    await conn.commit()


# ── 0219 ─────────────────────────────────────────────────────────────────────


async def _flag(conn, *, enabled: bool, actor=None, updated_at=SEEDED_AT, description=SEED_DESCRIPTION) -> None:
    await conn.execute(text(
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, "
        "created_at, updated_at, updated_by_user_id) "
        "VALUES (:id, :key, :d, :on, 100, :c, :u, :actor)"
    ), {"id": uuid.uuid4(), "key": KEY, "d": description, "on": enabled, "c": SEEDED_AT, "u": updated_at,
        "actor": actor})
    await conn.commit()


async def _read_flag(conn):
    row = (await conn.execute(text(
        "SELECT enabled_global, description, created_at, updated_at, updated_by_user_id "
        "FROM feature_flags WHERE key = :key"
    ), {"key": KEY})).one()
    await conn.commit()
    return row


async def test_0219_flips_the_untouched_seed_and_round_trips(conn):
    await _scratch(conn)
    await _flag(conn, enabled=False)

    await _run(conn, M0219, "upgrade")
    on, description, created, updated, actor = await _read_flag(conn)
    assert on is True
    assert description == SEED_DESCRIPTION + MARKER
    assert actor is None and updated > created

    # A second upgrade finds nothing to flip: the description gains no second marker.
    await _run(conn, M0219, "upgrade")
    assert (await _read_flag(conn))[1] == SEED_DESCRIPTION + MARKER

    await _run(conn, M0219, "downgrade")
    on, description, created, updated, actor = await _read_flag(conn)
    assert (on, description, actor) == (False, SEED_DESCRIPTION, None)
    assert updated == created, "the downgrade restores the untouched seed exactly"

    # ... so a re-upgrade flips it again.
    await _run(conn, M0219, "upgrade")
    assert (await _read_flag(conn))[:2] == (True, SEED_DESCRIPTION + MARKER)


async def test_0219_a_seed_without_a_description_gains_only_the_marker(conn):
    await _scratch(conn)
    await _flag(conn, enabled=False, description=None)
    await _run(conn, M0219, "upgrade")
    assert (await _read_flag(conn))[:2] == (True, MARKER)


@pytest.mark.parametrize(
    "enabled, actor, moved",
    [
        (False, ADMIN, False),   # an admin switched it off (actor stamped)
        (False, None, True),     # touched without an actor: updated_at moved
        (True, ADMIN, True),     # an admin turned it on
    ],
    ids=["admin-off", "moved-off", "admin-on"],
)
async def test_0219_leaves_a_row_somebody_touched_alone(conn, enabled, actor, moved):
    await _scratch(conn)
    later = datetime(2026, 6, 1, tzinfo=timezone.utc) if moved else SEEDED_AT
    await _flag(conn, enabled=enabled, actor=actor, updated_at=later)
    before = await _read_flag(conn)

    await _run(conn, M0219, "upgrade")
    assert await _read_flag(conn) == before
    await _run(conn, M0219, "downgrade")
    assert await _read_flag(conn) == before


async def test_0219_an_admin_patch_after_the_upgrade_survives_the_downgrade(conn):
    await _scratch(conn)
    await _flag(conn, enabled=False)
    await _run(conn, M0219, "upgrade")
    # PATCH /feature-flags/{key}: the actor is stamped (here the admin keeps it ON).
    await conn.execute(text(
        "UPDATE feature_flags SET updated_by_user_id = :a, updated_at = now() WHERE key = :key"
    ), {"a": ADMIN, "key": KEY})
    await conn.commit()
    before = await _read_flag(conn)

    await _run(conn, M0219, "downgrade")
    assert await _read_flag(conn) == before
    assert before[0] is True


# ── 0220 ─────────────────────────────────────────────────────────────────────


async def _pref(conn, events) -> uuid.UUID:
    """``events`` is NOT NULL in the table: ``None`` is stored as the JSON ``null``."""
    pid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO notification_preferences (id, user_id, channel, enabled, events) "
        "VALUES (:id, :u, 'email', true, CAST(:e AS jsonb))"
    ), {"id": pid, "u": uuid.uuid4(), "e": json.dumps(events)})
    return pid


async def _events(conn) -> dict:
    rows = (await conn.execute(text("SELECT id, events FROM notification_preferences"))).all()
    await conn.commit()
    return {pid: events for pid, events in rows}


async def test_0220_rewrites_the_alias_and_restores_membership(conn):
    scratch = await _scratch(conn)
    only_alias = await _pref(conn, ["flaky_test_detected"])
    both = await _pref(conn, ["run_failed", "flaky_test_detected", "test.newly_flaky"])
    alias_last = await _pref(conn, ["run_failed", "flaky_test_detected"])
    untouched = {
        await _pref(conn, []): [],
        await _pref(conn, None): None,
        await _pref(conn, {"a": 1}): {"a": 1},
        await _pref(conn, ["run_failed", "test.newly_flaky"]): ["run_failed", "test.newly_flaky"],
    }
    await conn.commit()

    await _run(conn, M0220, "upgrade")
    after = await _events(conn)
    assert after[only_alias] == ["test.newly_flaky"]
    assert after[both] == ["run_failed", "test.newly_flaky"]
    assert after[alias_last] == ["run_failed", "test.newly_flaky"]
    for pid, events in untouched.items():
        assert after[pid] == events
    marked = dict((await conn.execute(text(
        "SELECT preference_id, had_alias_target FROM notification_pref_rewrites_0220"
    ))).all())
    assert marked == {only_alias: False, both: True, alias_last: False}
    await conn.commit()

    # A second rewrite finds nothing (the bookkeeping table already exists, so run the statements only).
    module = _migration(M0220)
    for statement in (module.RECORD_SQL, module.REWRITE_SQL):
        result = await conn.execute(text(statement))
        assert result.rowcount == 0
    await conn.commit()
    assert await _events(conn) == after

    await _run(conn, M0220, "downgrade")
    restored = await _events(conn)
    assert restored[only_alias] == ["flaky_test_detected"]
    assert set(restored[both]) == {"run_failed", "flaky_test_detected", "test.newly_flaky"}
    assert restored[alias_last] == ["run_failed", "flaky_test_detected"]
    for pid, events in untouched.items():
        assert restored[pid] == events
    gone = (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{scratch}.notification_pref_rewrites_0220"})).scalar_one()
    assert gone is None
    await conn.commit()


async def test_0220_a_person_who_unticked_it_after_the_upgrade_keeps_it_off(conn):
    await _scratch(conn)
    pid = await _pref(conn, ["flaky_test_detected", "run_failed"])
    await conn.commit()
    await _run(conn, M0220, "upgrade")
    await conn.execute(text(
        "UPDATE notification_preferences SET events = '[\"run_failed\"]'::jsonb WHERE id = :id"
    ), {"id": pid})
    await conn.commit()
    await _run(conn, M0220, "downgrade")
    assert (await _events(conn))[pid] == ["run_failed"]


# ── 0221 ─────────────────────────────────────────────────────────────────────


async def test_0221_adds_and_backfills_the_view_name_then_drops_it(conn):
    scratch = await _scratch(conn)
    user, view = uuid.uuid4(), uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO saved_views (id, user_id, name, filters) VALUES (:id, :u, 'Checkout regressions', '{}')"
    ), {"id": view, "u": user})
    bound, unbound, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    insert = text(
        "INSERT INTO digest_subscriptions (id, user_id, saved_view_id, name, schedule, channel) "
        "VALUES (:id, :u, :v, :n, 'WEEKLY', 'email')"
    )
    await conn.execute(insert, {"id": bound, "u": user, "v": view, "n": "Weekly"})
    await conn.execute(insert, {"id": unbound, "u": user, "v": None, "n": "Daily"})
    # A dangling id (the scratch copy has no FK): no view of that id -> nothing to snapshot.
    await conn.execute(insert, {"id": gone, "u": user, "v": uuid.uuid4(), "n": "Gone"})
    await conn.commit()

    await _run(conn, M0221, "upgrade")
    column = (await conn.execute(text(
        "SELECT is_nullable, data_type, character_maximum_length FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = 'digest_subscriptions' AND column_name = 'saved_view_name'"
    ), {"s": scratch})).one()
    assert tuple(column) == ("YES", "character varying", 255)
    names = dict((await conn.execute(text("SELECT id, saved_view_name FROM digest_subscriptions"))).all())
    assert names == {bound: "Checkout regressions", unbound: None, gone: None}
    await conn.commit()

    await _run(conn, M0221, "downgrade")
    columns = (await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = 'digest_subscriptions'"
    ), {"s": scratch})).scalars().all()
    assert "saved_view_name" not in columns
    await conn.commit()
