"""Add saved_views and digest_subscriptions tables (ENT-05).

Revision ID: 0035
Revises: 0034
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Saved Views ──────────────────────────────────────────────
    op.create_table(
        "saved_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("is_shared", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sv_user", "saved_views", ["user_id"])
    op.create_index("ix_sv_project", "saved_views", ["project_id"])

    # ── Digest Subscriptions ─────────────────────────────────────
    op.create_table(
        "digest_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("saved_view_id", UUID(as_uuid=True), sa.ForeignKey("saved_views.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schedule", sa.String(10), nullable=False, server_default="WEEKLY"),
        sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ds_user", "digest_subscriptions", ["user_id"])
    op.create_index("ix_ds_next", "digest_subscriptions", ["next_delivery_at"])


def downgrade() -> None:
    op.drop_table("digest_subscriptions")
    op.drop_table("saved_views")
