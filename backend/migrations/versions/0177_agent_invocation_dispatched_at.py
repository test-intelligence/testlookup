"""agent_invocations.dispatched_at: when the invocation was last handed to a worker (E1.2).

An invocation whose pipeline run has not appeared some minutes after dispatch
reads ``failed`` (a lost dispatch), and a retry sends it to a worker again. That
second dispatch needs its own clock: measuring it from ``created_at`` would read
the re-sent invocation as lost immediately, and moving ``created_at`` would
falsify when the request was made.

A nullable column with no index on an existing table: a metadata-only change,
nothing to build concurrently. Existing rows fall back to ``created_at``.

Revision ID: 0177
Revises: 0176
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0177"
down_revision = "0176"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_invocations", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_invocations", "dispatched_at")
