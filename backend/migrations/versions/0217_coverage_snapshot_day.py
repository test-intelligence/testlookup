"""Coverage snapshots: one row a project a UTC day, ``snapshot_day`` (W08 TRND-07).

``coverage_snapshots`` (0001) was meant to hold one row a project a day, but
its only uniqueness, ``uq_coverage_project_date``, is on ``snapshot_date``: a
``timestamptz`` defaulting to ``now()``, so two writes of the same day never
collide and a daily upsert has nothing to conflict on. No writer ever ran
(``take_coverage_snapshot`` was a stub), so the table is expected empty.

This adds ``snapshot_day DATE NOT NULL`` (the UTC day a snapshot describes) and
makes ``(project_id, snapshot_day)`` the unique key the beat upserts on:

1. the column, nullable;
2. any existing row gets the UTC day of its ``snapshot_date`` (``now()`` for a
   row that has none, so the NOT NULL below cannot fail on it); the count is
   logged;
3. two rows of one project and day collapse to one (the larger id is kept:
   defensive, nothing wrote two);
4. NOT NULL;
5. the old constraint is dropped (it never enforced a day);
6. ``uq_coverage_snapshots_project_day`` is built CONCURRENTLY inside
   ``autocommit_block()`` -- an index on an EXISTING table (the 0166+ rule,
   ``tests/regression/test_migration_index_builds_are_concurrent.py``); an
   INVALID leftover of a failed build is dropped first.

The downgrade drops the new index (CONCURRENTLY), restores
``uq_coverage_project_date`` by building its index CONCURRENTLY and attaching
it (``ADD CONSTRAINT ... UNIQUE USING INDEX``, which builds nothing), and
drops the column. The one data change it makes: rows written after this
migration in one transaction share a ``snapshot_date`` (the beat stages
several days of a project at once, and ``now()`` is the transaction's), which
the old key refuses -- such rows first get the UTC midnight of their own
``snapshot_day`` (unique per project, so the old key holds again).

``project_id`` stays nullable as it was (nothing writes NULL).

Revision ID: 0217
Revises: 0216
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "0217"
down_revision = "0216"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "coverage_snapshots"
COLUMN = "snapshot_day"
INDEX = "uq_coverage_snapshots_project_day"
OLD_CONSTRAINT = "uq_coverage_project_date"
#: The old constraint's index while it is rebuilt (downgrade only): attaching
#: it renames it to the constraint's name.
OLD_INDEX = "uq_coverage_project_date_ix"

_BACKFILL = (
    f"UPDATE {TABLE} SET {COLUMN} = (COALESCE(snapshot_date, now()) AT TIME ZONE 'UTC')::date "
    f"WHERE {COLUMN} IS NULL"
)
_COLLAPSE = (
    f"DELETE FROM {TABLE} a USING {TABLE} b "
    f"WHERE a.project_id = b.project_id AND a.{COLUMN} = b.{COLUMN} AND a.id < b.id"
)
#: Downgrade: rows whose (project_id, snapshot_date) the old key would refuse.
_SPREAD_SHARED_TIMESTAMPS = (
    f"UPDATE {TABLE} s SET snapshot_date = (s.{COLUMN}::timestamp AT TIME ZONE 'UTC') "
    f"FROM (SELECT project_id, snapshot_date FROM {TABLE} "
    f"GROUP BY project_id, snapshot_date HAVING COUNT(*) > 1) d "
    f"WHERE s.project_id = d.project_id AND s.snapshot_date = d.snapshot_date"
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Date(), nullable=True))
    if op.get_context().as_sql:
        op.execute(_BACKFILL)
        op.execute(_COLLAPSE)
    else:
        bind = op.get_bind()
        filled = bind.execute(sa.text(_BACKFILL)).rowcount
        collapsed = bind.execute(sa.text(_COLLAPSE)).rowcount
        logger.info(
            "0217 coverage_snapshots: snapshot_day backfilled on %s rows, %s same-day duplicates removed",
            filled, collapsed,
        )
    op.alter_column(TABLE, COLUMN, existing_type=sa.Date(), nullable=False)
    op.drop_constraint(OLD_CONSTRAINT, TABLE, type_="unique")
    _build_unique_index()


def _build_unique_index() -> None:
    """Step 6, re-runnable: an index on an existing table is built CONCURRENTLY
    (the 0166+ rule); an INVALID leftover of a failed build is dropped first
    (``CREATE ... IF NOT EXISTS`` would otherwise keep it, invalid, for good)."""
    invalid = False if op.get_context().as_sql else _index_is_invalid(op.get_bind())
    with op.get_context().autocommit_block():
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} (project_id, {COLUMN})"
        )


def downgrade() -> None:
    op.execute(_SPREAD_SHARED_TIMESTAMPS)
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {OLD_INDEX} ON {TABLE} (project_id, snapshot_date)"
        )
    # Attaches the finished index; builds nothing (the guard's allowed form).
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT {OLD_CONSTRAINT} UNIQUE USING INDEX {OLD_INDEX}")
    op.drop_column(TABLE, COLUMN)


def _index_is_invalid(bind) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": INDEX},
        ).first()
    )
