"""Migrations 0207-0209 on real PostgreSQL: who gets which stage flag, and what a downgrade removes.

* 0207 (AIC-06): ``llm_cost_budget`` flips ON only on an unused database (no
  user, no project) and only while it still holds 0064's OFF.
* 0208 (AIC-07, D20): the four read-only deep-pipeline stage rows are ON for a
  new install and OFF on a used one unless their legacy env variable is on;
  ``defect_commander`` is OFF everywhere; a hand-made row is never touched.
* 0209 (AGT-16): ``aiq_gap_refinement`` is ON on every install unless its env
  variable says otherwise.
* Downgrades delete only rows still equal to their seed; an edited row stays.
* The planner, fed by ``is_enabled(..., fresh=True)`` over the seeded rows,
  plans the four read-only stages and not ``defect_commander``; a global-ON
  ``defect_commander`` limited to one project is ON for that project only.

The migrations run through a real Alembic ``Operations`` context against a
SCRATCH schema (``feature_flags`` as ``LIKE ... INCLUDING ALL``, so the unique
key and defaults are real; ``users``/``projects`` as one-column stand-ins,
because the fresh-install predicate only tests ``EXISTS``). Everything happens
inside one transaction that is rolled back: the shared tables are never written.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
READ_ONLY = ("contract_validation", "log_intelligence", "regression_watchman", "change_ownership")
LEGACY_VARS = (
    "AIQ_CONTRACT_VALIDATION_ENABLED",
    "AIQ_CHANGE_OWNERSHIP_ENABLED",
    "AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED",
    "AIQ_GAP_REFINEMENT_ENABLED",
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(f"m_{name[:4]}_pg", VERSIONS / name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


M0207 = "0207_llm_cost_budget_on_for_new_installs.py"
M0208 = "0208_seed_agent_stage_flags.py"
M0209 = "0209_seed_aiq_gap_refinement_flag.py"


@pytest.fixture
def clean_env(monkeypatch):
    for name in LEGACY_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
async def conn():
    """One connection inside one transaction that is always rolled back."""
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            tx = await connection.begin()
            try:
                yield connection
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()


async def _scratch(conn, *, used: bool) -> None:
    """A scratch schema shadowing feature_flags/users/projects for this transaction."""
    base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
    scratch = f"w04c_{uuid.uuid4().hex[:10]}"
    await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
    await conn.execute(
        text(f'CREATE TABLE "{scratch}".feature_flags (LIKE "{base}".feature_flags INCLUDING ALL)')
    )
    await conn.execute(text(f'CREATE TABLE "{scratch}".users (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'CREATE TABLE "{scratch}".projects (id uuid PRIMARY KEY)'))
    await conn.execute(text(f'SET LOCAL search_path TO "{scratch}", "{base}"'))
    # The row as 0064 seeded it.
    await conn.execute(text(
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, "
        "created_at, updated_at) VALUES (gen_random_uuid(), 'llm_cost_budget', "
        "'Per-project LLM spend metering and caps', false, 100, now(), now())"
    ))
    if used:
        await conn.execute(text("INSERT INTO users (id) VALUES (gen_random_uuid())"))
        await conn.execute(text("INSERT INTO projects (id) VALUES (gen_random_uuid())"))


async def _migrate(conn, step: str, names: tuple[str, ...]) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    modules = [_migration(name) for name in names]

    def _run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        for module in modules:
            module.op = Operations(context)
            getattr(module, step)()

    await conn.run_sync(_run)


async def _upgrade(conn) -> None:
    await _migrate(conn, "upgrade", (M0207, M0208, M0209))


async def _downgrade(conn) -> None:
    await _migrate(conn, "downgrade", (M0209, M0208, M0207))


async def _flags(conn) -> dict[str, bool]:
    rows = (await conn.execute(text("SELECT key, enabled_global FROM feature_flags"))).all()
    return {key: bool(enabled) for key, enabled in rows}


async def test_a_new_install_gets_the_read_only_stages_on(conn, clean_env):
    await _scratch(conn, used=False)
    await _upgrade(conn)
    flags = await _flags(conn)
    assert {k: flags[k] for k in READ_ONLY} == dict.fromkeys(READ_ONLY, True)
    assert flags["defect_commander"] is False
    assert flags["async_decision_report_supersession"] is False
    assert flags["aiq_gap_refinement"] is True
    assert flags["llm_cost_budget"] is True, "0207 flips 0064's OFF on an unused database"
    rows = (await conn.execute(text(
        "SELECT key, rollout_percent, enabled_projects, enabled_roles, description, "
        "created_at = updated_at FROM feature_flags WHERE key <> 'llm_cost_budget'"
    ))).all()
    assert len(rows) == 7
    for key, rollout, projects, roles, description, untouched in rows:
        assert (rollout, projects, roles, untouched) == (100, None, None, True), key
        assert description


async def test_an_existing_install_keeps_them_off(conn, clean_env):
    await _scratch(conn, used=True)
    await _upgrade(conn)
    flags = await _flags(conn)
    assert {k: flags[k] for k in READ_ONLY} == dict.fromkeys(READ_ONLY, False)
    assert flags["defect_commander"] is False
    assert flags["async_decision_report_supersession"] is False
    assert flags["aiq_gap_refinement"] is True, "deterministic stages: ON for every install"
    assert flags["llm_cost_budget"] is False, "a used database keeps 0064's value"


@pytest.mark.parametrize("used", [False, True])
async def test_a_legacy_env_switch_turns_its_row_on_in_both(conn, clean_env, used):
    clean_env.setenv("AIQ_CONTRACT_VALIDATION_ENABLED", "true")
    clean_env.setenv("AIQ_CHANGE_OWNERSHIP_ENABLED", "Yes")
    clean_env.setenv("AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED", "1")
    clean_env.setenv("AIQ_GAP_REFINEMENT_ENABLED", "false")
    await _scratch(conn, used=used)
    await _upgrade(conn)
    flags = await _flags(conn)
    assert flags["contract_validation"] is True
    assert flags["change_ownership"] is True
    assert flags["async_decision_report_supersession"] is True
    assert flags["log_intelligence"] is (not used)
    assert flags["regression_watchman"] is (not used)
    assert flags["defect_commander"] is False
    assert flags["aiq_gap_refinement"] is False, "an operator's explicit off is kept"


async def test_a_hand_made_row_is_never_touched(conn, clean_env):
    await _scratch(conn, used=False)
    await conn.execute(text(
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, "
        "enabled_projects, created_at, updated_at) VALUES (gen_random_uuid(), 'defect_commander', "
        "'hand made', true, 50, CAST(:projects AS jsonb), now(), now())"
    ), {"projects": '["11111111-1111-1111-1111-111111111111"]'})
    # An operator who created the gap/refinement row by hand, OFF.
    await conn.execute(text(
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, "
        "created_at, updated_at) VALUES (gen_random_uuid(), 'aiq_gap_refinement', 'mine', false, 100, "
        "now(), now())"
    ))
    await _upgrade(conn)
    row = (await conn.execute(text(
        "SELECT enabled_global, description, rollout_percent, enabled_projects "
        "FROM feature_flags WHERE key = 'defect_commander'"
    ))).one()
    assert tuple(row) == (True, "hand made", 50, ["11111111-1111-1111-1111-111111111111"])
    gap = (await conn.execute(text(
        "SELECT enabled_global, description FROM feature_flags WHERE key = 'aiq_gap_refinement'"
    ))).one()
    assert tuple(gap) == (False, "mine"), "0209 must not switch on a row an operator made"
    await _downgrade(conn)
    survivors = await _flags(conn)
    assert survivors == {"defect_commander": True, "aiq_gap_refinement": False, "llm_cost_budget": False}


async def test_a_downgrade_deletes_only_untouched_seed_rows(conn, clean_env):
    await _scratch(conn, used=False)
    await _upgrade(conn)
    # An admin toggle (feature_flags.update_flag stamps updated_at).
    await conn.execute(text(
        "UPDATE feature_flags SET enabled_global = NOT enabled_global, "
        "updated_at = clock_timestamp() WHERE key = 'log_intelligence'"
    ))
    # An admin who only narrowed a row to one project.
    await conn.execute(text(
        "UPDATE feature_flags SET enabled_projects = CAST(:p AS jsonb) WHERE key = 'aiq_gap_refinement'"
    ), {"p": '["22222222-2222-2222-2222-222222222222"]'})
    await _downgrade(conn)
    assert await _flags(conn) == {
        "log_intelligence": False,
        "aiq_gap_refinement": True,
        "llm_cost_budget": False,
    }


async def test_0207_downgrade_leaves_a_used_database_alone(conn, clean_env):
    await _scratch(conn, used=True)
    await conn.execute(text("UPDATE feature_flags SET enabled_global = true WHERE key = 'llm_cost_budget'"))
    await _upgrade(conn)
    await _downgrade(conn)
    assert (await _flags(conn)) == {"llm_cost_budget": True}


async def test_a_re_run_is_a_no_op(conn, clean_env):
    await _scratch(conn, used=False)
    await _upgrade(conn)
    before = (await conn.execute(text("SELECT key, id, enabled_global FROM feature_flags ORDER BY key"))).all()
    await _migrate(conn, "upgrade", (M0208, M0209))
    after = (await conn.execute(text("SELECT key, id, enabled_global FROM feature_flags ORDER BY key"))).all()
    assert after == before


async def test_the_planner_follows_the_seeded_rows(conn, clean_env):
    from app.services.agent_planner import build_workflow_plan
    from app.services.feature_flags import is_enabled

    await _scratch(conn, used=False)
    await _upgrade(conn)
    project = uuid.uuid4()
    session = AsyncSession(bind=conn)
    try:
        resolved = {
            key: await is_enabled(key, db=session, project_id=project, fresh=True)
            for key in (*READ_ONLY, "defect_commander", "aiq_gap_refinement")
        }
        # The env fallback is ignored once the row exists.
        clean_env.setenv("AIQ_GAP_REFINEMENT_ENABLED", "false")
        assert await is_enabled("aiq_gap_refinement", db=session, project_id=project, fresh=True) is True
    finally:
        await session.close()
    assert resolved == {**dict.fromkeys(READ_ONLY, True), "defect_commander": False, "aiq_gap_refinement": True}

    plan = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["t-1", "t-2"],
        contract_validation_enabled=resolved["contract_validation"],
        log_intelligence_enabled=resolved["log_intelligence"],
        regression_watchman_enabled=resolved["regression_watchman"],
        change_ownership_enabled=resolved["change_ownership"],
        defect_commander_enabled=resolved["defect_commander"],
    )
    stages = {item["stage"]: item for item in plan["stages"]}
    for key in READ_ONLY:
        assert stages[key]["planned"] is True, (key, stages[key]["rationale"])
    assert stages["defect_commander"]["planned"] is False
    assert stages["defect_commander"]["rationale"] == "DefectCommander feature flag is off for this project"


async def test_defect_commander_on_for_one_project_only(conn, clean_env):
    from app.services.feature_flags import is_enabled

    await _scratch(conn, used=False)
    await _upgrade(conn)
    chosen, other = uuid.uuid4(), uuid.uuid4()
    # What the Edit dialog stores: global ON + the project allow-list.
    await conn.execute(text(
        "UPDATE feature_flags SET enabled_global = true, enabled_projects = CAST(:p AS jsonb), "
        "updated_at = clock_timestamp() WHERE key = 'defect_commander'"
    ), {"p": f'["{chosen}"]'})
    session = AsyncSession(bind=conn)
    try:
        assert await is_enabled("defect_commander", db=session, project_id=chosen, fresh=True) is True
        assert await is_enabled("defect_commander", db=session, project_id=other, fresh=True) is False
        assert await is_enabled("defect_commander", db=session, fresh=True) is False
    finally:
        await session.close()
