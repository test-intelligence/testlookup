"""Add a project-scoped queue for immutable pipeline event delivery.

Revision ID: 0263
Revises: 0262
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0263"
down_revision = "0262"
branch_labels = None
depends_on = None

TABLE = "pipeline_event_outbox"
INDEXES = {
    "schedule": ("status", "next_attempt_at", "created_at"),
    "lease": ("status", "lease_expires_at"),
    "delivered": ("status", "delivered_at"),
    "project": ("project_id", "status"),
    "pipeline": ("pipeline_id",),
    "timeline": ("pipeline_run_id", "created_at"),
    "run": ("test_run_id",),
    "test_case": ("test_case_id",),
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pipeline_id", UUID(as_uuid=True), sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE")),
        sa.Column("test_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE")),
        sa.Column("test_case_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE")),
        sa.Column("pipeline_run_id", sa.String(128), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(48)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending', 'sending', 'delivered', 'failed')", name="ck_pipeline_event_outbox_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_pipeline_event_outbox_attempts"),
    )
    for suffix, columns in INDEXES.items():
        op.create_index(f"ix_{TABLE}_{suffix}", TABLE, columns)


def downgrade() -> None:
    op.execute("""
        DO $pipeline_event_outbox$
        BEGIN
            IF EXISTS (SELECT 1 FROM pipeline_event_outbox WHERE status <> 'delivered') THEN
                RAISE EXCEPTION 'Drain unresolved pipeline event intents before removing the outbox';
            END IF;
        END;
        $pipeline_event_outbox$;
    """)
    op.drop_table(TABLE)
