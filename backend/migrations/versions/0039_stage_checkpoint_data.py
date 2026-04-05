"""Add checkpoint_data column to agent_stage_results for pipeline resume.

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_stage_results",
        sa.Column("checkpoint_data", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_stage_results", "checkpoint_data")
