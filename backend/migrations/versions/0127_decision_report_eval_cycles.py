"""Persist report-level evaluation corpus cycles.

Revision ID: 0127
Revises: 0126
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_report_eval_cycles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_key", sa.String(length=128), nullable=False),
        sa.Column("corpus_version", sa.String(length=120), nullable=False),
        sa.Column("corpus_sha256", sa.String(length=64), nullable=False),
        sa.Column("report_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "unavailable_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "consecutive_passes", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("evaluated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pass', 'warn', 'fail')", name="ck_drec_status"
        ),
        sa.CheckConstraint(
            "corpus_sha256 ~ '^[0-9a-f]{64}$'", name="ck_drec_corpus_hash"
        ),
        sa.CheckConstraint("report_count >= 0", name="ck_drec_report_count"),
        sa.CheckConstraint(
            "consecutive_passes >= 0", name="ck_drec_consecutive_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["evaluated_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_key", name="uq_drec_cycle_key"),
    )
    op.create_index(
        "ix_drec_corpus_evaluated",
        "decision_report_eval_cycles",
        ["corpus_version", "evaluated_at"],
    )
    op.create_index("ix_drec_status", "decision_report_eval_cycles", ["status"])


def downgrade() -> None:
    op.drop_index("ix_drec_status", table_name="decision_report_eval_cycles")
    op.drop_index(
        "ix_drec_corpus_evaluated", table_name="decision_report_eval_cycles"
    )
    op.drop_table("decision_report_eval_cycles")
