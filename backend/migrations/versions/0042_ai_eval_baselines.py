"""Add ai_eval_baselines table for evaluation-as-release-gate (Phase 5).

Tracks baseline metrics per agent/task_type so that prompt/model/routing
changes can be compared against a known-good baseline before shipping.

Revision ID: 0042
Revises: 0041
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_eval_baselines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False, server_default="v1"),
        sa.Column("model_name", sa.String(200), nullable=True),
        # Baseline metrics
        sa.Column("baseline_accuracy", sa.Float(), nullable=True),
        sa.Column("baseline_precision", sa.Float(), nullable=True),
        sa.Column("baseline_recall", sa.Float(), nullable=True),
        sa.Column("baseline_f1", sa.Float(), nullable=True),
        # Gate thresholds
        sa.Column("min_accuracy", sa.Float(), nullable=False, server_default="0.80"),
        sa.Column("min_f1", sa.Float(), nullable=False, server_default="0.75"),
        sa.Column("max_regression_pct", sa.Float(), nullable=False, server_default="5.0"),
        # Metadata
        sa.Column("eval_run_id", UUID(as_uuid=True), sa.ForeignKey("ai_eval_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dataset_id", UUID(as_uuid=True), sa.ForeignKey("ai_eval_datasets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_aeb_task_agent", "ai_eval_baselines", ["task_type", "agent_name"])
    op.create_unique_constraint("uq_aeb_task_agent_prompt", "ai_eval_baselines", ["task_type", "agent_name", "prompt_version"])


def downgrade() -> None:
    op.drop_constraint("uq_aeb_task_agent_prompt", "ai_eval_baselines", type_="unique")
    op.drop_index("ix_aeb_task_agent", table_name="ai_eval_baselines")
    op.drop_table("ai_eval_baselines")
