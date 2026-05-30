"""add knowledge_chunks and knowledge_sync_events tables

Revision ID: 0054
Revises: 0053
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0054"
down_revision = "0053"


def upgrade() -> None:
    op.create_table(
        "knowledge_sync_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True),
                  sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("previous_hash", sa.String(64), nullable=True),
        sa.Column("content_changed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("chunk_count", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kse_source_created", "knowledge_sync_events", ["source_id", "created_at"])
    op.create_index("ix_kse_project_created", "knowledge_sync_events", ["project_id", "created_at"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True),
                  sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vector_id", sa.String(64), nullable=False, unique=True),
        sa.Column("section_heading", sa.String(500), nullable=True),
        sa.Column("requirement_id", sa.String(200), nullable=True),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text_preview", sa.String(500), nullable=True),
        sa.Column("token_count", sa.Integer, nullable=True),
        sa.Column("sync_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kc_source_active", "knowledge_chunks", ["source_id", "is_active"])
    op.create_index("ix_kc_project_active", "knowledge_chunks", ["project_id", "is_active"])
    op.create_index("ix_kc_sync_version", "knowledge_chunks", ["source_id", "sync_version"])


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_sync_events")
