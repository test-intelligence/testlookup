"""webhook_subscriptions + webhook_deliveries + outbound_webhooks feature flag

Revision ID: 0068
Revises: 0067
Create Date: 2026-04-14

Tier 2 item 6 — customer-managed outbound webhooks. Two new tables plus
the ``outbound_webhooks`` feature flag (disabled by default).

``webhook_subscriptions`` holds per-project customer config: target URL,
event list, retry budget, and bookkeeping counters. Secrets are stored
in ``secret_service`` under scope ``webhook_subscription`` so a DB dump
cannot recover them — only the boolean ``has_secret`` is on the row.

``webhook_deliveries`` is the audit log, one row per (subscription,
event emission). Retries update the same row so we keep a single
authoritative outcome per emission rather than a chain of attempt rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_url", sa.String(length=1000), nullable=False),
        sa.Column(
            "events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("has_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_webhook_sub_project", "webhook_subscriptions", ["project_id"])
    op.create_index(
        "ix_webhook_sub_enabled",
        "webhook_subscriptions",
        ["enabled", "project_id"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "event_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_preview", sa.String(length=2000), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_webhook_delivery_sub_created",
        "webhook_deliveries",
        ["subscription_id", "created_at"],
    )
    op.create_index(
        "ix_webhook_delivery_status",
        "webhook_deliveries",
        ["status", "created_at"],
    )

    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'outbound_webhooks', "
            "'Customer-managed outbound webhook subscriptions (run.completed, "
            "defect.promoted, release.decided, flaky.quarantined, quota.exceeded). "
            "Tier 2 item 6.', false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM feature_flags WHERE key = 'outbound_webhooks'")
    )
    op.drop_index("ix_webhook_delivery_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_delivery_sub_created", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_sub_enabled", table_name="webhook_subscriptions")
    op.drop_index("ix_webhook_sub_project", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
