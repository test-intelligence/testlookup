"""Promote ``notification_preferences.events`` from JSON to JSONB.

Revision ID: 0083
Revises: 0082
Create Date: 2026-05-16

Database audit 2026-05-16 (docs/DATABASE_AUDIT_2026-05-16.md, finding P1-3)
flagged this column as inconsistent with its sibling ``webhook_subscriptions.events``
(line 2847 in models/postgres.py) which already uses JSONB. Both columns
hold the same logical data — a list of event-type strings the consumer
subscribed to — and both will be queried by "list subscribers for event X"
in the future. JSONB supports GIN indexing and the ``@>`` containment
operator; JSON does not.

This migration is a pure type-change with no data shape difference, so the
``USING events::jsonb`` cast preserves every existing row intact (it's a
list serialised as JSON → still a valid JSONB list).

The downgrade casts back to JSON via ``USING events::json`` for symmetry.
There's no data loss in either direction.
"""
from alembic import op


revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notification_preferences "
        "ALTER COLUMN events TYPE JSONB USING events::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE notification_preferences "
        "ALTER COLUMN events TYPE JSON USING events::json"
    )
