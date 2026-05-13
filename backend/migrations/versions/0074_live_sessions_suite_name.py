"""Add suite_name column to live_sessions.

Revision ID: 0074
Revises: 0073
Create Date: 2026-05-10

Lets the SDK supply a run-level suite identifier (testlookup.suite, with
testlookup.launch as fallback) on session create. ``stream_service`` stamps
the resulting TestRun.primary_suite_name from it immediately so every page
linking to the run shows a single user-configured suite label.

Nullable for backwards compatibility — existing rows from before this
migration simply keep suite_name=NULL.

(Originally numbered 0074 against 0072; re-parented onto 0073 so the
``run_comparison_reports`` migration that landed in parallel doesn't leave
Alembic with two head revisions on startup.)
"""
from alembic import op
import sqlalchemy as sa


revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_sessions",
        sa.Column("suite_name", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("live_sessions", "suite_name")
