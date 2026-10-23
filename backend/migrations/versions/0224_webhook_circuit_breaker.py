"""Outbound webhook circuit breaker; ``outbound_webhooks`` ON for a new install (W10 INT-07).

Four columns on ``webhook_subscriptions``, no backfill, no index, no CHECK:

* ``consecutive_failures INTEGER NOT NULL DEFAULT 0``: deliveries in a row that
  ended FAILED or DLQ with this subscription's bookkeeping (a retried attempt
  counts once, when its retries are spent); a SUCCESS resets it. Distinct from
  ``failure_count``, which counts attempts and is never reset.
* ``breaker_threshold INTEGER NOT NULL DEFAULT 10``: the subscription is paused
  when the counter reaches it (1-100, validated in the API).
* ``disabled_at TIMESTAMPTZ NULL`` and ``disabled_reason VARCHAR(40) NULL``:
  when and why it was disabled -- ``circuit_open`` (the breaker) or ``manual``
  (a person). An existing disabled subscription reads ``NULL``, shown as plain
  "Disabled". The vocabulary is judged in the service, so there is no CHECK.

Migration 0068 seeded ``outbound_webhooks`` OFF. A new install now starts with
it ON, by 0201's rule: the flag is flipped only on a database that has never
been used -- no user and no project yet, exactly the state ``alembic upgrade
head`` runs in on a new install -- and only while the row still holds 0068's
OFF. An existing install keeps its stored value; an operator turns it on with
``PATCH /api/v1/feature-flags/outbound_webhooks``.

The downgrade is the mirror flag statement (only an untouched database goes
back to OFF), then the four columns are dropped in reverse order.

Revision ID: 0224
Revises: 0223
"""
from alembic import op
import sqlalchemy as sa

revision = "0224"
down_revision = "0223"
branch_labels = None
depends_on = None

#: Named ``_FLAGS`` so the feature-flag catalogue guard reads the key.
_FLAGS = ("outbound_webhooks",)

TABLE = "webhook_subscriptions"

# The fresh-install condition, shared by upgrade and downgrade so the two can
# never disagree about which databases they may touch (pattern 0201 / 0212).
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

# The key written literally so the catalogue guard's ``WHERE key = '<key>'``
# scan sees it.
UPGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
    "WHERE key = 'outbound_webhooks' AND enabled_global = false AND "
    + FRESH_INSTALL
)

DOWNGRADE_SQL = (
    "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
    "WHERE key = 'outbound_webhooks' AND enabled_global = true AND "
    + FRESH_INSTALL
)


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        TABLE,
        sa.Column("breaker_threshold", sa.Integer(), nullable=False, server_default=sa.text("10")),
    )
    op.add_column(TABLE, sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("disabled_reason", sa.String(40), nullable=True))
    op.execute(sa.text(UPGRADE_SQL))


def downgrade() -> None:
    # Only an untouched database goes back to 0068's OFF; a used one keeps the
    # value its admins chose, exactly as upgrade() left it alone.
    op.execute(sa.text(DOWNGRADE_SQL))
    op.drop_column(TABLE, "disabled_reason")
    op.drop_column(TABLE, "disabled_at")
    op.drop_column(TABLE, "breaker_threshold")
    op.drop_column(TABLE, "consecutive_failures")
