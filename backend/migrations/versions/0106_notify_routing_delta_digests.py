"""ownership-routed notifications + delta digests

Revision ID: 0106
Revises: 0105
Create Date: 2026-07-10

PMF backlog US-7.3 / US-7.4.

US-7.3 — ownership-routed transition notifications:

* New table ``team_notification_channels`` — one row per (project,
  team_name) mapping an ownership team (the free-text ``team_name`` used
  by ``service_ownership_rules``) to a notification target
  (``channel_type`` = email | slack | teams, ``target`` = webhook URL or
  email address). A separate table (rather than columns on the rule row)
  because many rules share one team — per-rule channel columns would
  duplicate and drift.
* ``notification_logs`` gains ``routed_team`` + ``routing_fallback`` so
  every transition delivery records where it was routed and, when it fell
  back to the project default channels, why (unowned | no_team_channel |
  mixed_ownership | routing_error | delivery_failed).

US-7.4 — delta digests:

* ``digest_subscriptions`` gains ``send_when_unchanged`` (default TRUE =
  current behaviour: a zero-change window still sends a one-line digest;
  FALSE = skip delivery entirely). The delta-window watermark reuses the
  existing ``last_delivered_at`` column — no new column needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── US-7.3: team → notification channel mapping ──────────────────────────
    op.create_table(
        "team_notification_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(20), nullable=False),
        sa.Column("target", sa.String(2000), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "project_id", "team_name",
            name="uq_team_notif_channel_project_team",
        ),
    )
    op.create_index(
        "ix_team_notif_channels_project",
        "team_notification_channels",
        ["project_id"],
    )

    # ── US-7.3: routing decision audit on the delivery log ───────────────────
    op.add_column(
        "notification_logs",
        sa.Column("routed_team", sa.String(255), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("routing_fallback", sa.String(50), nullable=True),
    )

    # ── US-7.4: zero-change digest behaviour ─────────────────────────────────
    op.add_column(
        "digest_subscriptions",
        sa.Column(
            "send_when_unchanged", sa.Boolean(), nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("digest_subscriptions", "send_when_unchanged")
    op.drop_column("notification_logs", "routing_fallback")
    op.drop_column("notification_logs", "routed_team")
    op.drop_index(
        "ix_team_notif_channels_project", table_name="team_notification_channels"
    )
    op.drop_table("team_notification_channels")
