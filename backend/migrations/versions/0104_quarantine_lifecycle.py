"""quarantine lifecycle — owner + SLA + auto-promotion

Revision ID: 0104
Revises: 0103
Create Date: 2026-07-09

PMF backlog US-5.4 / US-5.5 (+US-5.6 thresholds): quarantine becomes a
workflow, not a graveyard.

``flaky_quarantine_requests`` gains:

* ``owner_user_id``          — resolved from the ownership rules for the
                               test's suite at activation, falling back to
                               the approving QA lead (SET NULL on user delete).
* ``defect_id``              — optional auto-created INTERNAL defect record
                               (Jira posting is Epic 6, not this migration).
* ``sla_days`` / ``stale_at`` — SLA snapshot at activation + the precomputed
                               staleness deadline; ``stale_notified_at`` is
                               the once-per-entry anchor for the
                               ``test.quarantine_stale`` notification.
* ``consecutive_passes`` / ``last_stability_run_id`` — pass-streak counter
                               advanced at run finalization (idempotent per
                               run via the stamp); a single failure resets it.
* ``ready_to_promote`` / ``ready_notified_at`` — threshold reached with
                               auto_promote OFF: surfaced for one-click
                               release + the once-per-crossing
                               ``test.ready_to_unquarantine`` notification.

New table ``quarantine_lifecycle_policies`` — one row per project (unique);
a missing row resolves to code defaults (SLA 14d, auto_create_defect off,
auto_promote off, promote after 20 passes, detection floor 20% flip rate
over 10 runs), so no backfill is needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── US-5.4: owner + ticket + SLA on the request row ─────────────────────
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column(
            "owner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column(
            "defect_id",
            UUID(as_uuid=True),
            sa.ForeignKey("defects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column("sla_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column("stale_notified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── US-5.5: auto-promotion stability tracking ────────────────────────────
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column(
            "consecutive_passes", sa.Integer(), nullable=False, server_default="0",
        ),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column(
            "last_stability_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column(
            "ready_to_promote", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
    )
    op.add_column(
        "flaky_quarantine_requests",
        sa.Column("ready_notified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Per-project lifecycle policy ─────────────────────────────────────────
    op.create_table(
        "quarantine_lifecycle_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("sla_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column(
            "auto_create_defect", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "auto_promote", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "promote_after_passes", sa.Integer(), nullable=False, server_default="20",
        ),
        sa.Column(
            "detection_flip_rate_threshold",
            sa.Float(),
            nullable=False,
            server_default="0.2",
        ),
        sa.Column(
            "detection_min_runs", sa.Integer(), nullable=False, server_default="10",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("quarantine_lifecycle_policies")
    op.drop_column("flaky_quarantine_requests", "ready_notified_at")
    op.drop_column("flaky_quarantine_requests", "ready_to_promote")
    op.drop_column("flaky_quarantine_requests", "last_stability_run_id")
    op.drop_column("flaky_quarantine_requests", "consecutive_passes")
    op.drop_column("flaky_quarantine_requests", "stale_notified_at")
    op.drop_column("flaky_quarantine_requests", "stale_at")
    op.drop_column("flaky_quarantine_requests", "sla_days")
    op.drop_column("flaky_quarantine_requests", "defect_id")
    op.drop_column("flaky_quarantine_requests", "owner_user_id")
