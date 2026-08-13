"""Add the generic agent action proposal/execution ledger.

Revision ID: 0125
Revises: 0124
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_action_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("target_type", sa.String(length=60), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rollback_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'pending_review', 'approved', 'executing', 'executed', 'failed', 'rejected', 'rolled_back')",
            name="ck_agent_action_status",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_action_request_hash",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_run_id"], ["test_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["agent_pipeline_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_action_project_created",
        "agent_action_ledger",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_agent_action_status",
        "agent_action_ledger",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_agent_action_target",
        "agent_action_ledger",
        ["project_id", "target_type", "target_id"],
    )
    op.create_unique_constraint(
        "uq_agent_action_project_idempotency",
        "agent_action_ledger",
        ["project_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_agent_action_project_idempotency",
        "agent_action_ledger",
        type_="unique",
    )
    op.drop_index("ix_agent_action_target", table_name="agent_action_ledger")
    op.drop_index("ix_agent_action_status", table_name="agent_action_ledger")
    op.drop_index("ix_agent_action_project_created", table_name="agent_action_ledger")
    op.drop_table("agent_action_ledger")
