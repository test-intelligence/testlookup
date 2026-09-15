"""Record the user who triggered an agent pipeline (T3).

Revision ID: 0188
Revises: 0187
Create Date: 2026-09-15

Automatic and historical runs keep NULL. The foreign key uses SET NULL so a
user deletion preserves the pipeline and review history. No index is needed:
the value is copied by primary-key pipeline lookup and is not a query filter.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0188"
down_revision = "0187"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_pipeline_runs",
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_pipeline_runs_requested_by_users",
        "agent_pipeline_runs",
        "users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_pipeline_runs_requested_by_users",
        "agent_pipeline_runs",
        type_="foreignkey",
    )
    op.drop_column("agent_pipeline_runs", "requested_by")
