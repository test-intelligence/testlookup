"""Add multi-agent pipeline and chat tables.

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── agent_pipeline_runs ───────────────────────────────────────
    op.create_table(
        "agent_pipeline_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("test_run_id", UUID(as_uuid=True),
                  sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_type", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pipeline_runs_test_run", "agent_pipeline_runs", ["test_run_id"])
    op.create_index("ix_pipeline_runs_status", "agent_pipeline_runs", ["status"])

    # ── agent_stage_results ───────────────────────────────────────
    op.create_table(
        "agent_stage_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_run_id", UUID(as_uuid=True),
                  sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_data", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )

    # ── chat_sessions ─────────────────────────────────────────────
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_sessions_user", "chat_sessions", ["user_id"])

    # ── chat_messages ─────────────────────────────────────────────
    op.create_table(
        "chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", UUID(as_uuid=True),
                  sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("sources", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_messages_session", "chat_messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("agent_stage_results")
    op.drop_table("agent_pipeline_runs")
