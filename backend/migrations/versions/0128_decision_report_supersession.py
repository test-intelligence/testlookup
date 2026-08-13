"""Durable asynchronous DecisionReport supersession requests.

Revision ID: 0128
Revises: 0127
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_report_supersession_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(length=120), nullable=False, server_default="children_terminal"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_report_id", sa.String(length=64), nullable=True),
        sa.Column("published_report_version", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'rejected', 'failed')",
            name="ck_drsr_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_drsr_attempts_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_pipeline_run_id", name="uq_drsr_parent_pipeline"),
    )
    op.create_index(
        "ix_drsr_status_next_attempt",
        "decision_report_supersession_requests",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_drsr_status_next_attempt",
        table_name="decision_report_supersession_requests",
    )
    op.drop_table("decision_report_supersession_requests")
