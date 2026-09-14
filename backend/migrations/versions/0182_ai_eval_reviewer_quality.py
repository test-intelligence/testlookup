"""Persist G3 reviewer-quality observation batches and rollups (E9.4).

The architecture used ``0181+`` as a planning label. Origin/main already uses
0181 for invocation config snapshots, so this migration takes the next head.
The table is new, so its lookup index is created transactionally with it.

Revision ID: 0182
Revises: 0181
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0182"
down_revision = "0181"
branch_labels = None
depends_on = None

TABLE = "ai_eval_reviewer_quality"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manifest_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mutation_observations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("clean_observations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("human_outcomes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("regressions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("semantic_sample_counts", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("clean_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("human_outcome_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deterministic_recall", sa.Float(), nullable=True),
        sa.Column("second_model_recall", sa.Float(), nullable=True),
        sa.Column("recall_delta", sa.Float(), nullable=True),
        sa.Column("false_flag_rate", sa.Float(), nullable=True),
        sa.Column("false_omission_rate", sa.Float(), nullable=True),
        sa.Column("auto_disable_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_disable_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "evaluated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source IN ('observation_batch', 'scheduled')",
            name="ck_aerq_source",
        ),
        sa.CheckConstraint(
            "status IN ('pass', 'fail', 'insufficient_samples')",
            name="ck_aerq_status",
        ),
        sa.CheckConstraint(
            "clean_sample_count >= 0 AND human_outcome_count >= 0",
            name="ck_aerq_sample_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "window_started_at <= window_ended_at",
            name="ck_aerq_window_order",
        ),
    )
    op.create_index(
        "ix_aerq_project_agent_evaluated",
        TABLE,
        ["project_id", "agent_id", "evaluated_at"],
    )


def downgrade() -> None:
    op.drop_table(TABLE)
