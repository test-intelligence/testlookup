"""Add service_ownership_rules table for component/team routing (ENT-04).

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_ownership_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("match_type", sa.String(30), nullable=False),
        sa.Column("match_pattern", sa.String(500), nullable=False),
        sa.Column("service_name", sa.String(255), nullable=False),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("team_contact", sa.String(500), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sor_project", "service_ownership_rules", ["project_id"])
    op.create_index("ix_sor_active", "service_ownership_rules", ["project_id", "is_active"])


def downgrade() -> None:
    op.drop_table("service_ownership_rules")
