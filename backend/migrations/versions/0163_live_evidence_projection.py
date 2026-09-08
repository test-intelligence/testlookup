"""Add durable live-ingestion attempts, receipts, and projection checkpoints.

Revision ID: 0163
Revises: 0162
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0163"
down_revision = "0162"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_ingestion_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("batch_id", sa.String(length=255), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("first_event_id", sa.String(length=64), nullable=True),
        sa.Column("last_event_id", sa.String(length=64), nullable=True),
        sa.Column("first_stream_id", sa.String(length=64), nullable=True),
        sa.Column("last_stream_id", sa.String(length=64), nullable=True),
        sa.Column("projected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("event_count >= 0", name="ck_live_ingestion_attempt_count"),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "batch_id", name="uq_live_ingestion_attempt_session_batch"
        ),
    )
    op.create_index(
        "ix_live_ingestion_attempt_run_created",
        "live_ingestion_attempts",
        ["run_id", "created_at"],
    )

    op.create_table(
        "live_event_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=64), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("projected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("event_index >= 0", name="ck_live_event_receipt_index"),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["live_ingestion_attempts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_live_event_receipt_event"),
    )
    op.create_index(
        "ix_live_event_receipt_run_stream",
        "live_event_receipts",
        ["run_id", "stream_id"],
    )

    op.create_table(
        "live_projection_checkpoints",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_key", sa.String(length=500), nullable=False),
        sa.Column("consumer_group", sa.String(length=100), nullable=False),
        sa.Column("last_stream_id", sa.String(length=64), nullable=False),
        sa.Column("last_event_id", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("live_projection_checkpoints")
    op.drop_index("ix_live_event_receipt_run_stream", table_name="live_event_receipts")
    op.drop_table("live_event_receipts")
    op.drop_index(
        "ix_live_ingestion_attempt_run_created", table_name="live_ingestion_attempts"
    )
    op.drop_table("live_ingestion_attempts")
