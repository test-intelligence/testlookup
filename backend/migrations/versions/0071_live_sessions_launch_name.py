"""Add launch_name to live_sessions for ReportPortal-style integration parity.

Revision ID: 0071
Revises: 0070
Create Date: 2026-05-09

The new column carries the human-readable launch label (analogous to
``rp.launch`` in ReportPortal). It is read from the standardized
``testlookup.launch`` properties / system-property / env-var key and
displayed in Live Execution and Runs columns.

Nullable for backwards compatibility — existing rows from before this
migration simply keep launch_name=NULL and continue to render with
build_number / client_name as before.
"""
from alembic import op
import sqlalchemy as sa


revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_sessions",
        sa.Column("launch_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("live_sessions", "launch_name")
