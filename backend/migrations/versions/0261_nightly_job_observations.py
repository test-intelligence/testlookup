"""Persist the latest observed attempt and success of two nightly jobs.

The seeded monitoring boundary prevents pre-upgrade history from being
reported as missed work. Older worker logs and counters are not backfilled.

Revision ID: 0261
Revises: 0260
"""

from alembic import op
import sqlalchemy as sa

revision = "0261"
down_revision = "0260"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "nightly_job_observations",
        sa.Column("schedule_key", sa.String(64), primary_key=True),
        sa.Column("monitoring_since", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("enabled_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_slot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_slot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.bulk_insert(table, [{"schedule_key": "quarantine_maintenance"}, {"schedule_key": "perf_baseline_refresh"}])


def downgrade() -> None:
    op.drop_table("nightly_job_observations")
