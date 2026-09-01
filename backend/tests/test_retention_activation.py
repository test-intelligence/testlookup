"""S1 — "activate what already ships".

Retention has shipped for a while, but ``project_retention_policies.enabled``
defaults to ``False`` and nothing ever prompted an operator to turn it on, so
the feature is present, discoverable and inert. These tests cover the three
things S1 promises:

1. a per-user dismissal that actually persists per USER (not per browser),
2. a project list that reports retention posture in ONE query, and
3. the guarantee that no migration silently enables retention on upgrade.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers.projects import _retention_status
from app.services import ui_dismissal_service


# ── (1) dismissal vocabulary + idempotency ───────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_dismissal_key_is_refused(mocker):
    """A typo'd key must 422, not land in the table.

    A dismissal that never matches on read is indistinguishable from one that
    was never recorded — the user dismisses the prompt, it comes back, and
    nothing anywhere reports an error.
    """
    db = mocker.AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await ui_dismissal_service.record_dismissal(
            db, uuid.uuid4(), "retention_activation_nudg"  # missing trailing 'e'
        )

    assert exc.value.status_code == 422
    assert "Unknown dismissal key" in exc.value.detail
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_key_stages_insert_but_does_not_commit(mocker):
    """The service stages only — the router owns the commit.

    Guarded repo-wide by the transaction-boundary ratchet; asserted here so a
    stray ``db.commit()`` fails this suite too, with a message that says why.
    """
    db = mocker.AsyncMock()

    await ui_dismissal_service.record_dismissal(
        db, uuid.uuid4(), ui_dismissal_service.RETENTION_ACTIVATION_NUDGE
    )

    db.execute.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismissal_insert_is_idempotent_by_construction(mocker):
    """Dismissing twice must not raise.

    Read-then-write would let two concurrent requests — a double-clicked
    button, a retried POST — both pass the existence check, and the second
    INSERT would violate the unique constraint and 500 a dismissal that had
    actually succeeded. Assert the ON CONFLICT clause is present in the
    compiled statement rather than trusting the shape of the call.
    """
    db = mocker.AsyncMock()

    await ui_dismissal_service.record_dismissal(
        db, uuid.uuid4(), ui_dismissal_service.RETENTION_ACTIVATION_NUDGE
    )

    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "ON CONFLICT" in compiled.upper()
    assert "DO NOTHING" in compiled.upper()


# ── (2) retention posture in the project list ────────────────────────────────


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (True, "enabled"),
        (False, "disabled"),
        (None, "unconfigured"),
    ],
)
def test_retention_status_mapping(enabled, expected):
    """``None`` means "never configured", NOT "disabled".

    ``project_retention_policies.enabled`` is NOT NULL, so a NULL can only come
    from the LEFT JOIN finding no row. Collapsing that into "disabled" would
    hide exactly the projects the activation nudge exists to surface.
    """
    assert _retention_status(enabled) == expected


def test_project_list_resolves_retention_without_n_plus_one():
    """The posture must ride on the list query, not a per-project lookup.

    Asserted against the source because the failure mode is a performance
    cliff that a mocked unit test renders invisible: with a per-project call,
    a 200-project install issues 201 queries and every test still passes.
    """
    source = Path(__file__).resolve().parents[1] / "app" / "routers" / "projects.py"
    body = source.read_text(encoding="utf-8")
    list_projects = body.split("async def list_projects", 1)[1].split("\n@router", 1)[0]

    assert "outerjoin" in list_projects, (
        "list_projects must LEFT JOIN project_retention_policies; a per-project "
        "lookup reintroduces the N+1 this slice exists to avoid"
    )
    # Exactly one execute in the handler — the join, and nothing else.
    assert list_projects.count("await db.execute") == 1


# ── (3) the upgrade guarantee ────────────────────────────────────────────────


def test_no_migration_enables_retention():
    """No migration may switch retention on for an existing deployment.

    Enabling it on upgrade would start irreversibly deleting data on
    deployments that never opted in. The nudge asks; migrations do not decide.
    This scans every revision rather than only the newest, because the
    guarantee is about the whole history, not this slice's own file.
    """
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    offenders: list[str] = []

    # UPDATE ... project_retention_policies ... SET ... enabled = true
    update_enable = re.compile(
        r"UPDATE\s+project_retention_policies.*?enabled\s*=\s*true",
        re.IGNORECASE | re.DOTALL,
    )
    # A column default flipping to true would enable it for every future row
    # without a single UPDATE statement.
    default_true = re.compile(
        r"project_retention_policies.*?server_default\s*=\s*[\"']?true",
        re.IGNORECASE | re.DOTALL,
    )

    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "project_retention_policies" not in text:
            continue
        if update_enable.search(text) or default_true.search(text):
            offenders.append(path.name)

    assert offenders == [], (
        "these migrations would enable retention on upgrade, silently starting "
        f"data deletion on deployments that never opted in: {offenders}"
    )


def test_s1_migration_upgrade_body_executes(mocker):
    """Run ``upgrade()`` and ``downgrade()`` with a mocked ``op``.

    Every other migration test in this file reads source text; none executes
    the functions. This one does, which catches a typo'd ``op`` call or a
    column type that fails to construct.

    **What it deliberately does NOT prove.** This process has already imported
    the app models, so any ``sqlalchemy.dialects`` attribute resolves here
    regardless of what this module imports. A migration relying on another
    module's import side-effect would still pass. That is acceptable because
    ``migrations/env.py:13`` does ``from app.models.postgres import *`` before
    any revision runs, so the real alembic path has the same imports loaded —
    but it means this test cannot be cited as protection against import
    fragility. It covers execution, not import hygiene.

    ``downgrade()`` is executed too: ``database.downgrade-implemented`` checks
    only that a body exists, not that it runs.
    """
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0145_user_ui_dismissals.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0145", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    mocker.patch.object(module, "op", mocker.MagicMock())

    module.upgrade()    # must not raise
    module.downgrade()  # ditto — a downgrade that raises is not a downgrade

    assert module.op.create_table.call_count == 1
    assert module.op.drop_table.call_count == 1
def test_s1_migration_casts_seeded_flag_id_to_uuid(mocker):
    """The seeded ``feature_flags`` row binds a stable string id into a uuid
    column. asyncpg sends a bare str as character varying, so Postgres rejects
    the statement — ``column "id" is of type uuid but expression is of type
    character varying`` — and the whole upgrade (and the downgrade DELETE, which
    compares the same id) aborts. That took the Backend PostgreSQL upgrade job
    red for every branch.

    ``test_s1_migration_upgrade_body_executes`` cannot catch this: it mocks
    ``op``, so ``op.execute`` never reaches a database and a datatype mismatch is
    invisible. Assert on the SQL text the migration actually hands ``op`` — both
    the INSERT and the DELETE must cast the id, exactly as the sibling 0144 flag
    seed does.
    """
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0145_user_ui_dismissals.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0145_cast", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    mocker.patch.object(module, "op", mocker.MagicMock())

    module.upgrade()
    upgrade_sql = " ".join(
        str(call.args[0]) for call in module.op.execute.call_args_list if call.args
    )
    assert "feature_flags" in upgrade_sql
    assert "CAST(:id AS uuid)" in upgrade_sql
    # The bare, uncast bind is exactly what asyncpg rejects.
    assert "(:id," not in upgrade_sql.replace(" ", "")

    module.op.reset_mock()
    module.downgrade()
    downgrade_sql = " ".join(
        str(call.args[0]) for call in module.op.execute.call_args_list if call.args
    )
    assert "CAST(:id AS uuid)" in downgrade_sql
    assert "id=:id" not in downgrade_sql.replace(" ", "")


def test_s1_migration_does_not_touch_the_policy_table_at_all():
    """0145 adds a dismissal store and a flag. It must not reach further.

    Narrower than the sweep above and deliberately so: this is the file this
    slice owns, and the cheapest way to catch a well-meaning edit that decides
    to "helpfully" backfill a policy row while it is in there.
    """
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0145_user_ui_dismissals.py"
    )
    body = migration.read_text(encoding="utf-8")
    upgrade = body.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]

    assert "project_retention_policies" not in upgrade
