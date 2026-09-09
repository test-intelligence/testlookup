"""Add durable, fenced semantic full-reindex checkpoints.

Revision ID: 0164
Revises: 0163
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0164"
down_revision = "0163"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "semantic_reindex_jobs",
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("high_water_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("high_water_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cursor_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("processed_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_owner", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence_token", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'finalizing', 'succeeded')",
            name="ck_semantic_reindex_job_status",
        ),
        sa.CheckConstraint("state_version = 1", name="ck_semantic_reindex_job_state_version"),
        sa.CheckConstraint("processed_count >= 0", name="ck_semantic_reindex_job_processed_count"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("scope_key"),
        sa.UniqueConstraint("job_id", name="uq_semantic_reindex_jobs_job_id"),
    )
    op.create_index(
        "ix_semantic_reindex_jobs_status_lease",
        "semantic_reindex_jobs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_test_cases_semantic_reindex_keyset",
        "test_cases",
        ["created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_cases_semantic_reindex_keyset", table_name="test_cases")
    op.drop_index(
        "ix_semantic_reindex_jobs_status_lease",
        table_name="semantic_reindex_jobs",
    )
    op.drop_table("semantic_reindex_jobs")
