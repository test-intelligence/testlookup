"""flaky_quarantine_requests + flaky_auto_quarantine feature flag

Revision ID: 0065
Revises: 0064
Create Date: 2026-04-14

Tier 1 item 3 — Flaky-test auto-quarantine with QA Lead approval.

Adds one table (``flaky_quarantine_requests``) plus a partial UNIQUE index
that enforces at most one LIVE (non-terminal) quarantine record per
``(project_id, test_fingerprint)``. Terminal-state rows (RELEASED, REJECTED,
EXPIRED) are retained as history — a fresh quarantine cycle creates a new
row rather than mutating the old one, so the decision trail for any test is
immutable and append-only.

Also seeds the ``flaky_auto_quarantine`` feature flag (disabled by default)
so the whole subsystem is observable-zero until an admin turns it on.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flaky_quarantine_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=500), nullable=True),
        sa.Column("suite_name", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="PROPOSED",
        ),
        sa.Column(
            "detection_method",
            sa.String(length=50),
            nullable=False,
            server_default="pass_fail_ratio",
        ),
        sa.Column("flip_rate", sa.Float(), nullable=True),
        sa.Column("flip_window_size", sa.Integer(), nullable=True),
        sa.Column("pass_count", sa.Integer(), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rejected_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("quarantine_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantine_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "quarantine_duration_days",
            sa.Integer(),
            nullable=False,
            server_default="14",
        ),
        sa.Column("recheck_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rationale",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_fqr_project_status",
        "flaky_quarantine_requests",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_fqr_fingerprint",
        "flaky_quarantine_requests",
        ["project_id", "test_fingerprint"],
    )
    # Partial UNIQUE — at most one LIVE row per test fingerprint per project.
    # This is the constraint that makes ``propose_quarantine`` idempotent:
    # re-running detection on an already-proposed fingerprint updates the
    # existing row rather than fanning out duplicate proposals.
    op.create_index(
        "ux_fqr_live_per_fingerprint",
        "flaky_quarantine_requests",
        ["project_id", "test_fingerprint"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('DETECTED', 'PROPOSED', 'APPROVED', 'QUARANTINED', "
            "'RECHECK_SCHEDULED', 'RE_QUARANTINED')"
        ),
    )

    # Seed feature flag — off by default.
    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'flaky_auto_quarantine', "
            "'Flaky-test auto-quarantine workflow (QA Lead approval, "
            "recheck-after-N-days, audit trail). Tier 1 item 3.', "
            "false, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM feature_flags WHERE key = 'flaky_auto_quarantine'")
    )
    op.drop_index("ux_fqr_live_per_fingerprint", table_name="flaky_quarantine_requests")
    op.drop_index("ix_fqr_fingerprint", table_name="flaky_quarantine_requests")
    op.drop_index("ix_fqr_project_status", table_name="flaky_quarantine_requests")
    op.drop_table("flaky_quarantine_requests")
