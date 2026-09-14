"""Add append-only release incident and rollback outcomes (E9.9).

Revision ID: 0185
Revises: 0184
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0185"
down_revision = "0184"
branch_labels = None
depends_on = None

TABLE = "release_outcomes"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outcome_kind", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "marked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome_kind IN ('incident', 'rollback')",
            name="ck_release_outcomes_kind",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) >= 3",
            name="ck_release_outcomes_reason",
        ),
    )
    # Both indexes belong to this newly-created table, so building them inside
    # the migration transaction cannot lock a populated production table.
    op.create_index(
        "ix_release_outcomes_release_marked",
        TABLE,
        ["release_id", "marked_at"],
    )
    op.create_index(
        "ix_release_outcomes_project_marked",
        TABLE,
        ["project_id", "marked_at"],
    )


def downgrade() -> None:
    op.drop_table(TABLE)
