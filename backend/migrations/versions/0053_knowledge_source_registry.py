"""add knowledge_sources table

Revision ID: 0053
Revises: 0052
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0053"
down_revision = "0052"


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("canonical_url", sa.String(2000), nullable=False),
        sa.Column("external_id", sa.String(500), nullable=True),
        sa.Column("owner_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("classification", sa.String(20), nullable=False, server_default="internal"),
        sa.Column("is_archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_ks_project_url", "knowledge_sources", ["project_id", "canonical_url"])
    op.create_index("ix_ks_project_type", "knowledge_sources", ["project_id", "source_type"])
    op.create_index("ix_ks_project_status", "knowledge_sources", ["project_id", "sync_status"])
    op.create_index("ix_ks_owner", "knowledge_sources", ["owner_id"])


def downgrade() -> None:
    op.drop_table("knowledge_sources")
