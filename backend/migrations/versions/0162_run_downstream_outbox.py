"""Add durable post-ingestion downstream dispatch intents.

Revision ID: 0162
Revises: 0161
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0162"
down_revision = "0161"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_downstream_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=60), nullable=False),
        sa.Column("input_version", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("queue", sa.String(length=80), nullable=False, server_default="default"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dispatch_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("processing_task_id", sa.String(length=80), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_detail", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('waiting', 'pending', 'sending', 'published', "
            "'processing', 'completed', 'failed')",
            name="ck_run_downstream_outbox_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_run_downstream_outbox_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "dispatch_failures >= 0",
            name="ck_run_downstream_outbox_dispatch_failures_nonnegative",
        ),
        sa.CheckConstraint(
            "execution_attempts >= 0",
            name="ck_run_downstream_outbox_execution_attempts_nonnegative",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "operation",
            "input_version",
            name="uq_run_downstream_operation_version",
        ),
    )
    op.create_index(
        "ix_run_downstream_outbox_due_project",
        "run_downstream_outbox",
        ["status", "next_attempt_at", "project_id", "created_at"],
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("dispatch_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("delivery_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Preserve the association for existing run.completed rows when the run
    # still exists. Invalid/deleted historical payloads deliberately remain
    # NULL so the new foreign key can be installed safely.
    op.execute(
        """
        UPDATE webhook_deliveries AS delivery
        SET run_id = run.id
        FROM test_runs AS run
        WHERE delivery.event_type = 'run.completed'
          AND delivery.event_payload ? 'run_id'
          AND delivery.event_payload ->> 'run_id' = run.id::text
        """
    )
    op.create_foreign_key(
        "fk_webhook_deliveries_run_id",
        "webhook_deliveries",
        "test_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_webhook_delivery_run_id",
        "webhook_deliveries",
        ["run_id"],
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("dispatch_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("dispatch_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_delivery_dispatch_due",
        "webhook_deliveries",
        ["status", "next_dispatch_at"],
    )
    op.create_index(
        "uq_webhook_delivery_delivery_key",
        "webhook_deliveries",
        ["delivery_key"],
        unique=True,
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivery_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("preference_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_logs_preference_id",
        "notification_logs",
        "notification_preferences",
        ["preference_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivery_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivery_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivery_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("next_delivery_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_notification_log_delivery_key",
        "notification_logs",
        ["delivery_key"],
        unique=True,
    )
    op.create_index(
        "ix_notification_log_delivery_due",
        "notification_logs",
        ["status", "next_delivery_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_log_delivery_due",
        table_name="notification_logs",
    )
    op.drop_index(
        "uq_notification_log_delivery_key",
        table_name="notification_logs",
    )
    op.drop_column("notification_logs", "updated_at")
    op.drop_column("notification_logs", "next_delivery_at")
    op.drop_column("notification_logs", "delivery_lease_expires_at")
    op.drop_column("notification_logs", "delivery_started_at")
    op.drop_column("notification_logs", "delivery_token")
    op.drop_column("notification_logs", "delivery_attempts")
    op.drop_column("notification_logs", "delivery_metadata")
    op.drop_constraint(
        "fk_notification_logs_preference_id",
        "notification_logs",
        type_="foreignkey",
    )
    op.drop_column("notification_logs", "preference_id")
    op.drop_column("notification_logs", "delivery_key")
    op.drop_index(
        "uq_webhook_delivery_delivery_key",
        table_name="webhook_deliveries",
    )
    op.drop_index(
        "ix_webhook_delivery_run_id",
        table_name="webhook_deliveries",
    )
    op.drop_constraint(
        "fk_webhook_deliveries_run_id",
        "webhook_deliveries",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_webhook_delivery_dispatch_due",
        table_name="webhook_deliveries",
    )
    op.drop_column("webhook_deliveries", "dispatch_token")
    op.drop_column("webhook_deliveries", "dispatch_lease_expires_at")
    op.drop_column("webhook_deliveries", "next_dispatch_at")
    op.drop_column("webhook_deliveries", "dispatch_attempts")
    op.drop_column("webhook_deliveries", "delivery_key")
    op.drop_column("webhook_deliveries", "run_id")
    op.drop_column("webhook_deliveries", "dispatch_failures")
    op.drop_index(
        "ix_run_downstream_outbox_due_project",
        table_name="run_downstream_outbox",
    )
    op.drop_table("run_downstream_outbox")
