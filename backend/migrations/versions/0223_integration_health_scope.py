"""Integration health per (provider, scope): a scope key and a two-column primary key (W10 INT-04).

Integration Health kept ONE latest row per provider, so a per-project GitHub /
GitHub Enterprise or GitLab integration had nowhere to record its own probe.
Both health tables gain ``scope_key VARCHAR(64) NOT NULL DEFAULT ''``: ``''``
is deployment-wide (every existing row, so there is no backfill), a project
row's key is the project id. No foreign key: the key is a string shared by
kinds, and the beat prunes the rows of an integration that was removed.

* ``integration_health_checks``: the primary key becomes
  ``(provider, scope_key)``. A key cannot be built CONCURRENTLY, so the unique
  index is built that way first (inside ``autocommit_block()``) and the key
  then ADOPTS it: ``ADD CONSTRAINT ... PRIMARY KEY USING INDEX`` only attaches
  the finished index (the concurrent-index guard's allowed route). The old key
  is named as the catalog names it (``integration_health_checks_pkey`` on
  every install 0030 made); offline (``--sql``) that name is assumed. The
  table holds about ten rows, so the ALTER's brief ACCESS EXCLUSIVE lock is a
  no-op.
* ``integration_probe_results``: the history of one project reads through the
  new index ``ix_ipr_provider_scope_time (provider, scope_key, checked_at)``,
  built CONCURRENTLY. ``ix_ipr_provider_time`` is kept (the deployment-wide
  trend reads it).

A concurrent build that failed half-way leaves an INVALID index under its
name, which ``CREATE ... IF NOT EXISTS`` would then skip: each build first
drops an INVALID leftover of its own name, CONCURRENTLY. The two columns are
added ``IF NOT EXISTS`` because every ``autocommit_block()`` commits what ran
before it, so a rerun after a failed build finds them.

The downgrade first deletes every scoped row (it would otherwise read as a
deployment-wide one), drops the history index CONCURRENTLY, builds the
single-column unique index CONCURRENTLY, swaps the key back onto it the same
way, and drops both columns.

Revision ID: 0223
Revises: 0222
"""
from alembic import op
import sqlalchemy as sa

revision = "0223"
down_revision = "0222"
branch_labels = None
depends_on = None

TABLE = "integration_health_checks"
HISTORY = "integration_probe_results"
PKEY = "integration_health_checks_pkey"
SCOPED_KEY_INDEX = "uq_integration_health_checks_provider_scope"
PROVIDER_KEY_INDEX = "integration_health_checks_provider_key"
HISTORY_INDEX = "ix_ipr_provider_scope_time"

BUILD_SCOPED_KEY = (
    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {SCOPED_KEY_INDEX} "
    f"ON {TABLE} (provider, scope_key)"
)
BUILD_HISTORY_INDEX = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {HISTORY_INDEX} "
    f"ON {HISTORY} (provider, scope_key, checked_at)"
)
BUILD_PROVIDER_KEY = (
    f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {PROVIDER_KEY_INDEX} "
    f"ON {TABLE} (provider)"
)
DROP_HISTORY_INDEX = f"DROP INDEX CONCURRENTLY IF EXISTS {HISTORY_INDEX}"
DELETE_SCOPED_HEALTH = f"DELETE FROM {TABLE} WHERE scope_key <> ''"
DELETE_SCOPED_HISTORY = f"DELETE FROM {HISTORY} WHERE scope_key <> ''"
RESTORE_PROVIDER_KEY = (
    f"ALTER TABLE {TABLE} DROP CONSTRAINT {PKEY}, "
    f"ADD CONSTRAINT {PKEY} PRIMARY KEY USING INDEX {PROVIDER_KEY_INDEX}"
)


def _scope_column() -> sa.Column:
    return sa.Column("scope_key", sa.String(64), nullable=False, server_default="")


def upgrade() -> None:
    op.add_column(TABLE, _scope_column(), if_not_exists=True)
    op.add_column(HISTORY, _scope_column(), if_not_exists=True)

    offline = op.get_context().as_sql
    if not offline:
        _drop_invalid_leftover(op.get_bind(), SCOPED_KEY_INDEX)
    with op.get_context().autocommit_block():
        op.execute(BUILD_SCOPED_KEY)

    current_key = PKEY if offline else _primary_key_name(op.get_bind())
    op.execute(
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {current_key}, "
        f"ADD CONSTRAINT {PKEY} PRIMARY KEY USING INDEX {SCOPED_KEY_INDEX}"
    )

    if not offline:
        _drop_invalid_leftover(op.get_bind(), HISTORY_INDEX)
    with op.get_context().autocommit_block():
        op.execute(BUILD_HISTORY_INDEX)


def downgrade() -> None:
    # A scoped row would read as a deployment-wide one once the key is gone.
    op.execute(DELETE_SCOPED_HEALTH)
    op.execute(DELETE_SCOPED_HISTORY)

    with op.get_context().autocommit_block():
        op.execute(DROP_HISTORY_INDEX)

    if not op.get_context().as_sql:
        _drop_invalid_leftover(op.get_bind(), PROVIDER_KEY_INDEX)
    with op.get_context().autocommit_block():
        op.execute(BUILD_PROVIDER_KEY)

    op.execute(RESTORE_PROVIDER_KEY)

    op.drop_column(HISTORY, "scope_key")
    op.drop_column(TABLE, "scope_key")


def _primary_key_name(bind) -> str:
    """The name the catalog gives the health table's primary key today."""
    name = bind.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = CAST(:table AS regclass) AND contype = 'p'"
        ),
        {"table": TABLE},
    ).scalar_one_or_none()
    if not name:
        raise RuntimeError(f"{TABLE} has no primary key to replace")
    # A catalog identifier, quoted as the server would quote it.
    return '"' + str(name).replace('"', '""') + '"'


def _drop_invalid_leftover(bind, index: str) -> None:
    """Drop ``index`` CONCURRENTLY when a failed concurrent build left it INVALID.

    ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` would skip such a leftover and
    the key could not adopt it. A valid index of that name is left alone.
    """
    invalid = bind.execute(
        sa.text(
            "SELECT NOT i.indisvalid FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.oid = to_regclass(:index)"
        ),
        {"index": index},
    ).scalar_one_or_none()
    if invalid:
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index}")
