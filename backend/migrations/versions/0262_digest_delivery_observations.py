"""Preserve scheduled digest due work independently of its dispatch claim.

Five nullable observation fields; no delivery is replayed or marked successful
by this migration. Historical claimed-but-unfinished work cannot be inferred
from a future next_delivery_at, so there is deliberately no invented backfill.

Revision ID: 0262
Revises: 0261
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0262"
down_revision = "0261"
branch_labels = None
depends_on = None

TABLE = "digest_subscriptions"
COLUMNS = (
    ("pending_delivery_since_at", sa.DateTime(timezone=True)),
    ("delivery_observation_token", UUID(as_uuid=True)),
    ("last_delivery_attempt_at", sa.DateTime(timezone=True)),
    ("last_content_generated_at", sa.DateTime(timezone=True)),
    ("last_delivery_outcome", sa.String(20)),
)


def upgrade() -> None:
    for name, column_type in COLUMNS:
        op.add_column(TABLE, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column(TABLE, name)
