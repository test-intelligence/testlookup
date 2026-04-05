"""Add run_baselines and run_diffs tables for baseline persistence.

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Run Baselines — records which baseline was chosen and why ─────────────
    op.create_table(
        "run_baselines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("baseline_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("selection_reason", sa.String(100), nullable=False),   # "latest_passing" | "approved_release" | "no_baseline"
        sa.Column("classification", sa.String(50), nullable=False),       # run-level regression classification
        sa.Column("baseline_build_number", sa.String(100), nullable=True),
        sa.Column("pass_rate_delta", sa.Float(), nullable=True),
        sa.Column("commit_range", sa.JSON(), nullable=True),              # {from_commit, to_commit, commits_between}
        sa.Column("config_drift", sa.JSON(), nullable=True),              # [{field, old_value, new_value}]
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_run_baselines_run_id", "run_baselines", ["run_id"], unique=True)
    op.create_index("ix_run_baselines_baseline", "run_baselines", ["baseline_run_id"])

    # ── Run Diffs — full diff payload for reuse without recomputation ─────────
    op.create_table(
        "run_diffs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("baseline_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("diff_payload", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_run_diffs_run_id", "run_diffs", ["run_id"], unique=True)


def downgrade() -> None:
    op.drop_table("run_diffs")
    op.drop_table("run_baselines")
