"""Persist AI reports for run and suite comparisons.

Revision ID: 0073
Revises: 0072
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_comparison_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("left_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("right_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_name", sa.String(length=500), nullable=True),
        sa.Column("suite_name_normalized", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("compare_payload", sa.JSON(), nullable=False),
        sa.Column("ai_report", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prompt_version", sa.String(length=50), nullable=False, server_default="run_compare_v1"),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["left_run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["right_run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "left_run_id",
            "right_run_id",
            "suite_name_normalized",
            "prompt_version",
            name="uq_run_comparison_report_scope",
        ),
    )
    op.create_index(
        "ix_run_comparison_reports_project",
        "run_comparison_reports",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_run_comparison_reports_runs",
        "run_comparison_reports",
        ["left_run_id", "right_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_comparison_reports_runs", table_name="run_comparison_reports")
    op.drop_index("ix_run_comparison_reports_project", table_name="run_comparison_reports")
    op.drop_table("run_comparison_reports")
