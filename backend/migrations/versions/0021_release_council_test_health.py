"""Release Council audit trail + Test Health Coach persistence.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Release Council: audit trail + input snapshot on release_decisions ────
    op.add_column(
        "release_decisions",
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "release_decisions",
        sa.Column("override_audit", sa.JSON(), nullable=True),
    )
    op.add_column(
        "release_decisions",
        sa.Column("original_recommendation", sa.String(20), nullable=True),
    )
    op.add_column(
        "release_decisions",
        sa.Column("original_risk_score", sa.Integer(), nullable=True),
    )

    # ── Test Health Coach: persisted recommendations ─────────────────────────
    op.create_table(
        "test_health_recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("test_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_name", sa.String(1000), nullable=False),
        sa.Column("health_score", sa.Integer(), nullable=False),
        sa.Column("violations", sa.JSON(), default=list),
        sa.Column("critical_count", sa.Integer(), default=0),
        sa.Column("warning_count", sa.Integer(), default=0),
        sa.Column("recommendation", sa.Text()),
        sa.Column("anti_patterns", sa.JSON(), default=list),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_thr_run", "test_health_recommendations", ["test_run_id"])
    op.create_index("ix_thr_test_case", "test_health_recommendations", ["test_case_id"])

    # ── Flaky Coach: project-level aggregated flaky results ──────────────────
    op.create_table(
        "flaky_coach_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(1000), nullable=False),
        sa.Column("suite_name", sa.String(500), nullable=True),
        sa.Column("failure_rate", sa.Float(), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False, default=0),
        sa.Column("failed_runs", sa.Integer(), nullable=False, default=0),
        sa.Column("flaky_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantine_recommendation", sa.String(30), nullable=False, server_default="MONITOR"),
        sa.Column("stabilization_actions", sa.JSON(), default=list),
        sa.Column("impact_score", sa.Float(), default=0.0),
        sa.Column("status_history", sa.JSON(), default=list),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_fcr_project", "flaky_coach_results", ["project_id"])
    op.create_index("ix_fcr_fingerprint", "flaky_coach_results", ["test_fingerprint"])
    op.create_index("ix_fcr_quarantine", "flaky_coach_results", ["quarantine_recommendation"])


def downgrade() -> None:
    op.drop_table("flaky_coach_results")
    op.drop_table("test_health_recommendations")
    op.drop_column("release_decisions", "original_risk_score")
    op.drop_column("release_decisions", "original_recommendation")
    op.drop_column("release_decisions", "override_audit")
    op.drop_column("release_decisions", "input_snapshot")
