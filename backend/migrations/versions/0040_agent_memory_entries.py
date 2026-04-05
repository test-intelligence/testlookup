"""Add agent_memory_entries table for unified memory and retrieval (P3).

Revision ID: 0040
Revises: 0039
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pipeline_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_pipeline_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("error_signature", sa.Text(), nullable=True),
        sa.Column("failure_category", sa.String(50), nullable=True),
        sa.Column("root_cause_summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_ame_project_id", "agent_memory_entries", ["project_id"])
    op.create_index("ix_ame_run_id", "agent_memory_entries", ["run_id"])
    op.create_index("ix_ame_entity", "agent_memory_entries", ["entity_type", "entity_id"])
    op.create_index("ix_ame_project_entity", "agent_memory_entries", ["project_id", "entity_type"])
    op.create_index("ix_ame_created", "agent_memory_entries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ame_created", table_name="agent_memory_entries")
    op.drop_index("ix_ame_project_entity", table_name="agent_memory_entries")
    op.drop_index("ix_ame_entity", table_name="agent_memory_entries")
    op.drop_index("ix_ame_run_id", table_name="agent_memory_entries")
    op.drop_index("ix_ame_project_id", table_name="agent_memory_entries")
    op.drop_table("agent_memory_entries")
