"""Persist cancellation before an invocation pipeline exists.

Revision ID: 0191
Revises: 0190
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0191"
down_revision = "0190"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_invocations",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("agent_invocations", "cancel_requested")
