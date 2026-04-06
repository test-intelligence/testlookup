"""EM-2: Extend digest_subscriptions for event-driven report delivery.

Adds scope_type, scope_value, trigger_filter columns and a composite index
for efficient event-driven subscription lookup. Widens the schedule column
from String(10) to String(20) to accommodate PER_RUN, PER_RELEASE, PER_SUITE.

Revision ID: 0047
Revises: 0046
"""
from alembic import op
import sqlalchemy as sa

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen schedule column from 10 to 20 chars for new enum values
    op.alter_column(
        "digest_subscriptions",
        "schedule",
        type_=sa.String(20),
        existing_type=sa.String(10),
        existing_nullable=False,
    )

    # Add new scope and trigger columns
    op.add_column("digest_subscriptions", sa.Column("scope_type", sa.String(20), nullable=True, server_default="project"))
    op.add_column("digest_subscriptions", sa.Column("scope_value", sa.String(255), nullable=True))
    op.add_column("digest_subscriptions", sa.Column("trigger_filter", sa.String(20), nullable=True, server_default="all"))

    # Composite index for event-driven subscription dispatch
    op.create_index("ix_ds_schedule_active", "digest_subscriptions", ["schedule", "is_active", "is_paused"])


def downgrade() -> None:
    op.drop_index("ix_ds_schedule_active", table_name="digest_subscriptions")
    op.drop_column("digest_subscriptions", "trigger_filter")
    op.drop_column("digest_subscriptions", "scope_value")
    op.drop_column("digest_subscriptions", "scope_type")
    op.alter_column(
        "digest_subscriptions",
        "schedule",
        type_=sa.String(10),
        existing_type=sa.String(20),
        existing_nullable=False,
    )
