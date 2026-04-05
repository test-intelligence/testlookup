"""Add integration_probe_results history table and extend integration_health_checks (OPS-01).

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Probe history table ──────────────────────────────────────
    op.create_table(
        "integration_probe_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("auth_valid", sa.Boolean(), nullable=True),
        sa.Column("payload_valid", sa.Boolean(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ipr_provider_time", "integration_probe_results", ["provider", "checked_at"])

    # ── Extend existing health check table ───────────────────────
    op.add_column("integration_health_checks",
        sa.Column("consecutive_failures", sa.Integer(), server_default="0"))
    op.add_column("integration_health_checks",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("integration_health_checks", "last_success_at")
    op.drop_column("integration_health_checks", "consecutive_failures")
    op.drop_table("integration_probe_results")
