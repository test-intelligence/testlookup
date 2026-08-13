"""Add durable delivery intent for approved agent actions.

Revision ID: 0126
Revises: 0125
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_action_dispatch_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name="ck_agent_action_outbox_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_agent_action_outbox_attempts_nonnegative",
        ),
        sa.ForeignKeyConstraint(["action_id"], ["agent_action_ledger.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", name="uq_agent_action_outbox_action"),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_action_outbox_idempotency"),
    )
    op.create_index(
        "ix_agent_action_outbox_status_next",
        "agent_action_dispatch_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_action_outbox_status_next", table_name="agent_action_dispatch_outbox")
    op.drop_table("agent_action_dispatch_outbox")
