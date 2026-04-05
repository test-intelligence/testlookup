"""Add tenant_onboarding_status and product_usage_events tables.

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Onboarding Progress ──────────────────────────────────────────────────
    op.create_table(
        "tenant_onboarding_status",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_key", sa.String(50), nullable=False),    # create_project | upload_run | connect_jira | connect_telemetry | view_intelligence
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),  # pending | completed | skipped
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tos_project_step", "tenant_onboarding_status", ["project_id", "step_key"], unique=True)

    # ── Product Usage Events ─────────────────────────────────────────────────
    op.create_table(
        "product_usage_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_name", sa.String(100), nullable=False),   # first_run_viewed | first_intelligence_opened | first_defect_promoted | first_release_decision | first_integration_connected
        sa.Column("event_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pue_user", "product_usage_events", ["user_id"])
    op.create_index("ix_pue_event", "product_usage_events", ["event_name"])
    op.create_index("ix_pue_created", "product_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("product_usage_events")
    op.drop_table("tenant_onboarding_status")
