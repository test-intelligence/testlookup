"""Freeze resolved request configuration on agent invocations (E4.2/T11).

The snapshot is credential-free and contains no provider ``base_url``. The
worker copies it into ``agent_pipeline_runs.execution_metadata`` before any
stage executes, while current environment safety ceilings are re-applied.

No index is needed: the worker loads the column through the invocation primary
key and runtime consumers read the copied JSON from their pipeline row.

Revision ID: 0181
Revises: 0180
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0181"
down_revision = "0180"
branch_labels = None
depends_on = None

TABLE = "agent_invocations"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("resolved_config_snapshot", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(TABLE, "resolved_config_snapshot")
