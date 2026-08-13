"""Anchor the mutable release decision row to its producing pipeline.

Revision ID: 0119
Revises: 0118
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "release_decisions",
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_release_decisions_pipeline_run_id",
        "release_decisions",
        "agent_pipeline_runs",
        ["pipeline_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_release_decisions_pipeline_run_id",
        "release_decisions",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_release_decisions_pipeline_run_id", table_name="release_decisions")
    op.drop_constraint(
        "fk_release_decisions_pipeline_run_id",
        "release_decisions",
        type_="foreignkey",
    )
    op.drop_column("release_decisions", "pipeline_run_id")
