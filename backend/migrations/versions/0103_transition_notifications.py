"""transition-based notifications — state store + per-project policy

Revision ID: 0103
Revises: 0102
Create Date: 2026-07-09

PMF backlog US-7.1 / US-7.2: transition-only notifications (the
alert-fatigue fix). Two tables:

* ``notification_test_states`` — one row per (project, test_fingerprint)
  tracking the confirmed pass/fail state, the consecutive-failure count,
  known-flaky membership, and the last run that advanced the row
  (idempotency anchor for re-finalization).

* ``notification_transition_policies`` — one row per project controlling
  which transition events fire, the consecutive-failure threshold, and
  whether the legacy per-run fan-out (run_failed / run_passed /
  high_failure_rate) still sends.

Backfill: every project existing at upgrade time gets an explicit policy
row with ``transitions_enabled=false, per_run_events_enabled=true`` so
EXISTING projects keep exactly their current notification behaviour.
A missing row (any project created after this migration) resolves to the
new-project default in code: transitions ON, per-run spam OFF.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_test_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="passing"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_notified_state", sa.String(20), nullable=True),
        sa.Column("is_known_flaky", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "last_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "project_id", "test_fingerprint",
            name="uq_notif_test_state_project_fp",
        ),
    )
    op.create_index(
        "ix_notif_test_states_project", "notification_test_states", ["project_id"]
    )

    op.create_table(
        "notification_transition_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("transitions_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("per_run_events_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled_events", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("consecutive_failure_threshold", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Backfill: existing projects keep their current behaviour — per-run
    # fan-out stays ON, transition engine stays OFF until someone opts in.
    # enabled_events is pre-populated with all five transition events so
    # opting in later is a single transitions_enabled flip.
    op.execute(
        """
        INSERT INTO notification_transition_policies
            (id, project_id, transitions_enabled, per_run_events_enabled,
             enabled_events, consecutive_failure_threshold, created_at, updated_at)
        SELECT gen_random_uuid(), p.id, false, true,
               '["test.newly_failing","test.recovered","test.newly_flaky","test.quarantined","test.unquarantined"]'::jsonb,
               2, now(), now()
        FROM projects p
        """
    )


def downgrade() -> None:
    op.drop_table("notification_transition_policies")
    op.drop_index("ix_notif_test_states_project", table_name="notification_test_states")
    op.drop_table("notification_test_states")
