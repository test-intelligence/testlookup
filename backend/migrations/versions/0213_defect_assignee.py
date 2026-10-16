"""A defect assignee: ``defects.assignee_user_id`` (DEF-01).

A defect could carry a team (``owner_team``) but never a person, so the
Defects page had no way to say who is working on a row. This adds a nullable
``assignee_user_id`` (FK ``users.id``, ``ON DELETE SET NULL``: deleting a user
leaves the defect unassigned, never deleted) and a partial index for "the
defects assigned in this project" (most defects are unassigned).

No backfill: nobody is assigned before this change.

The downgrade drops the index and the column.

Revision ID: 0213
Revises: 0212
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0213"
down_revision = "0212"
branch_labels = None
depends_on = None

TABLE = "defects"
COLUMN = "assignee_user_id"
INDEX = "ix_defects_project_assignee"
INDEX_WHERE = "assignee_user_id IS NOT NULL"


def upgrade() -> None:
    # A nullable column with no default: a catalogue change, no table rewrite;
    # the FK validation scans nothing (every value is NULL).
    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # An index on an existing table is built CONCURRENTLY (the 0166+ rule,
    # tests/regression/test_migration_index_builds_are_concurrent.py); an INVALID
    # leftover of a failed build is dropped first.
    invalid = False if op.get_context().as_sql else _index_is_invalid(op.get_bind())
    with op.get_context().autocommit_block():
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} (project_id, {COLUMN}) WHERE {INDEX_WHERE}"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
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
