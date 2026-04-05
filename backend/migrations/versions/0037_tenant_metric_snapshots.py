"""Add tenant_metric_snapshots table for project-scoped observability (OPS-04).

Revision ID: 0037
Revises: 0036
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_metric_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("total_runs", sa.Integer(), server_default="0"),
        sa.Column("total_tests", sa.Integer(), server_default="0"),
        sa.Column("avg_pass_rate", sa.Float(), nullable=True),
        sa.Column("failed_runs", sa.Integer(), server_default="0"),
        sa.Column("ai_analyses_count", sa.Integer(), server_default="0"),
        sa.Column("release_decisions_count", sa.Integer(), server_default="0"),
        sa.Column("audit_events_count", sa.Integer(), server_default="0"),
    )
    op.create_index("ix_tms_project_time", "tenant_metric_snapshots", ["project_id", "recorded_at"])


def downgrade() -> None:
    op.drop_table("tenant_metric_snapshots")
