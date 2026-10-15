"""A release owner: ``releases.owner_user_id`` (REL-02).

Release cards printed the PROJECT name where an owner belonged, because a
release had no owner. This adds a nullable ``owner_user_id`` (FK ``users.id``,
``ON DELETE SET NULL``: deleting a user leaves the release unowned, never
deleted) and a partial index for "releases this user owns".

Backfill: an existing release whose ``created_by_id`` names a real person (not a
service account, not a synthetic account, not deactivated) gets that person as
its owner. In practice only seeded rows carry ``created_by_id`` -- no
application code wrote it before W05 -- so most releases start with "No owner".
An owner already set is never overwritten, and ``updated_at`` is not touched
(an owner backfill is not an edit).

The downgrade drops the index and the column.

Revision ID: 0211
Revises: 0210
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0211"
down_revision = "0210"
branch_labels = None
depends_on = None

COLUMN = "owner_user_id"
INDEX = "ix_releases_owner"
INDEX_WHERE = "owner_user_id IS NOT NULL"

BACKFILL_SQL = (
    "UPDATE releases r SET owner_user_id = r.created_by_id FROM users u "
    "WHERE u.id = r.created_by_id AND r.owner_user_id IS NULL "
    "AND u.is_service_account IS NOT TRUE AND u.is_synthetic IS NOT TRUE "
    "AND u.is_active IS NOT FALSE"
)


def upgrade() -> None:
    op.add_column(
        "releases",
        sa.Column(
            COLUMN,
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        INDEX,
        "releases",
        [COLUMN],
        postgresql_where=sa.text(INDEX_WHERE),
    )
    op.execute(sa.text(BACKFILL_SQL))


def downgrade() -> None:
    op.drop_index(INDEX, table_name="releases")
    op.drop_column("releases", COLUMN)
