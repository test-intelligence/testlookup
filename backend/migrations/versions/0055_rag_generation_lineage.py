"""add generation_batches, generation_case_sources, requirement_coverage tables
and RAG columns on managed_test_cases

Revision ID: 0055
Revises: 0054
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0055"
down_revision = "0054"


def upgrade() -> None:
    # ── generation_batches ────────────────────────────────────────────────────
    op.create_table(
        "generation_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt_text", sa.Text, nullable=True),
        sa.Column("source_ids", sa.JSON, nullable=True),
        sa.Column("generation_mode", sa.String(20), nullable=False, server_default="raw"),
        sa.Column("generation_config", sa.JSON, nullable=True),
        sa.Column("cases_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cases_accepted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cases_rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("coverage_score", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("prompt_redacted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("llm_model_used", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_gb_project_created", "generation_batches", ["project_id", "created_at"])
    op.create_index("ix_gb_author", "generation_batches", ["created_by_id"])

    # ── generation_case_sources ───────────────────────────────────────────────
    op.create_table(
        "generation_case_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", UUID(as_uuid=True),
                  sa.ForeignKey("generation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", UUID(as_uuid=True),
                  sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True),
                  sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_vector_id", sa.String(64), nullable=False),
        sa.Column("relevance_score", sa.Float, nullable=True),
        sa.Column("section_heading", sa.String(500), nullable=True),
        sa.Column("chunk_text_preview", sa.String(500), nullable=True),
        sa.Column("source_content_hash_at_generation", sa.String(64), nullable=True),
        sa.Column("is_stale", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("stale_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_gcs_case_chunk", "generation_case_sources", ["case_id", "chunk_vector_id"])
    op.create_index("ix_gcs_case_id", "generation_case_sources", ["case_id"])
    op.create_index("ix_gcs_batch_id", "generation_case_sources", ["batch_id"])
    op.create_index("ix_gcs_source_id", "generation_case_sources", ["source_id"])
    op.create_index("ix_gcs_stale", "generation_case_sources", ["is_stale"])

    # ── requirement_coverage ──────────────────────────────────────────────────
    op.create_table(
        "requirement_coverage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", UUID(as_uuid=True),
                  sa.ForeignKey("generation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.String(200), nullable=False),
        sa.Column("requirement_text", sa.Text, nullable=True),
        sa.Column("coverage_status", sa.String(20), nullable=False, server_default="uncovered"),
        sa.Column("covered_by_case_ids", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_rc_batch_req", "requirement_coverage", ["batch_id", "requirement_id"])
    op.create_index("ix_rc_batch_id", "requirement_coverage", ["batch_id"])
    op.create_index("ix_rc_project_req", "requirement_coverage", ["project_id", "requirement_id"])

    # ── Add RAG columns to managed_test_cases ─────────────────────────────────
    op.add_column("managed_test_cases", sa.Column(
        "generation_batch_id", UUID(as_uuid=True),
        sa.ForeignKey("generation_batches.id", ondelete="SET NULL"), nullable=True,
    ))
    op.add_column("managed_test_cases", sa.Column(
        "is_stale", sa.Boolean, nullable=False, server_default="false",
    ))
    op.add_column("managed_test_cases", sa.Column(
        "stale_reason", sa.Text, nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("managed_test_cases", "stale_reason")
    op.drop_column("managed_test_cases", "is_stale")
    op.drop_column("managed_test_cases", "generation_batch_id")
    op.drop_table("requirement_coverage")
    op.drop_table("generation_case_sources")
    op.drop_table("generation_batches")
