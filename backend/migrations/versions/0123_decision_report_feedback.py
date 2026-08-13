"""Add immutable DecisionReport feedback and correction records.

Revision ID: 0123
Revises: 0122
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_report_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("report_evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feedback_kind", sa.String(length=30), nullable=False),
        sa.Column("utility_rating", sa.String(length=25), nullable=True),
        sa.Column("claim_id", sa.String(length=128), nullable=True),
        sa.Column("claim_kind", sa.String(length=20), nullable=True),
        sa.Column("correction_type", sa.String(length=30), nullable=True),
        sa.Column("corrected_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.CheckConstraint("report_version >= 1", name="ck_decision_report_feedback_report_version_positive"),
        sa.CheckConstraint("feedback_kind IN ('utility', 'claim_correction')", name="ck_decision_report_feedback_kind"),
        sa.CheckConstraint("utility_rating IS NULL OR utility_rating IN ('useful', 'partially_useful', 'not_useful')", name="ck_decision_report_feedback_utility_rating"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decision_report_feedback_report",
        "decision_report_feedback",
        ["project_id", "test_run_id", "report_id", "report_version"],
    )
    op.create_index(
        "ix_decision_report_feedback_created",
        "decision_report_feedback",
        ["created_at"],
    )
    op.create_unique_constraint(
        "uq_decision_report_feedback_user_idempotency",
        "decision_report_feedback",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_decision_report_feedback_user_idempotency",
        "decision_report_feedback",
        type_="unique",
    )
    op.drop_index("ix_decision_report_feedback_created", table_name="decision_report_feedback")
    op.drop_index("ix_decision_report_feedback_report", table_name="decision_report_feedback")
    op.drop_table("decision_report_feedback")
